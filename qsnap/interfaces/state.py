"""IStateManager — abstract cross-run state persistence interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from qsnap.models.results import DeferredBlockcommit, FullBackupInfo, SnapshotInfo


class IStateManager(ABC):
    """Abstract interface for persisting cross-run state per VM.

    Default implementation uses JSON files under
    ``/var/lib/qsnap/state/``.  Tests use ``InMemoryStateManager``.
    """

    @abstractmethod
    def get_last_allocation(self, vm_name: str) -> int | None:
        """Return the last recorded allocation size for *vm_name*, or None."""
        ...

    @abstractmethod
    def set_last_allocation(self, vm_name: str, alloc: int) -> None:
        """Record the current allocation size for *vm_name*."""
        ...

    @abstractmethod
    def record_snapshot(self, vm_name: str, info: SnapshotInfo) -> None:
        """Record a snapshot entry for *vm_name*."""
        ...

    @abstractmethod
    def get_snapshots(self, vm_name: str) -> list[SnapshotInfo]:
        """Return all recorded snapshots for *vm_name*, sorted by creation time."""
        ...

    @abstractmethod
    def get_deferred_operations(self, vm_name: str) -> list[DeferredBlockcommit]:
        """Return all deferred blockcommit operations for *vm_name*."""
        ...

    @abstractmethod
    def add_deferred_blockcommit(
        self, vm_name: str, snapshots: list[str], reason: str
    ) -> None:
        """Queue a deferred blockcommit for *vm_name* with the given *reason*."""
        ...

    @abstractmethod
    def clear_deferred_operations(self, vm_name: str) -> None:
        """Clear all deferred blockcommit operations for *vm_name*."""
        ...

    @abstractmethod
    def update_deferred_warning(
        self, vm_name: str, index: int, timestamp: datetime
    ) -> None:
        """Update ``last_warned_at`` on the deferred operation at *index*."""
        ...

    @abstractmethod
    def get_last_full_backup(self, target_path: str) -> FullBackupInfo | None:
        """Return the last recorded full backup for *target_path*, or None."""
        ...

    @abstractmethod
    def set_last_full_backup(
        self, target_path: str, name: str, timestamp: datetime
    ) -> None:
        """Record a full backup for *target_path* with the given *name* and *timestamp*."""
        ...
