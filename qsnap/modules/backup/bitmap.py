"""BitmapBackupProvider — NBD pull-model incremental backup via libvirt.

Implements ``IBackupProvider``.  Does NOT inherit from Core (design D1).
Dependency: ``IShell`` only.

Uses ``virsh backup-begin`` with a pull-model NBD Unix socket to export
dirty blocks, then ``qemu-img convert -n nbd:unix:<socket>`` to pull
them into a standalone qcow2 file on the target — no backing chain.

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
4. ``qemu-img convert -n nbd:unix:<socket> <target_file>``
5. Verify the target file (if ``target.verify != "off"``).
6. After a successful AND verified export, delete all superseded
   (older) qsnap checkpoints for this VM+target via
   ``virsh checkpoint-delete --metadata`` — the successor already
   exists, so rotation never opens a zero-checkpoint window.
7. On export/verify failure, preserve the prior checkpoint for retry,
   delete the just-created successor checkpoint best-effort, and
   delete the partial target file.
8. Socket and XML temp files are always cleaned up in a ``finally``
   block.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import BackupResult, ShellResult, SnapshotInfo
from qsnap.utils.nbd import (
    get_first_disk_target,
    nbd_full_export,
    write_backup_xml,
    write_checkpoint_xml,
)
from qsnap.utils.parsing import parse_timestamp
from qsnap.utils.verification import verify_backup

logger = logging.getLogger(__name__)


class BitmapBackupProvider(IBackupProvider):
    """Backup provider using NBD pull-model via ``virsh backup-begin``."""

    def __init__(
        self,
        shell: IShell,
        state: IStateManager | None = None,
    ) -> None:
        self._shell = shell
        self._state = state

    # ── IBackupProvider implementation ────────────────────────────────

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
        rate_limit: str = "no",
        *,
        full_verify_before_rebase: str = "metadata",
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
    ) -> list[BackupResult]:
        """Transfer missing snapshots via NBD pull-model.

        See module docstring for the NBD backup lifecycle.

        ``rate_limit`` is accepted for interface compatibility but ignored
        — NBD-based transfers cannot be throttled via ``rsync --bwlimit``.

        ``full_verify_before_rebase`` is accepted for interface
        compatibility but ignored — the bitmap path does not use
        ``qemu-img rebase``.

        ``compression_type`` selects the compression algorithm for the
        NBD convert command (``"zstd"`` default, ``"zlib"`` alternative).
        Only effective when ``target.compress`` is ``True``.

        ``stall_timeout`` is the stall-detection timeout in seconds for
        the convert command.  When ``0``, falls back to fixed-timeout
        :meth:`IShell.run`.
        """
        existing = self.list(target)
        existing_names = {s.name for s in existing}

        target_hash = self.target_hash(str(target.path))

        results: list[BackupResult] = []

        for snapshot in snapshots:
            if snapshot.name in existing_names:
                continue

            target_file = target.path / f"{snapshot.name}.qcow2"
            socket_path = f"/tmp/qsnap-backup-{os.getpid()}.sock"

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

                # Step 4: Pull dirty blocks via NBD.
                # libvirt's NBD server exports each disk under its
                # target device name (e.g., "vda").  We must specify
                # exportname in the NBD URI to connect to the correct
                # export.
                disk_target = get_first_disk_target(
                    self._shell,
                    vm_config.name,
                )
                nbd_uri = f"nbd:unix:{socket_path}"
                if disk_target:
                    nbd_uri = f"nbd:unix:{socket_path}:exportname={disk_target}"

                convert_cmd = [
                    "qemu-img",
                    "convert",
                    "-O",
                    "qcow2",
                ]
                # Compression: -c compresses the output qcow2.  When
                # compression_type is "zstd", adds -o compression_type=zstd
                # for 11x faster compression than the default zlib (D7).
                if target.compress:
                    convert_cmd.append("-c")
                    if compression_type == "zstd":
                        convert_cmd.extend(["-o", "compression_type=zstd"])
                convert_cmd.extend([nbd_uri, str(target_file)])
                start_time = time.monotonic()
                if stall_timeout > 0:
                    convert_result = self._shell.run_with_stall_detection(
                        convert_cmd,
                        output_file=target_file,
                        stall_timeout=stall_timeout,
                    )
                else:
                    convert_result = self._shell.run(convert_cmd, timeout=600)
                elapsed = time.monotonic() - start_time
                if not convert_result.success:
                    # Export failed: preserve the prior checkpoint for
                    # retry, delete the just-created successor checkpoint
                    # best-effort (it must not become the newest baseline
                    # of a failed export), and delete the partial target
                    # file so retention cleanup does not find it and log
                    # a misleading ``[delete] removed backup`` message
                    # (design D3).
                    self._cleanup_partial_file(target_file)
                    self._delete_checkpoint_best_effort(vm_config.name, successor)
                    results.append(
                        BackupResult(
                            success=False,
                            snapshot_name=snapshot.name,
                            source_path=snapshot.path,
                            target_path=target_file,
                            bytes_transferred=0,
                            error=convert_result.error,
                        )
                    )
                    continue

                # Step 5: Verification (if enabled).
                verify_error = verify_backup(
                    self._shell,
                    str(snapshot.path),
                    str(target_file),
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
                # Step 8: NBD job abort + socket + XML temp file cleanup
                # (always, even on failure).  Abort the virsh
                # backup-begin job to release the VM state change lock
                # (mirrors nbd_full_export in qsnap/utils/nbd.py).
                # domjobabort is idempotent — safe to call when no job
                # is running.  On failure, log a WARNING but do NOT
                # propagate the error — the socket cleanup is the
                # critical path and must still proceed.
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
                # Socket cleanup.
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
        """Create a standalone FULL backup via NBD full export.

        ``vm_name`` is the full, untruncated VM name (e.g.
        ``"3.Projects_opencode"``), passed from Core's
        ``vm_config.name``.  It is used directly for
        ``nbd_full_export()`` and ``full_name`` generation — the method
        SHALL NOT extract the VM name from the snapshot filename.

        Uses the shared :func:`nbd_full_export` helper (no
        ``--incremental`` flag) to produce a standalone qcow2 on the
        target.  A checkpoint named
        ``qsnap-{target_hash}-{yyyymmddTHHMMSS}`` is created
        **atomically** with the FULL's ``backup-begin`` (design D1/D2):
        the baseline bitmap coincides with the FULL's freeze point, so
        the first incremental after a FULL exports every block dirtied
        since the FULL started — a faithful, gap-free chain.  On export
        failure the just-created checkpoint is deleted best-effort by
        :func:`nbd_full_export`, preserving any prior baseline.

        Uses an atomic pattern: convert to a ``.tmp`` file, then rename
        on success.  On failure, the ``.tmp`` file is removed.

        When ``compress=True``, the ``-c`` flag is passed through to
        :func:`nbd_full_export` and on to ``qemu-img convert``,
        producing a compressed qcow2.  When ``compression_type`` is
        ``"zstd"``, ``-o compression_type=zstd`` is added for faster
        compression.

        This method SHALL NOT call ``self._state.record_full_backup()``
        — state recording is Core's responsibility after post-create
        verification passes (matches ``FileCopyBackupProvider``
        behavior).
        """
        # Generate full backup name: vm.FULL.YYYYMMDD.qcow2
        date_str = source_snapshot.timestamp.strftime("%Y%m%d")
        full_name = f"{vm_name}.FULL.{date_str}"
        target_file = target.path / f"{full_name}.qcow2"
        tmp_file = target.path / f"{full_name}.qcow2.tmp"

        # The baseline checkpoint is created atomically with the FULL's
        # backup-begin (design D1/D2).
        checkpoint_name = self._new_checkpoint_name(self.target_hash(str(target.path)))

        # Run NBD full-export to .tmp file (no --incremental flag;
        # checkpoint XML passed for atomic baseline creation).
        # Compression is passed through via the -c flag.
        nbd_result = nbd_full_export(
            self._shell,
            vm_name,
            str(tmp_file),
            compress=compress,
            compression_type=compression_type,
            stall_timeout=stall_timeout,
            checkpoint_name=checkpoint_name,
        )
        if not nbd_result.success:
            # Remove .tmp on failure — no final file created.
            self._shell.run(["rm", "-f", str(tmp_file)], timeout=10)
            return BackupResult(
                success=False,
                snapshot_name=source_snapshot.name,
                source_path=source_snapshot.path,
                target_path=target_file,
                bytes_transferred=0,
                error=nbd_result.error,
            )

        # Atomic rename: mv .tmp to final name
        mv_cmd = ["mv", str(tmp_file), str(target_file)]
        mv_result = self._shell.run(mv_cmd, timeout=30)
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
        # verification passes (design D4 — matches FileCopyBackupProvider
        # behavior, which also does not self-record).  The provider SHALL
        # NOT call self._state.record_full_backup() here.

        return BackupResult(
            success=True,
            snapshot_name=source_snapshot.name,
            source_path=source_snapshot.path,
            target_path=target_file,
            bytes_transferred=bytes_transferred,
            error=None,
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
