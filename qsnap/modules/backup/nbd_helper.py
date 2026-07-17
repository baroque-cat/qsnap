"""Shared NBD full-export helper for FULL backups.

Provides utilities used by both ``FileCopyBackupProvider`` and
``BitmapBackupProvider`` for creating FULL backups via the libvirt
pull-model NBD API (``virsh backup-begin`` + ``qemu-img convert -n
nbd:unix:<socket>``).

The NBD pull-model exports a frozen point-in-time view of the disk
through a Unix socket served by the QEMU process itself.  No external
process opens the qcow2 file directly, so there is no lock conflict
with the running VM's exclusive write lock.

Functions:
- :func:`is_vm_running` — detect VM running state via ``virsh dominfo``.
- :func:`is_libvirt_new_enough` — verify libvirt >= 6.0 for ``backup-begin``.
- :func:`nbd_full_export` — run the full NBD export + convert lifecycle.
- :func:`write_backup_xml` — write the libvirt pull-model backup XML.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

from qsnap.interfaces.shell import IShell
from qsnap.models.results import ShellResult

logger = logging.getLogger(__name__)

_MIN_LIBVIRT_MAJOR = 6


def is_vm_running(shell: IShell, vm_name: str) -> bool:
    """Check whether *vm_name* is running via ``virsh dominfo``.

    Parses the ``State:`` line from the ``virsh dominfo`` output.
    Returns ``True`` if the state is ``running``, ``False`` for any
    other state (including ``shut off``).

    On command failure, logs a WARNING and returns ``False`` — the
    caller should fall back to direct convert (design D8).
    """
    result = shell.run(
        ["virsh", "dominfo", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        logger.warning(
            "Failed to detect VM running state for %s: %s — "
            "assuming stopped (direct convert will be attempted)",
            vm_name,
            result.error,
        )
        return False

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("state:"):
            state = stripped.split(":", 1)[1].strip().lower()
            return "running" in state
    return False


def is_libvirt_new_enough(shell: IShell) -> bool:
    """Check whether libvirt >= 6.0 via ``virsh --version``.

    libvirt 6.0+ is required for the ``virsh backup-begin`` pull-model
    NBD API.  Returns ``True`` if the version is sufficient, ``False``
    otherwise (including when the version cannot be parsed).

    Does NOT raise — callers use the return value to decide whether to
    fall back to direct convert (design D-risk: NBD export fails on old
    libvirt).
    """
    result = shell.run(["virsh", "--version"], timeout=30)
    if not result.success:
        return False

    match = re.search(r"(\d+)\.(\d+)", result.stdout)
    if not match:
        return False

    major = int(match.group(1))
    return major >= _MIN_LIBVIRT_MAJOR


def _get_first_disk_target(shell: IShell, vm_name: str) -> str | None:
    """Get the first disk target device name via ``virsh domblklist``.

    Returns the target device name (e.g., ``"vda"``) or ``None`` if
    the command fails or no disks are found.
    """
    result = shell.run(
        ["virsh", "domblklist", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return None

    for line in result.stdout.splitlines():
        stripped = line.strip()
        # Skip header lines and separator lines.
        if not stripped or stripped.startswith("Target") or stripped.startswith("-"):
            continue
        parts = stripped.split()
        if parts:
            return parts[0]

    return None


def write_backup_xml(socket_path: str) -> Path:
    """Write a libvirt pull-model backup XML to a temp file.

    The XML uses ``mode='pull'`` with a Unix socket transport.  No
    checkpoint is created (no ``--incremental`` flag is passed to
    ``virsh backup-begin`` by the caller).

    Returns the path to the temp file containing the XML.
    """
    xml_content = (
        f"<domainbackup mode='pull'>\n"
        f"  <server transport='unix' socket='{socket_path}'/>\n"
        f"</domainbackup>\n"
    )
    fd, tmp_path = tempfile.mkstemp(prefix="qsnap-backup-", suffix=".xml")
    with os.fdopen(fd, "w") as f:
        f.write(xml_content)
    return Path(tmp_path)


def nbd_full_export(
    shell: IShell,
    vm_name: str,
    target_file: str | Path,
    compress: bool = False,
) -> ShellResult:
    """Export a full disk via NBD pull-model and convert to *target_file*.

    Lifecycle (design D2):
    (a) Remove any stale socket at ``/tmp/qsnap-backup-{pid}.sock``.
    (b) Write backup XML with pull-mode Unix socket.
    (c) Run ``virsh backup-begin --domain <vm> <xml>`` WITHOUT
        ``--incremental`` (full export, no checkpoint).
    (d) Run ``qemu-img convert [-c] -O qcow2 nbd:unix:<socket>
        <target_file>`` to pull the full disk.  When *compress* is
        ``True``, the ``-c`` flag is passed to ``qemu-img convert``,
        producing a compressed qcow2 (experimentally verified with
        qemu-img 11.0.2).
    (e) Clean up the socket via ``rm -f`` in a ``finally`` block.

    Args:
        shell: :class:`IShell` instance for running commands.
        vm_name: Domain name passed to ``virsh backup-begin``.
        target_file: Destination path for the converted qcow2.
        compress: When ``True``, add ``-c`` to ``qemu-img convert`` to
            enable compression.  Defaults to ``False`` (backwards-
            compatible with existing callers).

    Returns the :class:`ShellResult` from the final step — the
    ``qemu-img convert`` result on success/failure of that step, or
    the ``virsh backup-begin`` result if that step failed.  The socket
    is always cleaned up regardless of outcome.
    """
    socket_path = f"/tmp/qsnap-backup-{os.getpid()}.sock"

    # (a) Remove stale socket.
    shell.run(["rm", "-f", socket_path], timeout=10)

    # (b) Write backup XML.
    backup_xml_path = write_backup_xml(socket_path)

    try:
        # (c) Start NBD export via virsh backup-begin (no --incremental).
        backup_cmd = [
            "virsh",
            "backup-begin",
            "--domain",
            vm_name,
            str(backup_xml_path),
        ]
        backup_result = shell.run(backup_cmd, timeout=120)
        if not backup_result.success:
            return backup_result

        # (d) Pull full disk via NBD.
        # libvirt's NBD server exports each disk under its target
        # device name (e.g., "vda").  We must specify exportname in
        # the NBD URI to connect to the correct export.
        disk_target = _get_first_disk_target(shell, vm_name)
        nbd_uri = f"nbd:unix:{socket_path}"
        if disk_target:
            nbd_uri = f"nbd:unix:{socket_path}:exportname={disk_target}"

        convert_cmd = ["qemu-img", "convert", "-O", "qcow2"]
        if compress:
            convert_cmd.append("-c")
        convert_cmd.append(nbd_uri)
        convert_cmd.append(str(target_file))
        convert_result = shell.run(convert_cmd, timeout=3600)
        return convert_result

    finally:
        # (e) Socket cleanup (always, even on failure).
        shell.run(["rm", "-f", socket_path], timeout=10)
