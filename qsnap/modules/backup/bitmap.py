"""BitmapBackupProvider — NBD pull-model incremental backup via libvirt.

Implements ``IBackupProvider``.  Does NOT inherit from Core (design D1).
Dependencies: ``IShell`` and ``INbdClient``
(third constructor parameter — the dirty-block transfer transport).

Uses ``virsh backup-begin`` with a pull-model NBD Unix socket to export
the frozen point-in-time view.  **FULL** backups are transferred via
``qemu-img convert`` (C code, parallel coroutines, ~850 MB/s zstd) —
replacing the former Python ``pread``/``pwrite`` loop + write-side
``qemu-nbd`` with ``driver=compress``.  **Incremental** backups are
still pulled by the unified NBD transfer engine (:meth:`_transfer`):
the loop negotiates ``base:allocation`` (and
``qemu:dirty-bitmap:backup-<disk>`` for incrementals) meta-contexts,
queries block status, and ``pread``/``pwrite``s only the relevant
extents into the target qcow2 served by a forked ``qemu-nbd``.

For FULL backups (no prior checkpoint): ``qemu-img convert`` reads
from the NBD source socket (running VMs) or directly from the source
qcow2 file (stopped VMs), writing to the target qcow2 with optional
``-c`` compression, parallel coroutines (``-m N``, configurable),
and out-of-order writes (``-W``, configurable).  For
incremental exports: ``zero_skip=False`` intersects dirty extents with
allocated extents and copies only dirty∩allocated blocks into a
**backing-chained** qcow2 delta (created via ``qemu-img create -b
<previous backup> -F qcow2``).

Every ``virsh backup-begin`` receives a checkpoint XML as its third
positional argument, so the successor checkpoint (the dirty-bitmap
baseline for the next incremental) is created **atomically at the
export's freeze point** — never post-hoc via a standalone
``virsh checkpoint-create-as`` call.  The bitmap baseline therefore
always coincides with the exported point-in-time view, and the backup
chain (FULL + incrementals) is gap-free by construction.

**NBD backup lifecycle:**

1. Remove any stale socket at ``/tmp/qsnap-backup-{pid}.sock``.
2. Write backup XML with NBD Unix socket to a temp file.  When a prior
   qsnap checkpoint exists, an ``<incremental>`` element naming that
   checkpoint is embedded in the XML (the ``--incremental`` CLI flag
   does not exist in any version of virsh ``backup-begin``).  Also
   write a checkpoint XML naming the successor checkpoint
   (``qsnap-{target_hash}-{yyyymmddTHHMMSS}-{6_hex}``).
3. ``virsh backup-begin --domain VM <backupxml> <checkpointxml>``
   starts the NBD export (full when no ``<incremental>`` element,
   incremental otherwise) and atomically creates the successor
   checkpoint at the export's freeze point.
4. Transfer the export:
   - FULL (no prior checkpoint): ``qemu-img convert`` reads from
     ``nbd:unix:<socket>`` (running VM) or ``<source_path>`` (stopped
     VM) and writes to the target qcow2 with optional ``-c``
     compression.  No write-side ``qemu-nbd``, no Python
     ``pread``/``pwrite`` loop.
   - Incremental (prior checkpoint): negotiate
     ``base:allocation`` + ``qemu:dirty-bitmap:backup-<disk>``,
     intersect dirty∩allocated, ``pread``/``pwrite`` only dirty blocks
     into ``<name>.qcow2.tmp`` (backing: the previous backup at the
     target), served through a forked ``qemu-nbd`` (uncompressed —
     design D6), then atomically renamed to the final name.
5. Verify the target file (if ``target.verify != "off"``):
   :func:`verify_full_backup` for full pulls,
   :func:`verify_bitmap_incremental` (format, virtual-size,
   backing-filename, dirty-size regression barrier) for incrementals.
6. After a successful AND verified export, delete all superseded
   (older) qsnap checkpoints for this VM+target via
   ``virsh checkpoint-delete`` (full delete with ``--metadata``
   fallback) — the successor already exists, so rotation never
   opens a zero-checkpoint window.
7. On export/verify failure, preserve the prior checkpoint for retry,
   delete the just-created successor checkpoint best-effort, and
   delete the partial target file.  If ``backup-begin`` fails with
   "Bitmap already exists" (stale checkpoint from a crashed prior
   run), force-clean all qsnap checkpoints for this VM+target and
   retry with a fresh successor name (collision recovery, design D6).
8. Socket(s), the forked ``qemu-nbd`` process (via pidfile, incremental
   only), the ``.tmp`` file, and XML temp files are always cleaned up
   in a ``finally`` block.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.nbd import INbdClient
from qsnap.interfaces.shell import IShell
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import BackupInfo, BackupResult, NbdExtent, ShellResult
from qsnap.utils.extents import overlap_with_allocation, unify_extents
from qsnap.utils.nbd import (
    is_vm_running,
    write_backup_xml,
    write_checkpoint_xml,
)
from qsnap.utils.parsing import parse_disk_from_snapshot_name, parse_timestamp
from qsnap.utils.verification import verify_bitmap_incremental, verify_full_backup

logger = logging.getLogger(__name__)

_BASE_ALLOCATION_CONTEXT = "base:allocation"
"""NBD standard meta-context advertising allocated vs hole/zero extents."""


@dataclass(frozen=True)
class _CopyResult:
    """Outcome of the dirty-block copy loop (design D2).

    ``error`` is ``None`` on success.  ``previous_path`` is the resolved
    backing file (needed by verification's backing-filename check) and
    ``dirty_bytes`` the sum of dirty extent lengths measured before the
    copy (feeds the verification regression barrier).
    """

    error: str | None
    previous_path: Path | None
    dirty_bytes: int


class BitmapBackupProvider(IBackupProvider):
    """Backup provider using NBD pull-model via ``virsh backup-begin``."""

    def __init__(
        self,
        shell: IShell,
        nbd: INbdClient | None = None,
    ) -> None:
        """Create the provider.

        ``nbd`` is the NBD transport for the incremental dirty-block
        copy loop (design D1).  ``DefaultFactory`` wires a
        :class:`LibnbdClient`; tests inject a ``MockNbdClient``.  It is
        optional only so paths that never touch the client (FULL
        backups, listing, deletion) can be exercised without it — the
        incremental copy loop fails with an actionable error when it is
        ``None``.
        """
        self._shell = shell
        self._nbd = nbd

    # ── IBackupProvider implementation ────────────────────────────────

    def _query_virtual_size(self, source_path: Path) -> int | None:
        """Query the virtual disk size of *source_path* via ``qemu-img info``.

        Returns the virtual-size in bytes, or ``None`` on failure (the
        caller falls back to creating a size-less qcow2, which will
        fail on the first pwrite — the error is then surfaced normally).
        """
        info_result = self._shell.run(
            ["qemu-img", "info", "--force-share", "--output=json", str(source_path)],
            timeout=60,
            check=True,
        )
        if not info_result.success:
            return None
        try:
            info = cast(dict[str, object], json.loads(info_result.stdout))
        except json.JSONDecodeError:
            return None
        vsize_raw = info.get("virtual-size", 0)
        if isinstance(vsize_raw, (int, float)):
            return int(vsize_raw) or None
        return None

    def _cleanup_partial_file(self, target_file: Path) -> None:
        """Best-effort deletion of a partially-transferred file.

        Called after a transfer or verification failure to remove the
        partial target file so retention cleanup does not find it and
        log a misleading ``[delete] removed backup`` message (design D2).
        Failures are logged but never propagated — the caller is already
        in a failure path.
        """
        try:
            result = self._shell.run(["rm", "-f", str(target_file)], timeout=10, check=True)
            if not result.success:
                logger.warning(
                    "Failed to delete partial backup file %s: %s",
                    target_file,
                    result.error,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning(
                "Failed to delete partial backup file %s: %s",
                target_file,
                exc,
            )

    # ── run_backup (orthogonal) ─────────────────────────────────────

    def run_backup(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        disk: DiskConfig,
        *,
        force_full: bool = False,
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
        convert_parallel: int = 4,
        convert_out_of_order: bool = True,
    ) -> BackupResult:
        """Create exactly one backup for *disk* on *target*.

        See :meth:`IBackupProvider.run_backup` for the interface contract.
        """
        target_hash = self.target_hash(str(target.path))
        disk_target = disk.target

        # 1. Discover the newest checkpoint for this VM+target+disk.
        candidates = self._list_checkpoints_for_target(vm_config.name, target_hash, disk_target)
        prior = self._select_newest(candidates, target_hash, disk_target, vm_config.name)

        # 2. VM power state.
        running = is_vm_running(self._shell, vm_config.name)

        # 3. Stopped-VM defer (design D6): stopped + checkpoint exists →
        #    no data transferred, no baseline update.
        if prior is not None and not running:
            logger.info(
                "VM %s stopped — backup deferred for disk %s",
                vm_config.name,
                disk_target,
            )
            return BackupResult(
                success=True,
                snapshot_name="",
                source_path=disk.base_image,
                target_path=Path(),
                bytes_transferred=0,
                error=None,
                disk=disk_target,
                deferred=True,
            )

        # 4. Blockjob probe (design D9): when an active blockjob is
        #    present on the disk, defer the backup for this run.
        if running:
            blockjob_cmd = [
                "virsh",
                "blockjob",
                "--domain",
                vm_config.name,
                "--path",
                str(disk.base_image),
            ]
            blockjob_result = self._shell.run(blockjob_cmd, timeout=30, check=True)
            if blockjob_result.success and "No current block job" not in blockjob_result.stdout:
                logger.info(
                    "[backup] %s: blockjob active on disk %s — backup deferred for this run",
                    vm_config.name,
                    disk_target,
                )
                return BackupResult(
                    success=True,
                    snapshot_name="",
                    source_path=disk.base_image,
                    target_path=Path(),
                    bytes_transferred=0,
                    error=None,
                    disk=disk_target,
                    deferred=True,
                )

        # 5. Determine backup kind: FULL when no checkpoint or forced.
        is_full = prior is None or force_full

        # 6. Freeze-timestamp naming (design D3).
        freeze_ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        hex_suffix = secrets.token_hex(3)
        if is_full:
            backup_name = f"{vm_config.name}.FULL.{freeze_ts}_{disk_target}_{hex_suffix}"
        else:
            backup_name = f"{vm_config.name}.{freeze_ts}_{disk_target}_{hex_suffix}"

        target_file = target.path / f"{backup_name}.qcow2"
        tmp_file = Path(f"{target_file}.tmp")

        # 7. Successor checkpoint name (created atomically with
        #    backup-begin on the running-VM path).
        successor = self._new_checkpoint_name(target_hash, disk_target, taken=set(candidates))

        # Reported checkpoint — None unless backup-begin succeeds on
        # a running VM (Core's rollback deletes exactly this checkpoint
        # on failure).
        reported_checkpoint: str | None = None

        start_time = time.monotonic()

        try:
            if running:
                # ── Running VM: NBD export ──────────────────────────
                socket_path = f"/tmp/qsnap-backup-{os.getpid()}-{disk_target}.sock"
                self._shell.run(["rm", "-f", socket_path], timeout=10)

                backup_xml_path = write_backup_xml(
                    socket_path,
                    incremental=prior if not is_full else None,
                    disk=disk_target,
                )
                checkpoint_xml_path = write_checkpoint_xml(successor)

                # backup-begin (checkpoint XML is third positional arg
                # → atomically created at freeze point).
                backup_cmd = [
                    "virsh",
                    "backup-begin",
                    "--domain",
                    vm_config.name,
                    str(backup_xml_path),
                    str(checkpoint_xml_path),
                ]
                backup_result = self._shell.run(backup_cmd, timeout=120, check=True)
                if not backup_result.success:
                    # Collision recovery (design D6).
                    if self._is_collision_error(backup_result.error):
                        logger.warning(
                            "[backup] %s: checkpoint/bitmap collision "
                            "detected (%s) — force-cleaning stale "
                            "checkpoints and retrying",
                            vm_config.name,
                            backup_result.error,
                        )
                        self._force_cleanup_checkpoints(vm_config.name, target_hash, disk_target)
                        candidates = self._list_checkpoints_for_target(
                            vm_config.name, target_hash, disk_target
                        )
                        # Re-determine prior after cleanup: all
                        # checkpoints for this target+disk were wiped,
                        # so prior will be None → this becomes a FULL.
                        # The old backup XML (with incremental=prior)
                        # is invalid now — rewrite it for the new kind.
                        was_incremental = not is_full
                        prior = self._select_newest(
                            candidates,
                            target_hash,
                            disk_target,
                            vm_config.name,
                        )
                        is_full = prior is None or force_full
                        if was_incremental and is_full:
                            with contextlib.suppress(OSError):
                                backup_xml_path.unlink(missing_ok=True)
                            backup_xml_path = write_backup_xml(
                                socket_path,
                                disk=disk_target,
                            )
                        elif not is_full and prior is not None:
                            with contextlib.suppress(OSError):
                                backup_xml_path.unlink(missing_ok=True)
                            backup_xml_path = write_backup_xml(
                                socket_path,
                                incremental=prior,
                                disk=disk_target,
                            )
                        successor = self._new_checkpoint_name(
                            target_hash,
                            disk_target,
                            taken=set(candidates),
                        )
                        checkpoint_xml_path = write_checkpoint_xml(successor)
                        backup_cmd = [
                            "virsh",
                            "backup-begin",
                            "--domain",
                            vm_config.name,
                            str(backup_xml_path),
                            str(checkpoint_xml_path),
                        ]
                        backup_result = self._shell.run(backup_cmd, timeout=120, check=True)
                    if not backup_result.success:
                        for xml_path in (backup_xml_path, checkpoint_xml_path):
                            with contextlib.suppress(OSError):
                                xml_path.unlink(missing_ok=True)
                        self._shell.run(["rm", "-f", socket_path], timeout=10)
                        return BackupResult(
                            success=False,
                            snapshot_name=backup_name,
                            source_path=disk.base_image,
                            target_path=target_file,
                            bytes_transferred=0,
                            error=backup_result.error,
                            disk=disk_target,
                        )

                reported_checkpoint = successor

                # Transfer.
                if is_full:
                    transfer_error, dirty_bytes = self._full_pull_lifecycle(
                        vm_name=vm_config.name,
                        tmp_file=tmp_file,
                        final_file=target_file,
                        socket_path=socket_path,
                        source_path=None,
                        compress=target.compress,
                        compression_type=compression_type,
                        stall_timeout=stall_timeout,
                        backup_xml_path=backup_xml_path,
                        checkpoint_xml_path=checkpoint_xml_path,
                        disk_target=disk_target,
                        convert_parallel=convert_parallel,
                        convert_out_of_order=convert_out_of_order,
                    )
                    previous_path: Path | None = None
                else:
                    write_socket = f"/tmp/qsnap-write-{os.getpid()}-{disk_target}.sock"
                    pid_file = Path(f"/tmp/qsnap-qemu-nbd-{os.getpid()}-{disk_target}.pid")

                    copy = self._copy_dirty_blocks(
                        vm_config.name,
                        target,
                        target_file,
                        socket_path,
                        write_socket,
                        pid_file,
                        disk_target,
                        stall_timeout,
                    )
                    transfer_error = copy.error
                    dirty_bytes = copy.dirty_bytes
                    previous_path = copy.previous_path

                if transfer_error is not None:
                    self._cleanup_partial_file(target_file)
                    self._delete_checkpoint_best_effort(vm_config.name, successor)
                    return BackupResult(
                        success=False,
                        snapshot_name=backup_name,
                        source_path=disk.base_image,
                        target_path=target_file,
                        bytes_transferred=0,
                        error=transfer_error,
                        disk=disk_target,
                        checkpoint=reported_checkpoint,
                    )

                # Verify (if enabled).
                if is_full:
                    verify_error = verify_full_backup(
                        self._shell,
                        target_file,
                        target.verify,
                    )
                else:
                    verify_error = verify_bitmap_incremental(
                        self._shell,
                        str(disk.base_image),
                        str(target_file),
                        str(previous_path),
                        dirty_bytes,
                        target.verify,
                    )
                if verify_error is not None:
                    self._cleanup_partial_file(target_file)
                    self._delete_checkpoint_best_effort(vm_config.name, successor)
                    return BackupResult(
                        success=False,
                        snapshot_name=backup_name,
                        source_path=disk.base_image,
                        target_path=target_file,
                        bytes_transferred=0,
                        error=verify_error,
                        disk=disk_target,
                        checkpoint=reported_checkpoint,
                    )

                # Post-transfer validation for incrementals.
                if not is_full:
                    # Chain-to-FULL traversability.
                    chain_cmd = [
                        "qemu-img",
                        "info",
                        "--force-share",
                        "--backing-chain",
                        "--output=json",
                        str(target_file),
                    ]
                    chain_result = self._shell.run(chain_cmd, timeout=60, check=True)
                    chain_ok = False
                    if chain_result.success:
                        try:
                            chain_data = json.loads(chain_result.stdout)
                            if isinstance(chain_data, list) and len(chain_data) > 0:
                                chain_ok = True
                        except json.JSONDecodeError:
                            pass
                    if not chain_ok:
                        logger.critical(
                            "chain-to-FULL verification failed for %s",
                            target_file,
                        )
                        self._cleanup_partial_file(target_file)
                        self._delete_checkpoint_best_effort(vm_config.name, successor)
                        return BackupResult(
                            success=False,
                            snapshot_name=backup_name,
                            source_path=disk.base_image,
                            target_path=target_file,
                            bytes_transferred=0,
                            error="chain-to-FULL not traversable",
                            disk=disk_target,
                        )

                    # Checkpoint existence.
                    checkpoints = self._list_checkpoints_for_target(
                        vm_config.name, target_hash, disk_target
                    )
                    if not checkpoints:
                        logger.critical(
                            "no checkpoint found after incremental transfer for VM %s",
                            vm_config.name,
                        )
                        self._cleanup_partial_file(target_file)
                        self._delete_checkpoint_best_effort(vm_config.name, successor)
                        return BackupResult(
                            success=False,
                            snapshot_name=backup_name,
                            source_path=disk.base_image,
                            target_path=target_file,
                            bytes_transferred=0,
                            error="checkpoint missing — next incremental impossible",
                            disk=disk_target,
                        )

                # Checkpoint rotation.
                self._delete_superseded_checkpoints(
                    vm_config.name, target_hash, disk_target, successor
                )

            else:
                # ── Stopped VM: offline FULL ──────────────────────────
                # Stopped VM with no checkpoint → offline FULL via
                # qemu-img convert from the source disk file.
                # No checkpoint is created for offline FULLs.
                source_path = disk.base_image
                transfer_error, dirty_bytes = self._full_pull_lifecycle(
                    vm_name=vm_config.name,
                    tmp_file=tmp_file,
                    final_file=target_file,
                    socket_path=None,
                    source_path=source_path,
                    compress=target.compress,
                    compression_type=compression_type,
                    stall_timeout=stall_timeout,
                    backup_xml_path=None,
                    checkpoint_xml_path=None,
                    convert_parallel=convert_parallel,
                    convert_out_of_order=convert_out_of_order,
                )
                if transfer_error is not None:
                    return BackupResult(
                        success=False,
                        snapshot_name=backup_name,
                        source_path=disk.base_image,
                        target_path=target_file,
                        bytes_transferred=0,
                        error=transfer_error,
                        disk=disk_target,
                    )

            # Get final file size.
            try:
                bytes_transferred = target_file.stat().st_size
            except OSError:
                bytes_transferred = 0

            elapsed = time.monotonic() - start_time
            return BackupResult(
                success=True,
                snapshot_name=backup_name,
                source_path=disk.base_image,
                target_path=target_file,
                bytes_transferred=bytes_transferred,
                error=None,
                duration=elapsed,
                disk=disk_target,
                checkpoint=reported_checkpoint,
            )

        finally:
            if running:
                # Terminate forked qemu-nbd (delta path).
                pid_file = Path(f"/tmp/qsnap-qemu-nbd-{os.getpid()}-{disk_target}.pid")
                self._terminate_qemu_nbd(pid_file)
                write_socket = f"/tmp/qsnap-write-{os.getpid()}-{disk_target}.sock"
                self._shell.run(["rm", "-f", write_socket, str(pid_file)], timeout=10)
                # Partial .tmp removal.
                self._shell.run(["rm", "-f", str(tmp_file)], timeout=10)
                # Abort backup-begin job.
                abort_result = self._shell.run(
                    ["virsh", "domjobabort", "--domain", vm_config.name],
                    timeout=30,
                    check=True,
                )
                if not abort_result.success:
                    logger.warning(
                        "virsh domjobabort failed for VM %s (job may have already terminated): %s",
                        vm_config.name,
                        abort_result.error,
                    )
                socket_path = f"/tmp/qsnap-backup-{os.getpid()}-{disk_target}.sock"
                self._shell.run(["rm", "-f", socket_path], timeout=10)
                # Temp XML cleanup.
                for xml_path in (backup_xml_path, checkpoint_xml_path):
                    try:
                        xml_path.unlink(missing_ok=True)
                    except OSError as exc:
                        logger.warning(
                            "Failed to remove temp XML file %s: %s",
                            xml_path,
                            exc,
                        )
            else:
                # Stopped VM: just clean up .tmp.
                self._shell.run(["rm", "-f", str(tmp_file)], timeout=10)

    # ── shared FULL-pull lifecycle (design D7) ────────────────────────

    def _qemu_img_convert_transfer(
        self,
        *,
        socket_path: str | None,
        source_path: Path | None,
        tmp_file: Path,
        compress: bool,
        compression_type: str,
        stall_timeout: int,
        disk_target: str | None = None,
        parallel: int = 4,
        out_of_order: bool = True,
    ) -> tuple[str | None, int]:
        """Execute ``qemu-img convert`` via :meth:`run_with_stall_detection`.

        Constructs and executes the ``qemu-img convert`` command that
        replaces the Python ``pread``/``pwrite`` loop + write-side
        ``qemu-nbd`` for FULL backups (design D1/D5).

        For **running VMs** (*socket_path* set, *source_path* ``None``):
        reads from the NBD source socket started by ``virsh
        backup-begin``::

            qemu-img convert [-c] -O qcow2 [-o compression_type=<type>] \\
                -m 4 -W -p nbd:unix:<socket>:exportname=<disk_target> <tmp_file>

        The ``:exportname=<disk_target>`` suffix is required because
        libvirt's NBD server exports each disk under its target device
        name (e.g., ``vda``).

        For **stopped VMs** (*source_path* set, *socket_path* ``None``):
        reads directly from the source qcow2 file::

            qemu-img convert [-c] -O qcow2 [-o compression_type=<type>] \\
                -m 4 -W -p <source_path> <tmp_file>

        When *compress* is ``True``, ``-c`` and
        ``-o compression_type=<compression_type>`` are included.
        When ``False``, neither is present.

        ``-m N`` (parallel coroutines, configurable via
        ``convert_parallel``), ``-W`` (out-of-order writes, configurable
        via ``convert_out_of_order``), and ``-p`` (progress bar) are
        included for optimal throughput.

        Executed via :meth:`IShell.run_with_stall_detection` with
        *tmp_file* as ``output_file`` and *stall_timeout* as
        ``stall_timeout`` — monitors output file growth and kills the
        process only when no progress is detected.

        Returns ``(error, bytes_transferred)`` — *error* is ``None`` on
        success; *bytes_transferred* is the size of the resulting
        ``.tmp`` file (0 on failure).
        """
        cmd: list[str] = ["qemu-img", "convert"]
        if compress:
            cmd.extend(["-c", "-O", "qcow2", "-o", f"compression_type={compression_type}"])
        else:
            cmd.extend(["-O", "qcow2"])
        cmd.extend(["-m", str(parallel)])
        if out_of_order:
            cmd.append("-W")
        cmd.append("-p")

        # Source: NBD socket for running VMs, file path for stopped VMs.
        if socket_path is not None:
            # libvirt exports each disk under its target device name
            # (e.g., "vda"); the exportname suffix is required.
            if disk_target:
                cmd.append(f"nbd:unix:{socket_path}:exportname={disk_target}")
            else:
                cmd.append(f"nbd:unix:{socket_path}")
        else:
            assert source_path is not None, "either socket_path or source_path must be set"
            cmd.append(str(source_path))

        # Target: .tmp file.
        cmd.append(str(tmp_file))

        result = self._shell.run_with_stall_detection(
            cmd,
            output_file=tmp_file,
            stall_timeout=stall_timeout,
            check=True,
        )
        if not result.success:
            return result.error or "qemu-img convert failed", 0

        # Measure bytes transferred from the output file size.
        try:
            bytes_transferred = tmp_file.stat().st_size
        except OSError:
            bytes_transferred = 0

        return None, bytes_transferred

    def _full_pull_lifecycle(
        self,
        *,
        vm_name: str,
        tmp_file: Path,
        final_file: Path,
        socket_path: str | None,
        source_path: Path | None,
        compress: bool,
        compression_type: str,
        stall_timeout: int,
        backup_xml_path: Path | None,
        checkpoint_xml_path: Path | None,
        disk_target: str | None = None,
        convert_parallel: int = 4,
        convert_out_of_order: bool = True,
    ) -> tuple[str | None, int]:
        """Shared FULL-pull scaffolding for ``transfer_missing()`` and
        ``create_full_backup()`` (design D7).

        Handles: ``qemu-img convert`` via
        :meth:`_qemu_img_convert_transfer` (using
        :meth:`run_with_stall_detection`), ``mv .tmp → final``, and the
        ``finally`` cleanup.

        For **running VMs** (*socket_path* set, *source_path* ``None``):
        ``qemu-img convert`` reads from ``nbd:unix:<socket>``.
        *disk_target* (e.g., ``"vda"``) is forwarded as the NBD export
        name so libvirt serves the correct disk.

        For **stopped VMs** (*source_path* set, *socket_path* ``None``):
        ``qemu-img convert`` reads directly from the source qcow2 file.

        Returns ``(error, dirty_bytes)`` — *error* is ``None`` on
        success; *dirty_bytes* is the size of the transferred file.
        """
        try:
            # (1) Execute the FULL transfer via qemu-img convert (C code,
            # parallel coroutines, ~850 MB/s zstd).
            transfer_error, bytes_transferred = self._qemu_img_convert_transfer(
                socket_path=socket_path,
                source_path=source_path,
                tmp_file=tmp_file,
                compress=compress,
                compression_type=compression_type,
                stall_timeout=stall_timeout,
                disk_target=disk_target,
                parallel=convert_parallel,
                out_of_order=convert_out_of_order,
            )

            # (2) Atomic rename: mv .tmp to final name.
            if transfer_error is None:
                mv_result = self._shell.run(
                    ["mv", str(tmp_file), str(final_file)],
                    timeout=30,
                    check=True,
                )
                if not mv_result.success:
                    transfer_error = f"atomic rename failed: {mv_result.error}"

            return transfer_error, bytes_transferred

        finally:
            # Cleanup (always, even on failure — design D2, spec:
            # write-side lifecycle is crash-safe).
            # Remove .tmp file — a no-op on success (renamed to final),
            # removes the partial file on every failure/exception path.
            self._shell.run(["rm", "-f", str(tmp_file)], timeout=10)
            # Abort the virsh backup-begin job to release the VM state
            # change lock (design D2).  domjobabort is idempotent — safe
            # to call when no job is running (e.g. stopped-VM path).
            abort_result = self._shell.run(
                ["virsh", "domjobabort", "--domain", vm_name],
                timeout=30,
                check=True,
            )
            if not abort_result.success:
                logger.warning(
                    "virsh domjobabort failed for VM %s (job may have already terminated): %s",
                    vm_name,
                    abort_result.error,
                )
            # Source (libvirt) socket cleanup — only for running-VM path.
            if socket_path is not None:
                self._shell.run(["rm", "-f", socket_path], timeout=10)
            # Temp XML cleanup (local filesystem, not shell — keeps
            # the files out of the IShell command stream).
            for xml_path in (backup_xml_path, checkpoint_xml_path):
                if xml_path is not None:
                    try:
                        xml_path.unlink(missing_ok=True)
                    except OSError as exc:
                        logger.warning(
                            "Failed to remove temp XML file %s: %s",
                            xml_path,
                            exc,
                        )

    # ── unified NBD transfer engine (design D1/D2/D4) ────────────────

    def _start_write_server(
        self,
        target_file: Path,
        write_socket: str,
        pid_file: Path,
        compress: bool = False,
    ) -> ShellResult:
        """Start a forked ``qemu-nbd`` serving *target_file* for writing.

        When ``compress`` is ``True``, uses the qemu-nbd compress driver
        via ``--image-opts`` (design D6): the destination qcow2 receives
        compressed clusters.  When ``False``, uses ``--format=qcow2``
        (uncompressed random-access writes).

        ``--persistent`` keeps qemu-nbd alive after the destination
        client disconnects — without it qemu-nbd exits on the last
        disconnect, racing the pidfile-based termination.  Stale socket
        and pidfile are removed before start (crash-safe, spec: write-side
        lifecycle is crash-safe).

        Returns the :class:`ShellResult` from the ``qemu-nbd`` command.
        """
        self._shell.run(["rm", "-f", write_socket, str(pid_file)], timeout=10)
        if compress:
            cmd = [
                "qemu-nbd",
                "--fork",
                "--persistent",
                "--pid-file",
                str(pid_file),
                "--socket",
                write_socket,
                "--image-opts",
                (
                    f"driver=compress,"
                    f"file.driver=qcow2,"
                    f"file.file.driver=file,"
                    f"file.file.filename={target_file}"
                ),
            ]
        else:
            cmd = [
                "qemu-nbd",
                "--fork",
                "--persistent",
                "--pid-file",
                str(pid_file),
                "--socket",
                write_socket,
                "--format=qcow2",
                str(target_file),
            ]
        return self._shell.run(cmd, timeout=30)

    def _transfer(
        self,
        socket_path: str,
        write_socket: str,
        disk_target: str,
        meta_contexts: list[str],
        zero_skip: bool = False,
        compress: bool = False,
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
    ) -> tuple[str | None, int]:
        """Connect both NBD endpoints and transfer extents (design D1).

        Unified engine for both FULL and incremental transfers:

        - ``zero_skip=True`` (standalone FULL): queries only
          ``base:allocation``, copies all allocated extents, and skips
          all-zero chunks (design D9).
        - ``zero_skip=False`` (incremental): queries both
          ``base:allocation`` and ``qemu:dirty-bitmap:backup-<disk>``,
          intersects dirty∩allocated, and copies only dirty extents.

        ``compress`` and ``compression_type`` are accepted for interface
        completeness — they affect the write-side qemu-nbd started
        externally by :meth:`_start_write_server`, not the transfer loop
        itself.

        Stall watchdog (design D4): a monotonic last-progress timestamp
        is updated after every successful chunk write; if no chunk
        completes for ``stall_timeout`` seconds the loop aborts with
        ``"Stall detected: no progress for {N}s"``.  ``stall_timeout ==
        0`` disables the watchdog.  No threads — progress is checked
        between chunk writes.

        Flush (design D7): before disconnecting, calls ``dst.flush()``
        when ``dst.can_flush()`` returns ``True`` — ensures all pending
        writes reach stable storage on the destination.

        Returns ``(error, dirty_bytes)`` — *error* is ``None`` on
        success; *dirty_bytes* is the sum of transferred extent lengths
        (0 when the measurement never ran).  Both clients are always
        disconnected before returning.
        """
        assert self._nbd is not None  # guarded by callers
        src = self._nbd
        # The destination is a second, independent connection of the
        # same concrete transport (LibnbdClient in production,
        # MockNbdClient in tests — both are zero-arg constructible).
        # Two simultaneous connections are required because the loop
        # interleaves pread(source) → pwrite(destination) per chunk.
        dst = type(self._nbd)()
        bitmap_context = f"qemu:dirty-bitmap:backup-{disk_target}"

        try:
            conn = src.connect(
                f"nbd+unix:///?socket={socket_path}",
                disk_target,
                meta_contexts,
            )
            if not conn.success:
                return conn.error or "source NBD connect failed", 0
            dst_conn = dst.connect(f"nbd+unix:///?socket={write_socket}", "", [])
            if not dst_conn.success:
                return dst_conn.error or "destination NBD connect failed", 0

            # Query block status over the disk in max-request-size
            # windows, accumulating extents per meta-context.
            size = src.get_size()
            window = max(1, src.get_max_request_size())
            dirty_raw: list[NbdExtent] = []
            alloc_raw: list[NbdExtent] = []
            bitmap_seen = False
            offset = 0
            while offset < size:
                length = min(window, size - offset)
                status = src.block_status(offset, length)
                if not status.success:
                    return status.error or "block_status failed", 0
                payload = cast(dict[str, list[NbdExtent]], status.payload or {})
                if not zero_skip and bitmap_context in payload:
                    bitmap_seen = True
                dirty_raw.extend(payload.get(bitmap_context, []))
                alloc_raw.extend(payload.get(_BASE_ALLOCATION_CONTEXT, []))
                offset += length
            if not zero_skip and not bitmap_seen:
                # Fail loudly: without the dirty-bitmap meta-context the
                # loop would "succeed" with an empty delta — silent data
                # loss.  This means the export did not advertise the
                # bitmap (e.g. missing incremental baseline).
                return (
                    f"dirty bitmap meta-context {bitmap_context} not advertised "
                    "by the NBD export — cannot identify dirty extents"
                ), 0

            allocated = unify_extents(alloc_raw)
            if zero_skip:
                # FULL: copy all allocated extents (data=True means
                # allocated or zero — both need to be read to determine
                # whether the content is non-zero).
                to_copy = [e for e in allocated if e.data]
            else:
                # Incremental: intersect dirty∩allocated.
                dirty = unify_extents(dirty_raw)
                to_copy = overlap_with_allocation(dirty, allocated)
            dirty_bytes = sum(extent.length for extent in to_copy)

            # Copy each extent in chunks bounded by both endpoints'
            # max request size, running the stall watchdog between
            # chunk writes (design D4).
            chunk_size = max(
                1,
                min(src.get_max_request_size(), dst.get_max_request_size()),
            )
            last_progress = time.monotonic()
            for extent in to_copy:
                pos = extent.offset
                remaining = extent.length
                while remaining > 0:
                    count = min(chunk_size, remaining)
                    read = src.pread(pos, count)
                    if not read.success:
                        return read.error or "pread failed", dirty_bytes
                    data = read.payload
                    if not isinstance(data, bytes) or len(data) != count:
                        got = len(data) if isinstance(data, bytes) else "non-bytes payload"
                        return (
                            f"short read at offset {pos}: expected {count} bytes, got {got}"
                        ), dirty_bytes
                    # Zero-skip (design D9): skip pwrite for all-zero
                    # chunks — the destination qcow2 is already zero
                    # in unwritten regions.  Only for standalone FULL.
                    if zero_skip and data == b"\x00" * count:
                        pos += count
                        remaining -= count
                        continue
                    write = dst.pwrite(pos, data)
                    if not write.success:
                        return write.error or "pwrite failed", dirty_bytes
                    now = time.monotonic()
                    if stall_timeout > 0 and now - last_progress > stall_timeout:
                        return f"Stall detected: no progress for {stall_timeout}s", dirty_bytes
                    last_progress = now
                    pos += count
                    remaining -= count

            # Flush before disconnect (design D7): ensure all pending
            # writes reach stable storage on the destination.
            if dst.can_flush():
                flush_result = dst.flush()
                if not flush_result.success:
                    return flush_result.error or "flush failed", dirty_bytes

            return None, dirty_bytes
        finally:
            # Disconnect both clients — safe even when the connection
            # was never established (interface contract).
            dst.disconnect()
            src.disconnect()

    def _validate_backing_chain(self, path: Path) -> bool:
        """Check whether a backup file has an intact backing chain.

        Runs ``qemu-img info --force-share --backing-chain --output=json``
        which traverses the entire chain and fails if any file is
        missing.  Standalone files (FULLs with no backing) are
        considered valid — the command succeeds on standalone files.

        Returns ``True`` if the command succeeds (exit code 0),
        ``False`` otherwise.  Never raises exceptions.
        """
        result = self._shell.run(
            [
                "qemu-img",
                "info",
                "--force-share",
                "--backing-chain",
                "--output=json",
                str(path),
            ],
            timeout=60,
            check=True,
        )
        return result.success

    def _copy_dirty_blocks(
        self,
        vm_name: str,
        target: TargetConfig,
        target_file: Path,
        socket_path: str,
        write_socket: str,
        pid_file: Path,
        disk_target: str | None,
        stall_timeout: int,
    ) -> _CopyResult:
        """Copy only dirty∩allocated blocks into a backing-chained delta (design D2).

        Lifecycle (spec: nbd-dirty-block-transfer / dirty-block copy loop):

        1. Resolve the previous backup at the target (newest by
           timestamp; the FULL for the first incremental) and re-check
           its existence immediately before use — on disappearance,
           fail with a retryable-class error (design D3/R2).
        2. Create ``<name>.qcow2.tmp`` via ``qemu-img create -f qcow2
           -b <previous> -F qcow2``.
        3. Serve the ``.tmp`` through a forked ``qemu-nbd`` via
           :meth:`_start_write_server` (uncompressed — incrementals
           are never compressed, design D6).
        4. Call :meth:`_transfer` with ``zero_skip=False`` to copy
           dirty∩allocated extents.
        5. Terminate ``qemu-nbd`` via its pidfile, remove the write
           socket.
        6. Atomically ``mv <name>.qcow2.tmp <name>.qcow2``.

        Cleanup of the write side on failure paths (qemu-nbd
        termination, write socket/pidfile/``.tmp`` removal) is handled
        by the ``finally`` block of :meth:`transfer_missing`.
        """
        tmp_file = Path(f"{target_file}.tmp")

        if self._nbd is None:
            return _CopyResult(
                error=(
                    "no INbdClient configured — the bitmap incremental copy loop "
                    "requires an NBD client (DefaultFactory wires LibnbdClient)"
                ),
                previous_path=None,
                dirty_bytes=0,
            )
        if not disk_target:
            return _CopyResult(
                error=(
                    f"cannot determine disk target device for VM {vm_name} via "
                    "virsh domblklist — required for the qemu:dirty-bitmap meta-context"
                ),
                previous_path=None,
                dirty_bytes=0,
            )

        # (1) Resolve the previous backup — walk backwards through
        # backups (sorted ascending by timestamp) to find the newest
        # backup of THIS disk with an intact backing chain.  Backups are
        # scoped per disk (multi-disk refactor): an incremental for disk
        # X must chain to disk X's newest valid backup, never to another
        # disk's.  Rationale: if the newest backup has a broken chain
        # (its backing file was deleted by retention), qemu-img create -b
        # will fail.  Walking backwards skips broken-chain files and
        # chains to the last available valid backup (design D4).  FULLs
        # are standalone (no backing) and always valid.
        backups = self.list(target)
        if disk_target is not None:
            backups = [b for b in backups if b.disk == disk_target]
        previous: BackupInfo | None = None
        for backup in reversed(backups):
            # Check file existence (race guard — retention may have
            # deleted between list() and now).
            exists = self._shell.run(["test", "-f", str(backup.path)], timeout=10, check=True)
            if not exists.success:
                continue
            # FULLs are standalone — always valid.
            if not backup.is_full and not self._validate_backing_chain(backup.path):
                logger.warning(
                    "[backup] %s: backup %s has broken backing chain — skipping as previous",
                    vm_name,
                    backup.name,
                )
                continue
            previous = backup
            break
        if previous is None:
            return _CopyResult(
                error=(
                    f"no valid previous backup found at {target.path} — "
                    "all backups have broken backing chains. "
                    "Run: qsnap check --deep, then qsnap reconcile"
                ),
                previous_path=None,
                dirty_bytes=0,
            )
        # (1b) Re-check existence immediately before use (design D3/R2):
        # retention could in theory remove the previous backup between
        # listing and create.  Routed through IShell so the race is
        # observable/testable like every other external call.  The error
        # is retryable-class — the next run re-discovers the newest.
        exists = self._shell.run(["test", "-f", str(previous.path)], timeout=10, check=True)
        if not exists.success:
            return _CopyResult(
                error=(
                    f"previous backup {previous.path} vanished between listing and "
                    "delta creation (eof race) — next run re-discovers the newest"
                ),
                previous_path=previous.path,
                dirty_bytes=0,
            )

        # (2) Create the delta as a backing-chained qcow2.
        create_result = self._shell.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-b",
                str(previous.path),
                "-F",
                "qcow2",
                str(tmp_file),
            ],
            timeout=60,
            check=True,
        )
        if not create_result.success:
            return _CopyResult(
                error=f"qemu-img create delta failed: {create_result.error}",
                previous_path=previous.path,
                dirty_bytes=0,
            )

        # (3) Serve the .tmp delta through a forked qemu-nbd
        # (uncompressed — incrementals are never compressed, design D6).
        nbd_proc = self._start_write_server(
            tmp_file,
            write_socket,
            pid_file,
            compress=False,
        )
        if not nbd_proc.success:
            return _CopyResult(
                error=f"qemu-nbd failed to start: {nbd_proc.error}",
                previous_path=previous.path,
                dirty_bytes=0,
            )

        # (4) Connect both endpoints and copy dirty extents.
        bitmap_context = f"qemu:dirty-bitmap:backup-{disk_target}"
        error, dirty_bytes = self._transfer(
            socket_path,
            write_socket,
            disk_target,
            [_BASE_ALLOCATION_CONTEXT, bitmap_context],
            zero_skip=False,
            compress=False,
            stall_timeout=stall_timeout,
        )
        if error is not None:
            return _CopyResult(
                error=error,
                previous_path=previous.path,
                dirty_bytes=dirty_bytes,
            )

        # (5) Terminate qemu-nbd (the destination client already
        # disconnected, so the delta is flushed/closed) and remove the
        # write socket + pidfile BEFORE the atomic rename.
        self._terminate_qemu_nbd(pid_file)
        self._shell.run(["rm", "-f", write_socket, str(pid_file)], timeout=10)

        # (6) Atomic rename: mv .tmp to final name (same discipline as
        # the FULL path).
        mv_result = self._shell.run(["mv", str(tmp_file), str(target_file)], timeout=30, check=True)
        if not mv_result.success:
            return _CopyResult(
                error=f"atomic rename failed: {mv_result.error}",
                previous_path=previous.path,
                dirty_bytes=dirty_bytes,
            )

        return _CopyResult(error=None, previous_path=previous.path, dirty_bytes=dirty_bytes)

    def _terminate_qemu_nbd(self, pid_file: Path) -> None:
        """Terminate a forked qemu-nbd via its pidfile (best-effort, design D2).

        Reads the qemu-nbd PID from *pid_file* and sends SIGTERM via
        IShell ``kill``.  A missing/unreadable pidfile is skipped
        silently (the process never started or was already terminated).
        Kill failures log a WARNING and never propagate — cleanup is
        never fatal to a transfer.
        """
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        kill_result = self._shell.run(["kill", str(pid)], timeout=10, check=True)
        if not kill_result.success:
            logger.warning(
                "Failed to terminate qemu-nbd (pid %d): %s",
                pid,
                kill_result.error,
            )

    def list(self, target: TargetConfig) -> list[BackupInfo]:
        """List existing backups at *target*.

        Scans ``target.path`` for ``*.qcow2`` files and obtains metadata
        via ``qemu-img info --output=json``.  Returns an empty list if
        the target directory does not exist.
        """
        if not target.path.exists():
            return []

        backups: list[BackupInfo] = []
        for file in target.path.glob("*.qcow2"):
            info_cmd = [
                "qemu-img",
                "info",
                "--output=json",
                str(file),
            ]
            info_result = self._shell.run(info_cmd, timeout=60, check=True)
            if not info_result.success:
                continue

            name = file.stem
            timestamp = parse_timestamp(name, file)
            # Disk target is encoded in the backup name (both FULL and
            # incremental): ``{vm}[.FULL].{ts}_{disk}_{6hex}``.  Empty
            # string when the name cannot be parsed (foreign/legacy file).
            disk = parse_disk_from_snapshot_name(name) or ""
            is_full = ".FULL." in name

            backups.append(
                BackupInfo(
                    name=name,
                    path=file,
                    timestamp=timestamp,
                    disk=disk,
                    is_full=is_full,
                )
            )

        backups.sort(key=lambda s: s.timestamp)
        return backups

    def delete(self, backup: BackupInfo) -> ShellResult:
        """Delete a backup file via ``rm -f``."""
        cmd = ["rm", "-f", str(backup.path)]
        return self._shell.run(cmd, timeout=30)

    # ── bitmap-specific helpers ───────────────────────────────────────

    def list_checkpoints(self, vm_name: str) -> list[str]:
        """Return qsnap-owned checkpoint names for *vm_name*.

        Calls ``virsh checkpoint-list --name <vm>`` and filters by the
        ``qsnap-`` prefix.  On command failure (e.g., VM not defined,
        libvirt not running), logs a WARNING and returns an empty list —
        callers treat this as "no checkpoints" (non-fatal).
        """
        cmd = [
            "virsh",
            "checkpoint-list",
            "--name",
            "--domain",
            vm_name,
        ]
        result = self._shell.run(cmd, timeout=30, check=True)
        if not result.success:
            logger.warning(
                "Failed to list checkpoints for VM %s: %s",
                vm_name,
                result.error,
            )
            return []

        checkpoints: list[str] = []
        for line in result.stdout.strip().splitlines():
            name = line.strip()
            if name.startswith("qsnap-"):
                checkpoints.append(name)
        return checkpoints

    def _list_checkpoints_for_target(self, vm_name: str, target_hash: str, disk: str) -> list[str]:
        """Return qsnap checkpoints matching *target_hash* and *disk*.

        Checkpoint names are scoped per disk (multi-disk refactor) so the
        prefix includes the disk target: ``qsnap-{target_hash}-{disk}-``.
        """
        prefix = f"qsnap-{target_hash}-{disk}-"
        return [cp for cp in self.list_checkpoints(vm_name) if cp.startswith(prefix)]

    @staticmethod
    def _new_checkpoint_name(target_hash: str, disk: str, taken: set[str] | None = None) -> str:
        """Generate a unique successor checkpoint name (design D2/D6).

        Format:
        ``qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6_hex_chars}`` —
        the disk target scopes the checkpoint so each disk owns its own
        dirty-bitmap lineage (multi-disk refactor), followed by local
        time with seconds resolution plus a 6-character hex suffix
        (``secrets.token_hex(3)``) for collision resistance.  The suffix
        guarantees uniqueness even when two checkpoints are created
        within the same second (e.g., a fast FULL followed immediately
        by the first incremental of the same pipeline run — libvirt
        rejects duplicate names with "Bitmap already exists").

        The *taken* set (existing qsnap checkpoint names for this
        VM+target+disk) is checked as a last-resort guard; the random
        suffix makes collisions astronomically unlikely, but if it
        somehow collides, the timestamp is bumped forward one second at
        a time.
        """
        now = datetime.now()
        existing: set[str] = taken if taken is not None else set()
        for offset in range(60):
            candidate = (
                f"qsnap-{target_hash}-{disk}-"
                f"{(now + timedelta(seconds=offset)).strftime('%Y%m%dT%H%M%S')}"
                f"-{secrets.token_hex(3)}"
            )
            if candidate not in existing:
                return candidate
        # Practically unreachable (60 same-name collisions in a row);
        # fall back to microsecond resolution to guarantee uniqueness.
        return f"qsnap-{target_hash}-{disk}-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"

    @staticmethod
    def _parse_checkpoint_timestamp(name: str, target_hash: str, disk: str) -> datetime | None:
        """Parse the creation timestamp embedded in a checkpoint name.

        Checkpoint names are scoped per disk (multi-disk refactor):
        ``qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6_hex}``.  After
        stripping the ``qsnap-{target_hash}-{disk}-`` prefix the
        remainder is ``{yyyymmddTHHMMSS}`` optionally followed by a
        ``-{hex}`` suffix.  Timezone-aware matches are normalized to
        naive local time so they compare coherently.

        Returns ``None`` when no timestamp can be parsed — callers sort
        such names oldest (conservative, design D3).
        """
        prefix = f"qsnap-{target_hash}-{disk}-"
        remainder = name[len(prefix) :] if name.startswith(prefix) else name
        # New format with optional hex suffix: yyyymmddTHHMMSS[-hex]
        match = re.fullmatch(r"(\d{8}T\d{6})(?:-[0-9a-f]+)?", remainder)
        if match:
            try:
                return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
            except ValueError:
                return None
        # Fallback: timestamp embedded anywhere in the remainder (most
        # specific pattern first).
        patterns: list[tuple[str, str]] = [
            (r"(\d{8}T\d{6}[+-]\d{4})", "%Y%m%dT%H%M%S%z"),
            (r"(\d{8}T\d{6})", "%Y%m%dT%H%M%S"),
            (r"(\d{8}T\d{4})", "%Y%m%dT%H%M"),
            (r"(\d{8})", "%Y%m%d"),
        ]
        for regex, fmt in patterns:
            match = re.search(regex, remainder)
            if not match:
                continue
            try:
                parsed = datetime.strptime(match.group(1), fmt)
            except ValueError:
                continue
            if parsed.tzinfo is not None:
                # Normalize to naive local time for comparison with
                # new-format (naive, local-clock) timestamps.
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        return None

    def _newest_checkpoint(self, vm_name: str, target_hash: str, disk: str) -> str | None:
        """Return the newest qsnap checkpoint for this VM+target+disk.

        Thin wrapper over :meth:`_select_newest` that fetches the
        candidate list via ``virsh checkpoint-list --name``.  No
        snapshot state is consulted for checkpoint selection.
        """
        return self._select_newest(
            self._list_checkpoints_for_target(vm_name, target_hash, disk),
            target_hash,
            disk,
            vm_name,
        )

    def _select_newest(
        self,
        candidates: list[str],
        target_hash: str,
        disk: str,
        vm_name: str,
    ) -> str | None:
        """Select the newest checkpoint from *candidates* (design D3).

        Orders the given ``qsnap-{target_hash}-{disk}-*`` checkpoint
        names by the creation timestamp embedded in the name.  Names
        whose timestamp cannot be parsed sort oldest (conservative) and
        are logged at WARNING.  Returns ``None`` when *candidates* is
        empty.
        """
        if not candidates:
            return None
        newest: str | None = None
        newest_ts: datetime | None = None
        for name in candidates:
            ts = self._parse_checkpoint_timestamp(name, target_hash, disk)
            if ts is None:
                logger.warning(
                    "Cannot parse timestamp from checkpoint name %s for VM %s; "
                    "treating it as oldest",
                    name,
                    vm_name,
                )
                ts = datetime.min
            if newest_ts is None or ts > newest_ts:
                newest = name
                newest_ts = ts
        return newest

    def _delete_checkpoint_best_effort(self, vm_name: str, checkpoint_name: str) -> None:
        """Delete a checkpoint via ``virsh checkpoint-delete``.

        First attempts a **full** ``checkpoint-delete`` (no
        ``--metadata``) which removes both the checkpoint metadata and
        the associated dirty-bitmap data from the running VM.  If that
        fails (e.g., VM is not running, checkpoint in use), falls back
        to ``checkpoint-delete --metadata`` which removes only the
        metadata.

        Best-effort: failures are logged at WARNING and never
        propagated (design D3 — checkpoint cleanup is never fatal to a
        transfer).
        """
        # Full delete (metadata + bitmap data) — preferred when the VM
        # is running and the checkpoint is not in active use.
        full_cmd = [
            "virsh",
            "checkpoint-delete",
            "--domain",
            vm_name,
            checkpoint_name,
        ]
        full_result = self._shell.run(full_cmd, timeout=30, check=True)
        if full_result.success:
            return

        # Fallback: metadata-only delete (works even when the VM is
        # stopped or the checkpoint is in active use).
        meta_cmd = [
            "virsh",
            "checkpoint-delete",
            "--domain",
            vm_name,
            checkpoint_name,
            "--metadata",
        ]
        meta_result = self._shell.run(meta_cmd, timeout=30, check=True)
        if not meta_result.success:
            logger.warning(
                "Failed to delete checkpoint %s for VM %s (full: %s; metadata fallback: %s)",
                checkpoint_name,
                vm_name,
                full_result.error,
                meta_result.error,
            )

    def _delete_superseded_checkpoints(
        self,
        vm_name: str,
        target_hash: str,
        disk: str,
        successor: str,
    ) -> None:
        """Delete all qsnap checkpoints for this VM+target+disk older than *successor*.

        Called only after a successful AND verified export (design D3):
        the successor checkpoint already exists (created atomically with
        the export's ``backup-begin``), so deleting superseded
        checkpoints never opens a zero-checkpoint window.  Unparseable
        names sort oldest and are therefore always superseded.  A crash
        before this cleanup leaves a stale older checkpoint — harmless,
        because newest-wins discovery still picks the correct baseline
        and cleanup is retried here on the next successful run.
        """
        successor_ts = self._parse_checkpoint_timestamp(successor, target_hash, disk)
        for name in self._list_checkpoints_for_target(vm_name, target_hash, disk):
            if name == successor:
                continue
            ts = self._parse_checkpoint_timestamp(name, target_hash, disk)
            if ts is not None and successor_ts is not None and ts >= successor_ts:
                # Not older than the successor — keep.
                continue
            self._delete_checkpoint_best_effort(vm_name, name)

    @staticmethod
    def _is_collision_error(error: str | None) -> bool:
        """Check whether *error* indicates a checkpoint/bitmap collision.

        libvirt reports name collisions as "Bitmap already exists" or
        "checkpoint ... already exists" when a stale checkpoint+bitmap
        from a crashed prior run blocks the new checkpoint name.
        """
        if error is None:
            return False
        lower = error.lower()
        return "bitmap already exists" in lower or "already exists" in lower

    def _force_cleanup_checkpoints(self, vm_name: str, target_hash: str, disk: str) -> None:
        """Force-delete ALL qsnap checkpoints for this VM+target+disk.

        Used by collision recovery (design D6) when a stale
        checkpoint+bitmap blocks a new ``backup-begin``.  Unlike
        :meth:`_delete_superseded_checkpoints`, this deletes every
        qsnap checkpoint for the target+disk — including the newest —
        using full ``checkpoint-delete`` with ``--metadata`` fallback.
        """
        for name in self._list_checkpoints_for_target(vm_name, target_hash, disk):
            self._delete_checkpoint_best_effort(vm_name, name)

    @staticmethod
    def target_hash(target_path: str) -> str:
        """Short hash of *target_path* for checkpoint naming."""
        return hashlib.md5(target_path.encode()).hexdigest()[:8]  # noqa: S324
