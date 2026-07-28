"""Unit tests for JsonStateManager — concrete IStateManager implementation.

Tests verify allocation read/write, missing-state handling, snapshot
recording/listing with timestamp sorting, and the atomic write pattern
(crash-safety).  No source code is modified.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.models.results import DeferredBlockcommit, FullBackupInfo, SnapshotInfo
from qsnap.state.json_manager import JsonStateManager
from tests.mocks.mock_state import InMemoryStateManager

# ── helpers ──────────────────────────────────────────────────────────────


def _make_snapshot(
    name: str,
    ts: datetime,
    allocation: int = 1024,
    path: str = "/tmp/snap.qcow2",
) -> SnapshotInfo:
    return SnapshotInfo(
        name=name,
        path=Path(path),
        timestamp=ts,
        allocation=allocation,
    )


# ── tests ────────────────────────────────────────────────────────────────


def test_write_read_allocation(tmp_path: Path) -> None:
    """set_last_allocation then get_last_allocation round-trips the value."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_last_allocation("testvm", 4096)
    assert manager.get_last_allocation("testvm") == 4096

    # A second write overwrites the first.
    manager.set_last_allocation("testvm", 8192)
    assert manager.get_last_allocation("testvm") == 8192


def test_missing_state_returns_none(tmp_path: Path) -> None:
    """A VM with no state file returns None, not 0 and not an exception."""
    manager = JsonStateManager(state_dir=tmp_path)

    result = manager.get_last_allocation("nonexistent_vm")

    assert result is None


def test_record_and_list_snapshots(tmp_path: Path) -> None:
    """record_snapshot stores entries; get_snapshots returns them sorted by time."""
    manager = JsonStateManager(state_dir=tmp_path)

    snap_early = _make_snapshot("snap_early", datetime(2024, 1, 1, 11, 0, 0), allocation=3072)
    snap_mid = _make_snapshot("snap_mid", datetime(2024, 1, 1, 12, 0, 0), allocation=1024)
    snap_late = _make_snapshot("snap_late", datetime(2024, 1, 1, 13, 0, 0), allocation=2048)

    # Record out of chronological order.
    manager.record_snapshot("testvm", snap_mid)
    manager.record_snapshot("testvm", snap_late)
    manager.record_snapshot("testvm", snap_early)

    snapshots = manager.get_snapshots("testvm")

    assert len(snapshots) == 3
    # Sorted by timestamp ascending.
    assert snapshots[0].name == "snap_early"
    assert snapshots[1].name == "snap_mid"
    assert snapshots[2].name == "snap_late"
    # Fields are preserved.
    assert snapshots[0].timestamp == snap_early.timestamp
    assert snapshots[0].allocation == 3072
    assert snapshots[0].path == snap_early.path


def test_atomic_write_pattern(tmp_path: Path) -> None:
    """Atomic write: no .tmp remains on success; crash leaves original intact.

    This covers the CRITICAL risk in test-plan.md line 134: a crash during
    the rename step must not corrupt the existing state file.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    # ── Part 1: successful write leaves no .tmp file ──────────────────
    manager.set_last_allocation("testvm", 4096)

    state_file = tmp_path / "testvm.json"
    tmp_file = tmp_path / "testvm.json.tmp"

    assert state_file.exists(), "state file should exist after write"
    assert not tmp_file.exists(), "no .tmp file should remain after successful write"

    # ── Part 2: crash during os.replace leaves original file unchanged ──
    # Pre-write a valid state file with known data for a separate VM.
    manager.set_last_allocation("crashvm", 100)
    assert manager.get_last_allocation("crashvm") == 100

    crash_state_file = tmp_path / "crashvm.json"

    # Mock os.replace to raise mid-operation (the rename step).
    with (
        patch(
            "qsnap.state.json_manager.os.replace",
            side_effect=OSError("simulated crash during rename"),
        ),
        pytest.raises(OSError, match="simulated crash"),
    ):
        manager.set_last_allocation("crashvm", 999)

    # The original state file must still exist and be valid JSON — no
    # partial corruption is observable by a concurrent reader.
    assert crash_state_file.exists(), "original state file must still exist after crash"

    with open(crash_state_file, encoding="utf-8") as fh:
        data = json.load(fh)  # must parse without error
    assert data["last_allocation"] == 100, "original data must be unchanged after crash"

    # Re-reading through the manager must yield the original value.
    assert manager.get_last_allocation("crashvm") == 100


# ── deferred operations tests ────────────────────────────────────────────


def test_add_and_retrieve_deferred_blockcommit(tmp_path: Path) -> None:
    """add_deferred_blockcommit stores entry; get_deferred_operations returns it with correct fields."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", ["snap1.qcow2"], "apparmor")

    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 1

    op = ops[0]
    assert isinstance(op, DeferredBlockcommit)
    assert op.snapshots == ["snap1.qcow2"]
    assert op.reason == "apparmor"
    assert isinstance(op.since, datetime)
    # New entries have no warning timestamp yet.
    assert op.last_warned_at is None


def test_add_deferred_blockcommit_vm_running_reason(tmp_path: Path) -> None:
    """add_deferred_blockcommit stores entry with "vm_running" reason."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", ["snap1.qcow2", "snap2.qcow2"], "vm_running")

    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 1

    op = ops[0]
    assert isinstance(op, DeferredBlockcommit)
    assert op.snapshots == ["snap1.qcow2", "snap2.qcow2"]
    assert op.reason == "vm_running"
    assert isinstance(op.since, datetime)
    # New entries have no warning timestamp yet.
    assert op.last_warned_at is None


def test_add_deferred_blockcommit_active_layer_reason(tmp_path: Path) -> None:
    """add_deferred_blockcommit stores entry with "active_layer" reason."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", ["snap3.qcow2"], "active_layer")

    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 1

    op = ops[0]
    assert isinstance(op, DeferredBlockcommit)
    assert op.snapshots == ["snap3.qcow2"]
    assert op.reason == "active_layer"
    assert isinstance(op.since, datetime)
    # New entries have no warning timestamp yet.
    assert op.last_warned_at is None


