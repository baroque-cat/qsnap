"""Cross-cutting NBD (Network Block Device) utilities.

Provides stateless helper functions used by both
``FileCopyBackupProvider`` and ``BitmapBackupProvider`` for creating
FULL backups via the libvirt pull-model NBD API
(``virsh backup-begin`` + ``qemu-img convert -n nbd:unix:<socket>``).

These functions do not implement any ABC and are shared across module
boundaries, so they live in ``qsnap.utils`` rather than under
``qsnap.modules.backup``.

The NBD pull-model exports a frozen point-in-time view of the disk
through a Unix socket served by the QEMU process itself.  No external
process opens the qcow2 file directly, so there is no lock conflict
with the running VM's exclusive write lock.

Functions:
- :func:`is_vm_running` — detect VM running state via ``virsh dominfo``.
- :func:`is_libvirt_new_enough` — verify libvirt >= 7.2 for the
  incremental backup API (``backup-begin`` + checkpoint XML).
- :func:`nbd_full_export` — run the full NBD export + convert lifecycle.
- :func:`write_backup_xml` — write the libvirt pull-model backup XML.
- :func:`write_checkpoint_xml` — write the checkpoint XML for atomic
  checkpoint creation via ``virsh backup-begin``.
- :func:`get_first_disk_target` — get the first disk target device name.
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

_MIN_LIBVIRT_MAJOR = 7
_MIN_LIBVIRT_MINOR = 2


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


def is_libvirt_new_enough(
    shell: IShell,
    min_major: int = _MIN_LIBVIRT_MAJOR,
    min_minor: int = _MIN_LIBVIRT_MINOR,
) -> bool:
    """Check whether libvirt >= *min_major*.*min_minor* via ``virsh --version``.

    libvirt 7.2+ is required for the incremental backup API exercised by
    the NBD pull-model path — including the ``<incremental>`` backup-XML
    element and the checkpoint XML argument of ``virsh backup-begin``
    (per the libvirt knowledge base, the incremental backup API is
    complete since 7.2; the previous 6.0 threshold was insufficient).
    Returns ``True`` if the version is sufficient, ``False`` otherwise
    (including when the version cannot be parsed).

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
    minor = int(match.group(2))
    return (major, minor) >= (min_major, min_minor)


def get_first_disk_target(shell: IShell, vm_name: str) -> str | None:
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


def write_backup_xml(socket_path: str, incremental: str | None = None) -> Path:
    """Write a libvirt pull-model backup XML to a temp file.

    The XML uses ``mode='pull'`` with a Unix socket transport.

    When *incremental* is non-``None``, an ``<incremental>`` element
    naming an existing checkpoint is included as a child of
    ``<domainbackup>``, before the ``<server>`` element.  This is the
    correct libvirt mechanism for incremental NBD exports — the
    ``--incremental`` CLI flag does not exist in any version of virsh
    ``backup-begin`` (design D1).  When *incremental* is ``None``, the
    XML describes a full NBD export (no ``<incremental>`` element).

    Args:
        socket_path: Path to the Unix socket the NBD server listens on.
        incremental: Optional checkpoint name for incremental export.
            When ``None`` (default), a full export XML is produced.

    Returns the path to the temp file containing the XML.
    """
    incremental_element = f"  <incremental>{incremental}</incremental>\n" if incremental else ""
    xml_content = (
        f"<domainbackup mode='pull'>\n"
        f"{incremental_element}"
        f"  <server transport='unix' socket='{socket_path}'/>\n"
        f"</domainbackup>\n"
    )
    fd, tmp_path = tempfile.mkstemp(prefix="qsnap-backup-", suffix=".xml")
    with os.fdopen(fd, "w") as f:
        f.write(xml_content)
    return Path(tmp_path)


