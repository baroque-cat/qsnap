"""Mock implementations of all domain module ABCs.

Each mock satisfies its ABC and returns valid result types.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import RetentionPolicy, TargetConfig, VMConfig
from qsnap.models.results import (
    BackupResult,
    ChangeResult,
    CommitResult,
    RetentionItem,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
    SnapshotResult,
)


class MockSnapshotProvider(ISnapshotProvider):
    """Mock snapshot provider returning valid result types."""

    def create(
        self,
        vm_config: VMConfig,
        snapshot_name: str,
        disk: str,
        snapshot_path: Path,
        quiesce: bool = False,
    ) -> SnapshotResult:
        return SnapshotResult(
            success=True,
            name=snapshot_name,
            path=snapshot_path,
            new_allocation=65536,
            error=None,
            content_hash="a" * 64,
        )

    def list(self, vm_config: VMConfig) -> list[SnapshotInfo]:
        return []

    def delete(self, snapshot: SnapshotInfo) -> ShellResult:
        return ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )


class MockBackupProvider(IBackupProvider):
    """Mock backup provider returning valid result types."""

    def transfer_missing(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
        rate_limit: str = "no",
    ) -> list[BackupResult]:
        return [
            BackupResult(
                success=True,
                snapshot_name=s.name,
                source_path=s.path,
                target_path=target.path / s.name,
                bytes_transferred=1048576,
                error=None,
            )
            for s in snapshots
        ]

    def list(self, target: TargetConfig) -> list[SnapshotInfo]:
        return []

    def delete(self, backup: SnapshotInfo) -> ShellResult:
        return ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )

    def create_full_backup(
        self,
        source_snapshot: SnapshotInfo,
        target: TargetConfig,
        compress: bool = False,
        bucket_level: str = "",
    ) -> BackupResult:
        return BackupResult(
            success=True,
            snapshot_name=source_snapshot.name,
            source_path=source_snapshot.path,
            target_path=target.path / f"{source_snapshot.name}.FULL.{bucket_level or 'monthly'}.qcow2",
            bytes_transferred=1048576,
            error=None,
        )


class MockBitmapBackupProvider(IBackupProvider):
    """Mock bitmap backup provider returning valid result types.

    ``transfer_missing`` returns ``BackupResult`` objects whose
    ``target_path`` points to a standalone qcow2 file (no backing chain),
    reflecting bitmap backup semantics (design D3).
    """

    def __init__(self, shell: IShell | None = None) -> None:
        # Constructor accepts IShell but doesn't need it for mock behavior.
        self._shell = shell

    def transfer_missing(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
        rate_limit: str = "no",
    ) -> list[BackupResult]:
        return [
            BackupResult(
                success=True,
                snapshot_name=s.name,
                source_path=s.path,
                target_path=target.path / f"{s.name}.qcow2",
                bytes_transferred=1048576,
                error=None,
            )
            for s in snapshots
        ]

    def list(self, target: TargetConfig) -> list[SnapshotInfo]:
        return []

    def delete(self, backup: SnapshotInfo) -> ShellResult:
        return ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )

    def create_full_backup(
        self,
        source_snapshot: SnapshotInfo,
        target: TargetConfig,
        compress: bool = False,
        bucket_level: str = "",
    ) -> BackupResult:
        return BackupResult(
            success=True,
            snapshot_name=source_snapshot.name,
            source_path=source_snapshot.path,
            target_path=target.path / f"{source_snapshot.name}.FULL.{bucket_level or 'monthly'}.qcow2",
            bytes_transferred=1048576,
            error=None,
        )


class MockRetentionEngine(IRetentionEngine):
    """Mock retention engine returning a valid RetentionResult."""

    def evaluate(
        self,
        items: list[RetentionItem],
        policy: RetentionPolicy,
        now: datetime,
        preserve_day_of_week: str = "monday",
    ) -> RetentionResult:
        keep = [item.name for item in items]
        return RetentionResult(keep=keep, remove=[])


class MockChangeDetector(IChangeDetector):
    """Mock change detector returning a valid ChangeResult."""

    def has_changed(
        self, vm_config: VMConfig, disk: str | None = None
    ) -> ChangeResult:
        return ChangeResult(
            changed=True,
            last_allocation=1000000,
            current_allocation=2000000,
        )


class MockLifecycleManager(ILifecycleManager):
    """Mock lifecycle manager returning a valid CommitResult."""

    def blockcommit(
        self,
        vm_config: VMConfig,
        snapshots_to_merge: list[SnapshotInfo],
        *,
        deep_verify: bool = False,
    ) -> CommitResult:
        if not snapshots_to_merge:
            return CommitResult(success=True, committed_snapshot="", error=None)
        return CommitResult(
            success=True,
            committed_snapshot=snapshots_to_merge[0].name,
            error=None,
        )
