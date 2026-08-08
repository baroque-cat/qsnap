"""Mock implementations of all domain module ABCs.

Each mock satisfies its ABC and returns valid result types.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import DiskConfig, RetentionPolicy, TargetConfig, VMConfig
from qsnap.models.results import (
    BackupInfo,
    BackupResult,
    BaselineAssessment,
    ChangeResult,
    CommitResult,
    RetentionItem,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
    SnapshotResult,
    SnapshotSpec,
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

    def create_multi(
        self,
        vm_config: VMConfig,
        specs: Sequence[SnapshotSpec],
        quiesce: bool = False,
    ) -> list[SnapshotResult]:
        """Return one successful SnapshotResult per spec in order."""
        return [
            SnapshotResult(
                success=True,
                name=spec.name,
                path=spec.path,
                new_allocation=65536,
                error=None,
                disk=spec.disk,
            )
            for spec in specs
        ]

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

    ``run_backup`` returns ``BackupResult`` objects whose
    ``target_path`` points to a standalone qcow2 file (no backing chain),
    reflecting bitmap backup semantics (design D3).

    Configurable via constructor:
    - ``assessment``: BaselineAssessment returned by ``assess_baseline``
      (default: no_checkpoint).
    - ``backup_kind``: ``"full"``, ``"delta"``, or ``"recovered_delta"``
      — the kind set on BackupResult returned by ``run_backup``.
    """

    def __init__(
        self,
        shell: IShell | None = None,
        deferred: bool = False,
        assessment: BaselineAssessment | None = None,
        backup_kind: str = "delta",
    ) -> None:
        # Constructor accepts IShell but doesn't need it for mock behavior.
        # ``deferred=True`` simulates the stopped-VM-with-checkpoint case:
        # run_backup() reports success but defers the transfer (no file,
        # no checkpoint mutation, baseline not updated — design D8).
        self._shell = shell
        self._deferred = deferred
        self._assessment = assessment or BaselineAssessment(status="no_checkpoint")
        self._backup_kind = backup_kind

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
        """Return a successful backup result.

        Generates a freeze-timestamp name matching the orthogonal model.
        """
        disk_target = disk.target
        freeze_ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        hex_suffix = secrets.token_hex(3)
        if force_full or self._backup_kind == "full":
            name = f"{vm_config.name}.FULL.{freeze_ts}_{disk_target}_{hex_suffix}"
        else:
            name = f"{vm_config.name}.{freeze_ts}_{disk_target}_{hex_suffix}"
        return BackupResult(
            success=True,
            snapshot_name=name,
            source_path=disk.base_image,
            target_path=target.path / f"{name}.qcow2",
            bytes_transferred=1048576,
            error=None,
            disk=disk_target,
            deferred=self._deferred,
            kind=self._backup_kind,
        )

    def assess_baseline(
        self, vm_config: VMConfig, target: TargetConfig, disk: DiskConfig
    ) -> BaselineAssessment:
        """Return the configured baseline assessment (read-only)."""
        return self._assessment

    def list(self, target: TargetConfig) -> list[BackupInfo]:
        return []

    def delete(self, backup: BackupInfo) -> ShellResult:
        return ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
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

    The ``current_allocation`` parameter controls the value returned
    by ``has_changed()`` in ``ChangeResult.current_allocation``.  Core
    tests use this to simulate "changed" vs "unchanged" source disk
    for the ``backup_create="onchange"`` gate.

    Args:
        changed: Whether the VM disk has changed since last check.
        last_allocation: Last recorded allocation size in bytes.
        current_allocation: Current allocation size in bytes.
    """

    def __init__(
        self,
        changed: bool = True,
        last_allocation: int = 1000000,
        current_allocation: int = 2000000,
    ) -> None:
        self._changed = changed
        self._last_alloc = last_allocation
        self._current_alloc = current_allocation

    @property
    def current_allocation(self) -> int:
        """The current allocation value returned by ``has_changed()``."""
        return self._current_alloc

    @current_allocation.setter
    def current_allocation(self, value: int) -> None:
        self._current_alloc = value

    @property
    def last_allocation(self) -> int:
        """The last allocation value returned by ``has_changed()``."""
        return self._last_alloc

    @last_allocation.setter
    def last_allocation(self, value: int) -> None:
        self._last_alloc = value

    @property
    def changed(self) -> bool:
        """The ``changed`` flag returned by ``has_changed()``.

        Set to ``False`` for "unchanged" scenarios, ``True`` (default)
        for "changed" or detector-failure scenarios.
        """
        return self._changed

    @changed.setter
    def changed(self, value: bool) -> None:
        self._changed = value

    def has_changed(self, vm_config: VMConfig, disk: str) -> ChangeResult:
        return ChangeResult(
            changed=self._changed,
            last_allocation=self._last_alloc,
            current_allocation=self._current_alloc,
            disk=disk,
        )


class MockLifecycleManager(ILifecycleManager):
    """Mock lifecycle manager returning a valid CommitResult."""

    def blockcommit(
        self,
        vm_config: VMConfig,
        snapshots_to_merge: list[SnapshotInfo],
        *,
        disk: str,
        base_image: Path,
        deep_verify: bool = False,
    ) -> CommitResult:
        if not snapshots_to_merge:
            return CommitResult(success=True, committed_snapshot="", error=None)
        return CommitResult(
            success=True,
            committed_snapshot=snapshots_to_merge[0].name,
            error=None,
        )
