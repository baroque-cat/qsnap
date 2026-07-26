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
    def remove_snapshot(self, vm_name: str, snapshot_name: str) -> bool:
        """Remove the named snapshot for *vm_name*.

        Returns ``True`` if the snapshot was found and removed, ``False``
        if no matching snapshot existed.
        """
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
    def add_deferred_blockcommit(self, vm_name: str, snapshots: list[str], reason: str) -> None:
        """Queue a deferred blockcommit for *vm_name* with the given *reason*."""
        ...

    @abstractmethod
    def clear_deferred_operations(self, vm_name: str) -> None:
        """Clear all deferred blockcommit operations for *vm_name*."""
        ...

    @abstractmethod
    def update_deferred_warning(self, vm_name: str, index: int, timestamp: datetime) -> None:
        """Update ``last_warned_at`` on the deferred operation at *index*."""
        ...

    @abstractmethod
    def get_last_full_backup(self, target_path: str) -> FullBackupInfo | None:
        """Return the last recorded full backup for *target_path*, or None."""
        ...

    @abstractmethod
    def set_last_full_backup(self, target_path: str, name: str, timestamp: datetime) -> None:
        """Record a full backup for *target_path* with the given *name* and *timestamp*."""
        ...

    @abstractmethod
    def get_full_backups(self, target_path: str) -> list[FullBackupInfo]:
        """Return all recorded full backups for *target_path*, newest last."""
        ...

    @abstractmethod
    def record_full_backup(
        self, target_path: str, name: str, timestamp: datetime, bucket_level: str
    ) -> None:
        """Append a full backup record for *target_path* with bucket level."""
        ...

    @abstractmethod
    def record_incremental_dependency(
        self, target_path: str, incremental_name: str, full_name: str
    ) -> None:
        """Record that *incremental_name* depends on *full_name* on *target_path*."""
        ...

    @abstractmethod
    def get_incremental_dependencies(self, target_path: str, full_name: str) -> list[str]:
        """Return the incremental backup names that depend on *full_name*."""
        ...

    @abstractmethod
    def remove_full_backup(self, target_path: str, name: str) -> bool:
        """Remove the full backup record for *name* on *target_path*.

        Returns ``True`` if the entry was found and removed, ``False``
        if no matching entry existed.
        """
        ...

    @abstractmethod
    def remove_incremental_dependency(
        self, target_path: str, incremental_name: str, full_name: str
    ) -> bool:
        """Remove a dependency record where *incremental_name* depends on *full_name*.

        Returns ``True`` if the dependency was found and removed,
        ``False`` if no matching dependency existed.
        """
        ...

    @abstractmethod
    def get_last_backup_allocation(self, target_path: str) -> int | None:
        """Return the last recorded backup allocation for *target_path*, or None.

        Used by the ``backup_create="onchange"`` gate in
        :meth:`Core._backup_target` to decide whether to skip the
        backup transfer for a target whose VM disk has not changed since
        the last successful backup to that target.
        """
        ...

    @abstractmethod
    def set_last_backup_allocation(self, target_path: str, alloc: int) -> None:
        """Record the backup allocation baseline for *target_path*."""
        ...

    @abstractmethod
    def clear_last_backup_allocation(self, target_path: str) -> bool:
        """Remove the ``last_backup_allocation`` baseline for *target_path*.

        Returns ``True`` if an entry was found and removed, ``False``
        if no matching entry existed.
        """
        ...

    @abstractmethod
    def remove_all_incremental_dependencies(
        self, target_path: str, full_name: str
    ) -> int:
        """Remove ALL incremental dependency records linked to *full_name*.

        Returns the count of removed dependency entries.
        """
        ...
