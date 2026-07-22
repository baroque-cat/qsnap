"""BitmapBackupProvider — NBD pull-model incremental backup via libvirt.

Implements ``IBackupProvider``.  Does NOT inherit from Core (design D1).
Dependencies: ``IShell``, optional ``IStateManager``, and ``INbdClient``
(third constructor parameter — the dirty-block transfer transport).

Uses ``virsh backup-begin`` with a pull-model NBD Unix socket to export
the frozen point-in-time view.  Both FULL and incremental exports are
pulled by the **unified NBD transfer engine** (:meth:`_transfer`):
the loop negotiates ``base:allocation`` (and
``qemu:dirty-bitmap:backup-<disk>`` for incrementals) meta-contexts,
queries block status, and ``pread``/``pwrite``s only the relevant
extents into the target qcow2 served by a forked ``qemu-nbd``.

For FULL exports (no prior checkpoint): ``zero_skip=True`` copies all
allocated extents, skipping all-zero chunks.  For incremental exports:
``zero_skip=False`` intersects dirty extents with allocated extents
and copies only dirty∩allocated blocks into a **backing-chained** qcow2
delta (created via ``qemu-img create -b <previous backup> -F qcow2``).

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
   (``qsnap-{target_hash}-{yyyymmddTHHMMSS}``).
3. ``virsh backup-begin --domain VM <backupxml> <checkpointxml>``
   starts the NBD export (full when no ``<incremental>`` element,
   incremental otherwise) and atomically creates the successor
   checkpoint at the export's freeze point.
4. Pull the export via the unified NBD engine (:meth:`_transfer`):
   - FULL (no prior checkpoint, ``zero_skip=True``): ``pread`` all
     allocated extents from the source, ``pwrite`` to the destination
     (served by a forked ``qemu-nbd`` with compress driver when
     ``target.compress`` is ``True``), skipping all-zero chunks.
   - Incremental (prior checkpoint, ``zero_skip=False``): negotiate
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
   ``virsh checkpoint-delete --metadata`` — the successor already
   exists, so rotation never opens a zero-checkpoint window.
7. On export/verify failure, preserve the prior checkpoint for retry,
   delete the just-created successor checkpoint best-effort, and
   delete the partial target file.
8. Socket(s), the forked ``qemu-nbd`` process (via pidfile), the
   ``.tmp`` file, and XML temp files are always cleaned up in a
   ``finally`` block.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.nbd import INbdClient
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import BackupResult, NbdExtent, ShellResult, SnapshotInfo
from qsnap.utils.extents import overlap_with_allocation, unify_extents
from qsnap.utils.nbd import (
    get_first_disk_target,
    write_backup_xml,
    write_checkpoint_xml,
)
from qsnap.utils.parsing import parse_timestamp
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
        state: IStateManager | None = None,
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
        self._state = state
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
            result = self._shell.run(["rm", "-f", str(target_file)], timeout=10)
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

    def transfer_missing(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
        *,
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
    ) -> list[BackupResult]:
        """Transfer missing snapshots via NBD pull-model.

        See module docstring for the NBD backup lifecycle.

        ``compression_type`` selects the compression algorithm for the
        full-pull transfer (``"zstd"`` default, ``"zlib"``
        alternative) and only takes effect when ``target.compress`` is
        ``True`` and a **full** export is pulled (no prior checkpoint).
        Bitmap incrementals are written uncompressed via random-access
        ``pwrite`` (design D6 — qcow2 compressed clusters can only be
        produced by the compress driver on the write-side qemu-nbd).

        ``stall_timeout`` is the stall-detection timeout in seconds.
        It drives the in-process progress watchdog (abort with
        ``"Stall detected: no progress for {N}s"`` when no chunk
        completes for N seconds).  When ``0``, stall detection is
        disabled.
        """
        existing = self.list(target)
        existing_names = {s.name for s in existing}

        target_hash = self.target_hash(str(target.path))

        results: list[BackupResult] = []
        compress_notice_logged = False

        for snapshot in snapshots:
            if snapshot.name in existing_names:
                continue

            # Stale-state detection: if the source snapshot file
            # doesn't exist on disk, skip it and clean up the stale
            # state entry (self-healing — design D3).
            exists = self._shell.run(["test", "-f", str(snapshot.path)], timeout=10)
            if not exists.success:
                logger.warning(
                    "Source snapshot %s no longer exists on disk — "
                    "skipping and removing stale state entry",
                    snapshot.path,
                )
                if self._state is not None:
                    self._state.remove_snapshot(vm_config.name, snapshot.name)
                continue

            target_file = target.path / f"{snapshot.name}.qcow2"
            tmp_file = Path(f"{target_file}.tmp")
            socket_path = f"/tmp/qsnap-backup-{os.getpid()}.sock"
            write_socket = f"/tmp/qsnap-write-{os.getpid()}.sock"
            pid_file = Path(f"/tmp/qsnap-qemu-nbd-{os.getpid()}.pid")

            # Determine the prior checkpoint for an incremental export
            # (design D3: newest-wins discovery via ``virsh
            # checkpoint-list``, re-evaluated per snapshot so the
            # successor created earlier in this loop becomes the
            # baseline for the next export).  When no prior checkpoint
            # exists, a full NBD export is performed — with an atomic
            # successor checkpoint, so the FULL run leaves a valid
            # baseline by construction.  The same listing also feeds
            # successor-name uniqueness (design D2).
            candidates = self._list_checkpoints_for_target(vm_config.name, target_hash)
            prior = self._select_newest(candidates, target_hash, vm_config.name)

            # The successor checkpoint is created atomically with this
            # export's backup-begin (design D1/D2): its dirty-bitmap
            # baseline coincides with the export's freeze point.
            successor = self._new_checkpoint_name(target_hash, taken=set(candidates))

            # Step 1: Remove stale socket.
            self._shell.run(["rm", "-f", socket_path], timeout=10)

            # Step 2: Build and write backup XML + checkpoint XML.  The
            # incremental checkpoint is passed via the <incremental> XML
            # element, NOT via a --incremental CLI flag (the flag does
            # not exist in any version of virsh backup-begin).
            backup_xml_path = write_backup_xml(socket_path, incremental=prior)
            checkpoint_xml_path = write_checkpoint_xml(successor)

            try:
                # Step 3: Start NBD export via virsh backup-begin.  The
                # checkpoint XML is the third positional argument —
                # libvirt creates the successor checkpoint atomically at
                # the export's freeze point (design D1).  No --incremental
                # CLI flag is passed — it does not exist in any version
                # of virsh backup-begin.
                backup_cmd = [
                    "virsh",
                    "backup-begin",
                    "--domain",
                    vm_config.name,
                    str(backup_xml_path),
                    str(checkpoint_xml_path),
                ]

                backup_result = self._shell.run(backup_cmd, timeout=120)
                if not backup_result.success:
                    # backup-begin is atomic: the successor checkpoint
                    # was NOT created — the prior checkpoint remains the
                    # newest valid baseline.  No rollback needed.
                    results.append(
                        BackupResult(
                            success=False,
                            snapshot_name=snapshot.name,
                            source_path=snapshot.path,
                            target_path=target_file,
                            bytes_transferred=0,
                            error=backup_result.error,
                        )
                    )
                    continue

                # Step 4: Pull the export.
                # libvirt's NBD server exports each disk under its
                # target device name (e.g., "vda"); the export name is
                # needed both for the convert URI and for the dirty
                # bitmap meta-context name.
                disk_target = get_first_disk_target(
                    self._shell,
                    vm_config.name,
                )
                start_time = time.monotonic()
                dirty_bytes = 0
                previous_path: Path | None = None
                if prior is None:
                    # Full export (no baseline checkpoint): pull the
                    # entire frozen view into a standalone qcow2 via
                    # the unified NBD engine with zero_skip=True.
                    # Compression applies here (and in create_full_backup)
                    # only — bitmap incrementals are uncompressed
                    # (design D6).
                    #
                    # (4a) Create a standalone qcow2 .tmp (no backing).
                    # Pass the source disk's virtual size so the target
                    # can accept pwrite at any offset.  When
                    # target.compress, set the qcow2 compression_type
                    # at creation time (design D6, spec:
                    # nbd-bitmap-backup).
                    virtual_size = self._query_virtual_size(snapshot.path)
                    create_cmd: list[str] = [
                        "qemu-img",
                        "create",
                        "-f",
                        "qcow2",
                    ]
                    if target.compress:
                        create_cmd.extend(["-o", f"compression_type={compression_type}"])
                    create_cmd.append(str(tmp_file))
                    if virtual_size is not None:
                        create_cmd.append(str(virtual_size))
                    create_result = self._shell.run(create_cmd, timeout=60)
                    if not create_result.success:
                        transfer_error = f"qemu-img create failed: {create_result.error}"
                        elapsed = time.monotonic() - start_time
                        self._cleanup_partial_file(target_file)
                        self._delete_checkpoint_best_effort(vm_config.name, successor)
                        results.append(
                            BackupResult(
                                success=False,
                                snapshot_name=snapshot.name,
                                source_path=snapshot.path,
                                target_path=target_file,
                                bytes_transferred=0,
                                error=transfer_error,
                            )
                        )
                        continue
                    # (4b) Start the write-side qemu-nbd (compress driver
                    # when target.compress, design D6).
                    nbd_proc = self._start_write_server(
                        tmp_file,
                        write_socket,
                        pid_file,
                        compress=target.compress,
                        compression_type=compression_type,
                    )
                    if not nbd_proc.success:
                        transfer_error = f"qemu-nbd failed to start: {nbd_proc.error}"
                        elapsed = time.monotonic() - start_time
                        self._cleanup_partial_file(target_file)
                        self._delete_checkpoint_best_effort(vm_config.name, successor)
                        results.append(
                            BackupResult(
                                success=False,
                                snapshot_name=snapshot.name,
                                source_path=snapshot.path,
                                target_path=target_file,
                                bytes_transferred=0,
                                error=transfer_error,
                            )
                        )
                        continue
                    # (4c) Transfer via the unified engine (zero_skip=True).
                    transfer_error, dirty_bytes = self._transfer(
                        socket_path,
                        write_socket,
                        disk_target or "",
                        [_BASE_ALLOCATION_CONTEXT],
                        zero_skip=True,
                        compress=target.compress,
                        compression_type=compression_type,
                        stall_timeout=stall_timeout,
                    )
                    # (4d) Terminate qemu-nbd before the atomic rename.
                    self._terminate_qemu_nbd(pid_file)
                    self._shell.run(["rm", "-f", write_socket, str(pid_file)], timeout=10)
                    if transfer_error is None:
                        # (4e) Atomic rename: mv .tmp to final name.
                        mv_result = self._shell.run(
                            ["mv", str(tmp_file), str(target_file)], timeout=30
                        )
                        if not mv_result.success:
                            transfer_error = f"atomic rename failed: {mv_result.error}"
                else:
                    # Incremental export: in-process dirty-block copy
                    # loop via the unified NBD engine (design D2) with
                    # zero_skip=False — copies only dirty∩allocated
                    # extents into a backing-chained qcow2 delta.
                    if target.compress and not compress_notice_logged:
                        logger.info(
                            "bitmap incrementals are uncompressed — "
                            "target.compress applies to FULL backups only (design D6)"
                        )
                        compress_notice_logged = True
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
                elapsed = time.monotonic() - start_time
                if transfer_error is not None:
                    # Export failed: preserve the prior checkpoint for
                    # retry, delete the just-created successor checkpoint
                    # best-effort (it must not become the newest baseline
                    # of a failed export), and delete the partial target
                    # file so retention cleanup does not find it and log
                    # a misleading ``[delete] removed backup`` message
                    # (design D3).  The ``.tmp`` file is removed by the
                    # ``finally`` block.
                    self._cleanup_partial_file(target_file)
                    self._delete_checkpoint_best_effort(vm_config.name, successor)
                    results.append(
                        BackupResult(
                            success=False,
                            snapshot_name=snapshot.name,
                            source_path=snapshot.path,
                            target_path=target_file,
                            bytes_transferred=0,
                            error=transfer_error,
                        )
                    )
                    continue

                # Step 5: Verification (if enabled).  Incrementals use
                # the bitmap-specific verifier (backing-filename check +
                # dirty-size regression barrier); full pulls produce a
                # standalone qcow2 and are verified with the FULL
                # verifier.  ``target.verify == "compare"`` means
                # chain-traversing ``qemu-img compare`` (same semantics
                # as verify_bitmap_incremental).
                if prior is None:
                    verify_error = verify_full_backup(
                        self._shell,
                        target_file,
                        target.verify,
                        source_path=snapshot.path,
                    )
                else:
                    verify_error = verify_bitmap_incremental(
                        self._shell,
                        str(snapshot.path),
                        str(target_file),
                        str(previous_path),
                        dirty_bytes,
                        target.verify,
                    )
                if verify_error is not None:
                    # Same failure handling as the convert-failure path:
                    # preserve prior, delete successor best-effort,
                    # delete the partially-transferred file (design D3).
                    self._cleanup_partial_file(target_file)
                    self._delete_checkpoint_best_effort(vm_config.name, successor)
                    results.append(
                        BackupResult(
                            success=False,
                            snapshot_name=snapshot.name,
                            source_path=snapshot.path,
                            target_path=target_file,
                            bytes_transferred=0,
                            error=verify_error,
                        )
                    )
                    continue

                # Step 6: Checkpoint rotation (design D3): only after a
                # successful AND verified export, delete all superseded
                # (older) qsnap checkpoints for this VM+target.  The
                # successor checkpoint already exists (created atomically
                # in step 3), so deletion never opens a zero-checkpoint
                # window.  Delete failures are WARNING, never fatal.
                self._delete_superseded_checkpoints(vm_config.name, target_hash, successor)

                # Get file size for bytes_transferred.
                try:
                    bytes_transferred = target_file.stat().st_size
                except OSError:
                    bytes_transferred = 0

                results.append(
                    BackupResult(
                        success=True,
                        snapshot_name=snapshot.name,
                        source_path=snapshot.path,
                        target_path=target_file,
                        bytes_transferred=bytes_transferred,
                        error=None,
                        duration=elapsed,
                    )
                )

            finally:
                # Step 8: write-side + NBD job abort + socket + XML temp
                # file cleanup (always, even on failure — design D2,
                # spec: write-side lifecycle is crash-safe).
                #
                # Terminate the forked qemu-nbd serving the .tmp delta
                # via its pidfile (best-effort — the process may never
                # have started on early failures; on success it was
                # already terminated before the atomic rename).
                self._terminate_qemu_nbd(pid_file)
                # Write socket + pidfile removal.
                self._shell.run(["rm", "-f", write_socket, str(pid_file)], timeout=10)
                # Partial .tmp removal — a no-op on success (the .tmp
                # was renamed to the final file), removes the partial
                # delta on every failure/exception path.
                self._shell.run(["rm", "-f", str(tmp_file)], timeout=10)
                # Abort the virsh
                # backup-begin job to release the VM state change lock
                # (design D2).  domjobabort is idempotent — safe to
                # call when no job is running.  On failure, log a
                # WARNING but do NOT propagate the error — the socket
                # cleanup is the critical path and must still proceed.
                abort_cmd = [
                    "virsh",
                    "domjobabort",
                    "--domain",
                    vm_config.name,
                ]
                abort_result = self._shell.run(abort_cmd, timeout=30)
                if not abort_result.success:
                    logger.warning(
                        "virsh domjobabort failed for VM %s (job may have already terminated): %s",
                        vm_config.name,
                        abort_result.error,
                    )
                # Source (libvirt) socket cleanup.
                self._shell.run(["rm", "-f", socket_path], timeout=10)
                # Temp XML cleanup (local filesystem, not shell — keeps
                # the files out of the IShell command stream).
                for xml_path in (backup_xml_path, checkpoint_xml_path):
                    try:
                        xml_path.unlink(missing_ok=True)
                    except OSError as exc:
                        logger.warning(
                            "Failed to remove temp XML file %s: %s",
                            xml_path,
                            exc,
                        )

        return results

    # ── unified NBD transfer engine (design D1/D2/D4) ────────────────

    def _start_write_server(
        self,
        target_file: Path,
        write_socket: str,
        pid_file: Path,
        compress: bool = False,
        compression_type: str = "zstd",
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

        # (1) Resolve the previous backup (newest at target).
        backups = self.list(target)
        previous = backups[-1] if backups else None
        if previous is None:
            return _CopyResult(
                error=(
                    f"no previous backup found at {target.path} — cannot create "
                    "a backing-chained delta without a FULL anchor"
                ),
                previous_path=None,
                dirty_bytes=0,
            )
        # (1b) Re-check existence immediately before use (design D3/R2):
        # retention could in theory remove the previous backup between
        # listing and create.  Routed through IShell so the race is
        # observable/testable like every other external call.  The error
        # is retryable-class — the next run re-discovers the newest.
        exists = self._shell.run(["test", "-f", str(previous.path)], timeout=10)
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
        mv_result = self._shell.run(["mv", str(tmp_file), str(target_file)], timeout=30)
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
        kill_result = self._shell.run(["kill", str(pid)], timeout=10)
        if not kill_result.success:
            logger.warning(
                "Failed to terminate qemu-nbd (pid %d): %s",
                pid,
                kill_result.error,
            )

    def create_full_backup(
        self,
        vm_name: str,
        source_snapshot: SnapshotInfo,
        target: TargetConfig,
        compress: bool = False,
        bucket_level: str = "monthly",
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
    ) -> BackupResult:
        """Create a standalone FULL backup via the unified NBD engine.

        ``vm_name`` is the full, untruncated VM name (e.g.
        ``"3.Projects_opencode"``), passed from Core's
        ``vm_config.name``.  It is used directly for
        ``virsh backup-begin`` and ``full_name`` generation — the method
        SHALL NOT extract the VM name from the snapshot filename.

        Uses the unified NBD transfer engine (:meth:`_transfer`) with
        ``zero_skip=True`` to produce a standalone qcow2 on the target.
        A checkpoint named
        ``qsnap-{target_hash}-{yyyymmddTHHMMSS}`` is created
        **atomically** with the FULL's ``backup-begin`` (design D1/D2):
        the baseline bitmap coincides with the FULL's freeze point, so
        the first incremental after a FULL exports every block dirtied
        since the FULL started — a faithful, gap-free chain.  On export
        failure the just-created checkpoint is deleted best-effort,
        preserving any prior baseline.

        Lifecycle:

        1. ``qemu-img create -f qcow2 <tmp>`` — standalone qcow2.
        2. Fork ``qemu-nbd`` (compress driver when ``compress=True``,
           design D6) via :meth:`_start_write_server`.
        3. ``virsh backup-begin`` with checkpoint XML (full export, no
           ``<incremental>`` element).
        4. :meth:`_transfer` with ``meta_contexts=["base:allocation"]``,
           ``zero_skip=True`` — pread allocated extents, pwrite to dest,
           skip all-zero chunks (design D9).
        5. ``dst.flush()`` (design D7) → terminate qemu-nbd →
           ``virsh domjobabort`` → socket cleanup.
        6. Atomic ``mv <tmp> <final>``.

        Uses an atomic pattern: transfer to a ``.tmp`` file, then rename
        on success.  On failure, the ``.tmp`` file is removed.

        When ``compress=True``, the write-side qemu-nbd uses the
        compress driver (design D6), producing a compressed qcow2.

        This method SHALL NOT call ``self._state.record_full_backup()``
        — state recording is Core's responsibility after post-create
        verification passes.
        """
        # Generate full backup name: vm.FULL.YYYYMMDD.qcow2
        date_str = source_snapshot.timestamp.strftime("%Y%m%d")
        full_name = f"{vm_name}.FULL.{date_str}"
        target_file = target.path / f"{full_name}.qcow2"
        tmp_file = target.path / f"{full_name}.qcow2.tmp"

        # The baseline checkpoint is created atomically with the FULL's
        # backup-begin (design D1/D2).
        target_hash = self.target_hash(str(target.path))
        checkpoint_name = self._new_checkpoint_name(target_hash)

        socket_path = f"/tmp/qsnap-backup-{os.getpid()}.sock"
        write_socket = f"/tmp/qsnap-write-{os.getpid()}.sock"
        pid_file = Path(f"/tmp/qsnap-qemu-nbd-{os.getpid()}.pid")

        # Remove stale socket.
        self._shell.run(["rm", "-f", socket_path], timeout=10)

        # Write backup XML (full, no <incremental>) + checkpoint XML.
        backup_xml_path = write_backup_xml(socket_path)
        checkpoint_xml_path = write_checkpoint_xml(checkpoint_name)

        try:
            # (1) Create a standalone qcow2 .tmp file (no backing).
            # Pass the source disk's virtual size so the target can
            # accept pwrite at any offset.  When compress=True, set
            # the qcow2 compression_type at creation time so the
            # compress driver writes zstd/zlib clusters (design D6,
            # spec: nbd-bitmap-backup).
            virtual_size = self._query_virtual_size(source_snapshot.path)
            create_cmd: list[str] = [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
            ]
            if compress:
                create_cmd.extend(["-o", f"compression_type={compression_type}"])
            create_cmd.append(str(tmp_file))
            if virtual_size is not None:
                create_cmd.append(str(virtual_size))
            create_result = self._shell.run(create_cmd, timeout=60)
            if not create_result.success:
                self._delete_checkpoint_best_effort(vm_name, checkpoint_name)
                return BackupResult(
                    success=False,
                    snapshot_name=source_snapshot.name,
                    source_path=source_snapshot.path,
                    target_path=target_file,
                    bytes_transferred=0,
                    error=f"qemu-img create failed: {create_result.error}",
                )

            # (2) Start the write-side qemu-nbd (compress driver when
            # compress=True, design D6).
            nbd_proc = self._start_write_server(
                tmp_file,
                write_socket,
                pid_file,
                compress=compress,
                compression_type=compression_type,
            )
            if not nbd_proc.success:
                self._delete_checkpoint_best_effort(vm_name, checkpoint_name)
                return BackupResult(
                    success=False,
                    snapshot_name=source_snapshot.name,
                    source_path=source_snapshot.path,
                    target_path=target_file,
                    bytes_transferred=0,
                    error=f"qemu-nbd failed to start: {nbd_proc.error}",
                )

            # (3) Start NBD export via virsh backup-begin (no
            # <incremental> — full export).  The checkpoint XML is the
            # third positional argument; libvirt creates the checkpoint
            # atomically at the export's freeze point.
            backup_cmd = [
                "virsh",
                "backup-begin",
                "--domain",
                vm_name,
                str(backup_xml_path),
                str(checkpoint_xml_path),
            ]
            backup_result = self._shell.run(backup_cmd, timeout=120)
            if not backup_result.success:
                # backup-begin is atomic: the successor checkpoint was
                # NOT created, so there is nothing to roll back.
                return BackupResult(
                    success=False,
                    snapshot_name=source_snapshot.name,
                    source_path=source_snapshot.path,
                    target_path=target_file,
                    bytes_transferred=0,
                    error=backup_result.error,
                )

            # (4) Transfer via the unified engine (zero_skip=True).
            disk_target = get_first_disk_target(self._shell, vm_name)
            transfer_error, _ = self._transfer(
                socket_path,
                write_socket,
                disk_target or "",
                [_BASE_ALLOCATION_CONTEXT],
                zero_skip=True,
                compress=compress,
                compression_type=compression_type,
                stall_timeout=stall_timeout,
            )

            if transfer_error is not None:
                # Export failed: delete the just-created checkpoint
                # best-effort so it cannot become the newest baseline
                # of a failed export (design D3).
                self._delete_checkpoint_best_effort(vm_name, checkpoint_name)
                return BackupResult(
                    success=False,
                    snapshot_name=source_snapshot.name,
                    source_path=source_snapshot.path,
                    target_path=target_file,
                    bytes_transferred=0,
                    error=transfer_error,
                )

            # (5) Terminate qemu-nbd (the destination client already
            # disconnected and flushed inside _transfer).
            self._terminate_qemu_nbd(pid_file)
            self._shell.run(["rm", "-f", write_socket, str(pid_file)], timeout=10)

            # (6) Atomic rename: mv .tmp to final name.
            mv_result = self._shell.run(["mv", str(tmp_file), str(target_file)], timeout=30)
            if not mv_result.success:
                return BackupResult(
                    success=False,
                    snapshot_name=source_snapshot.name,
                    source_path=source_snapshot.path,
                    target_path=target_file,
                    bytes_transferred=0,
                    error=mv_result.error,
                )

            # Get file size
            try:
                bytes_transferred = target_file.stat().st_size
            except OSError:
                bytes_transferred = 0

            # State recording is Core's responsibility after post-create
            # verification passes (design D4).  The provider SHALL NOT call
            # self._state.record_full_backup() here.

            return BackupResult(
                success=True,
                snapshot_name=source_snapshot.name,
                source_path=source_snapshot.path,
                target_path=target_file,
                bytes_transferred=bytes_transferred,
                error=None,
            )

        finally:
            # Terminate qemu-nbd (best-effort — may have already been
            # terminated on the success path).
            self._terminate_qemu_nbd(pid_file)
            # Write socket + pidfile removal.
            self._shell.run(["rm", "-f", write_socket, str(pid_file)], timeout=10)
            # Partial .tmp removal — a no-op on success (the .tmp was
            # renamed to the final file).
            self._shell.run(["rm", "-f", str(tmp_file)], timeout=10)
            # Abort the virsh backup-begin job to release the VM state
            # change lock (design D2).  domjobabort is idempotent.
            abort_result = self._shell.run(
                ["virsh", "domjobabort", "--domain", vm_name],
                timeout=30,
            )
            if not abort_result.success:
                logger.warning(
                    "virsh domjobabort failed for VM %s (job may have already terminated): %s",
                    vm_name,
                    abort_result.error,
                )
            # Source (libvirt) socket cleanup.
            self._shell.run(["rm", "-f", socket_path], timeout=10)
            # Temp XML cleanup (local filesystem, not shell).
            for xml_path in (backup_xml_path, checkpoint_xml_path):
                try:
                    xml_path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning(
                        "Failed to remove temp XML file %s: %s",
                        xml_path,
                        exc,
                    )

    def list(self, target: TargetConfig) -> list[SnapshotInfo]:
        """List existing backups at *target*.

        Scans ``target.path`` for ``*.qcow2`` files and obtains metadata
        via ``qemu-img info --output=json``.  Returns an empty list if
        the target directory does not exist.
        """
        if not target.path.exists():
            return []

        snapshots: list[SnapshotInfo] = []
        for file in target.path.glob("*.qcow2"):
            info_cmd = [
                "qemu-img",
                "info",
                "--output=json",
                str(file),
            ]
            info_result = self._shell.run(info_cmd, timeout=60)
            if not info_result.success:
                continue

            try:
                info = json.loads(info_result.stdout)
            except json.JSONDecodeError:
                continue

            name = file.stem
            actual_size = int(info.get("actual-size", 0))
            timestamp = parse_timestamp(name, file)

            snapshots.append(
                SnapshotInfo(
                    name=name,
                    path=file,
                    timestamp=timestamp,
                    allocation=actual_size,
                )
            )

        snapshots.sort(key=lambda s: s.timestamp)
        return snapshots

    def delete(self, backup: SnapshotInfo) -> ShellResult:
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
        result = self._shell.run(cmd, timeout=30)
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

    def _list_checkpoints_for_target(self, vm_name: str, target_hash: str) -> list[str]:
        """Return qsnap checkpoints matching *target_hash*."""
        prefix = f"qsnap-{target_hash}-"
        return [cp for cp in self.list_checkpoints(vm_name) if cp.startswith(prefix)]

    @staticmethod
    def _new_checkpoint_name(target_hash: str, taken: set[str] | None = None) -> str:
        """Generate a unique successor checkpoint name (design D2).

        Format: ``qsnap-{target_hash}-{yyyymmddTHHMMSS}`` — local time
        with seconds resolution, the same clock used for snapshot
        naming.  Only this format is ever generated for new checkpoints;
        legacy names (``qsnap-{target_hash}-{snapshot_name}``) remain
        parseable by discovery (design D3).

        Seconds resolution collides when two checkpoints are created
        within the same second (e.g. a fast FULL followed immediately
        by the first incremental of the same pipeline run — libvirt
        rejects the duplicate name).  Design D2 requires uniqueness
        per creation, so when *taken* (the existing qsnap checkpoint
        names for this VM+target) contains the candidate, the timestamp
        is bumped forward one second at a time until unique.  The bump
        only affects the name, never the checkpoint's actual creation
        time, and ordering semantics are preserved (the bumped name is
        still the newest).
        """
        now = datetime.now()
        existing: set[str] = taken if taken is not None else set()
        for offset in range(60):
            candidate = (
                f"qsnap-{target_hash}-{(now + timedelta(seconds=offset)).strftime('%Y%m%dT%H%M%S')}"
            )
            if candidate not in existing:
                return candidate
        # Practically unreachable (60 same-name collisions in a row);
        # fall back to microsecond resolution to guarantee uniqueness.
        return f"qsnap-{target_hash}-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"

    @staticmethod
    def _parse_checkpoint_timestamp(name: str, target_hash: str) -> datetime | None:
        """Parse the creation timestamp embedded in a checkpoint name.

        New-format names (``qsnap-{target_hash}-{yyyymmddTHHMMSS}``)
        carry the timestamp as the entire trailing segment.  Legacy
        names (``qsnap-{target_hash}-{snapshot_name}``) carry it inside
        the snapshot-name segment (same patterns as
        :func:`qsnap.utils.parsing.parse_timestamp`, most specific
        first).  Timezone-aware matches are normalized to naive local
        time so they compare coherently with new-format timestamps.

        Returns ``None`` when no timestamp can be parsed — callers sort
        such names oldest (conservative, design D3).
        """
        prefix = f"qsnap-{target_hash}-"
        remainder = name[len(prefix) :] if name.startswith(prefix) else name
        if re.fullmatch(r"\d{8}T\d{6}", remainder):
            try:
                return datetime.strptime(remainder, "%Y%m%dT%H%M%S")
            except ValueError:
                return None
        # Legacy fallback: timestamp embedded in the snapshot-name
        # segment (e.g. ``3.Projects_opencode.20260721T0018_vda``).
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

    def _newest_checkpoint(self, vm_name: str, target_hash: str) -> str | None:
        """Return the newest qsnap checkpoint for this VM+target (design D3).

        Thin wrapper over :meth:`_select_newest` that fetches the
        candidate list via ``virsh checkpoint-list --name`` —
        ``IStateManager`` is never consulted for checkpoint selection.
        """
        return self._select_newest(
            self._list_checkpoints_for_target(vm_name, target_hash),
            target_hash,
            vm_name,
        )

    def _select_newest(
        self,
        candidates: list[str],
        target_hash: str,
        vm_name: str,
    ) -> str | None:
        """Select the newest checkpoint from *candidates* (design D3).

        Orders the given ``qsnap-{target_hash}-*`` checkpoint names by
        the creation timestamp embedded in the name (new format first,
        legacy format as fallback).  Names whose timestamp cannot be
        parsed sort oldest (conservative) and are logged at WARNING.
        Returns ``None`` when *candidates* is empty.
        """
        if not candidates:
            return None
        newest: str | None = None
        newest_ts: datetime | None = None
        for name in candidates:
            ts = self._parse_checkpoint_timestamp(name, target_hash)
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
        """Delete a checkpoint via ``virsh checkpoint-delete --metadata``.

        Best-effort: failures are logged at WARNING and never
        propagated (design D3 — checkpoint cleanup is never fatal to a
        transfer).
        """
        del_cmd = [
            "virsh",
            "checkpoint-delete",
            "--domain",
            vm_name,
            checkpoint_name,
            "--metadata",
        ]
        del_result = self._shell.run(del_cmd, timeout=30)
        if not del_result.success:
            logger.warning(
                "Failed to delete checkpoint %s for VM %s: %s",
                checkpoint_name,
                vm_name,
                del_result.error,
            )

    def _delete_superseded_checkpoints(
        self,
        vm_name: str,
        target_hash: str,
        successor: str,
    ) -> None:
        """Delete all qsnap checkpoints for this VM+target older than *successor*.

        Called only after a successful AND verified export (design D3):
        the successor checkpoint already exists (created atomically with
        the export's ``backup-begin``), so deleting superseded
        checkpoints never opens a zero-checkpoint window.  Unparseable
        names sort oldest and are therefore always superseded.  A crash
        before this cleanup leaves a stale older checkpoint — harmless,
        because newest-wins discovery still picks the correct baseline
        and cleanup is retried here on the next successful run.
        """
        successor_ts = self._parse_checkpoint_timestamp(successor, target_hash)
        for name in self._list_checkpoints_for_target(vm_name, target_hash):
            if name == successor:
                continue
            ts = self._parse_checkpoint_timestamp(name, target_hash)
            if ts is not None and successor_ts is not None and ts >= successor_ts:
                # Not older than the successor — keep.
                continue
            self._delete_checkpoint_best_effort(vm_name, name)

    @staticmethod
    def target_hash(target_path: str) -> str:
        """Short hash of *target_path* for checkpoint naming."""
        return hashlib.md5(target_path.encode()).hexdigest()[:8]  # noqa: S324
