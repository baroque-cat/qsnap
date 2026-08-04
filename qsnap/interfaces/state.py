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
    def get_last_allocation(self, vm_name: str, disk: str) -> int | None:
        """Return the last recorded allocation size for *vm_name*/*disk*, or None."""
        ...

    @abstractmethod
    def set_last_allocation(self, vm_name: str, disk: str, alloc: int) -> None:
        """Record the current allocation size for *vm_name*/*disk*."""
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
    def add_deferred_blockcommit(
        self, vm_name: str, disk: str, snapshots: list[str], reason: str
    ) -> None:
        """Queue a deferred blockcommit for *vm_name*/*disk* with the given *reason*."""
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
    def set_last_full_backup(
        self, target_path: str, name: str, timestamp: datetime, disk: str
    ) -> None:
        """Record a full backup for *target_path*/*disk* with *name* and *timestamp*."""
        ...

    @abstractmethod
    def get_full_backups(self, target_path: str) -> list[FullBackupInfo]:
        """Return all recorded full backups for *target_path*, newest last."""
        ...

    @abstractmethod
    def record_full_backup(
        self, target_path: str, name: str, timestamp: datetime, disk: str
    ) -> None:
        """Append a full backup record for *target_path*/*disk*."""
        ...

    @abstractmethod
    def record_incremental_dependency(
        self, target_path: str, incremental_name: str, full_name: str
    ) -> None:
        """Record that *incremental_name* depends on *full_name* on *target_path*.

        Deliberately has NO ``disk`` parameter (see spec
        ``state-management``): the disk target is already encoded in both
        the FULL and incremental backup file names (``..._{disk}_{hex}``),
        and FULL records carry their own ``disk`` field.  Keying the
        dependency by ``(target_path, full_name)`` is therefore unambiguous
        without a separate disk dimension.
        """
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
    def get_last_backup_allocation(self, target_path: str, disk: str) -> int | None:
        """Return the last recorded backup allocation for *target_path*/*disk*.

        Used by the ``backup_create="onchange"`` gate in
        :meth:`Core._backup_target` to decide whether to skip the backup
        transfer for a target whose VM disk has not changed since the last
        successful backup of *disk* to that target.
        """
        ...

    @abstractmethod
    def set_last_backup_allocation(self, target_path: str, disk: str, alloc: int) -> None:
        """Record the backup allocation baseline for *target_path*/*disk*."""
        ...

    @abstractmethod
    def clear_last_backup_allocation(self, target_path: str, disk: str) -> bool:
        """Remove the ``last_backup_allocation`` baseline for *target_path*/*disk*.

        Returns ``True`` if an entry was found and removed, ``False``
        if no matching entry existed.
        """
        ...

    @abstractmethod
    def remove_all_incremental_dependencies(self, target_path: str, full_name: str) -> int:
        """Remove ALL incremental dependency records linked to *full_name*.

        Returns the count of removed dependency entries.
        """
        ...

    @abstractmethod
    def reset_vm_state(self, vm_name: str) -> None:
        """Atomically clear all per-VM state.

        Clears the ``snapshots`` list, ``last_allocation`` baseline, and
        ``deferred_operations`` queue for *vm_name*.  Used by
        :meth:`Core.restore` to reset VM state after disk replacement.
        """
        ...

    @abstractmethod
    def reset_target_state(self, target_path: str) -> None:
        """Atomically clear all per-target state.

        Clears full backup records, incremental dependencies, and the
        ``last_backup_allocation`` baseline for *target_path*.  Used by
        :meth:`Core.restore` to reset target state after VM disk
        replacement.
        """
        ...

    @abstractmethod
    def reset_vm_disk_state(self, vm_name: str, disk: str) -> None:
        """Atomically clear the per-VM state that belongs to *disk* only.

        Per-disk counterpart of :meth:`reset_vm_state`, used by
        :meth:`Core.restore` after replacing a single disk so that the
        state of the VM's *other* disks is preserved (design D4).

        Clears, for *vm_name*:

        - every recorded snapshot whose ``disk`` equals *disk* (snapshots
          of other disks are kept);
        - the ``last_allocation`` entry keyed by *disk* (a legacy bare-int
          ``last_allocation`` without per-disk keys is treated as absent
          and left untouched);
        - every deferred blockcommit operation whose ``disk`` equals
          *disk* (deferred operations for other disks are kept).
        """
        ...

    @abstractmethod
    def reset_target_disk_state(self, target_path: str, vm_name: str, disk: str) -> None:
        """Atomically clear the per-target state that belongs to one disk.

        Per-disk counterpart of :meth:`reset_target_state`, used by
        :meth:`Core.restore` after replacing a single disk so that backup
        state of *other* VMs and *other* disks on the same target is
        preserved (design D4).

        Clears, for *target_path*:

        - every full backup record whose name starts with
          ``"{vm_name}."`` **and** whose ``disk`` equals *disk*;
        - every incremental-dependency entry whose FULL anchor belongs to
          ``(vm_name, disk)`` — the disk is derived from the FULL backup
          name via :func:`parse_disk_from_snapshot_name`;
        - the ``last_backup_allocation`` entry keyed by *disk*.
        """
        ...
