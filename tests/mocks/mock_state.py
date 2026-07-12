"""InMemoryStateManager — mock IStateManager for unit tests.

Stores state in a dict.  All methods fully functional.
"""

from __future__ import annotations

from qsnap.interfaces.state import IStateManager
from qsnap.models.results import SnapshotInfo


class InMemoryStateManager(IStateManager):
    """In-memory state manager for unit tests."""

    def __init__(self) -> None:
        self._state: dict[str, dict[str, object]] = {}

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

    def get_snapshots(self, vm_name: str) -> list[SnapshotInfo]:
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return []
        snapshots = vm_state.get("snapshots", [])
        if not snapshots:
            return []
        return sorted(snapshots, key=lambda s: s.timestamp)  # type: ignore[union-attr]