def write_checkpoint_xml(checkpoint_name: str) -> Path:
    """Write a libvirt checkpoint XML to a temp file.

    The checkpoint XML is passed as the third positional argument to
    ``virsh backup-begin --domain <vm> <backup.xml> <checkpoint.xml>``
    so the successor checkpoint is created **atomically** at the
    export's freeze point (design D1/D5).  This replaces the post-hoc
    ``virsh checkpoint-create-as`` call that previously ran after the
    export had finished.

    Args:
        checkpoint_name: Name of the checkpoint to create.  Embedded as
            ``<domaincheckpoint><name>{checkpoint_name}</name></domaincheckpoint>``.

    Returns the path to the temp file containing the XML.
    """
    xml_content = f"<domaincheckpoint><name>{checkpoint_name}</name></domaincheckpoint>"
    fd, tmp_path = tempfile.mkstemp(prefix="qsnap-checkpoint-", suffix=".xml")
    with os.fdopen(fd, "w") as f:
        f.write(xml_content)
    return Path(tmp_path)


def nbd_full_export(
    shell: IShell,
    vm_name: str,
    target_file: str | Path,
    compress: bool = False,
    compression_type: str = "zstd",
    stall_timeout: int = 1800,
    checkpoint_name: str | None = None,
) -> ShellResult:
    """Export a full disk via NBD pull-model and convert to *target_file*.

    Lifecycle (design D2/D5):
        (a) Remove any stale socket at ``/tmp/qsnap-backup-{pid}.sock``.
        (b) Write backup XML with pull-mode Unix socket.  When
            *checkpoint_name* is non-``None``, also write a checkpoint
            XML via :func:`write_checkpoint_xml`.
        (c) Run ``virsh backup-begin --domain <vm> <backup.xml>
            [<checkpoint.xml>]`` WITHOUT ``--incremental`` (full export).
            When a checkpoint XML is passed as the third positional
            argument, libvirt creates the successor checkpoint
            **atomically at the export's freeze point** (design D1) —
            its dirty-bitmap baseline coincides with the frozen view,
            so writes during the export are tracked for the next
            incremental.  When *checkpoint_name* is ``None``, the
            command line is byte-identical to the pre-checkpoint
            behavior (used by the file-copy path, which creates no
            checkpoints).
        (d) Run ``qemu-img convert [-c] [-o compression_type=<type>]
            -O qcow2 nbd:unix:<socket> <target_file>`` to pull the full
            disk.  When *compress* is ``True``, the ``-c`` flag is passed
            to ``qemu-img convert``, producing a compressed qcow2.  When
            *compression_type* is ``"zstd"``, ``-o compression_type=zstd``
            is added for 11x faster compression than the default zlib.
            On convert failure after a checkpoint was created, the
            just-created checkpoint is deleted best-effort (WARNING on
            failure) so it cannot become the newest baseline of a failed
            export (design D3 risk mitigation).
        (e) Call ``virsh domjobabort --domain <vm>`` to terminate the
            backup job and release the state change lock, then clean up
            the socket via ``rm -f`` and remove the XML temp files.
            Steps (e) SHALL execute in a ``finally`` block and SHALL
            run regardless of whether ``qemu-img convert`` succeeded or
            failed.

    Args:
        shell: :class:`IShell` instance for running commands.
        vm_name: Domain name passed to ``virsh backup-begin``.
        target_file: Destination path for the converted qcow2.
        compress: When ``True``, add ``-c`` to ``qemu-img convert`` to
            enable compression.  Defaults to ``False`` (backwards-
            compatible with existing callers).
        compression_type: Compression algorithm (``"zstd"`` default,
            ``"zlib"`` alternative).  Only effective when *compress* is
            ``True``.  When ``"zstd"``, adds ``-o compression_type=zstd``.
        stall_timeout: Stall-detection timeout in seconds for the
            convert command.  When ``0``, falls back to
            :meth:`IShell.run` with a fixed 3600s timeout.
        checkpoint_name: When non-``None``, a checkpoint XML naming this
            checkpoint is written and passed as the third positional
            argument to ``virsh backup-begin``, creating the checkpoint
            atomically with the export.  Defaults to ``None`` (no
            checkpoint — file-copy behavior).

    Returns the :class:`ShellResult` from the final step — the
    ``qemu-img convert`` result on success/failure of that step, or
    the ``virsh backup-begin`` result if that step failed.  The socket
    and both XML temp files are always cleaned up regardless of outcome.
    """
    socket_path = f"/tmp/qsnap-backup-{os.getpid()}.sock"

    # (a) Remove stale socket.
    shell.run(["rm", "-f", socket_path], timeout=10)

    # (b) Write backup XML (and checkpoint XML when requested).
    backup_xml_path = write_backup_xml(socket_path)
    checkpoint_xml_path: Path | None = None
    if checkpoint_name is not None:
        checkpoint_xml_path = write_checkpoint_xml(checkpoint_name)

    try:
        # (c) Start NBD export via virsh backup-begin (no --incremental).
        # The checkpoint XML is the third positional argument; libvirt
        # creates the checkpoint atomically at the export's freeze point.
        backup_cmd = [
            "virsh",
            "backup-begin",
            "--domain",
            vm_name,
            str(backup_xml_path),
        ]
        if checkpoint_xml_path is not None:
            backup_cmd.append(str(checkpoint_xml_path))
        backup_result = shell.run(backup_cmd, timeout=120)
        if not backup_result.success:
            # backup-begin is atomic: the successor checkpoint was NOT
            # created, so there is nothing to roll back.
            return backup_result

        # (d) Pull full disk via NBD.
        # libvirt's NBD server exports each disk under its target
        # device name (e.g., "vda").  We must specify exportname in
        # the NBD URI to connect to the correct export.
        disk_target = get_first_disk_target(shell, vm_name)
        nbd_uri = f"nbd:unix:{socket_path}"
        if disk_target:
            nbd_uri = f"nbd:unix:{socket_path}:exportname={disk_target}"

        convert_cmd = ["qemu-img", "convert", "-O", "qcow2"]
        if compress:
            convert_cmd.append("-c")
            if compression_type == "zstd":
                convert_cmd.extend(["-o", "compression_type=zstd"])
        convert_cmd.append(nbd_uri)
        convert_cmd.append(str(target_file))
        if stall_timeout > 0:
            convert_result = shell.run_with_stall_detection(
                convert_cmd,
                output_file=Path(target_file),
                stall_timeout=stall_timeout,
            )
        else:
            convert_result = shell.run(convert_cmd, timeout=3600)
        if not convert_result.success and checkpoint_name is not None:
            # The export failed but the checkpoint created atomically
            # with backup-begin still exists.  Delete it best-effort so
            # it cannot become the newest baseline of a failed export —
            # the prior checkpoint must remain the valid baseline for
            # the next run (retry safety, design D3).
            del_result = shell.run(
                [
                    "virsh",
                    "checkpoint-delete",
                    "--domain",
                    vm_name,
                    checkpoint_name,
                    "--metadata",
                ],
                timeout=30,
            )
            if not del_result.success:
                logger.warning(
                    "Failed to delete checkpoint %s for VM %s after failed export: %s",
                    checkpoint_name,
                    vm_name,
                    del_result.error,
                )
        return convert_result

    finally:
        # (e) NBD job abort + socket cleanup (always, even on failure).
        # Abort the virsh backup-begin job to release the VM state
        # change lock (design D3).  domjobabort is idempotent — safe
        # to call when no job is running.  On failure, log a WARNING
        # but do NOT propagate the error — the socket cleanup is the
        # critical path and must still proceed.
        abort_cmd = ["virsh", "domjobabort", "--domain", vm_name]
        abort_result = shell.run(abort_cmd, timeout=30)
        if not abort_result.success:
            logger.warning(
                "virsh domjobabort failed for VM %s (job may have already terminated): %s",
                vm_name,
                abort_result.error,
            )
        # Remove the socket file.
        shell.run(["rm", "-f", socket_path], timeout=10)
        # Remove the XML temp files (local filesystem, not shell —
        # keeps them out of the IShell command stream).
        for xml_path in (backup_xml_path, checkpoint_xml_path):
            if xml_path is None:
                continue
            try:
                xml_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Failed to remove temp XML file %s: %s", xml_path, exc)
