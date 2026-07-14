"""JsonStateManager — concrete IStateManager using JSON files."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from qsnap.interfaces.state import IStateManager
from qsnap.models.results import DeferredBlockcommit, SnapshotInfo


class JsonStateManager(IStateManager):
    """Concrete state manager persisting per-VM state as JSON files.

    State files live at ``{state_dir}/{vm_name}.json``.  Writes are
    atomic: data is written to a ``.tmp`` file, then ``os.rename`` is
    used to replace the target file atomically.
    """

    def __init__(self, state_dir: str | Path = "/var/lib/qsnap/state") -> None:
        self._state_dir = Path(state_dir)

    # ── internal helpers ───────────────────────────────────────────────

    def _state_path(self, vm_name: str) -> Path:
        return self._state_dir / f"{vm_name}.json"

    def _tmp_path(self, vm_name: str) -> Path:
        return self._state_dir / f"{vm_name}.json.tmp"

    def _load(self, vm_name: str) -> dict[str, object]:
        """Load the state dict for *vm_name*, or empty dict if no file."""
        path = self._state_path(vm_name)
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as fh:
            data: dict[str, object] = json.load(fh)
        return data

    def _save(self, vm_name: str, data: dict[str, object]) -> None:
        """Atomically write *data* to the state file for *vm_name*."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._tmp_path(vm_name)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp, self._state_path(vm_name))

    @staticmethod
    def _snapshot_to_dict(info: SnapshotInfo) -> dict[str, str | int]:
        return {
            "name": info.name,
            "path": str(info.path),
            "timestamp": info.timestamp.isoformat(),
            "allocation": info.allocation,
        }

    @staticmethod
    def _dict_to_snapshot(d: dict[str, object]) -> SnapshotInfo:
        return SnapshotInfo(
            name=str(d["name"]),
            path=Path(str(d["path"])),
            timestamp=datetime.fromisoformat(str(d["timestamp"])),
            allocation=int(d["allocation"]),
        )

    # ── IStateManager implementation ──────────────────────────────────

    def get_last_allocation(self, vm_name: str) -> int | None:
        data = self._load(vm_name)
        value = data.get("last_allocation")
        if value is None:
            return None
        return int(value)  # type: ignore[arg-type]

    def set_last_allocation(self, vm_name: str, alloc: int) -> None:
        data = self._load(vm_name)
        data["last_allocation"] = alloc
        self._save(vm_name, data)

    def record_snapshot(self, vm_name: str, info: SnapshotInfo) -> None:
        data = self._load(vm_name)
        raw_list = data.get("snapshots", [])
        snapshots: list[dict[str, str | int]] = list(raw_list)  # type: ignore[arg-type]
        snapshots.append(self._snapshot_to_dict(info))
        data["snapshots"] = snapshots
        self._save(vm_name, data)

    def get_snapshots(self, vm_name: str) -> list[SnapshotInfo]:
        data = self._load(vm_name)
        raw_list = data.get("snapshots", [])
        if not raw_list:
            return []
        snapshots: list[SnapshotInfo] = [
            self._dict_to_snapshot(d)  # type: ignore[arg-type]
            for d in raw_list  # type: ignore[union-attr]
        ]
        snapshots.sort(key=lambda s: s.timestamp)
        return snapshots

    # ── Deferred operations ───────────────────────────────────────────

    @staticmethod
    def _deferred_to_dict(item: DeferredBlockcommit) -> dict[str, object]:
        return {
            "snapshots": list(item.snapshots),
            "reason": item.reason,
            "since": item.since.isoformat(),
        }

    @staticmethod
    def _dict_to_deferred(d: dict[str, object]) -> DeferredBlockcommit:
        return DeferredBlockcommit(
            snapshots=list(d.get("snapshots", [])),  # type: ignore[arg-type]
            reason=str(d["reason"]),
            since=datetime.fromisoformat(str(d["since"])),
        )

    def get_deferred_operations(self, vm_name: str) -> list[DeferredBlockcommit]:
        data = self._load(vm_name)
        raw_list = data.get("deferred_operations", [])
        if not raw_list:
            return []
        return [
            self._dict_to_deferred(d)  # type: ignore[arg-type]
            for d in raw_list  # type: ignore[union-attr]
        ]

    def add_deferred_blockcommit(
        self, vm_name: str, snapshots: list[str], reason: str
    ) -> None:
        data = self._load(vm_name)
        raw_list: list[dict[str, object]] = list(data.get("deferred_operations", []))  # type: ignore[arg-type]
        raw_list.append(
            self._deferred_to_dict(
                DeferredBlockcommit(
                    snapshots=list(snapshots),
                    reason=reason,
                    since=datetime.now(),
                )
            )
        )
        data["deferred_operations"] = raw_list
        self._save(vm_name, data)

    def clear_deferred_operations(self, vm_name: str) -> None:
        data = self._load(vm_name)
        data["deferred_operations"] = []
        self._save(vm_name, data)