def test_add_and_retrieve_deferred_operations(tmp_path: Path) -> None:
    """Alternate: add_deferred_blockcommit round-trips through get_deferred_operations."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", ["snap1.qcow2"], "apparmor")

    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 1

    op = ops[0]
    assert isinstance(op, DeferredBlockcommit)
    assert op.snapshots == ["snap1.qcow2"]
    assert op.reason == "apparmor"
    assert isinstance(op.since, datetime)


def test_clear_deferred_operations(tmp_path: Path) -> None:
    """clear_deferred_operations removes all queued operations for a VM."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", ["snap1.qcow2"], "apparmor")
    manager.add_deferred_blockcommit("vm1", ["snap2.qcow2", "snap3.qcow2"], "selinux")

    assert len(manager.get_deferred_operations("vm1")) == 2

    manager.clear_deferred_operations("vm1")

    assert manager.get_deferred_operations("vm1") == []


def test_no_deferred_operations_empty_list(tmp_path: Path) -> None:
    """A VM with no state file returns an empty list, not None or an exception."""
    manager = JsonStateManager(state_dir=tmp_path)

    result = manager.get_deferred_operations("vm_new")

    assert result == []


def test_deferred_operations_persisted_to_json(tmp_path: Path) -> None:
    """Deferred operations survive across JsonStateManager instances pointing to the same dir."""
    manager1 = JsonStateManager(state_dir=tmp_path)
    manager1.add_deferred_blockcommit("vm1", ["snap1.qcow2"], "apparmor")
    manager1.add_deferred_blockcommit("vm1", ["snap2.qcow2"], "selinux")

    # New manager instance, same state directory — must load persisted data.
    manager2 = JsonStateManager(state_dir=tmp_path)
    ops = manager2.get_deferred_operations("vm1")

    assert len(ops) == 2
    assert ops[0].snapshots == ["snap1.qcow2"]
    assert ops[0].reason == "apparmor"
    assert isinstance(ops[0].since, datetime)
    assert ops[1].snapshots == ["snap2.qcow2"]
    assert ops[1].reason == "selinux"
    assert isinstance(ops[1].since, datetime)


def test_deferred_blockcommit_dataclass_fields() -> None:
    """DeferredBlockcommit is a frozen dataclass with snapshots, reason, since, last_warned_at fields."""
    item = DeferredBlockcommit(
        snapshots=["snap1.qcow2"],
        reason="apparmor",
        since=datetime(2024, 1, 1, 12, 0, 0),
    )

    # Fields exist and hold correct values.
    assert item.snapshots == ["snap1.qcow2"]
    assert item.reason == "apparmor"
    assert item.since == datetime(2024, 1, 1, 12, 0, 0)
    # last_warned_at defaults to None when not provided.
    assert item.last_warned_at is None

    # The last_warned_at field exists on the dataclass.
    field_names = {f.name for f in dataclasses.fields(DeferredBlockcommit)}
    assert "last_warned_at" in field_names

    # Frozen: mutation raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.reason = "selinux"  # type: ignore[misc]


def test_state_round_trips_last_warned_at(tmp_path: Path) -> None:
    """_deferred_to_dict / _dict_to_deferred preserve last_warned_at."""
    warned = datetime(2025, 6, 1, 10, 0, 0)
    original = DeferredBlockcommit(
        snapshots=["snap1.qcow2"],
        reason="apparmor",
        since=datetime(2024, 1, 1, 12, 0, 0),
        last_warned_at=warned,
    )

    d = JsonStateManager._deferred_to_dict(original)
    assert d["last_warned_at"] == warned.isoformat()

    restored = JsonStateManager._dict_to_deferred(d)
    assert restored.last_warned_at == warned


def test_old_state_file_backward_compatible(tmp_path: Path) -> None:
    """Old state files without last_warned_at key load with last_warned_at=None."""
    # Direct test of _dict_to_deferred with a dict missing last_warned_at.
    raw_dict: dict[str, object] = {
        "snapshots": ["snap1.qcow2"],
        "reason": "apparmor",
        "since": "2024-01-01T12:00:00",
    }
    restored = JsonStateManager._dict_to_deferred(raw_dict)
    assert restored.last_warned_at is None

    # Also verify through the full file-load path.
    state_file = tmp_path / "vm1.json"
    state_file.write_text(
        json.dumps({"deferred_operations": [raw_dict]}),
        encoding="utf-8",
    )

    manager = JsonStateManager(state_dir=tmp_path)
    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 1
    assert ops[0].last_warned_at is None


def test_update_deferred_warning(tmp_path: Path) -> None:
    """update_deferred_warning sets last_warned_at on the deferred entry at index."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", ["snap1.qcow2"], "apparmor")

    # Initially None.
    ops = manager.get_deferred_operations("vm1")
    assert ops[0].last_warned_at is None

    # Update warning timestamp.
    warned = datetime(2025, 6, 1, 10, 0, 0)
    manager.update_deferred_warning("vm1", 0, warned)

    ops = manager.get_deferred_operations("vm1")
    assert ops[0].last_warned_at == warned


# ── full backup tracking tests ───────────────────────────────────────────


def test_set_and_get_last_full_backup(tmp_path: Path) -> None:
    """set_last_full_backup then get_last_full_backup round-trips the values."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    name = "full-2024-01-01"
    ts = datetime(2024, 1, 1, 12, 0, 0)

    manager.set_last_full_backup(target, name, ts)

    result = manager.get_last_full_backup(target)

    assert result is not None
    assert isinstance(result, FullBackupInfo)
    assert result.name == name
    assert result.timestamp == ts
    assert result.path == Path(target) / name


def test_full_backup_state_saved_and_retrieved(tmp_path: Path) -> None:
    """Full backup state survives across JsonStateManager instances (disk reload)."""
    target = "/mnt/backup/testvm"
    name = "full-2024-06-01"
    ts = datetime(2024, 6, 1, 9, 30, 0)

    manager1 = JsonStateManager(state_dir=tmp_path)
    manager1.set_last_full_backup(target, name, ts)

    # New manager instance, same state directory — must load persisted data.
    manager2 = JsonStateManager(state_dir=tmp_path)
    result = manager2.get_last_full_backup(target)

    assert result is not None
    assert isinstance(result, FullBackupInfo)
    assert result.name == name
    assert result.timestamp == ts
    assert result.path == Path(target) / name


