"""InMemoryStateManager — mock IStateManager for unit tests.

Stores state in a dict.  All methods fully functional.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.interfaces.state import IStateManager
from qsnap.models.results import CommitIntent, DeferredBlockcommit, FullBackupInfo, SnapshotInfo
from qsnap.utils.parsing import parse_disk_from_snapshot_name


class InMemoryStateManager(IStateManager):
    """In-memory state manager for unit tests."""

    @staticmethod
    def _to_extended_name(name: str) -> str:
        """Normalize a FULL backup name to extended form (with .qcow2).

        Mirrors JsonStateManager._to_extended_name for test parity
        (design D4).
        """
        if name.endswith(".qcow2"):
            return name
        return name + ".qcow2"

    @staticmethod
    def _normalize_full_name(full_name: str) -> str:
        """Normalize a FULL backup name to stem form (without .qcow2).

        Mirrors JsonStateManager._normalize_full_name for test parity
        (design D3).
        """
        if full_name.endswith(".qcow2"):
            return Path(full_name).stem
        return full_name

    def __init__(self) -> None:
        self._state: dict[str, dict[str, object]] = {}
        self._full_backups: dict[str, list[FullBackupInfo]] = {}
        self._dependencies: dict[str, dict[str, list[str]]] = {}
        self._target_state: dict[str, dict[str, int]] = {}
        self._commit_intents: dict[tuple[str, str], CommitIntent] = {}

    def get_last_allocation(self, vm_name: str, disk: str) -> int | None:
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return None
        value = vm_state.get("last_allocation")
        if not isinstance(value, dict):
            return None
        disk_value = value.get(disk)
        if disk_value is None:
            return None
        return int(disk_value)  # type: ignore[arg-type]

    def set_last_allocation(self, vm_name: str, disk: str, alloc: int) -> None:
        if vm_name not in self._state:
            self._state[vm_name] = {}
        existing = self._state[vm_name].get("last_allocation")
        per_disk: dict[str, int] = existing if isinstance(existing, dict) else {}
        per_disk[disk] = alloc
        self._state[vm_name]["last_allocation"] = per_disk

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

    def add_deferred_blockcommit(
        self, vm_name: str, disk: str, snapshots: list[str], reason: str
    ) -> None:
        if vm_name not in self._state:
            self._state[vm_name] = {}
        deferred = self._state[vm_name].setdefault("deferred_operations", [])
        deferred.append(
            DeferredBlockcommit(
                snapshots=list(snapshots),
                reason=reason,
                since=datetime.now(),
                disk=disk,
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
                    disk=item.disk,
                    last_warned_at=timestamp,
                )

    # ── Full backup tracking ──────────────────────────────────────────

    def get_last_full_backup(self, target_path: str) -> FullBackupInfo | None:
        entries = self._full_backups.get(target_path)
        if not entries:
            return None
        return entries[-1]

    def set_last_full_backup(
        self, target_path: str, name: str, timestamp: datetime, disk: str
    ) -> None:
        self.record_full_backup(target_path, name, timestamp, disk)

    def get_full_backups(self, target_path: str) -> list[FullBackupInfo]:
        return list(self._full_backups.get(target_path, []))

    def record_full_backup(
        self,
        target_path: str,
        name: str,
        timestamp: datetime,
        disk: str,
    ) -> None:
        """Append a full backup record for *target_path*/*disk*.

        Mirrors JsonStateManager.record_full_backup: normalizes *name*
        to extended form and derives *path* from the normalized name
        (design D4).
        """
        from pathlib import Path

        normalized_name = self._to_extended_name(name)
        entries = self._full_backups.setdefault(target_path, [])
        entries.append(
            FullBackupInfo(
                name=normalized_name,
                path=Path(target_path) / normalized_name,
                timestamp=timestamp,
                disk=disk,
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
        """Remove a full backup record from in-memory state.

        Mirrors JsonStateManager.remove_full_backup: normalizes the
        lookup *name* to extended form before matching, so both stem
        and extended callers remove the same record (design D4).
        """
        lookup_name = self._to_extended_name(name)
        entries = self._full_backups.get(target_path, [])
        for entry in entries:
            if entry.name == lookup_name:
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

    def get_last_backup_allocation(self, target_path: str, disk: str) -> int | None:
        """Return the last backup allocation for *target_path*/*disk*."""
        entry = self._target_state.get(target_path)
        if entry is None:
            return None
        return entry.get(disk)

    def set_last_backup_allocation(self, target_path: str, disk: str, alloc: int) -> None:
        """Record the last backup allocation for *target_path*/*disk*."""
        entry = self._target_state.setdefault(target_path, {})
        entry[disk] = alloc

    def clear_last_backup_allocation(self, target_path: str, disk: str) -> bool:
        """Remove the ``last_backup_allocation`` baseline for *target_path*/*disk*.

        Returns ``True`` if an entry was found and removed, ``False``
        if no matching entry existed.
        """
        entry = self._target_state.get(target_path)
        if entry is None or disk not in entry:
            return False
        del entry[disk]
        return True

    def remove_all_incremental_dependencies(self, target_path: str, full_name: str) -> int:
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

    # ── Bulk state reset (restore support) ───────────────────────────

    def reset_vm_state(self, vm_name: str) -> None:
        """Clear all per-VM state: snapshots, last_allocation, deferred_operations.

        Also clears every commit-intent record for the VM (mock parity for
        the intent journal — a full VM reset must not leave a stale intent
        behind; see harden-blockcommit-races test plan).
        """
        self._commit_intents = {
            (vm, disk): intent
            for (vm, disk), intent in self._commit_intents.items()
            if vm != vm_name
        }
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return
        vm_state["snapshots"] = []
        vm_state["last_allocation"] = {}
        vm_state["deferred_operations"] = []

    def reset_target_state(self, target_path: str) -> None:
        """Clear all per-target state: full_backups, dependencies, target_state."""
        self._full_backups.pop(target_path, None)
        self._dependencies.pop(target_path, None)
        self._target_state.pop(target_path, None)

    # ── Per-disk state reset (restore support, design D4) ─────────────

    def reset_vm_disk_state(self, vm_name: str, disk: str) -> None:
        """Clear only the per-VM state that belongs to *disk* (mock parity).

        Mirrors :meth:`JsonStateManager.reset_vm_disk_state`: removes the
        snapshots, ``last_allocation`` entry, and deferred operations that
        belong to *disk*; state of the VM's other disks is preserved.  In
        addition, the commit-intent record for ``(vm_name, disk)`` is
        cleared (harden-blockcommit-races intent journal).
        """
        self._commit_intents.pop((vm_name, disk), None)
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return

        snapshots = vm_state.get("snapshots", [])
        if isinstance(snapshots, list):
            vm_state["snapshots"] = [s for s in snapshots if getattr(s, "disk", None) != disk]

        last_alloc = vm_state.get("last_allocation")
        if isinstance(last_alloc, dict) and disk in last_alloc:
            del last_alloc[disk]

        deferred = vm_state.get("deferred_operations", [])
        if isinstance(deferred, list):
            vm_state["deferred_operations"] = [
                d for d in deferred if getattr(d, "disk", None) != disk
            ]

    def reset_target_disk_state(self, target_path: str, vm_name: str, disk: str) -> None:
        """Clear only the per-target state that belongs to (vm_name, disk).

        Mirrors :meth:`JsonStateManager.reset_target_disk_state`: removes
        the full-backup records, incremental dependencies, and
        ``last_backup_allocation`` entry that belong to ``(vm_name, disk)``;
        backup state of other VMs and other disks is preserved.
        """
        vm_prefix = f"{vm_name}."

        entries = self._full_backups.get(target_path)
        # Remember the stored disk of every original FULL record so the
        # dependency cleanup below removes exactly the deps of the removed
        # FULL records (legacy FULL names without a parseable disk segment
        # still match via their stored disk field).
        full_disk_by_name: dict[str, str | None] = {}
        if entries is not None:
            # Dependency keys are stored in normalized stem form (see
            # _normalize_full_name), so the map uses the same form.
            full_disk_by_name = {self._normalize_full_name(e.name): e.disk for e in entries}
            self._full_backups[target_path] = [
                e for e in entries if not (e.name.startswith(vm_prefix) and e.disk == disk)
            ]

        target_deps = self._dependencies.get(target_path)
        if target_deps is not None:
            removable = []
            for full_key in target_deps:
                if not full_key.startswith(vm_prefix):
                    continue
                resolved = full_disk_by_name.get(full_key)
                if resolved is None:
                    resolved = parse_disk_from_snapshot_name(full_key)
                if resolved == disk:
                    removable.append(full_key)
            for full_key in removable:
                del target_deps[full_key]

        entry = self._target_state.get(target_path)
        if entry is not None and disk in entry:
            del entry[disk]

    # ── Crash evidence / recovery gating (recover-lost-checkpoint-bitmaps) ──

    def get_boot_id(self, vm_name: str) -> str | None:
        """Return the host boot_id recorded for *vm_name*, or ``None``."""
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return None
        boot_id = vm_state.get("boot_id")
        if boot_id is None:
            return None
        return str(boot_id)

    def set_boot_id(self, vm_name: str, boot_id: str) -> None:
        """Record the current host boot_id for *vm_name*."""
        if vm_name not in self._state:
            self._state[vm_name] = {}
        self._state[vm_name]["boot_id"] = boot_id

    def get_last_commit_ts(self, vm_name: str, disk: str) -> str | None:
        """Return the per-disk last_commit_ts for *vm_name*/*disk*, or ``None``."""
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return None
        markers = vm_state.get("last_commit_ts")
        if not isinstance(markers, dict):
            return None
        value = markers.get(disk)
        if value is None:
            return None
        return str(value)

    def set_last_commit_ts(self, vm_name: str, disk: str, timestamp: str) -> None:
        """Record the last commit timestamp for *vm_name*/*disk*."""
        if vm_name not in self._state:
            self._state[vm_name] = {}
        existing = self._state[vm_name].get("last_commit_ts")
        markers: dict[str, str] = existing if isinstance(existing, dict) else {}
        markers[disk] = timestamp
        self._state[vm_name]["last_commit_ts"] = markers

    # ── Commit intent journal (harden-blockcommit-races) ──────────────

    def set_commit_in_progress(
        self,
        vm_name: str,
        disk: str,
        snapshots: list[str],
        base: str,
        started_ts: str,
    ) -> None:
        """Upsert the commit-intent record for (*vm_name*, *disk*).

        At most one record exists per disk — a second call with the same
        *disk* replaces the previous record (upsert semantics, mirroring
        :meth:`JsonStateManager.set_commit_in_progress`).
        """
        self._commit_intents[(vm_name, disk)] = CommitIntent(
            disk=disk,
            snapshots=list(snapshots),
            base=base,
            started_ts=started_ts,
        )

    def get_commit_in_progress(self, vm_name: str) -> list[CommitIntent]:
        """Return all commit-intent records for *vm_name*."""
        return [intent for (vm, _disk), intent in self._commit_intents.items() if vm == vm_name]

    def clear_commit_in_progress(self, vm_name: str, disk: str) -> None:
        """Remove the commit-intent record for (*vm_name*, *disk*).

        No-op when no record exists for this disk.
        """
        self._commit_intents.pop((vm_name, disk), None)
