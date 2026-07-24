"""Cross-cutting NBD (Network Block Device) utilities.

Provides stateless helper functions used by ``BitmapBackupProvider``
for creating FULL backups via the libvirt pull-model NBD API
(``virsh backup-begin`` + unified NBD transfer engine).

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
        check=True,
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
    result = shell.run(["virsh", "--version"], timeout=30, check=True)
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
        check=True,
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