def test_get_last_full_backup_returns_none_when_empty(tmp_path: Path) -> None:
    """get_last_full_backup on a target with no full backup returns None."""
    manager = JsonStateManager(state_dir=tmp_path)

    result = manager.get_last_full_backup("/mnt/backup/never_used")

    assert result is None


# ── multi-FULL tracking tests ──────────────────────────────────────


def test_record_and_get_full_backups(tmp_path: Path) -> None:
    """record_full_backup then get_full_backups returns the recorded FULL."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    name = "full-2024-01-01"
    ts = datetime(2024, 1, 1, 12, 0, 0)

    manager.record_full_backup(target, name, ts)

    backups = manager.get_full_backups(target)

    assert len(backups) == 1
    assert isinstance(backups[0], FullBackupInfo)
    assert backups[0].name == name
    assert backups[0].timestamp == ts
    assert backups[0].path == Path(target) / name


def test_multiple_fulls_tracked_per_target(tmp_path: Path) -> None:
    """Multiple FULLs recorded for the same target are all returned, oldest first."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    ts1 = datetime(2024, 1, 1, 12, 0, 0)
    ts2 = datetime(2024, 2, 1, 12, 0, 0)
    ts3 = datetime(2024, 3, 1, 12, 0, 0)

    manager.record_full_backup(target, "full-2024-01-01", ts1)
    manager.record_full_backup(target, "full-2024-02-01", ts2)
    manager.record_full_backup(target, "full-2024-03-01", ts3)

    backups = manager.get_full_backups(target)

    assert len(backups) == 3
    assert backups[0].name == "full-2024-01-01"
    assert backups[1].name == "full-2024-02-01"
    assert backups[2].name == "full-2024-03-01"


# ── incremental-to-FULL dependency tracking tests ──────────────────


def test_dependency_recorded_after_rebase(tmp_path: Path) -> None:
    """record_incremental_dependency then get_incremental_dependencies round-trips."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_incremental_dependency(target, "incr-001", "full-2024-01-01")

    deps = manager.get_incremental_dependencies(target, "full-2024-01-01")
    assert deps == ["incr-001"]


def test_multiple_incrementals_depend_on_same_full(tmp_path: Path) -> None:
    """Multiple incrementals depending on the same FULL are all returned."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_incremental_dependency(target, "incr-001", "full-2024-01-01")
    manager.record_incremental_dependency(target, "incr-002", "full-2024-01-01")
    manager.record_incremental_dependency(target, "incr-003", "full-2024-01-01")

    deps = manager.get_incremental_dependencies(target, "full-2024-01-01")
    assert len(deps) == 3
    assert "incr-001" in deps
    assert "incr-002" in deps
    assert "incr-003" in deps


def test_get_incremental_dependencies_empty(tmp_path: Path) -> None:
    """get_incremental_dependencies on a FULL with no dependents returns empty list."""
    manager = JsonStateManager(state_dir=tmp_path)

    deps = manager.get_incremental_dependencies("/mnt/backup/testvm", "full-orphan")
    assert deps == []


def test_duplicate_dependency_not_recorded(tmp_path: Path) -> None:
    """Recording the same incremental dependency twice does not create duplicates."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_incremental_dependency(target, "incr-001", "full-2024-01-01")
    manager.record_incremental_dependency(target, "incr-001", "full-2024-01-01")

    deps = manager.get_incremental_dependencies(target, "full-2024-01-01")
    assert deps == ["incr-001"]


# ── full_name normalization tests (design D3) ─────────────────────────


@pytest.mark.unit
def test_get_incremental_deps_with_stem_key(tmp_path: Path) -> None:
    """Lookup with .qcow2 form finds dependency stored with stem form.

    Record a dependency with the stem key ``vm.FULL.20260727``, then
    lookup using the extended ``vm.FULL.20260727.qcow2`` form.  The
    normalization should strip ``.qcow2`` and find the stored entry.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_incremental_dependency(target, "incr-001", "vm.FULL.20260727")

    deps = manager.get_incremental_dependencies(target, "vm.FULL.20260727.qcow2")
    assert deps == ["incr-001"]


