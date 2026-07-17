"""InMemoryStateManager — mock IStateManager for unit tests.

Stores state in a dict.  All methods fully functional.
"""

from __future__ import annotations

from datetime import datetime

from qsnap.interfaces.state import IStateManager
from qsnap.models.results import DeferredBlockcommit, FullBackupInfo, SnapshotInfo


class InMemoryStateManager(IStateManager):
    """In-memory state manager for unit tests."""

    def __init__(self) -> None:
        self._state: dict[str, dict[str, object]] = {}
        self._full_backups: dict[str, list[FullBackupInfo]] = {}
        self._dependencies: dict[str, dict[str, list[str]]] = {}

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
        deps = target_deps.setdefault(full_name, [])
        if incremental_name not in deps:
            deps.append(incremental_name)

    def get_incremental_dependencies(self, target_path: str, full_name: str) -> list[str]:
        target_deps = self._dependencies.get(target_path, {})
        return list(target_deps.get(full_name, []))
