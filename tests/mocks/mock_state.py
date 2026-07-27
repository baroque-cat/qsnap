"""InMemoryStateManager — mock IStateManager for unit tests.

Stores state in a dict.  All methods fully functional.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.interfaces.state import IStateManager
from qsnap.models.results import DeferredBlockcommit, FullBackupInfo, SnapshotInfo


class InMemoryStateManager(IStateManager):
    """In-memory state manager for unit tests."""

    @staticmethod
    def _normalize_full_name(full_name: str) -> str:
        """Normalize a FULL backup name to stem form (without ``.qcow2``).

        Mirrors ``JsonStateManager._normalize_full_name`` for test parity
        (design D3).
        """
        if full_name.endswith(".qcow2"):
            return Path(full_name).stem
        return full_name

    def __init__(self) -> None:
        self._state: dict[str, dict[str, object]] = {}
        self._full_backups: dict[str, list[FullBackupInfo]] = {}
        self._dependencies: dict[str, dict[str, list[str]]] = {}
        self._target_state: dict[str, int] = {}

    def get_last_allocation(self, vm_name: str) -> int | None:
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return None
        value = vm_state.get("last_allocation")
        if value is None:
            return None
        return int(value)  # type: ignore[arg-type]

    def set_last_allocation(self, vm_name: str, alloc: int) -> None:
        if vm_name not in self._state:
            self._state[vm_name] = {}
        self._state[vm_name]["last_allocation"] = alloc

    def record_snapshot(self, vm_name: str, info: SnapshotInfo) -> None:
        if vm_name not in self._state:
            self._state[vm_name] = {}
        snapshots = self._state[vm_name].setdefault("snapshots", [])
        snapshots.append(info)

    def remove_snapshot(self, vm_name: str, snapshot_name: str) -> bool:
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return False
        snapshots = vm_state.get("snapshots", [])
        for s in snapshots:
            if s.name == snapshot_name:  # type: ignore[union-attr]
                snapshots.remove(s)
                return True
        return False

    def get_snapshots(self, vm_name: str) -> list[SnapshotInfo]:
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return []
        snapshots = vm_state.get("snapshots", [])
        if not snapshots:
            return []
        return sorted(snapshots, key=lambda s: s.timestamp)  # type: ignore[union-attr]

    def get_deferred_operations(self, vm_name: str) -> list[DeferredBlockcommit]:
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return []
        deferred = vm_state.get("deferred_operations", [])
        if not deferred:
            return []
        return list(deferred)  # type: ignore[return-value]

    def add_deferred_blockcommit(self, vm_name: str, snapshots: list[str], reason: str) -> None:
        if vm_name not in self._state:
            self._state[vm_name] = {}
        deferred = self._state[vm_name].setdefault("deferred_operations", [])
        deferred.append(
            DeferredBlockcommit(
                snapshots=list(snapshots),
                reason=reason,
                since=datetime.now(),
            )
        )

    def clear_deferred_operations(self, vm_name: str) -> None:
        if vm_name not in self._state:
            return
        self._state[vm_name]["deferred_operations"] = []

    def update_deferred_warning(self, vm_name: str, index: int, timestamp: datetime) -> None:
        """Update ``last_warned_at`` on the deferred operation at *index*."""
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return
        deferred = vm_state.get("deferred_operations", [])
        if isinstance(deferred, list) and 0 <= index < len(deferred):
            item = deferred[index]
            if isinstance(item, DeferredBlockcommit):
                deferred[index] = DeferredBlockcommit(
                    snapshots=item.snapshots,
                    reason=item.reason,
                    since=item.since,
                    last_warned_at=timestamp,
                )

    # ── Full backup tracking ──────────────────────────────────────────

    def get_last_full_backup(self, target_path: str) -> FullBackupInfo | None:
        entries = self._full_backups.get(target_path)
        if not entries:
            return None
        return entries[-1]

    def set_last_full_backup(self, target_path: str, name: str, timestamp: datetime) -> None:
        self.record_full_backup(target_path, name, timestamp, "monthly")

    def get_full_backups(self, target_path: str) -> list[FullBackupInfo]:
        return list(self._full_backups.get(target_path, []))

    def record_full_backup(
        self,
        target_path: str,
        name: str,
        timestamp: datetime,
        bucket_level: str,
    ) -> None:
        from pathlib import Path

        entries = self._full_backups.setdefault(target_path, [])
        entries.append(
            FullBackupInfo(
                name=name,
                path=Path(target_path) / name,
                timestamp=timestamp,
                bucket_level=bucket_level,
            )
        )

    def record_incremental_dependency(
        self, target_path: str, incremental_name: str, full_name: str
    ) -> None:
        target_deps = self._dependencies.setdefault(target_path, {})
        normalized = self._normalize_full_name(full_name)
        deps = target_deps.setdefault(normalized, [])
        if incremental_name not in deps:
            deps.append(incremental_name)

    def get_incremental_dependencies(self, target_path: str, full_name: str) -> list[str]:
        target_deps = self._dependencies.get(target_path, {})
        normalized = self._normalize_full_name(full_name)
        return list(target_deps.get(normalized, []))

    def remove_full_backup(self, target_path: str, name: str) -> bool:
        """Remove a full backup record from in-memory state."""
        entries = self._full_backups.get(target_path, [])
        for entry in entries:
            if entry.name == name:
                entries.remove(entry)
                return True
        return False

    def remove_incremental_dependency(
        self, target_path: str, incremental_name: str, full_name: str
    ) -> bool:
        """Remove an incremental dependency from in-memory state.

        Accepts both stem and extended forms of *full_name* — normalizes
        to stem before lookup (design D3).
        """
        target_deps = self._dependencies.get(target_path, {})
        normalized = self._normalize_full_name(full_name)
        deps = target_deps.get(normalized, [])
        if incremental_name in deps:
            deps.remove(incremental_name)
            return True
        return False

    # ── Per-target backup allocation tracking ─────────────────────────

    def get_last_backup_allocation(self, target_path: str) -> int | None:
        """Return the last backup allocation for *target_path*, or None."""
        return self._target_state.get(target_path)

    def set_last_backup_allocation(self, target_path: str, alloc: int) -> None:
        """Record the last backup allocation for *target_path*."""
        self._target_state[target_path] = alloc

    def clear_last_backup_allocation(self, target_path: str) -> bool:
        """Remove the ``last_backup_allocation`` baseline for *target_path*.

        Returns ``True`` if an entry was found and removed, ``False``
        if no matching entry existed.
        """
        if target_path not in self._target_state:
            return False
        del self._target_state[target_path]
        return True

    def remove_all_incremental_dependencies(
        self, target_path: str, full_name: str
    ) -> int:
        """Remove ALL incremental dependencies linked to *full_name*.

        Accepts both stem and extended forms of *full_name* — normalizes
        to stem before lookup (design D3).

        Returns the count of removed dependency entries.
        """
        deps = self._dependencies.get(target_path, {})
        normalized = self._normalize_full_name(full_name)
        if normalized not in deps:
            return 0
        count = len(deps[normalized])
        del deps[normalized]
        return count