@pytest.mark.unit
def test_get_incremental_deps_with_qcow2_key_finds_stem_stored(tmp_path: Path) -> None:
    """Record with .qcow2 form normalizes to stem; lookup with stem finds it.

    Record a dependency using the extended ``vm.FULL.20260727.qcow2``
    form as the *full_name*.  The normalization in
    ``record_incremental_dependency`` should strip the ``.qcow2``
    extension and store the entry under the stem key
    ``vm.FULL.20260727``.  A subsequent lookup with the stem form must
    return the recorded incremental.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_incremental_dependency(target, "incr-001", "vm.FULL.20260727.qcow2")

    # Lookup via stem form — must find the entry stored under stem.
    deps = manager.get_incremental_dependencies(target, "vm.FULL.20260727")
    assert deps == ["incr-001"]

    # Lookup via .qcow2 form also works (normalized both ways).
    deps_qcow2 = manager.get_incremental_dependencies(
        target, "vm.FULL.20260727.qcow2"
    )
    assert deps_qcow2 == ["incr-001"]


# ── legacy dependency key migration tests (design D3) ──────────────────


@pytest.mark.unit
def test_legacy_qcow2_keys_migrated_to_stem_on_load(tmp_path: Path) -> None:
    """Legacy _dependencies.json with .qcow2 keys is auto-migrated to stem on load.

    Write a ``_dependencies.json`` file containing a ``.qcow2`` key
    (e.g. ``"vm.FULL.20260727.qcow2"``).  When ``JsonStateManager``
    loads and migrates it, the key should be renamed to stem form
    (``"vm.FULL.20260727"``), and a lookup with either form must
    succeed.  The file on disk must be rewritten with stem keys only.
    """
    state_dir = tmp_path
    dep_file = state_dir / "_dependencies.json"
    dep_data = {
        "/mnt/backup/testvm": {
            "vm.FULL.20260727.qcow2": ["incr-001"],
        },
    }
    dep_file.write_text(json.dumps(dep_data), encoding="utf-8")

    manager = JsonStateManager(state_dir=state_dir)

    # Lookup with stem form must find the migrated entry.
    deps = manager.get_incremental_dependencies(
        "/mnt/backup/testvm", "vm.FULL.20260727"
    )
    assert deps == ["incr-001"]

    # Lookup with .qcow2 form also works after migration.
    deps_qcow2 = manager.get_incremental_dependencies(
        "/mnt/backup/testvm", "vm.FULL.20260727.qcow2"
    )
    assert deps_qcow2 == ["incr-001"]

    # File on disk must have been rewritten with stem key only.
    with open(dep_file, encoding="utf-8") as fh:
        loaded = json.load(fh)
    target_deps = loaded["/mnt/backup/testvm"]
    assert "vm.FULL.20260727" in target_deps
    assert "vm.FULL.20260727.qcow2" not in target_deps


@pytest.mark.unit
def test_already_migrated_deps_file_loaded_unchanged(tmp_path: Path) -> None:
    """Already-migrated _dependencies.json (stem keys only) is loaded as-is.

    Write a ``_dependencies.json`` that already uses stem keys (no
    ``.qcow2`` suffixes).  After loading via ``JsonStateManager``, the
    data must be accessible and the file content on disk must remain
    unchanged — no migration rewrite should occur.
    """
    state_dir = tmp_path
    dep_file = state_dir / "_dependencies.json"
    dep_data = {
        "/mnt/backup/testvm": {
            "vm.FULL.20260727": ["incr-001"],
            "vm.FULL.20260728": ["incr-002", "incr-003"],
        },
    }
    dep_file.write_text(json.dumps(dep_data), encoding="utf-8")

    # Record the original file content for comparison.
    with open(dep_file, encoding="utf-8") as fh:
        original_raw = fh.read()

    manager = JsonStateManager(state_dir=state_dir)

    # Both entries are accessible after load (no data loss).
    deps1 = manager.get_incremental_dependencies(
        "/mnt/backup/testvm", "vm.FULL.20260727"
    )
    assert deps1 == ["incr-001"]

    deps2 = manager.get_incremental_dependencies(
        "/mnt/backup/testvm", "vm.FULL.20260728"
    )
    assert deps2 == ["incr-002", "incr-003"]

    # The file must remain unchanged — no migration rewrite triggered.
    with open(dep_file, encoding="utf-8") as fh:
        after_raw = fh.read()
    assert after_raw == original_raw, (
        "Already-migrated deps file must not be rewritten (idempotent)"
    )


@pytest.mark.unit
def test_mixed_keys_migrated_correctly(tmp_path: Path) -> None:
    """Mixed .qcow2 and stem keys: only .qcow2 key is migrated, stem key untouched.

    Write a ``_dependencies.json`` containing both a ``.qcow2`` key and
    a stem key.  After load, the ``.qcow2`` key must be migrated to
    stem, the existing stem key must be intact, and the file on disk
    must contain only stem keys.
    """
    state_dir = tmp_path
    dep_file = state_dir / "_dependencies.json"
    dep_data = {
        "/mnt/backup/testvm": {
            "vm.FULL.20260727.qcow2": ["incr-old"],
            "vm.FULL.20260728": ["incr-new"],
        },
    }
    dep_file.write_text(json.dumps(dep_data), encoding="utf-8")

    manager = JsonStateManager(state_dir=state_dir)

    # Migrated .qcow2 key must be accessible via stem.
    deps_old = manager.get_incremental_dependencies(
        "/mnt/backup/testvm", "vm.FULL.20260727"
    )
    assert deps_old == ["incr-old"]

    # Unchanged stem key must still be accessible.
    deps_new = manager.get_incremental_dependencies(
        "/mnt/backup/testvm", "vm.FULL.20260728"
    )
    assert deps_new == ["incr-new"]

    # The .qcow2 key must also be accessible via .qcow2 lookup
    # (normalization strips extension to find the migrated stem key).
    deps_old_qcow2 = manager.get_incremental_dependencies(
        "/mnt/backup/testvm", "vm.FULL.20260727.qcow2"
    )
    assert deps_old_qcow2 == ["incr-old"]

    # File on disk must contain only stem keys.
    with open(dep_file, encoding="utf-8") as fh:
        loaded = json.load(fh)
    target_deps = loaded["/mnt/backup/testvm"]
    assert "vm.FULL.20260727" in target_deps
    assert "vm.FULL.20260727.qcow2" not in target_deps
    assert "vm.FULL.20260728" in target_deps
    assert target_deps["vm.FULL.20260727"] == ["incr-old"]
    assert target_deps["vm.FULL.20260728"] == ["incr-new"]


# ── InMemoryStateManager normalization tests (design D3) ───────────────


@pytest.mark.mock
def test_inmemory_get_deps_with_stem_key() -> None:
    """InMemoryStateManager: lookup with .qcow2 form finds stem-stored dependency.

    Record a dependency with the stem form of ``full_name``, then
    lookup with the ``.qcow2`` form.  The normalization must strip the
    extension and return the recorded entry.
    """
    manager = InMemoryStateManager()

    target = "/mnt/backup/testvm"
    manager.record_incremental_dependency(target, "incr-001", "vm.FULL.20260727")

    deps = manager.get_incremental_dependencies(target, "vm.FULL.20260727.qcow2")
    assert deps == ["incr-001"]


@pytest.mark.mock
def test_inmemory_record_qcow2_key_finds_stem_stored() -> None:
    """InMemoryStateManager: record with .qcow2 form normalizes to stem.

    Record a dependency using the ``.qcow2`` form of ``full_name``.
    The normalization must strip the extension and store under the
    stem key.  Lookup with stem form must return the entry.
    """
    manager = InMemoryStateManager()

    target = "/mnt/backup/testvm"
    manager.record_incremental_dependency(target, "incr-001", "vm.FULL.20260727.qcow2")

    deps = manager.get_incremental_dependencies(target, "vm.FULL.20260727")
    assert deps == ["incr-001"]

    # Lookup with .qcow2 form also works (normalized both ways).
    deps_qcow2 = manager.get_incremental_dependencies(
        target, "vm.FULL.20260727.qcow2"
    )
    assert deps_qcow2 == ["incr-001"]


# ── _full_backups.json migration tests ──────────────────────────────


def test_full_backups_json_old_format_auto_migrated(tmp_path: Path) -> None:
    """Old-format _full_backups.json (dict values) is auto-migrated to list on load."""
    # Write old format: {target_path: {name, path, timestamp}} (dict, not list)
    old_data = {
        "/mnt/backup/testvm": {
            "name": "full-2024-01-01",
            "path": "/mnt/backup/testvm/full-2024-01-01",
            "timestamp": "2024-01-01T12:00:00",
        },
    }
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)
    full_backups_file = state_dir / "_full_backups.json"
    full_backups_file.write_text(json.dumps(old_data), encoding="utf-8")

    manager = JsonStateManager(state_dir=state_dir)

    # get_full_backups should auto-migrate the dict to a list.
    backups = manager.get_full_backups("/mnt/backup/testvm")
    assert len(backups) == 1
    assert backups[0].name == "full-2024-01-01"
    assert backups[0].timestamp == datetime(2024, 1, 1, 12, 0, 0)
    assert backups[0].path == Path("/mnt/backup/testvm/full-2024-01-01")

    # get_last_full_backup should also work (returns last from list).
    last = manager.get_last_full_backup("/mnt/backup/testvm")
    assert last is not None
    assert last.name == "full-2024-01-01"


def test_full_backups_json_new_format_loaded_as_is(tmp_path: Path) -> None:
    """New-format _full_backups.json (list values) is loaded as-is."""
    new_data = {
        "/mnt/backup/testvm": [
            {
                "name": "full-2024-01-01",
                "path": "/mnt/backup/testvm/full-2024-01-01",
                "timestamp": "2024-01-01T12:00:00",
                "bucket_level": "monthly",
            },
            {
                "name": "full-2024-03-01",
                "path": "/mnt/backup/testvm/full-2024-03-01",
                "timestamp": "2024-03-01T12:00:00",
                "bucket_level": "weekly",
            },
        ],
    }
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)
    full_backups_file = state_dir / "_full_backups.json"
    full_backups_file.write_text(json.dumps(new_data), encoding="utf-8")

    manager = JsonStateManager(state_dir=state_dir)

    backups = manager.get_full_backups("/mnt/backup/testvm")
    assert len(backups) == 2
    assert backups[0].name == "full-2024-01-01"
    assert backups[1].name == "full-2024-03-01"


# ── Fault tolerance & safety: state file corruption and rotation ──────────


def test_corrupt_state_file_renamed_and_empty_state_returned(
    tmp_path: Path,
) -> None:
    """Corrupt JSON file is renamed and empty state is returned.

    Write a corrupt JSON file (e.g. ``{ broken json``) to
    ``{tmp_path}/testvm.json``.  Create ``JsonStateManager``, call
    ``get_last_allocation("testvm")``.  Assert: returns None.  Assert:
    the corrupt file was renamed to ``testvm.json.broken.{timestamp}``
    (check it starts with "testvm.json.broken.").  Assert: the original
    ``testvm.json`` no longer exists.
    """
    state_file = tmp_path / "testvm.json"
    state_file.write_text("{ broken json", encoding="utf-8")

    manager = JsonStateManager(state_dir=tmp_path)
    result = manager.get_last_allocation("testvm")

    assert result is None

    # Original state file must no longer exist.
    assert not state_file.exists(), "Original state file should be renamed away after corruption"

    # A broken file should exist.
    broken_files = list(tmp_path.glob("testvm.json.broken.*"))
    assert len(broken_files) == 1, (
        f"Expected exactly one broken file, got {len(broken_files)}: "
        f"{[f.name for f in broken_files]}"
    )
    assert broken_files[0].name.startswith("testvm.json.broken."), (
        f"Broken file name should start with 'testvm.json.broken.', got {broken_files[0].name}"
    )


def test_clean_state_file_loads_normally(tmp_path: Path) -> None:
    """Valid JSON state file loads without corruption handling.

    Write valid JSON ``{"last_allocation": 4096}`` to
    ``{tmp_path}/testvm.json``.  Create ``JsonStateManager``.  Assert
    ``get_last_allocation("testvm")`` returns 4096.
    """
    state_file = tmp_path / "testvm.json"
    state_file.write_text(
        json.dumps({"last_allocation": 4096}),
        encoding="utf-8",
    )

    manager = JsonStateManager(state_dir=tmp_path)
    assert manager.get_last_allocation("testvm") == 4096


def test_missing_state_file_returns_none_gracefully(tmp_path: Path) -> None:
    """No state file → get_last_allocation returns None without raising.

    This covers the same scenario as ``test_missing_state_returns_none``
    but with explicit emphasis on graceful handling (no exception, no
    side effects on the filesystem).
    """
    manager = JsonStateManager(state_dir=tmp_path)

    # Verify the directory is empty.
    assert not any(tmp_path.iterdir()), "State directory should be empty"

    # Should return None gracefully for any non-existent VM.
    result = manager.get_last_allocation("nonexistent")
    assert result is None

    # Also check a second non-existent VM for confidence.
    assert manager.get_last_allocation("another_vm") is None


def test_first_save_creates_state_file_only(tmp_path: Path) -> None:
    """First save: only the state file is created, no backup files.

    Create ``JsonStateManager(state_dir=tmp_path, state_backup_count=3)``.
    Call ``set_last_allocation("testvm", 4096)``.  Assert: ``testvm.json``
    exists.  Assert: NO backup files exist (``testvm.json.1``,
    ``testvm.json.2``, ``testvm.json.3`` should NOT exist).  First save
    has no previous file to rotate.
    """
    manager = JsonStateManager(state_dir=tmp_path, state_backup_count=3)

    manager.set_last_allocation("testvm", 4096)

    state_file = tmp_path / "testvm.json"
    assert state_file.exists(), "State file should be created on first save"

    # No backup files should exist — nothing to rotate.
    for i in range(1, 4):
        backup = tmp_path / f"testvm.json.{i}"
        assert not backup.exists(), f"Backup file {backup.name} should NOT exist on first save"

    # Verify content is correct.
    assert manager.get_last_allocation("testvm") == 4096


def test_subsequent_saves_rotate_state_files(tmp_path: Path) -> None:
    """Subsequent saves rotate previous state into numbered backups.

    Create ``JsonStateManager(state_dir=tmp_path, state_backup_count=3)``.
    Call ``set_last_allocation("testvm", 100)`` then
    ``set_last_allocation("testvm", 200)``.  Assert: ``testvm.json``
    contains 200.  Assert: ``testvm.json.1`` contains 100 (previous version).
    Call ``set_last_allocation("testvm", 300)``.  Assert: ``testvm.json.1``
    now contains 200, ``testvm.json.2`` contains 100.
    """
    manager = JsonStateManager(state_dir=tmp_path, state_backup_count=3)

    # First save — no rotation.
    manager.set_last_allocation("testvm", 100)

    # Second save — rotates 100 → testvm.json.1.
    manager.set_last_allocation("testvm", 200)

    assert manager.get_last_allocation("testvm") == 200

    backup1 = tmp_path / "testvm.json.1"
    assert backup1.exists(), "testvm.json.1 should exist after second save"
    with open(backup1, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["last_allocation"] == 100, (
        f"testvm.json.1 should contain previous value (100), got {data}"
    )

    # Third save — pushes 200 → .1, 100 → .2.
    manager.set_last_allocation("testvm", 300)

    assert manager.get_last_allocation("testvm") == 300

    with open(tmp_path / "testvm.json.1", encoding="utf-8") as fh:
        assert json.load(fh)["last_allocation"] == 200
    with open(tmp_path / "testvm.json.2", encoding="utf-8") as fh:
        assert json.load(fh)["last_allocation"] == 100


def test_backup_count_limit_enforced(tmp_path: Path) -> None:
    """Backup count limit is enforced — oldest entries discarded.

    Create ``JsonStateManager(state_dir=tmp_path, state_backup_count=2)``.
    Write 4 times: set_last_allocation("testvm", 100), 200, 300, 400.
    Assert: ``testvm.json`` exists (400).  ``testvm.json.1`` exists (300).
    ``testvm.json.2`` exists (200).  ``testvm.json.3`` does NOT exist
    (limit is 2, so oldest 100 is discarded).
    """
    manager = JsonStateManager(state_dir=tmp_path, state_backup_count=2)

    manager.set_last_allocation("testvm", 100)
    manager.set_last_allocation("testvm", 200)
    manager.set_last_allocation("testvm", 300)
    manager.set_last_allocation("testvm", 400)

    assert manager.get_last_allocation("testvm") == 400

    # Backup 1 should contain 300
    backup1 = tmp_path / "testvm.json.1"
    assert backup1.exists()
    with open(backup1, encoding="utf-8") as fh:
        assert json.load(fh)["last_allocation"] == 300

    # Backup 2 should contain 200
    backup2 = tmp_path / "testvm.json.2"
    assert backup2.exists()
    with open(backup2, encoding="utf-8") as fh:
        assert json.load(fh)["last_allocation"] == 200

    # Backup 3 must NOT exist — limit is 2, oldest (100) discarded.
    backup3 = tmp_path / "testvm.json.3"
    assert not backup3.exists(), "testvm.json.3 should NOT exist — backup count limit is 2"


def test_state_backup_count_zero_disables_rotation(tmp_path: Path) -> None:
    """``state_backup_count=0`` disables backup rotation entirely.

    Create ``JsonStateManager(state_dir=tmp_path, state_backup_count=0)``.
    Call ``set_last_allocation("testvm", 100)`` then
    ``set_last_allocation("testvm", 200)``.  Assert: ``testvm.json``
    exists (contains 200).  Assert: NO backup files exist.
    """
    manager = JsonStateManager(state_dir=tmp_path, state_backup_count=0)

    manager.set_last_allocation("testvm", 100)
    manager.set_last_allocation("testvm", 200)

    state_file = tmp_path / "testvm.json"
    assert state_file.exists()
    with open(state_file, encoding="utf-8") as fh:
        assert json.load(fh)["last_allocation"] == 200

    # No backup files should exist.
    for i in range(1, 5):
        backup = tmp_path / f"testvm.json.{i}"
        assert not backup.exists(), (
            f"Backup file {backup.name} should NOT exist when state_backup_count=0"
        )


# ── FULL backup deduplication tests (design D4) ───────────────────────────


def test_deduplicate_duplicate_full_entries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Duplicate (name, target_path) tuples are removed on load, keeping the first.

    Create a _full_backups.json with duplicate entries (same name
    appearing twice for the same target).  Load via JsonStateManager.
    Assert: only ONE entry remains; an INFO log was emitted for each
    removed duplicate.
    """
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)

    # Write a state file with duplicate entries.
    full_backups_data = {
        "/mnt/backup/testvm": [
            {
                "name": "full-2024-01-01",
                "path": "/mnt/backup/testvm/full-2024-01-01",
                "timestamp": "2024-01-01T12:00:00",
                "bucket_level": "monthly",
            },
            {
                "name": "full-2024-02-01",
                "path": "/mnt/backup/testvm/full-2024-02-01",
                "timestamp": "2024-02-01T12:00:00",
                "bucket_level": "monthly",
            },
            # DUPLICATE of the first entry (same name + same target_path).
            {
                "name": "full-2024-01-01",
                "path": "/mnt/backup/testvm/full-2024-01-01",
                "timestamp": "2024-01-01T12:00:00",
                "bucket_level": "monthly",
            },
        ],
    }
    full_backups_file = state_dir / "_full_backups.json"
    full_backups_file.write_text(json.dumps(full_backups_data), encoding="utf-8")

    caplog.set_level(logging.INFO)

    manager = JsonStateManager(state_dir=state_dir)
    backups = manager.get_full_backups("/mnt/backup/testvm")

    # Only the first occurrence should remain (the duplicate removed).
    assert len(backups) == 2
    assert backups[0].name == "full-2024-01-01"
    assert backups[1].name == "full-2024-02-01"

    # Deduplication log should have been emitted.
    assert (
        "Deduplicated FULL backup entry: full-2024-01-01 for target /mnt/backup/testvm"
        in caplog.text
    )


