"""JsonStateManager — concrete IStateManager using JSON files."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import cast

from qsnap.interfaces.state import IStateManager
from qsnap.models.results import DeferredBlockcommit, FullBackupInfo, SnapshotInfo

logger = logging.getLogger(__name__)


class JsonStateManager(IStateManager):
    """Concrete state manager persisting per-VM state as JSON files.

    State files live at ``{state_dir}/{vm_name}.json``.  Writes are
    atomic: data is written to a ``.tmp`` file, then ``os.rename`` is
    used to replace the target file atomically.

    On ``_load`` corruption (``json.JSONDecodeError``), the corrupt file
    is renamed to ``{vm_name}.json.broken.{timestamp}`` and an empty
    state dict is returned (with a CRITICAL log).

    On ``_save``, previous state file versions are rotated up to
    ``state_backup_count`` backups: ``vm.json`` → ``vm.json.1`` →
    ``vm.json.2``.  When ``state_backup_count`` is 0, no rotation
    occurs.
    """

    def __init__(
        self,
        state_dir: str | Path = "/var/lib/qsnap/state",
        state_backup_count: int = 2,
    ) -> None:
        self._state_dir = Path(state_dir)
        self._state_backup_count = state_backup_count

    # ── internal helpers ───────────────────────────────────────────────

    def _state_path(self, vm_name: str) -> Path:
        return self._state_dir / f"{vm_name}.json"

    def _tmp_path(self, vm_name: str) -> Path:
        return self._state_dir / f"{vm_name}.json.tmp"

    def _backup_path(self, vm_name: str, n: int) -> Path:
        return self._state_dir / f"{vm_name}.json.{n}"

    def _load(self, vm_name: str) -> dict[str, object]:
        """Load the state dict for *vm_name*, or empty dict if no file.

        On ``json.JSONDecodeError`` (corrupt file), the file is renamed
        to ``{vm_name}.json.broken.{timestamp}``, a CRITICAL log is
        emitted, and an empty dict is returned.
        """
        path = self._state_path(vm_name)
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                data: dict[str, object] = json.load(fh)
            return data
        except json.JSONDecodeError:
            timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            broken_path = self._state_dir / f"{vm_name}.json.broken.{timestamp}"
            try:
                shutil.move(str(path), str(broken_path))
            except OSError as exc:
                logger.critical(
                    "State file for VM %s is corrupt and could not be renamed: %s",
                    vm_name,
                    exc,
                )
                return {}
            logger.critical(
                "State file for VM %s was corrupt — renamed to %s. Starting with empty state.",
                vm_name,
                broken_path,
            )
            return {}

    def _save(self, vm_name: str, data: dict[str, object]) -> None:
        """Atomically write *data* to the state file for *vm_name*.

        Before writing, previous state file versions are rotated up to
        ``state_backup_count`` backups.  When ``state_backup_count`` is
        0, no rotation occurs.
        """
        self._state_dir.mkdir(parents=True, exist_ok=True)
        state_path = self._state_path(vm_name)

        # Rotate previous versions (only if the main file exists).
        if self._state_backup_count > 0 and state_path.exists():
            self._rotate_backups(vm_name)

        tmp = self._tmp_path(vm_name)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp, state_path)

    def _rotate_backups(self, vm_name: str) -> None:
        """Rotate ``vm.json`` → ``vm.json.1`` → … up to backup count.

        Uses ``shutil.move`` for shifting ``.N`` → ``.N+1`` (atomic).
        The initial ``vm.json`` → ``vm.json.1`` uses ``shutil.copy2`` to
        preserve the original file (atomic write guarantee: if the
        subsequent ``os.replace`` fails, the original ``vm.json`` still
        exists).
        """
        n = self._state_backup_count
        state_path = self._state_path(vm_name)

        # Shift from highest to lowest to avoid overwriting.
        for i in range(n, 0, -1):
            dst = self._backup_path(vm_name, i)
            if i > n:
                # Discard oldest beyond limit.
                if dst.exists():
                    dst.unlink()
                continue

            if i == 1:
                # vm.json → vm.json.1: COPY (not move) to preserve original.
                if state_path.exists():
                    shutil.copy2(str(state_path), str(dst))
            else:
                # .N-1 → .N: MOVE (atomic shift).
                src = self._backup_path(vm_name, i - 1)
                if src.exists():
                    shutil.move(str(src), str(dst))

    @staticmethod
    def _snapshot_to_dict(info: SnapshotInfo) -> dict[str, str | int | None]:
        d: dict[str, str | int | None] = {
            "name": info.name,
            "path": str(info.path),
            "timestamp": info.timestamp.isoformat(),
            "allocation": info.allocation,
        }
        if info.content_hash is not None:
            d["content_hash"] = info.content_hash
        return d

    @staticmethod
    def _dict_to_snapshot(d: dict[str, object]) -> SnapshotInfo:
        return SnapshotInfo(
            name=str(d["name"]),
            path=Path(str(d["path"])),
            timestamp=datetime.fromisoformat(str(d["timestamp"])),
            allocation=int(str(d["allocation"])),
            content_hash=str(d["content_hash"]) if "content_hash" in d else None,
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
        snapshots.append(cast(dict[str, str | int], self._snapshot_to_dict(info)))
        data["snapshots"] = snapshots
        self._save(vm_name, data)

    def remove_snapshot(self, vm_name: str, snapshot_name: str) -> bool:
        data = self._load(vm_name)
        raw_list = data.get("snapshots", [])
        if not raw_list:
            return False
        snapshots: list[dict[str, str | int]] = list(raw_list)  # type: ignore[arg-type]
        original_len = len(snapshots)
        snapshots = [s for s in snapshots if s.get("name") != snapshot_name]
        if len(snapshots) == original_len:
            return False
        data["snapshots"] = snapshots
        self._save(vm_name, data)
        return True

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
        d: dict[str, object] = {
            "snapshots": list(item.snapshots),
            "reason": item.reason,
            "since": item.since.isoformat(),
        }
        if item.last_warned_at is not None:
            d["last_warned_at"] = item.last_warned_at.isoformat()
        return d

    @staticmethod
    def _dict_to_deferred(d: dict[str, object]) -> DeferredBlockcommit:
        last_warned_at: datetime | None = None
        raw_lwa = d.get("last_warned_at")
        if raw_lwa is not None:
            last_warned_at = datetime.fromisoformat(str(raw_lwa))
        return DeferredBlockcommit(
            snapshots=list(d.get("snapshots", [])),  # type: ignore[arg-type]
            reason=str(d["reason"]),
            since=datetime.fromisoformat(str(d["since"])),
            last_warned_at=last_warned_at,
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

    def add_deferred_blockcommit(self, vm_name: str, snapshots: list[str], reason: str) -> None:
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

    def update_deferred_warning(self, vm_name: str, index: int, timestamp: datetime) -> None:
        """Update ``last_warned_at`` on the deferred operation at *index*."""
        data = self._load(vm_name)
        raw_list: list[dict[str, object]] = list(data.get("deferred_operations", []))  # type: ignore[arg-type]
        if 0 <= index < len(raw_list):
            raw_list[index]["last_warned_at"] = timestamp.isoformat()
            data["deferred_operations"] = raw_list
            self._save(vm_name, data)

    # ── Full backup tracking ──────────────────────────────────────────

    def _full_backups_path(self) -> Path:
        return self._state_dir / "_full_backups.json"

    def _load_full_backups(self) -> dict[str, list[dict[str, str]]]:
        """Load full-backups data, auto-migrating old single-dict format.

        Old format: ``{target_path: {name, path, timestamp}}``
        New format: ``{target_path: [{name, path, timestamp, bucket_level}, ...]}``

        If a value is a dict (not a list), it is wrapped in a single-element
        list for backward compatibility.
        """
        path = self._full_backups_path()
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as fh:
            raw: dict[str, object] = json.load(fh)
        # Auto-migrate: wrap single dicts in lists.
        data: dict[str, list[dict[str, str]]] = {}
        for key, val in raw.items():
            if isinstance(val, list):
                data[key] = val  # type: ignore[assignment]
            elif isinstance(val, dict):
                data[key] = [val]  # type: ignore[list-item]
            else:
                logger.warning("Unexpected entry in _full_backups.json for %s", key)
        return data

    def _save_full_backups(self, data: dict[str, list[dict[str, str]]]) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._state_dir / "_full_backups.json.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp, self._full_backups_path())

    def get_last_full_backup(self, target_path: str) -> FullBackupInfo | None:
        """Return the most recent full backup for *target_path*.

        Uses the new multi-FULL format.  ``set_last_full_backup`` still
        works for backward compatibility but now appends to the list.
        """
        data = self._load_full_backups()
        entries = data.get(target_path)
        if not entries:
            return None
        entry = entries[-1]  # newest is last
        return FullBackupInfo(
            name=str(entry["name"]),
            path=Path(str(entry["path"])),
            timestamp=datetime.fromisoformat(str(entry["timestamp"])),
            bucket_level=str(entry.get("bucket_level", "monthly")),
        )

    def set_last_full_backup(self, target_path: str, name: str, timestamp: datetime) -> None:
        """Record a full backup (backward-compatible, appends to list)."""
        self.record_full_backup(target_path, name, timestamp, "monthly")

    def get_full_backups(self, target_path: str) -> list[FullBackupInfo]:
        """Return all recorded full backups for *target_path*, oldest first."""
        data = self._load_full_backups()
        entries = data.get(target_path, [])
        return [
            FullBackupInfo(
                name=str(e["name"]),
                path=Path(str(e["path"])),
                timestamp=datetime.fromisoformat(str(e["timestamp"])),
                bucket_level=str(e.get("bucket_level", "monthly")),
            )
            for e in entries
        ]

    def record_full_backup(
        self,
        target_path: str,
        name: str,
        timestamp: datetime,
        bucket_level: str,
    ) -> None:
        """Append a full backup record for *target_path*."""
        data = self._load_full_backups()
        entries = data.get(target_path, [])
        entries.append(
            {
                "name": name,
                "path": str(Path(target_path) / name),
                "timestamp": timestamp.isoformat(),
                "bucket_level": bucket_level,
            }
        )
        data[target_path] = entries
        self._save_full_backups(data)

    # ── Incremental → FULL dependency tracking ───────────────────────

    def _dependencies_path(self) -> Path:
        return self._state_dir / "_dependencies.json"

    def _load_dependencies(self) -> dict[str, dict[str, list[str]]]:
        """Load dependency data.

        Format: ``{target_path: {full_name: [incremental_name, ...]}}``
        """
        path = self._dependencies_path()
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as fh:
            data: dict[str, dict[str, list[str]]] = json.load(fh)
        return data

    def _save_dependencies(self, data: dict[str, dict[str, list[str]]]) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._state_dir / "_dependencies.json.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp, self._dependencies_path())

    def record_incremental_dependency(
        self, target_path: str, incremental_name: str, full_name: str
    ) -> None:
        """Record that *incremental_name* depends on *full_name*."""
        data = self._load_dependencies()
        target_deps = data.get(target_path, {})
        deps = target_deps.get(full_name, [])
        if incremental_name not in deps:
            deps.append(incremental_name)
        target_deps[full_name] = deps
        data[target_path] = target_deps
        self._save_dependencies(data)

    def get_incremental_dependencies(self, target_path: str, full_name: str) -> list[str]:
        """Return the incremental backup names that depend on *full_name*."""
        data = self._load_dependencies()
        target_deps = data.get(target_path, {})
        return list(target_deps.get(full_name, []))

    def remove_full_backup(self, target_path: str, name: str) -> bool:
        """Remove a full backup record from persistent state."""
        data = self._load_full_backups()
        entries = data.get(target_path, [])
        original_len = len(entries)
        data[target_path] = [e for e in entries if e.get("name") != name]
        if len(data[target_path]) == original_len:
            return False
        self._save_full_backups(data)
        return True

    def remove_incremental_dependency(
        self, target_path: str, incremental_name: str, full_name: str
    ) -> bool:
        """Remove an incremental dependency from persistent state."""
        data = self._load_dependencies()
        target_deps = data.get(target_path, {})
        deps = target_deps.get(full_name, [])
        if incremental_name not in deps:
            return False
        deps.remove(incremental_name)
        target_deps[full_name] = deps
        data[target_path] = target_deps
        self._save_dependencies(data)
        return True
