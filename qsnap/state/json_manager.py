"""JsonStateManager — concrete IStateManager using JSON files."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from qsnap.interfaces.state import IStateManager
from qsnap.models.results import CommitIntent, DeferredBlockcommit, FullBackupInfo, SnapshotInfo
from qsnap.utils.parsing import parse_disk_from_snapshot_name

logger = logging.getLogger(__name__)

# Fallback disk target used when migrating legacy state records that lack
# a disk field and whose name does not contain a parseable disk segment.
_LEGACY_FALLBACK_DISK = "vda"


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

        ENOSPC and other OS-level write failures are caught, logged at
        CRITICAL, and re-raised as ``RuntimeError`` so the per-VM
        exception handler in ``Core._run_pipeline`` can contain the
        failure to one VM (design D3).  State files are never deleted
        or renamed in the handler — losing the in-flight record is safe
        because state only advances after successful operations.
        """
        state_path = self._state_path(vm_name)
        tmp = self._tmp_path(vm_name)
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)

            # Write the new state to a temp file FIRST — if the write
            # fails (e.g., ENOSPC), no rotation occurs and the existing
            # state file is untouched (design D3 / state-recovery spec).
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, default=str)

            # Rotate previous versions (only if the main file exists).
            if self._state_backup_count > 0 and state_path.exists():
                self._rotate_backups(vm_name)

            os.replace(tmp, state_path)
        except OSError as exc:
            logger.critical(
                "Failed to save state for VM %s: %s (path: %s)",
                vm_name,
                exc,
                state_path,
            )
            # Best-effort remove the partial temp file so no stale
            # .tmp is left behind (pre-flight cleanup handles the
            # rest, but the state-recovery spec requires no rotation
            # and no stale tmp).
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise RuntimeError(f"State write failed for VM {vm_name}: {exc}") from exc

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
            "disk": info.disk,
        }
        return d

    @staticmethod
    def _dict_to_snapshot(d: dict[str, object]) -> SnapshotInfo:
        # Read-tolerance: old state files may contain a legacy hash
        # key — it is silently ignored (the field was removed in the
        # unify-nbd-transfer change).
        #
        # Multi-disk migration: legacy records lack a ``disk`` field.  The
        # disk target is recovered from the snapshot name (which embeds it
        # as ``_{disk}_``); when that fails, a fallback disk is used so the
        # record is not lost.
        name = str(d["name"])
        raw_disk = d.get("disk")
        if raw_disk is not None:
            disk = str(raw_disk)
        else:
            disk = parse_disk_from_snapshot_name(name) or _LEGACY_FALLBACK_DISK
            logger.info(
                "Migrated legacy snapshot record without disk field: %s -> disk=%s",
                name,
                disk,
            )
        return SnapshotInfo(
            name=name,
            path=Path(str(d["path"])),
            timestamp=datetime.fromisoformat(str(d["timestamp"])),
            allocation=int(str(d["allocation"])),
            disk=disk,
        )

    # ── IStateManager implementation ──────────────────────────────────

    def get_last_allocation(self, vm_name: str, disk: str) -> int | None:
        """Return the per-disk allocation baseline for *vm_name*/*disk*.

        Storage format is ``last_allocation: {disk: int}``.  Legacy state
        stored a bare integer (single-disk); such a value cannot be
        attributed to a specific disk, so it is treated as absent (returns
        None and a fresh per-disk baseline is established on next run).
        """
        data = self._load(vm_name)
        value = data.get("last_allocation")
        if not isinstance(value, dict):
            return None
        disk_value = value.get(disk)
        if disk_value is None:
            return None
        return int(disk_value)  # type: ignore[arg-type]

    def set_last_allocation(self, vm_name: str, disk: str, alloc: int) -> None:
        data = self._load(vm_name)
        existing = data.get("last_allocation")
        # Replace legacy bare-integer format with the per-disk dict.
        per_disk: dict[str, int] = existing if isinstance(existing, dict) else {}
        per_disk[disk] = alloc
        data["last_allocation"] = per_disk
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
            "disk": item.disk,
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
        # Multi-disk migration: legacy records lack a ``disk`` field.  The
        # disk is recovered from the first snapshot name when possible.
        raw_disk = d.get("disk")
        if raw_disk is not None:
            disk = str(raw_disk)
        else:
            snapshots = list(d.get("snapshots", []))  # type: ignore[arg-type]
            disk = _LEGACY_FALLBACK_DISK
            if snapshots:
                disk = parse_disk_from_snapshot_name(str(snapshots[0])) or disk
        return DeferredBlockcommit(
            snapshots=list(d.get("snapshots", [])),  # type: ignore[arg-type]
            reason=str(d["reason"]),
            since=datetime.fromisoformat(str(d["since"])),
            disk=disk,
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

    def add_deferred_blockcommit(
        self, vm_name: str, disk: str, snapshots: list[str], reason: str
    ) -> None:
        data = self._load(vm_name)
        raw_list: list[dict[str, object]] = list(data.get("deferred_operations", []))  # type: ignore[arg-type]
        raw_list.append(
            self._deferred_to_dict(
                DeferredBlockcommit(
                    snapshots=list(snapshots),
                    reason=reason,
                    since=datetime.now(),
                    disk=disk,
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
        New format: ``{target_path: [{name, path, timestamp}, ...]}``

        If a value is a dict (not a list), it is wrapped in a single-element
        list for backward compatibility.  Old entries containing extra
        deprecated keys are read-tolerantly — unknown fields are silently
        ignored.

        Name-extension migration (design D2): entries whose ``name`` or
        ``path`` lack the ``.qcow2`` extension (stem format, written by
        the buggy version after commit ``0811599``) are normalized to
        extended form on load.  ``name`` and ``path`` are checked
        **independently** — a per-field guard prevents double-append.
        When the stored ``path`` is stem-based, it is rebuilt as
        ``str(Path(target_path) / normalized_name)``.  This migration
        runs BEFORE deduplication so that a stem entry and its extended
        twin collapse into a single record.  Pre-regression production
        state (already extended) passes through unchanged.

        Deduplication migration (design D4): entries with duplicate
        ``(name, target_path)`` tuples are removed on load, keeping the
        first occurrence.  This fixes the double-recording bug where
        ``BitmapBackupProvider.create_full_backup()`` and
        ``Core._backup_target()`` both called ``record_full_backup()``.
        When duplicates are removed, the deduplicated state is persisted
        back to disk so the migration is one-time and idempotent.  An
        INFO log is emitted for each removed duplicate.
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

        # --- Name-extension migration (runs BEFORE dedup, design D2) ---
        had_extension_fix = False
        for target_path, entries in data.items():
            for entry in entries:
                name = str(entry.get("name", ""))
                path_val = str(entry.get("path", ""))

                name_fixed = not name.endswith(".qcow2")
                path_fixed = not path_val.endswith(".qcow2")

                if name_fixed:
                    entry["name"] = name + ".qcow2"
                    had_extension_fix = True
                if path_fixed:
                    # Rebuild path from the (possibly just-fixed) name.
                    entry["path"] = str(Path(target_path) / str(entry["name"]))
                    had_extension_fix = True

        # Deduplication migration: remove entries with duplicate
        # (name, target_path) tuples, keeping the first.  The target_path
        # is the dict key; the name is the "name" field in each entry.
        deduplicated_data: dict[str, list[dict[str, str]]] = {}
        had_duplicates = False
        for target_path, entries in data.items():
            seen_names: set[str] = set()
            unique_entries: list[dict[str, str]] = []
            for entry in entries:
                name = str(entry.get("name", ""))
                if name in seen_names:
                    had_duplicates = True
                    logger.info(
                        "Deduplicated FULL backup entry: %s for target %s",
                        name,
                        target_path,
                    )
                    continue
                seen_names.add(name)
                unique_entries.append(entry)
            deduplicated_data[target_path] = unique_entries

        had_changes = had_extension_fix or had_duplicates

        # Persist repaired state so the migration is one-time and
        # idempotent (subsequent loads find no changes, no logging).
        if had_changes:
            self._save_full_backups(deduplicated_data)

        return deduplicated_data

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
        Old entries with extra deprecated fields are read-tolerantly —
        unknown fields are silently ignored.
        """
        data = self._load_full_backups()
        entries = data.get(target_path)
        if not entries:
            return None
        entry = entries[-1]  # newest is last
        return self._entry_to_full_backup(entry)

    @staticmethod
    def _entry_to_full_backup(entry: dict[str, str]) -> FullBackupInfo:
        """Build a :class:`FullBackupInfo` from a stored entry.

        Multi-disk migration: legacy entries lack a ``disk`` field.  The
        disk is recovered from the backup name when possible, otherwise a
        fallback disk is used so the record is not lost.
        """
        name = str(entry["name"])
        raw_disk = entry.get("disk")
        if raw_disk is not None:
            disk = str(raw_disk)
        else:
            disk = parse_disk_from_snapshot_name(name) or _LEGACY_FALLBACK_DISK
        return FullBackupInfo(
            name=name,
            path=Path(str(entry["path"])),
            timestamp=datetime.fromisoformat(str(entry["timestamp"])),
            disk=disk,
        )

    def set_last_full_backup(
        self, target_path: str, name: str, timestamp: datetime, disk: str
    ) -> None:
        """Record a full backup (backward-compatible, appends to list)."""
        self.record_full_backup(target_path, name, timestamp, disk)

    def get_full_backups(self, target_path: str) -> list[FullBackupInfo]:
        """Return all recorded full backups for *target_path*, oldest first."""
        data = self._load_full_backups()
        entries = data.get(target_path, [])
        return [self._entry_to_full_backup(e) for e in entries]

    def record_full_backup(
        self,
        target_path: str,
        name: str,
        timestamp: datetime,
        disk: str,
    ) -> None:
        """Append a full backup record for *target_path*/*disk*.

        Normalizes *name* to extended form (appending ``.qcow2`` when
        missing) before persisting, and derives ``path`` from the
        normalized name.  This enforces the ``.qcow2`` name invariant
        defensively so no future caller can regress the format (design D1).
        """
        normalized_name = self._to_extended_name(name)
        data = self._load_full_backups()
        entries = data.get(target_path, [])
        entries.append(
            {
                "name": normalized_name,
                "path": str(Path(target_path) / normalized_name),
                "timestamp": timestamp.isoformat(),
                "disk": disk,
            }
        )
        data[target_path] = entries
        self._save_full_backups(data)

    # ── Incremental → FULL dependency tracking ───────────────────────

    def _dependencies_path(self) -> Path:
        return self._state_dir / "_dependencies.json"

    @staticmethod
    def _to_extended_name(name: str) -> str:
        """Normalize a FULL backup name to extended form (with ``.qcow2``).

        Inverse counterpart of :meth:`_normalize_full_name`.  Returns
        *name* unchanged if it already ends with ``.qcow2``, otherwise
        appends ``.qcow2``.  Used by :meth:`record_full_backup` and
        :meth:`remove_full_backup` to enforce the ``.qcow2`` name
        invariant for ``_full_backups.json`` entries.
        """
        if name.endswith(".qcow2"):
            return name
        return name + ".qcow2"

    @staticmethod
    def _normalize_full_name(full_name: str) -> str:
        """Normalize a FULL backup name to stem form (without ``.qcow2``).

        Storage uses stem keys (as produced by
        ``Core._resolve_chain_full_anchor``).  Callers may pass either
        stem (``vm.FULL.20260727``) or extended (``vm.FULL.20260727.qcow2``)
        forms — this normalizes both to stem for lookup (design D3).
        """
        if full_name.endswith(".qcow2"):
            return Path(full_name).stem
        return full_name

    def _load_dependencies(self) -> dict[str, dict[str, list[str]]]:
        """Load dependency data.

        Format: ``{target_path: {full_name: [incremental_name, ...]}}``

        Legacy migration (design D3): keys ending in ``.qcow2`` are
        renamed to their stem form on load.  Migration is idempotent —
        loading an already-migrated file produces no changes.
        """
        path = self._dependencies_path()
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as fh:
            data: dict[str, dict[str, list[str]]] = json.load(fh)
        # Migrate legacy .qcow2 keys to stem form (idempotent).
        migrated = False
        for _target_path, deps in data.items():
            keys_to_migrate = [k for k in deps if k.endswith(".qcow2")]
            for old_key in keys_to_migrate:
                new_key = Path(old_key).stem
                deps[new_key] = deps.pop(old_key)
                migrated = True
        if migrated:
            self._save_dependencies(data)
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
        """Record that *incremental_name* depends on *full_name*.

        Normalizes *full_name* to stem form before storage (design D3).
        """
        data = self._load_dependencies()
        target_deps = data.get(target_path, {})
        normalized = self._normalize_full_name(full_name)
        deps = target_deps.get(normalized, [])
        if incremental_name not in deps:
            deps.append(incremental_name)
        target_deps[normalized] = deps
        data[target_path] = target_deps
        self._save_dependencies(data)

    def get_incremental_dependencies(self, target_path: str, full_name: str) -> list[str]:
        """Return the incremental backup names that depend on *full_name*.

        Accepts both stem (``vm.FULL.20260727``) and extended
        (``vm.FULL.20260727.qcow2``) forms — normalizes to stem before
        lookup (design D3).
        """
        data = self._load_dependencies()
        target_deps = data.get(target_path, {})
        normalized = self._normalize_full_name(full_name)
        return list(target_deps.get(normalized, []))

    def remove_full_backup(self, target_path: str, name: str) -> bool:
        """Remove a full backup record from persistent state.

        Name-format tolerant (design D3): normalizes the lookup *name* to
        the extended form (appending ``.qcow2`` when missing) before
        matching against stored entries.  Both stem callers
        (e.g. ``Core._cleanup_backups``, which passes ``BackupInfo.name``
        from ``provider.list()`` — always a stem) and extended callers
        (which pass state-derived ``full.name``) remove the same record.
        """
        lookup_name = self._to_extended_name(name)
        data = self._load_full_backups()
        entries = data.get(target_path, [])
        original_len = len(entries)
        data[target_path] = [e for e in entries if e.get("name") != lookup_name]
        if len(data[target_path]) == original_len:
            return False
        self._save_full_backups(data)
        return True

    def remove_incremental_dependency(
        self, target_path: str, incremental_name: str, full_name: str
    ) -> bool:
        """Remove an incremental dependency from persistent state.

        Accepts both stem and extended forms of *full_name* — normalizes
        to stem before lookup (design D3).
        """
        data = self._load_dependencies()
        target_deps = data.get(target_path, {})
        normalized = self._normalize_full_name(full_name)
        deps = target_deps.get(normalized, [])
        if incremental_name not in deps:
            return False
        deps.remove(incremental_name)
        target_deps[normalized] = deps
        data[target_path] = target_deps
        self._save_dependencies(data)
        return True

    # ── Per-target backup allocation tracking ─────────────────────────

    def _target_state_path(self) -> Path:
        return self._state_dir / "_target_state.json"

    def _load_target_state(self) -> dict[str, dict[str, object]]:
        """Load per-target state data.

        Format: ``{target_path: {"last_backup_allocation": {disk: int}}}``

        On ``json.JSONDecodeError`` (corrupt file), the file is renamed
        to ``_target_state.json.broken.{timestamp}`` and an empty dict
        is returned (same recovery pattern as ``_load``).
        """
        path = self._target_state_path()
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                data: dict[str, dict[str, object]] = json.load(fh)
            return data
        except json.JSONDecodeError:
            timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            broken_path = self._state_dir / f"_target_state.json.broken.{timestamp}"
            try:
                shutil.move(str(path), str(broken_path))
            except OSError as exc:
                logger.critical(
                    "_target_state.json is corrupt and could not be renamed: %s",
                    exc,
                )
                return {}
            logger.critical(
                "_target_state.json was corrupt — renamed to %s. Starting with empty state.",
                broken_path,
            )
            return {}

    def _save_target_state(self, data: dict[str, dict[str, object]]) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._state_dir / "_target_state.json.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp, self._target_state_path())

    def get_last_backup_allocation(self, target_path: str, disk: str) -> int | None:
        """Return the last backup allocation for *target_path*/*disk*.

        Returns ``None`` when no baseline exists (first-run behaviour —
        backup always proceeds).  Legacy state stored a bare integer under
        ``last_backup_allocation`` (single-disk); such a value cannot be
        attributed to a specific disk and is treated as absent.
        """
        data = self._load_target_state()
        entry = data.get(target_path)
        if entry is None:
            return None
        value = entry.get("last_backup_allocation")
        if not isinstance(value, dict):
            return None
        disk_value = value.get(disk)
        if disk_value is None:
            return None
        return int(disk_value)  # type: ignore[arg-type]

    def set_last_backup_allocation(self, target_path: str, disk: str, alloc: int) -> None:
        """Record the last backup allocation for *target_path*/*disk*."""
        data = self._load_target_state()
        entry = data.get(target_path, {})
        existing = entry.get("last_backup_allocation")
        # Replace legacy bare-integer format with the per-disk dict.
        per_disk: dict[str, int] = existing if isinstance(existing, dict) else {}
        per_disk[disk] = alloc
        entry["last_backup_allocation"] = per_disk
        data[target_path] = entry
        self._save_target_state(data)

    def clear_last_backup_allocation(self, target_path: str, disk: str) -> bool:
        """Remove the ``last_backup_allocation`` baseline for *target_path*/*disk*.

        Returns ``True`` if an entry was found and removed, ``False``
        if no matching entry existed.
        """
        data = self._load_target_state()
        entry = data.get(target_path)
        if entry is None:
            return False
        value = entry.get("last_backup_allocation")
        if not isinstance(value, dict) or disk not in value:
            return False
        del value[disk]
        entry["last_backup_allocation"] = value
        data[target_path] = entry
        self._save_target_state(data)
        return True

    def remove_all_incremental_dependencies(self, target_path: str, full_name: str) -> int:
        """Remove ALL incremental dependencies linked to *full_name*.

        Accepts both stem and extended forms of *full_name* — normalizes
        to stem before lookup (design D3).

        Returns the count of removed dependency entries.
        """
        data = self._load_dependencies()
        target_deps = data.get(target_path, {})
        normalized = self._normalize_full_name(full_name)
        if normalized not in target_deps:
            return 0
        count = len(target_deps[normalized])
        del target_deps[normalized]
        data[target_path] = target_deps
        self._save_dependencies(data)
        return count

    # ── Bulk state reset (restore support) ───────────────────────────

    def reset_vm_state(self, vm_name: str) -> None:
        """Atomically clear all per-VM state.

        Clears the ``snapshots`` list, ``last_allocation`` baseline,
        ``deferred_operations`` queue, and ``commit_in_progress`` intent
        journal for *vm_name*.  If the VM has no state file, no file is
        created (no-op).
        """
        path = self._state_path(vm_name)
        if not path.exists():
            return
        data = self._load(vm_name)
        data["snapshots"] = []
        # Clear all per-disk allocation baselines (multi-disk format is a
        # dict keyed by disk; an empty dict clears every disk).
        data["last_allocation"] = {}
        data["deferred_operations"] = []
        data["commit_in_progress"] = []
        self._save(vm_name, data)

    def reset_target_state(self, target_path: str) -> None:
        """Atomically clear all per-target state.

        Removes the *target_path* entry from ``_full_backups.json``,
        ``_dependencies.json``, and ``_target_state.json``.  All three
        files are saved atomically.  If a file does not exist or the
        target is not present, that file is a no-op.
        """
        # _full_backups.json
        fb_path = self._full_backups_path()
        if fb_path.exists():
            data = self._load_full_backups()
            if target_path in data:
                del data[target_path]
                self._save_full_backups(data)

        # _dependencies.json
        dep_path = self._dependencies_path()
        if dep_path.exists():
            data = self._load_dependencies()
            if target_path in data:
                del data[target_path]
                self._save_dependencies(data)

        # _target_state.json
        ts_path = self._target_state_path()
        if ts_path.exists():
            data = self._load_target_state()
            if target_path in data:
                del data[target_path]
                self._save_target_state(data)

    # ── Per-disk state reset (restore support, design D4) ─────────────

    @staticmethod
    def _snapshot_entry_disk(entry: Mapping[str, object]) -> str:
        """Resolve the disk for a stored snapshot/FULL-backup entry.

        Mirrors the read-time resolution: an explicit ``disk`` field wins;
        otherwise the disk is parsed from the record name, falling back to
        the legacy fallback disk.
        """
        raw_disk = entry.get("disk")
        if raw_disk is not None:
            return str(raw_disk)
        name = str(entry.get("name", ""))
        return parse_disk_from_snapshot_name(name) or _LEGACY_FALLBACK_DISK

    def reset_vm_disk_state(self, vm_name: str, disk: str) -> None:
        """Atomically clear the per-VM state that belongs to *disk* only.

        Per-disk counterpart of :meth:`reset_vm_state`.  Removes only the
        snapshots, ``last_allocation`` entry, deferred operations, and
        commit-intent record that belong to *disk*; state of the VM's
        other disks is preserved.  If the VM has no state file, no file is
        created (no-op).
        """
        path = self._state_path(vm_name)
        if not path.exists():
            return
        data = self._load(vm_name)

        # Snapshots: keep only those NOT belonging to *disk*.
        raw_snaps = data.get("snapshots", [])
        if raw_snaps:
            snaps = cast(list[dict[str, object]], raw_snaps)
            data["snapshots"] = [s for s in snaps if self._snapshot_entry_disk(s) != disk]

        # last_allocation: remove the *disk* key from the per-disk dict.  A
        # legacy bare-int value cannot be attributed to a disk and is left
        # untouched (treated as absent).
        last_alloc = data.get("last_allocation")
        if isinstance(last_alloc, dict) and disk in last_alloc:
            del last_alloc[disk]
            data["last_allocation"] = last_alloc

        # Deferred operations: keep only those NOT belonging to *disk*.  The
        # disk is resolved the same way as on read (explicit field, else
        # parsed from the first snapshot name, else the legacy fallback).
        raw_deferred = data.get("deferred_operations", [])
        if raw_deferred:
            deferred = cast(list[dict[str, object]], raw_deferred)
            kept: list[dict[str, object]] = []
            for d in deferred:
                raw_disk = d.get("disk")
                if raw_disk is not None:
                    d_disk = str(raw_disk)
                else:
                    snaps_list = cast(list[str], d.get("snapshots", []))
                    d_disk = _LEGACY_FALLBACK_DISK
                    if snaps_list:
                        d_disk = parse_disk_from_snapshot_name(str(snaps_list[0])) or d_disk
                if d_disk != disk:
                    kept.append(d)
            data["deferred_operations"] = kept

        # Commit-intent journal: keep only records for OTHER disks.
        raw_intents = data.get("commit_in_progress", [])
        if raw_intents:
            intents = cast(list[dict[str, object]], raw_intents)
            data["commit_in_progress"] = [i for i in intents if i.get("disk") != disk]

        self._save(vm_name, data)

    def reset_target_disk_state(self, target_path: str, vm_name: str, disk: str) -> None:
        """Atomically clear the per-target state that belongs to one disk.

        Per-disk counterpart of :meth:`reset_target_state`.  Removes only
        the full-backup records, incremental dependencies, and
        ``last_backup_allocation`` entry that belong to ``(vm_name, disk)``;
        backup state of other VMs and other disks on the same target is
        preserved.
        """
        vm_prefix = f"{vm_name}."

        # _full_backups.json: drop entries whose name starts with
        # "{vm_name}." AND whose disk equals *disk*.  The resolved disk of
        # every original entry is remembered so the dependency cleanup
        # below removes exactly the deps of the removed FULL records.
        fb_path = self._full_backups_path()
        full_disk_by_name: dict[str, str] = {}
        if fb_path.exists():
            fb_data = self._load_full_backups()
            entries = fb_data.get(target_path, [])
            if entries:
                # Dependency keys are stored in normalized stem form (see
                # _normalize_full_name), so the map uses the same form.
                full_disk_by_name = {
                    self._normalize_full_name(
                        str(entry.get("name", ""))
                    ): self._snapshot_entry_disk(entry)
                    for entry in entries
                }
                kept_entries = [
                    entry
                    for entry in entries
                    if not (
                        str(entry.get("name", "")).startswith(vm_prefix)
                        and self._snapshot_entry_disk(entry) == disk
                    )
                ]
                if len(kept_entries) != len(entries):
                    fb_data[target_path] = kept_entries
                    self._save_full_backups(fb_data)

        # _dependencies.json: drop FULL-anchor keys that belong to
        # (vm_name, disk).  The disk is resolved from the corresponding
        # full-backup record first (so legacy FULL names without a
        # parseable disk segment still match via their stored disk
        # field); keys without a record fall back to name parsing.
        dep_path = self._dependencies_path()
        if dep_path.exists():
            dep_data = self._load_dependencies()
            target_deps = dep_data.get(target_path, {})
            if target_deps:
                removable = []
                for full_key in target_deps:
                    if not full_key.startswith(vm_prefix):
                        continue
                    resolved = full_disk_by_name.get(full_key)
                    if resolved is None:
                        resolved = parse_disk_from_snapshot_name(full_key)
                    if resolved == disk:
                        removable.append(full_key)
                if removable:
                    for full_key in removable:
                        del target_deps[full_key]
                    dep_data[target_path] = target_deps
                    self._save_dependencies(dep_data)

        # _target_state.json: remove the last_backup_allocation[disk] entry.
        ts_path = self._target_state_path()
        if ts_path.exists():
            ts_data = self._load_target_state()
            entry = ts_data.get(target_path)
            if entry is not None:
                value = entry.get("last_backup_allocation")
                if isinstance(value, dict) and disk in value:
                    del value[disk]
                    entry["last_backup_allocation"] = value
                    ts_data[target_path] = entry
                    self._save_target_state(ts_data)

    # ── Crash evidence / recovery gating (recover-lost-checkpoint-bitmaps) ──

    def get_boot_id(self, vm_name: str) -> str | None:
        """Return the host boot_id recorded for *vm_name*, or ``None``."""
        data = self._load(vm_name)
        boot_id = data.get("boot_id")
        if boot_id is None:
            return None
        return str(boot_id)

    def set_boot_id(self, vm_name: str, boot_id: str) -> None:
        """Record the current host boot_id for *vm_name*."""
        data = self._load(vm_name)
        data["boot_id"] = boot_id
        self._save(vm_name, data)

    def get_last_commit_ts(self, vm_name: str, disk: str) -> str | None:
        """Return the per-disk last_commit_ts for *vm_name*/*disk*, or ``None``."""
        data = self._load(vm_name)
        markers = data.get("last_commit_ts")
        if not isinstance(markers, dict):
            return None
        value = markers.get(disk)
        if value is None:
            return None
        return str(value)

    def set_last_commit_ts(self, vm_name: str, disk: str, timestamp: str) -> None:
        """Record the last commit timestamp for *vm_name*/*disk*."""
        data = self._load(vm_name)
        existing = data.get("last_commit_ts")
        markers: dict[str, str] = existing if isinstance(existing, dict) else {}
        markers[disk] = timestamp
        data["last_commit_ts"] = markers
        self._save(vm_name, data)

    # ── Commit intent journal ──────────────────────────────────────────

    @staticmethod
    def _intent_to_dict(intent: CommitIntent) -> dict[str, object]:
        return {
            "disk": intent.disk,
            "snapshots": list(intent.snapshots),
            "base": intent.base,
            "started_ts": intent.started_ts,
        }

    @staticmethod
    def _dict_to_intent(d: dict[str, object]) -> CommitIntent:
        return CommitIntent(
            disk=str(d["disk"]),
            snapshots=list(d.get("snapshots", [])),  # type: ignore[arg-type]
            base=str(d["base"]),
            started_ts=str(d["started_ts"]),
        )

    def set_commit_in_progress(
        self,
        vm_name: str,
        disk: str,
        snapshots: list[str],
        base: str,
        started_ts: str,
    ) -> None:
        """Upsert the commit-intent record for (*vm_name*, *disk*)."""
        data = self._load(vm_name)
        raw_list: list[dict[str, object]] = list(data.get("commit_in_progress", []))  # type: ignore[arg-type]
        # Upsert: replace existing record for the same disk.
        replaced = False
        for i, item in enumerate(raw_list):
            if item.get("disk") == disk:
                raw_list[i] = self._intent_to_dict(
                    CommitIntent(
                        disk=disk,
                        snapshots=list(snapshots),
                        base=base,
                        started_ts=started_ts,
                    )
                )
                replaced = True
                break
        if not replaced:
            raw_list.append(
                self._intent_to_dict(
                    CommitIntent(
                        disk=disk,
                        snapshots=list(snapshots),
                        base=base,
                        started_ts=started_ts,
                    )
                )
            )
        data["commit_in_progress"] = raw_list
        self._save(vm_name, data)

    def get_commit_in_progress(self, vm_name: str) -> list[CommitIntent]:
        """Return all commit-intent records for *vm_name*."""
        data = self._load(vm_name)
        raw_list = data.get("commit_in_progress", [])
        if not raw_list:
            return []
        return [
            self._dict_to_intent(d)  # type: ignore[arg-type]
            for d in raw_list  # type: ignore[union-attr]
        ]

    def clear_commit_in_progress(self, vm_name: str, disk: str) -> None:
        """Remove the commit-intent record for (*vm_name*, *disk*)."""
        data = self._load(vm_name)
        raw_list: list[dict[str, object]] = list(data.get("commit_in_progress", []))  # type: ignore[arg-type]
        new_list = [item for item in raw_list if item.get("disk") != disk]
        if len(new_list) == len(raw_list):
            return  # No record to clear — no-op (no save needed).
        data["commit_in_progress"] = new_list
        self._save(vm_name, data)