def test_deduplicate_no_duplicates_noop(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """No duplicate entries → all preserved, no deduplication log.

    Create a _full_backups.json where all (name, target_path) tuples are
    unique.  Load via JsonStateManager.  Assert all entries are preserved
    and no deduplication INFO log was emitted.
    """
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)

    # Write a state file with only unique entries.
    full_backups_data = {
        "/mnt/backup/testvm": [
            {
                "name": "full-2024-01-01",
                "path": "/mnt/backup/testvm/full-2024-01-01",
                "timestamp": "2024-01-01T12:00:00",
                "bucket_level": "monthly",
            },
            {
                "name": "full-2024-02-01",
                "path": "/mnt/backup/testvm/full-2024-02-01",
                "timestamp": "2024-02-01T12:00:00",
                "bucket_level": "monthly",
            },
            {
                "name": "full-2024-03-01",
                "path": "/mnt/backup/testvm/full-2024-03-01",
                "timestamp": "2024-03-01T12:00:00",
                "bucket_level": "weekly",
            },
        ],
    }
    full_backups_file = state_dir / "_full_backups.json"
    full_backups_file.write_text(json.dumps(full_backups_data), encoding="utf-8")

    caplog.set_level(logging.INFO)

    manager = JsonStateManager(state_dir=state_dir)
    backups = manager.get_full_backups("/mnt/backup/testvm")

    # All entries should be preserved.
    assert len(backups) == 3
    names = [b.name for b in backups]
    assert names == ["full-2024-01-01", "full-2024-02-01", "full-2024-03-01"]

    # No deduplication log should have been emitted.
    assert "Deduplicated FULL backup entry:" not in caplog.text


