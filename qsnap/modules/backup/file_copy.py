"""FileCopyBackupProvider — rsync-based backup transfer with qemu-img rebase.

Implements ``IBackupProvider``.  Does NOT inherit from Core (design D1).
Dependencies: ``IShell`` (required), ``IStateManager`` (optional — needed
for FULL backup tracking and incremental→FULL dependency recording).

For incremental backups (``target.incremental == True``), the backing
file path is rebased to a bare filename in the target directory using
``qemu-img rebase -u`` (design D5: metadata-only update, no data copy).

All transfers use ``rsync`` (design D3: no ``cp`` fallback).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import BackupResult, ShellResult, SnapshotInfo
from qsnap.modules.backup.nbd_helper import (
    is_libvirt_new_enough,
    is_vm_running,
    nbd_full_export,
)
from qsnap.modules.backup.verification import verify_backup, verify_full_backup
from qsnap.utils.parsing import parse_rate_limit, parse_timestamp, rate_limit_to_kib

logger = logging.getLogger(__name__)


class FileCopyBackupProvider(IBackupProvider):
    """File-copy backup provider using ``rsync`` + ``qemu-img rebase``."""

    def __init__(
        self,
        shell: IShell,
        state: IStateManager | None = None,
    ) -> None:
        self._shell = shell
        self._state = state

    # ── IBackupProvider implementation ────────────────────────────────

    @staticmethod
    def _find_full_anchor(target: TargetConfig) -> Path | None:
        """Find the most recent FULL anchor file in the target directory.

        Looks for ``*.FULL.*.qcow2`` files.  Parses the date from the
        filename (``YYYYMMDD``) and returns the most recent by date,
        or ``None`` if none exist.
        """
        sorted_anchors = FileCopyBackupProvider._get_sorted_full_anchors(target)
        return sorted_anchors[0] if sorted_anchors else None

    @staticmethod
    def _get_sorted_full_anchors(target: TargetConfig) -> list[Path]:
        """Return all FULL anchor files sorted by date (most recent first).

        Looks for ``*.FULL.*.qcow2`` files.  Parses the date from the
        filename (``YYYYMMDD``) and returns them sorted descending by
        date.  Returns an empty list if none exist.
        """
        if not target.path.exists():
            return []
        full_files = list(target.path.glob("*.FULL.*.qcow2"))
        if not full_files:
            return []

        def _extract_date(path: Path) -> str:
            match = re.search(r"\.FULL\.(\d{8})\.", path.name)
            return match.group(1) if match else ""

        return sorted(full_files, key=_extract_date, reverse=True)

    def transfer_missing(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
        rate_limit: str = "no",
    ) -> list[BackupResult]:
        """Copy snapshots not yet present at *target* using ``rsync``.

        1. Determine existing backups via ``list()``.
        2. For each missing snapshot: ``rsync --partial`` (or
           ``rsync --bwlimit=<kib> --partial`` when rate limiting).
        3. If incremental: ``qemu-img rebase -u -b <bare_backing> <target>``.
        4. After rebase, record incremental→FULL dependency in state.
        """
        existing = self.list(target)
        existing_names = {s.name for s in existing}

        results: list[BackupResult] = []

        for snapshot in snapshots:
            if snapshot.name in existing_names:
                continue

            # Stale state self-healing: before rsync, verify the snapshot
            # file still exists on disk.  If it was already blockcommitted
            # by a prior run that failed to update state, clean the entry
            # and skip the transfer (per chain-integrity-verification spec).
            if not os.path.exists(str(snapshot.path)):
                if self._state is not None:
                    self._state.remove_snapshot(
                        vm_config.name, snapshot.name
                    )
                logger.warning(
                    "Stale state entry: snapshot %s file not found on "
                    "disk — removed from state",
                    snapshot.name,
                )
                continue

            target_file = target.path / f"{snapshot.name}.qcow2"

            # Step 3: Transfer file (always rsync — design D3)
            if rate_limit != "no":
                bwlimit = rate_limit_to_kib(rate_limit)
                transfer_cmd = [
                    "rsync",
                    f"--bwlimit={bwlimit}",
                    "--partial",
                    "--progress",
                    str(snapshot.path),
                    str(target_file),
                ]
                logger.info(
                    "Transferring %s to %s (rate limit: %s)",
                    snapshot.name,
                    target_file,
                    rate_limit,
                )
            else:
                transfer_cmd = [
                    "rsync",
                    "--partial",
                    "--progress",
                    str(snapshot.path),
                    str(target_file),
                ]
                logger.info(
                    "Transferring %s to %s",
                    snapshot.name,
                    target_file,
                )

            logger.debug("Transfer command: %s", " ".join(transfer_cmd))

            start_time = time.monotonic()
            transfer_result = self._shell.run(transfer_cmd, timeout=3600)
            elapsed = time.monotonic() - start_time

            if not transfer_result.success:
                results.append(
                    BackupResult(
                        success=False,
                        snapshot_name=snapshot.name,
                        source_path=snapshot.path,
                        target_path=target_file,
                        bytes_transferred=0,
                        error=transfer_result.error,
                    )
                )
                continue

            # Get file size for bytes_transferred
            try:
                bytes_transferred = target_file.stat().st_size
            except OSError:
                bytes_transferred = 0

            # Log throughput
            if elapsed > 0 and bytes_transferred > 0:
                throughput_bps = int(bytes_transferred / elapsed)
                throughput_mib = throughput_bps / (1024 * 1024)
                logger.info(
                    "Transferred %s: %d bytes in %.1fs (%.1f MiB/s)",
                    snapshot.name,
                    bytes_transferred,
                    elapsed,
                    throughput_mib,
                )

                # Warn if throughput is less than 10% of configured rate limit
                if rate_limit != "no":
                    configured_bps = parse_rate_limit(rate_limit)
                    if configured_bps > 0:
                        ten_pct = configured_bps * 0.1
                        if throughput_bps < ten_pct:
                            logger.warning(
                                "Transfer of %s slower than expected: "
                                "%d B/s (limit: %d B/s). "
                                "Check target disk health.",
                                snapshot.name,
                                throughput_bps,
                                configured_bps,
                            )

            # Step 4: If incremental, rebase backing path (design D5)
            if target.incremental:
                # Check for FULL anchor in target directory
                # Try anchors from most recent to oldest, verifying M1
                # integrity before use (per backup-full-verification spec).
                full_anchor: Path | None = None
                sorted_anchors = self._get_sorted_full_anchors(target)
                for candidate in sorted_anchors:
                    m1_error = verify_full_backup(
                        self._shell, candidate, "metadata"
                    )
                    if m1_error is None:
                        full_anchor = candidate
                        break
                    else:
                        logger.warning(
                            "FULL anchor %s failed M1 verification — "
                            "trying older anchor: %s",
                            candidate.name,
                            m1_error,
                        )

                if full_anchor is None and sorted_anchors:
                    logger.warning(
                        "All FULL anchors in %s failed M1 verification "
                        "— skipping rebase for %s",
                        target.path,
                        snapshot.name,
                    )

                if full_anchor is not None:
                    # Rebase to FULL anchor
                    rebase_cmd = [
                        "qemu-img",
                        "rebase",
                        "-u",
                        "-b",
                        f"./{full_anchor.name}",
                        "-F",
                        "qcow2",
                        str(target_file),
                    ]
                    rebase_result = self._shell.run(rebase_cmd, timeout=60)
                    if not rebase_result.success:
                        results.append(
                            BackupResult(
                                success=False,
                                snapshot_name=snapshot.name,
                                source_path=snapshot.path,
                                target_path=target_file,
                                bytes_transferred=bytes_transferred,
                                error=f"rebase failed: {rebase_result.error}",
                            )
                        )
                        continue
                    # Record incremental→FULL dependency (Task 4.4)
                    if self._state is not None:
                        self._state.record_incremental_dependency(
                            str(target.path),
                            snapshot.name,
                            full_anchor.stem,
                        )
                else:
                    # No FULL anchor — use source backing filename
                    info_cmd = [
                        "qemu-img",
                        "info",
                        "--output=json",
                        str(snapshot.path),
                    ]
                    info_result = self._shell.run(info_cmd, timeout=60)
                    if info_result.success:
                        try:
                            info = json.loads(info_result.stdout)
                            backing_filename = info.get("backing-filename")
                            if backing_filename:
                                backing_basename = Path(backing_filename).name
                                rebase_cmd = [
                                    "qemu-img",
                                    "rebase",
                                    "-u",
                                    "-b",
                                    backing_basename,
                                    "-F",
                                    "qcow2",
                                    str(target_file),
                                ]
                                rebase_result = self._shell.run(rebase_cmd, timeout=60)
                                if not rebase_result.success:
                                    results.append(
                                        BackupResult(
                                            success=False,
                                            snapshot_name=snapshot.name,
                                            source_path=snapshot.path,
                                            target_path=target_file,
                                            bytes_transferred=bytes_transferred,
                                            error=f"rebase failed: {rebase_result.error}",
                                        )
                                    )
                                    continue
                        except (json.JSONDecodeError, KeyError, TypeError) as exc:
                            results.append(
                                BackupResult(
                                    success=False,
                                    snapshot_name=snapshot.name,
                                    source_path=snapshot.path,
                                    target_path=target_file,
                                    bytes_transferred=bytes_transferred,
                                    error=f"rebase failed: {exc}",
                                )
                            )
                            continue

            # Step 5: Verification (if enabled).
            verify_error = verify_backup(
                self._shell,
                str(snapshot.path),
                str(target_file),
                target.verify,
                expected_hash=snapshot.content_hash,
            )
            if verify_error is not None:
                results.append(
                    BackupResult(
                        success=False,
                        snapshot_name=snapshot.name,
                        source_path=snapshot.path,
                        target_path=target_file,
                        bytes_transferred=bytes_transferred,
                        error=verify_error,
                    )
                )
                continue

            results.append(
                BackupResult(
                    success=True,
                    snapshot_name=snapshot.name,
                    source_path=snapshot.path,
                    target_path=target_file,
                    bytes_transferred=bytes_transferred,
                    error=None,
                )
            )

        return results

    def create_full_backup(
        self,
        vm_name: str,
        source_snapshot: SnapshotInfo,
        target: TargetConfig,
        compress: bool = False,
        bucket_level: str = "monthly",
    ) -> BackupResult:
        """Create a standalone full (anchor) backup.

        ``vm_name`` is the full, untruncated VM name (e.g.
        ``"3.Projects_opencode"``), passed from Core's
        ``vm_config.name``.  It is used directly for ``is_vm_running()``,
        ``nbd_full_export()``, and ``full_name`` generation — the method
        SHALL NOT extract the VM name from the snapshot filename.

        Detects VM running state via ``virsh dominfo``.  When the VM is
        running and libvirt >= 6.0, uses the NBD pull-model (``virsh
        backup-begin`` + ``qemu-img convert -n nbd:``) to avoid lock
        conflicts on the active layer (design D1).  When the VM is
        stopped, uses direct ``qemu-img convert [-c]`` on the snapshot
        file (existing behavior, no lock conflict).

        Both NBD and direct-convert paths support compression via
        ``-c`` flag on ``qemu-img convert``.

        Uses an atomic pattern: convert to a ``.tmp`` file, then rename
        on success.  On failure, the ``.tmp`` file is removed and no
        final file is created.

        Callers are responsible for recording the FULL in state via
        ``IStateManager.record_full_backup()`` after optionally
        performing integrity verification.
        """
        # Generate full backup name: vm.FULL.YYYYMMDD.qcow2
        date_str = source_snapshot.timestamp.strftime("%Y%m%d")
        full_name = f"{vm_name}.FULL.{date_str}"
        target_file = target.path / f"{full_name}.qcow2"
        tmp_file = target.path / f"{full_name}.qcow2.tmp"

        # ── Method selection: NBD (running VM) vs direct convert (stopped) ──
        use_nbd = False
        if is_vm_running(self._shell, vm_name):
            if is_libvirt_new_enough(self._shell):
                use_nbd = True
            else:
                logger.warning(
                    "libvirt < 6.0 — NBD unavailable, attempting direct "
                    "convert (may fail on running VM)"
                )

        if use_nbd:
            # Run NBD full-export to .tmp file (no --force-share, no
            # checkpoint — design D3, D5).  Compression is passed
            # through to nbd_full_export() via the -c flag.
            nbd_result = nbd_full_export(self._shell, vm_name, str(tmp_file), compress=compress)
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
        else:
            # Direct convert path (stopped VM or fallback).
            convert_cmd = [
                "qemu-img",
                "convert",
            ]
            if compress:
                convert_cmd.append("-c")
            convert_cmd.extend(
                [
                    "-f",
                    "qcow2",
                    "-O",
                    "qcow2",
                    str(source_snapshot.path),
                    str(tmp_file),
                ]
            )

            convert_result = self._shell.run(convert_cmd, timeout=3600)
            if not convert_result.success:
                # Remove .tmp on failure — no final file created.
                self._shell.run(["rm", "-f", str(tmp_file)], timeout=10)
                return BackupResult(
                    success=False,
                    snapshot_name=source_snapshot.name,
                    source_path=source_snapshot.path,
                    target_path=target_file,
                    bytes_transferred=0,
                    error=convert_result.error,
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
        the target directory does not exist (no shell commands executed).
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
        """Delete a backup file via ``rm -f``.

        If the backup is a FULL (name matches ``*.FULL.*``), checks for
        dependent incrementals via ``state.get_incremental_dependencies()``.
        If dependents exist, skips deletion (ghost retention).  Cascade
        deletion of orphaned incrementals is handled by
        ``Core._cleanup_backups()``, not here.
        """
        # Check if this is a FULL backup
        is_full = ".FULL." in backup.name

        if is_full and self._state is not None:
            target_path = str(backup.path.parent)
            dependents = self._state.get_incremental_dependencies(target_path, backup.name)
            if dependents:
                logger.info(
                    "Ghost retention: skipping FULL %s — %d dependent "
                    "incremental(s) still in keep-set",
                    backup.name,
                    len(dependents),
                )
                return ShellResult(
                    success=True,
                    stdout="",
                    stderr="",
                    returncode=0,
                    error=None,
                )

        # Delete the backup file (FULL with no dependents, or non-FULL)
        cmd = ["rm", "-f", str(backup.path)]
        return self._shell.run(cmd, timeout=30)
