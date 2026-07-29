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
        *,
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
        convert_parallel: int = 4,
        convert_out_of_order: bool = True,
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
        vm_name: str,
        source_snapshot: SnapshotInfo,
        target: TargetConfig,
        compress: bool = False,
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
        convert_parallel: int = 4,
        convert_out_of_order: bool = True,
    ) -> BackupResult:
        return BackupResult(
            success=True,
            snapshot_name=source_snapshot.name,
            source_path=source_snapshot.path,
            target_path=target.path / f"{vm_name}.FULL.qcow2",
            bytes_transferred=1048576,
            error=None,
        )

    def list_checkpoints(self, vm_name: str) -> list[str]:
        """Return an empty list — no checkpoints in mock mode."""
        return []

    @staticmethod
    def target_hash(target_path: str) -> str:
        """Return a deterministic 8-char hash for the target path."""
        import hashlib

        return hashlib.md5(target_path.encode()).hexdigest()[:8]  # noqa: S324


class MockRetentionEngine(IRetentionEngine):
    """Mock retention engine returning a valid RetentionResult.

    Can be configured with explicit keep/remove lists.  When called
    without arguments, keeps all items and removes none (same behaviour
    as before).

    Args:
        keep: Specific item names to keep.  When ``None``, keeps all items.
        remove: Specific item names to remove.
    """

    def __init__(
        self,
        keep: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> None:
        self._keep = keep
        self._remove = remove or []

    def evaluate(
        self,
        items: list[RetentionItem],
        policy: RetentionPolicy,
        now: datetime,
    ) -> RetentionResult:
        if self._keep is not None:
            return RetentionResult(keep=list(self._keep), remove=list(self._remove))
        # Backward-compatible default: keep everything, remove nothing.
        return RetentionResult(keep=[item.name for item in items], remove=[])


class MockChangeDetector(IChangeDetector):
    """Mock change detector returning a valid ChangeResult.

    Can be configured with a ``changed`` flag and allocation values.
    When called without arguments, returns ``changed=True`` (same
    behaviour as before).

    Args:
        changed: Whether the VM disk has changed since last check.
        last_alloc: Last recorded allocation size in bytes.
        current_alloc: Current allocation size in bytes.
    """

    def __init__(
        self,
        changed: bool = True,
        last_alloc: int = 1000000,
        current_alloc: int = 2000000,
    ) -> None:
        self._changed = changed
        self._last_alloc = last_alloc
        self._current_alloc = current_alloc

    def has_changed(self, vm_config: VMConfig, disk: str | None = None) -> ChangeResult:
        return ChangeResult(
            changed=self._changed,
            last_allocation=self._last_alloc,
            current_allocation=self._current_alloc,
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
