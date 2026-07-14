"""FileCopyBackupProvider — file-copy backup transfer with qemu-img rebase.

Implements ``IBackupProvider``.  Does NOT inherit from Core (design D1).
Dependency: ``IShell`` only.

For incremental backups (``target.incremental == True``), the backing
file path is rebased to a bare filename in the target directory using
``qemu-img rebase -u`` (design D5: metadata-only update, no data copy).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.shell import IShell
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import BackupResult, ShellResult, SnapshotInfo
from qsnap.modules.backup.verification import verify_backup
from qsnap.utils.parsing import parse_timestamp

logger = logging.getLogger(__name__)


class FileCopyBackupProvider(IBackupProvider):
    """File-copy backup provider using ``cp`` + ``qemu-img rebase``."""

    def __init__(self, shell: IShell) -> None:
        self._shell = shell

    # ── IBackupProvider implementation ────────────────────────────────

    @staticmethod
    def _find_full_anchor(target: TargetConfig) -> Path | None:
        """Find the most recent FULL anchor file in the target directory.

        Looks for ``*.FULL.*.qcow2`` files.  Returns the path to the
        most recent one (by modification time), or ``None`` if none exist.
        """
        if not target.path.exists():
            return None
        full_files = list(target.path.glob("*.FULL.*.qcow2"))
        if not full_files:
            return None
        # Most recent by mtime
        return max(full_files, key=lambda f: f.stat().st_mtime)

    def transfer_missing(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
    ) -> list[BackupResult]:
        """Copy snapshots not yet present at *target*.

        1. Determine existing backups via ``list()``.
        2. For each missing snapshot: ``cp`` to ``target.path/<name>.qcow2``.
        3. If incremental: ``qemu-img rebase -u -b <bare_backing> <target>``.
        """
        existing = self.list(target)
        existing_names = {s.name for s in existing}

        results: list[BackupResult] = []

        for snapshot in snapshots:
            if snapshot.name in existing_names:
                continue

            target_file = target.path / f"{snapshot.name}.qcow2"

            # Step 2: Copy file
            cp_cmd = ["cp", str(snapshot.path), str(target_file)]
            cp_result = self._shell.run(cp_cmd, timeout=600)
            if not cp_result.success:
                results.append(
                    BackupResult(
                        success=False,
                        snapshot_name=snapshot.name,
                        source_path=snapshot.path,
                        target_path=target_file,
                        bytes_transferred=0,
                        error=cp_result.error,
                    )
                )
                continue

            # Get file size for bytes_transferred
            try:
                bytes_transferred = target_file.stat().st_size
            except OSError:
                bytes_transferred = 0

            # Step 3: If incremental, rebase backing path (design D5)
            if target.incremental:
                # Check for FULL anchor in target directory
                full_anchor = self._find_full_anchor(target)
                if full_anchor is not None:
                    # Rebase to FULL anchor
                    rebase_cmd = [
                        "qemu-img",
                        "rebase",
                        "-u",
                        "-b",
                        f"./{full_anchor.name}",
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

            # Step 4: Verification (if enabled).
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
        source_snapshot: SnapshotInfo,
        target: TargetConfig,
        compress: bool = False,
    ) -> BackupResult:
        """Create a standalone full (anchor) backup via ``qemu-img convert``.

        Runs ``qemu-img convert [-c] -f qcow2 -O qcow2`` to produce a
        self-contained qcow2 file.  Uses an atomic pattern: convert to
        a ``.tmp`` file, then rename on success.
        """
        # Generate full backup name: vm.FULL.YYYYMMDD.qcow2
        date_str = source_snapshot.timestamp.strftime("%Y%m%d")
        full_name = f"{source_snapshot.name.split('.')[0]}.FULL.{date_str}"
        target_file = target.path / f"{full_name}.qcow2"
        tmp_file = target.path / f"{full_name}.qcow2.tmp"

        # Build qemu-img convert command
        convert_cmd = [
            "qemu-img",
            "convert",
        ]
        if compress:
            convert_cmd.append("-c")
        convert_cmd.extend([
            "-f", "qcow2",
            "-O", "qcow2",
            str(source_snapshot.path),
            str(tmp_file),
        ])

        convert_result = self._shell.run(convert_cmd, timeout=3600)
        if not convert_result.success:
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
        """Delete a backup file via ``rm -f``."""
        cmd = ["rm", "-f", str(backup.path)]
        return self._shell.run(cmd, timeout=30)