def test_deduplicate_is_idempotent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Second load after deduplication is a no-op (idempotent).

    Create a _full_backups.json with duplicates.  Load it once
    (deduplication occurs and state is rewritten).  Then load it again.
    Assert: the second load does NOT emit a deduplication log and the
    state file has been rewritten with the deduplicated list.
    """

    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)

    # Write a state file with duplicate entries.
    full_backups_data = {
        "/mnt/backup/testvm": [
            {
                "name": "full-2024-01-01",
                "path": "/mnt/backup/testvm/full-2024-01-01",
                "timestamp": "2024-01-01T12:00:00",
                "bucket_level": "monthly",
            },
            {
                "name": "full-2024-02-01",
                "path": "/mnt/backup/testvm/full-2024-02-01",
                "timestamp": "2024-02-01T12:00:00",
                "bucket_level": "monthly",
            },
            # DUPLICATE of the first entry.
            {
                "name": "full-2024-01-01",
                "path": "/mnt/backup/testvm/full-2024-01-01",
                "timestamp": "2024-01-01T12:00:00",
                "bucket_level": "monthly",
            },
        ],
    }
    full_backups_file = state_dir / "_full_backups.json"
    full_backups_file.write_text(json.dumps(full_backups_data), encoding="utf-8")

    caplog.set_level(logging.INFO)

    # First load — deduplication should occur.
    manager1 = JsonStateManager(state_dir=state_dir)
    backups1 = manager1.get_full_backups("/mnt/backup/testvm")
    assert len(backups1) == 2
    assert (
        "Deduplicated FULL backup entry: full-2024-01-01 for target /mnt/backup/testvm"
        in caplog.text
    )

    # The state file should have been rewritten with the deduplicated list.
    with open(full_backups_file, encoding="utf-8") as fh:
        rewritten_data = json.load(fh)
    assert len(rewritten_data["/mnt/backup/testvm"]) == 2
    names_on_disk = [e["name"] for e in rewritten_data["/mnt/backup/testvm"]]
    assert names_on_disk == ["full-2024-01-01", "full-2024-02-01"]

    # Clear caplog before second load.
    caplog.clear()

    # Second load — should be a no-op (already deduplicated).
    manager2 = JsonStateManager(state_dir=state_dir)
    backups2 = manager2.get_full_backups("/mnt/backup/testvm")
    assert len(backups2) == 2
    assert backups2[0].name == "full-2024-01-01"
    assert backups2[1].name == "full-2024-02-01"

    # No deduplication log on the second load.
    assert "Deduplicated FULL backup entry:" not in caplog.text


# ── per-target backup allocation tracking ─────────────────────────────────


def test_per_target_backup_allocation_write_read(tmp_path: Path) -> None:
    """set_last_backup_allocation then get_last_backup_allocation round-trips the value."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_last_backup_allocation("/path/to/target", 12345)
    assert manager.get_last_backup_allocation("/path/to/target") == 12345


def test_per_target_backup_allocation_missing_returns_none(tmp_path: Path) -> None:
    """get_last_backup_allocation on a target with no recorded state returns None."""
    manager = JsonStateManager(state_dir=tmp_path)

    result = manager.get_last_backup_allocation("/nonexistent")
    assert result is None


def test_per_target_backup_allocation_independent(tmp_path: Path) -> None:
    """Per-target backup allocation state is independent — target A and B don't interfere."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_last_backup_allocation("/mnt/backup/target_a", 1000)
    manager.set_last_backup_allocation("/mnt/backup/target_b", 2000)

    assert manager.get_last_backup_allocation("/mnt/backup/target_a") == 1000
    assert manager.get_last_backup_allocation("/mnt/backup/target_b") == 2000


def test_target_state_json_atomic_write(tmp_path: Path) -> None:
    """After set_last_backup_allocation, _target_state.json exists with correct JSON and no .tmp file lingers."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_last_backup_allocation("/path/to/target", 12345)

    target_state_file = tmp_path / "_target_state.json"
    tmp_file = tmp_path / "_target_state.json.tmp"

    assert target_state_file.exists(), "_target_state.json must exist after write"
    assert not tmp_file.exists(), ".tmp file must NOT be left behind (atomic write via os.replace)"

    with open(target_state_file, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data == {"/path/to/target": {"last_backup_allocation": 12345}}


def test_target_state_json_missing_returns_none(tmp_path: Path) -> None:
    """Fresh state directory with no _target_state.json — get_last_backup_allocation returns None."""
    manager = JsonStateManager(state_dir=tmp_path)

    # Verify the state directory has no _target_state.json.
    assert not (tmp_path / "_target_state.json").exists()

    result = manager.get_last_backup_allocation("/some/target")
    assert result is None


def test_target_state_json_corrupted_renamed(tmp_path: Path) -> None:
    """Corrupted _target_state.json is renamed to .broken.{timestamp} and get_last_backup_allocation returns None."""
    target_state_file = tmp_path / "_target_state.json"
    target_state_file.write_text("{ this is invalid json", encoding="utf-8")

    manager = JsonStateManager(state_dir=tmp_path)

    result = manager.get_last_backup_allocation("/any/target")
    assert result is None, "Corrupted state must return None (graceful recovery)"

    # Original file must be gone.
    assert not target_state_file.exists(), "Corrupt _target_state.json must be renamed away"

    # A .broken.* file must exist.
    broken_files = list(tmp_path.glob("_target_state.json.broken.*"))
    assert len(broken_files) == 1, (
        f"Expected exactly one broken file, got {len(broken_files)}: "
        f"{[f.name for f in broken_files]}"
    )
    assert broken_files[0].name.startswith("_target_state.json.broken.")


# ── clear_last_backup_allocation tests ────────────────────────────────


def test_clear_backup_allocation_existing(tmp_path: Path) -> None:
    """clear_last_backup_allocation removes an existing baseline and returns True."""
    manager = JsonStateManager(state_dir=tmp_path)

    # Set a baseline first.
    manager.set_last_backup_allocation("/path/to/target", 12345)
    assert manager.get_last_backup_allocation("/path/to/target") == 12345

    # Clear it.
    result = manager.clear_last_backup_allocation("/path/to/target")
    assert result is True

    # Verify it's gone.
    assert manager.get_last_backup_allocation("/path/to/target") is None


def test_clear_backup_allocation_nonexistent(tmp_path: Path) -> None:
    """clear_last_backup_allocation on a target with no baseline returns False."""
    manager = JsonStateManager(state_dir=tmp_path)

    result = manager.clear_last_backup_allocation("/nonexistent/target")
    assert result is False


# ── remove_all_incremental_dependencies tests ─────────────────────────


def test_remove_all_incremental_deps_existing(tmp_path: Path) -> None:
    """remove_all_incremental_dependencies removes all deps for a FULL and returns count."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_incremental_dependency(target, "incr-001", "full-2024-01-01")
    manager.record_incremental_dependency(target, "incr-002", "full-2024-01-01")
    manager.record_incremental_dependency(target, "incr-003", "full-2024-01-01")

    # Verify deps exist.
    deps = manager.get_incremental_dependencies(target, "full-2024-01-01")
    assert len(deps) == 3

    # Remove all.
    count = manager.remove_all_incremental_dependencies(target, "full-2024-01-01")
    assert count == 3

    # Verify deps are gone (get_incremental_dependencies returns empty list).
    deps = manager.get_incremental_dependencies(target, "full-2024-01-01")
    assert deps == []


def test_remove_all_incremental_deps_nonexistent(tmp_path: Path) -> None:
    """remove_all_incremental_dependencies for a FULL with no deps returns 0."""
    manager = JsonStateManager(state_dir=tmp_path)

    count = manager.remove_all_incremental_dependencies(
        "/mnt/backup/testvm", "full-orphan"
    )
    assert count == 0


# ── atomic write test for clear_last_backup_allocation ────────────────


def test_json_clear_last_backup_allocation_atomic(tmp_path: Path) -> None:
    """clear_last_backup_allocation uses atomic writes — crash during os.replace preserves state.

    The clear operation delegates to _save_target_state which writes to a
    .tmp file then calls os.replace.  If os.replace raises (simulating a
    crash), the original _target_state.json must remain intact with its
    original data.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/path/to/target"
    manager.set_last_backup_allocation(target, 12345)

    # Verify baseline is set.
    assert manager.get_last_backup_allocation(target) == 12345

    target_state_file = tmp_path / "_target_state.json"
    assert target_state_file.exists(), "_target_state.json must exist after set"

    # Read original content before crash simulation.
    with open(target_state_file, encoding="utf-8") as fh:
        original_data = json.load(fh)

    # Mock os.replace to simulate a crash during the rename step.
    with (
        patch(
            "qsnap.state.json_manager.os.replace",
            side_effect=OSError("simulated crash during rename"),
        ),
        pytest.raises(OSError, match="simulated crash"),
    ):
        manager.clear_last_backup_allocation(target)

    # The original state file must still exist and contain the original data.
    assert target_state_file.exists(), (
        "_target_state.json must still exist after crash"
    )
    with open(target_state_file, encoding="utf-8") as fh:
        data_after_crash = json.load(fh)
    assert (
        data_after_crash == original_data
    ), "original data must be unchanged after simulated crash"

    # Re-reading through the manager must yield the original value.
    assert manager.get_last_backup_allocation(target) == 12345


# ── InMemoryStateManager tests ────────────────────────────────────────


def test_inmemory_clear_last_backup_allocation() -> None:
    """InMemoryStateManager.clear_last_backup_allocation correctly removes a baseline from its dict.

    Set a baseline, then clear it, verify True is returned and get returns None.
    """
    manager = InMemoryStateManager()

    target = "/path/to/target"

    # Set baseline.
    manager.set_last_backup_allocation(target, 12345)
    assert manager.get_last_backup_allocation(target) == 12345

    # Clear it.
    result = manager.clear_last_backup_allocation(target)
    assert result is True

    # Verify it's gone (get_last_backup_allocation returns None).
    assert manager.get_last_backup_allocation(target) is None

    # Verify the dict entry is truly removed, not just set to None.
    assert target not in manager._target_state
