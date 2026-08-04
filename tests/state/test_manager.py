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
        disk="vda",
    )


# ── tests ────────────────────────────────────────────────────────────────


def test_write_read_allocation(tmp_path: Path) -> None:
    """set_last_allocation then get_last_allocation round-trips the value."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_last_allocation("testvm", "vda", 4096)
    assert manager.get_last_allocation("testvm", "vda") == 4096

    # A second write overwrites the first.
    manager.set_last_allocation("testvm", "vda", 8192)
    assert manager.get_last_allocation("testvm", "vda") == 8192


def test_missing_state_returns_none(tmp_path: Path) -> None:
    """A VM with no state file returns None, not 0 and not an exception."""
    manager = JsonStateManager(state_dir=tmp_path)

    result = manager.get_last_allocation("nonexistent_vm", "vda")

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
    manager.set_last_allocation("testvm", "vda", 4096)

    state_file = tmp_path / "testvm.json"
    tmp_file = tmp_path / "testvm.json.tmp"

    assert state_file.exists(), "state file should exist after write"
    assert not tmp_file.exists(), "no .tmp file should remain after successful write"

    # ── Part 2: crash during os.replace leaves original file unchanged ──
    # Pre-write a valid state file with known data for a separate VM.
    manager.set_last_allocation("crashvm", "vda", 100)
    assert manager.get_last_allocation("crashvm", "vda") == 100

    crash_state_file = tmp_path / "crashvm.json"

    # Mock os.replace to raise mid-operation (the rename step).
    with (
        patch(
            "qsnap.state.json_manager.os.replace",
            side_effect=OSError("simulated crash during rename"),
        ),
        pytest.raises(OSError, match="simulated crash"),
    ):
        manager.set_last_allocation("crashvm", "vda", 999)

    # The original state file must still exist and be valid JSON — no
    # partial corruption is observable by a concurrent reader.
    assert crash_state_file.exists(), "original state file must still exist after crash"

    with open(crash_state_file, encoding="utf-8") as fh:
        data = json.load(fh)  # must parse without error
    assert data.get("last_allocation", {}).get("vda", None) == 100, (
        "original data must be unchanged after crash"
    )

    # Re-reading through the manager must yield the original value.
    assert manager.get_last_allocation("crashvm", "vda") == 100


# ── deferred operations tests ────────────────────────────────────────────


def test_add_and_retrieve_deferred_blockcommit(tmp_path: Path) -> None:
    """add_deferred_blockcommit stores entry; get_deferred_operations returns it with correct fields."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "apparmor")

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

    manager.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2", "snap2.qcow2"], "vm_running")

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

    manager.add_deferred_blockcommit("vm1", "vda", ["snap3.qcow2"], "active_layer")

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

    manager.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "apparmor")

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

    manager.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "apparmor")
    manager.add_deferred_blockcommit("vm1", "vda", ["snap2.qcow2", "snap3.qcow2"], "selinux")

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
    manager1.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "apparmor")
    manager1.add_deferred_blockcommit("vm1", "vda", ["snap2.qcow2"], "selinux")

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
        disk="vda",
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
        disk="vda",
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

    manager.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "apparmor")

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

    manager.set_last_full_backup(target, name, ts, "vda")

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
    manager1.set_last_full_backup(target, name, ts, "vda")

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

    manager.record_full_backup(target, name, ts, "vda")

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

    manager.record_full_backup(target, "full-2024-01-01", ts1, "vda")
    manager.record_full_backup(target, "full-2024-02-01", ts2, "vda")
    manager.record_full_backup(target, "full-2024-03-01", ts3, "vda")

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
    deps_qcow2 = manager.get_incremental_dependencies(target, "vm.FULL.20260727.qcow2")
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
    deps = manager.get_incremental_dependencies("/mnt/backup/testvm", "vm.FULL.20260727")
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
    deps1 = manager.get_incremental_dependencies("/mnt/backup/testvm", "vm.FULL.20260727")
    assert deps1 == ["incr-001"]

    deps2 = manager.get_incremental_dependencies("/mnt/backup/testvm", "vm.FULL.20260728")
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
    deps_old = manager.get_incremental_dependencies("/mnt/backup/testvm", "vm.FULL.20260727")
    assert deps_old == ["incr-old"]

    # Unchanged stem key must still be accessible.
    deps_new = manager.get_incremental_dependencies("/mnt/backup/testvm", "vm.FULL.20260728")
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
    deps_qcow2 = manager.get_incremental_dependencies(target, "vm.FULL.20260727.qcow2")
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
    result = manager.get_last_allocation("testvm", "vda")

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
        json.dumps({"last_allocation": {"vda": 4096}}),
        encoding="utf-8",
    )

    manager = JsonStateManager(state_dir=tmp_path)
    assert manager.get_last_allocation("testvm", "vda") == 4096


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
    result = manager.get_last_allocation("nonexistent", "vda")
    assert result is None

    # Also check a second non-existent VM for confidence.
    assert manager.get_last_allocation("another_vm", "vda") is None


def test_first_save_creates_state_file_only(tmp_path: Path) -> None:
    """First save: only the state file is created, no backup files.

    Create ``JsonStateManager(state_dir=tmp_path, state_backup_count=3)``.
    Call ``set_last_allocation("testvm", 4096)``.  Assert: ``testvm.json``
    exists.  Assert: NO backup files exist (``testvm.json.1``,
    ``testvm.json.2``, ``testvm.json.3`` should NOT exist).  First save
    has no previous file to rotate.
    """
    manager = JsonStateManager(state_dir=tmp_path, state_backup_count=3)

    manager.set_last_allocation("testvm", "vda", 4096)

    state_file = tmp_path / "testvm.json"
    assert state_file.exists(), "State file should be created on first save"

    # No backup files should exist — nothing to rotate.
    for i in range(1, 4):
        backup = tmp_path / f"testvm.json.{i}"
        assert not backup.exists(), f"Backup file {backup.name} should NOT exist on first save"

    # Verify content is correct.
    assert manager.get_last_allocation("testvm", "vda") == 4096


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
    manager.set_last_allocation("testvm", "vda", 100)

    # Second save — rotates 100 → testvm.json.1.
    manager.set_last_allocation("testvm", "vda", 200)

    assert manager.get_last_allocation("testvm", "vda") == 200

    backup1 = tmp_path / "testvm.json.1"
    assert backup1.exists(), "testvm.json.1 should exist after second save"
    with open(backup1, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["last_allocation"]["vda"] == 100, (
        f"testvm.json.1 should contain previous value (100), got {data}"
    )

    # Third save — pushes 200 → .1, 100 → .2.
    manager.set_last_allocation("testvm", "vda", 300)

    assert manager.get_last_allocation("testvm", "vda") == 300

    with open(tmp_path / "testvm.json.1", encoding="utf-8") as fh:
        assert json.load(fh)["last_allocation"]["vda"] == 200
    with open(tmp_path / "testvm.json.2", encoding="utf-8") as fh:
        assert json.load(fh)["last_allocation"]["vda"] == 100


def test_backup_count_limit_enforced(tmp_path: Path) -> None:
    """Backup count limit is enforced — oldest entries discarded.

    Create ``JsonStateManager(state_dir=tmp_path, state_backup_count=2)``.
    Write 4 times: set_last_allocation("testvm", 100), 200, 300, 400.
    Assert: ``testvm.json`` exists (400).  ``testvm.json.1`` exists (300).
    ``testvm.json.2`` exists (200).  ``testvm.json.3`` does NOT exist
    (limit is 2, so oldest 100 is discarded).
    """
    manager = JsonStateManager(state_dir=tmp_path, state_backup_count=2)

    manager.set_last_allocation("testvm", "vda", 100)
    manager.set_last_allocation("testvm", "vda", 200)
    manager.set_last_allocation("testvm", "vda", 300)
    manager.set_last_allocation("testvm", "vda", 400)

    assert manager.get_last_allocation("testvm", "vda") == 400

    # Backup 1 should contain 300
    backup1 = tmp_path / "testvm.json.1"
    assert backup1.exists()
    with open(backup1, encoding="utf-8") as fh:
        assert json.load(fh)["last_allocation"]["vda"] == 300

    # Backup 2 should contain 200
    backup2 = tmp_path / "testvm.json.2"
    assert backup2.exists()
    with open(backup2, encoding="utf-8") as fh:
        assert json.load(fh)["last_allocation"]["vda"] == 200

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

    manager.set_last_allocation("testvm", "vda", 100)
    manager.set_last_allocation("testvm", "vda", 200)

    state_file = tmp_path / "testvm.json"
    assert state_file.exists()
    with open(state_file, encoding="utf-8") as fh:
        assert json.load(fh)["last_allocation"]["vda"] == 200

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

    manager.set_last_backup_allocation("/path/to/target", "vda", 12345)
    assert manager.get_last_backup_allocation("/path/to/target", "vda") == 12345


def test_per_target_backup_allocation_missing_returns_none(tmp_path: Path) -> None:
    """get_last_backup_allocation on a target with no recorded state returns None."""
    manager = JsonStateManager(state_dir=tmp_path)

    result = manager.get_last_backup_allocation("/nonexistent", "vda")
    assert result is None


def test_per_target_backup_allocation_independent(tmp_path: Path) -> None:
    """Per-target backup allocation state is independent — target A and B don't interfere."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_last_backup_allocation("/mnt/backup/target_a", "vda", 1000)
    manager.set_last_backup_allocation("/mnt/backup/target_b", "vda", 2000)

    assert manager.get_last_backup_allocation("/mnt/backup/target_a", "vda") == 1000
    assert manager.get_last_backup_allocation("/mnt/backup/target_b", "vda") == 2000


def test_target_state_json_atomic_write(tmp_path: Path) -> None:
    """After set_last_backup_allocation, _target_state.json exists with correct JSON and no .tmp file lingers."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_last_backup_allocation("/path/to/target", "vda", 12345)

    target_state_file = tmp_path / "_target_state.json"
    tmp_file = tmp_path / "_target_state.json.tmp"

    assert target_state_file.exists(), "_target_state.json must exist after write"
    assert not tmp_file.exists(), ".tmp file must NOT be left behind (atomic write via os.replace)"

    with open(target_state_file, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data == {"/path/to/target": {"last_backup_allocation": {"vda": 12345}}}


def test_target_state_json_missing_returns_none(tmp_path: Path) -> None:
    """Fresh state directory with no _target_state.json — get_last_backup_allocation returns None."""
    manager = JsonStateManager(state_dir=tmp_path)

    # Verify the state directory has no _target_state.json.
    assert not (tmp_path / "_target_state.json").exists()

    result = manager.get_last_backup_allocation("/some/target", "vda")
    assert result is None


def test_target_state_json_corrupted_renamed(tmp_path: Path) -> None:
    """Corrupted _target_state.json is renamed to .broken.{timestamp} and get_last_backup_allocation returns None."""
    target_state_file = tmp_path / "_target_state.json"
    target_state_file.write_text("{ this is invalid json", encoding="utf-8")

    manager = JsonStateManager(state_dir=tmp_path)

    result = manager.get_last_backup_allocation("/any/target", "vda")
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
    manager.set_last_backup_allocation("/path/to/target", "vda", 12345)
    assert manager.get_last_backup_allocation("/path/to/target", "vda") == 12345

    # Clear it.
    result = manager.clear_last_backup_allocation("/path/to/target", "vda")
    assert result is True

    # Verify it's gone.
    assert manager.get_last_backup_allocation("/path/to/target", "vda") is None


def test_clear_backup_allocation_nonexistent(tmp_path: Path) -> None:
    """clear_last_backup_allocation on a target with no baseline returns False."""
    manager = JsonStateManager(state_dir=tmp_path)

    result = manager.clear_last_backup_allocation("/nonexistent/target", "vda")
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

    count = manager.remove_all_incremental_dependencies("/mnt/backup/testvm", "full-orphan")
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
    manager.set_last_backup_allocation(target, "vda", 12345)

    # Verify baseline is set.
    assert manager.get_last_backup_allocation(target, "vda") == 12345

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
        manager.clear_last_backup_allocation(target, "vda")

    # The original state file must still exist and contain the original data.
    assert target_state_file.exists(), "_target_state.json must still exist after crash"
    with open(target_state_file, encoding="utf-8") as fh:
        data_after_crash = json.load(fh)
    assert data_after_crash == original_data, (
        "original data must be unchanged after simulated crash"
    )

    # Re-reading through the manager must yield the original value.
    assert manager.get_last_backup_allocation(target, "vda") == 12345


# ── InMemoryStateManager tests ────────────────────────────────────────


def test_inmemory_clear_last_backup_allocation() -> None:
    """InMemoryStateManager.clear_last_backup_allocation correctly removes a baseline from its dict.

    Set a baseline, then clear it, verify True is returned and get returns None.
    """
    manager = InMemoryStateManager()

    target = "/path/to/target"

    # Set baseline.
    manager.set_last_backup_allocation(target, "vda", 12345)
    assert manager.get_last_backup_allocation(target, "vda") == 12345

    # Clear it.
    result = manager.clear_last_backup_allocation(target, "vda")
    assert result is True

    # Verify it's gone (get_last_backup_allocation returns None).
    assert manager.get_last_backup_allocation(target, "vda") is None

    # Verify the disk key is removed from the target entry.
    assert target in manager._target_state
    assert not manager._target_state[target]


# ── reset_vm_state tests ───────────────────────────────────────────────


def test_reset_vm_state_clears_snapshots(tmp_path: Path) -> None:
    """After reset_vm_state, get_snapshots returns an empty list."""
    manager = JsonStateManager(state_dir=tmp_path)

    snap1 = _make_snapshot("snap1", datetime(2024, 1, 1, 11, 0, 0))
    snap2 = _make_snapshot("snap2", datetime(2024, 1, 1, 12, 0, 0))
    manager.record_snapshot("testvm", snap1)
    manager.record_snapshot("testvm", snap2)

    assert len(manager.get_snapshots("testvm")) == 2

    manager.reset_vm_state("testvm")

    assert manager.get_snapshots("testvm") == []


def test_reset_vm_state_clears_last_allocation(tmp_path: Path) -> None:
    """After reset_vm_state, get_last_allocation returns None."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_last_allocation("testvm", "vda", 4096)
    assert manager.get_last_allocation("testvm", "vda") == 4096

    manager.reset_vm_state("testvm")

    assert manager.get_last_allocation("testvm", "vda") is None


def test_reset_vm_state_clears_deferred_operations(tmp_path: Path) -> None:
    """After reset_vm_state, get_deferred_operations returns an empty list."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("testvm", "vda", ["snap1.qcow2"], "apparmor")
    manager.add_deferred_blockcommit("testvm", "vda", ["snap2.qcow2"], "vm_running")

    assert len(manager.get_deferred_operations("testvm")) == 2

    manager.reset_vm_state("testvm")

    assert manager.get_deferred_operations("testvm") == []


def test_reset_vm_state_nonexistent_vm_no_error(tmp_path: Path) -> None:
    """resetting a VM that has no state file does not raise."""
    manager = JsonStateManager(state_dir=tmp_path)

    # No state file for this VM — should be a no-op.
    manager.reset_vm_state("nonexistent")

    # Still no state file was created.
    state_file = tmp_path / "nonexistent.json"
    assert not state_file.exists()


def test_reset_vm_state_saves_atomically(tmp_path: Path) -> None:
    """After reset_vm_state, the state file exists on disk and no .tmp file lingers."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_last_allocation("testvm", "vda", 4096)
    manager.record_snapshot("testvm", _make_snapshot("snap1", datetime(2024, 1, 1, 12, 0, 0)))

    # Verify state file exists before reset.
    state_file = tmp_path / "testvm.json"
    assert state_file.exists()

    manager.reset_vm_state("testvm")

    # After reset, state file exists and .tmp file does not linger.
    assert state_file.exists(), "state file must exist after reset"
    tmp_file = tmp_path / "testvm.json.tmp"
    assert not tmp_file.exists(), "no .tmp file should remain after atomic write"

    # Verify content on disk reflects the reset.
    with open(state_file, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["snapshots"] == []
    assert data["last_allocation"] == {}
    assert data["deferred_operations"] == []


# ── reset_target_state tests ───────────────────────────────────────────


def test_reset_target_state_removes_from_full_backups(tmp_path: Path) -> None:
    """After reset_target_state, get_full_backups returns an empty list."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_full_backup(target, "full-2024-01-01", datetime(2024, 1, 1, 12, 0, 0), "vda")
    manager.record_full_backup(target, "full-2024-02-01", datetime(2024, 2, 1, 12, 0, 0), "vda")

    assert len(manager.get_full_backups(target)) == 2

    manager.reset_target_state(target)

    assert manager.get_full_backups(target) == []


def test_reset_target_state_removes_from_dependencies(tmp_path: Path) -> None:
    """After reset_target_state, get_incremental_dependencies returns an empty list."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_incremental_dependency(target, "incr-001", "full-2024-01-01")
    manager.record_incremental_dependency(target, "incr-002", "full-2024-01-01")

    assert len(manager.get_incremental_dependencies(target, "full-2024-01-01")) == 2

    manager.reset_target_state(target)

    assert manager.get_incremental_dependencies(target, "full-2024-01-01") == []


def test_reset_target_state_removes_from_target_state(tmp_path: Path) -> None:
    """After reset_target_state, get_last_backup_allocation returns None."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.set_last_backup_allocation(target, "vda", 12345)

    assert manager.get_last_backup_allocation(target, "vda") == 12345

    manager.reset_target_state(target)

    assert manager.get_last_backup_allocation(target, "vda") is None


def test_reset_target_state_nonexistent_target_no_error(tmp_path: Path) -> None:
    """resetting a target that has no recorded state does not raise."""
    manager = JsonStateManager(state_dir=tmp_path)

    # No state files exist — should be a no-op.
    manager.reset_target_state("/nonexistent/target")

    # No error raised, state files still empty/absent.
    assert manager.get_full_backups("/nonexistent/target") == []
    assert manager.get_incremental_dependencies("/nonexistent/target", "any") == []
    assert manager.get_last_backup_allocation("/nonexistent/target", "vda") is None


def test_reset_target_state_saves_atomically(tmp_path: Path) -> None:
    """After reset_target_state, all three state files exist on disk with target removed."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_full_backup(target, "full-2024-01-01", datetime(2024, 1, 1, 12, 0, 0), "vda")
    manager.record_incremental_dependency(target, "incr-001", "full-2024-01-01")
    manager.set_last_backup_allocation(target, "vda", 12345)

    # Verify all three state files exist before reset.
    full_backups_file = tmp_path / "_full_backups.json"
    deps_file = tmp_path / "_dependencies.json"
    target_state_file = tmp_path / "_target_state.json"

    assert full_backups_file.exists()
    assert deps_file.exists()
    assert target_state_file.exists()

    manager.reset_target_state(target)

    # After reset, all three files should still exist (just target removed).
    assert full_backups_file.exists(), "_full_backups.json must exist after reset"
    assert deps_file.exists(), "_dependencies.json must exist after reset"
    assert target_state_file.exists(), "_target_state.json must exist after reset"

    # No .tmp files should linger.
    assert not (tmp_path / "_full_backups.json.tmp").exists()
    assert not (tmp_path / "_dependencies.json.tmp").exists()
    assert not (tmp_path / "_target_state.json.tmp").exists()

    # Verify target is removed from full backups file.
    with open(full_backups_file, encoding="utf-8") as fh:
        fb_data = json.load(fh)
    assert target not in fb_data

    # Verify target is removed from dependencies file.
    with open(deps_file, encoding="utf-8") as fh:
        deps_data = json.load(fh)
    assert target not in deps_data

    # Verify target is removed from target state file.
    with open(target_state_file, encoding="utf-8") as fh:
        ts_data = json.load(fh)
    assert target not in ts_data


# ── per-disk state reset (reset_vm_disk_state) ──────────────────────────


def test_reset_vm_disk_state_clears_only_given_disk(tmp_path: Path) -> None:
    """reset_vm_disk_state for vda clears only vda data; vdb data preserved.

    Pre-populate snapshots, last_allocation, and deferred operations for both
    vda and vdb.  Then call reset_vm_disk_state("myvm", "vda").  Only the vda
    records must be removed; vdb records must remain intact.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    # ── populate vda state ───────────────────────────────────────────
    snap_vda1 = SnapshotInfo(
        name="myvm.20250101T120000_vda_a1b2c3",
        path=Path("/tmp/snap_vda1.qcow2"),
        timestamp=datetime(2025, 1, 1, 12, 0, 0),
        allocation=1024,
        disk="vda",
    )
    snap_vda2 = SnapshotInfo(
        name="myvm.20250101T130000_vda_d3e4f5",
        path=Path("/tmp/snap_vda2.qcow2"),
        timestamp=datetime(2025, 1, 1, 13, 0, 0),
        allocation=2048,
        disk="vda",
    )

    # ── populate vdb state ───────────────────────────────────────────
    snap_vdb1 = SnapshotInfo(
        name="myvm.20250101T120000_vdb_111111",
        path=Path("/tmp/snap_vdb1.qcow2"),
        timestamp=datetime(2025, 1, 1, 12, 0, 0),
        allocation=512,
        disk="vdb",
    )
    snap_vdb2 = SnapshotInfo(
        name="myvm.20250101T130000_vdb_222222",
        path=Path("/tmp/snap_vdb2.qcow2"),
        timestamp=datetime(2025, 1, 1, 13, 0, 0),
        allocation=768,
        disk="vdb",
    )

    manager.record_snapshot("myvm", snap_vda1)
    manager.record_snapshot("myvm", snap_vda2)
    manager.record_snapshot("myvm", snap_vdb1)
    manager.record_snapshot("myvm", snap_vdb2)

    manager.set_last_allocation("myvm", "vda", 1000)
    manager.set_last_allocation("myvm", "vdb", 2000)

    manager.add_deferred_blockcommit("myvm", "vda", ["snap_vda.qcow2"], "apparmor")
    manager.add_deferred_blockcommit("myvm", "vdb", ["snap_vdb.qcow2"], "vm_running")

    # ── pre-assertions ───────────────────────────────────────────────
    assert len(manager.get_snapshots("myvm")) == 4
    assert manager.get_last_allocation("myvm", "vda") == 1000
    assert manager.get_last_allocation("myvm", "vdb") == 2000
    assert len(manager.get_deferred_operations("myvm")) == 2

    # ── reset vda disk ───────────────────────────────────────────────
    manager.reset_vm_disk_state("myvm", "vda")

    # ── vda is gone ──────────────────────────────────────────────────
    snaps = manager.get_snapshots("myvm")
    assert len(snaps) == 2, f"Expected 2 vdb snapshots, got {len(snaps)}"
    assert all(s.disk == "vdb" for s in snaps), (
        f"Only vdb snapshots should remain, got disks: {[s.disk for s in snaps]}"
    )

    assert manager.get_last_allocation("myvm", "vda") is None, (
        "vda last_allocation must be None after reset"
    )

    ops = manager.get_deferred_operations("myvm")
    assert len(ops) == 1, f"Expected 1 vdb deferred op, got {len(ops)}"
    assert ops[0].disk == "vdb", f"Only vdb deferred should remain, got disk={ops[0].disk!r}"

    # ── vdb is preserved ─────────────────────────────────────────────
    assert manager.get_last_allocation("myvm", "vdb") == 2000, (
        "vdb last_allocation must be preserved"
    )


def test_reset_vm_disk_state_legacy_bare_int(tmp_path: Path) -> None:
    """Legacy bare-integer last_allocation treated as absent after reset.

    When the state file contains a bare-integer ``last_allocation``
    (pre-per-disk format), reset_vm_disk_state must not error, and
    get_last_allocation must return None (the bare int is not attributable
    to any specific disk).
    """
    state_file = tmp_path / "myvm.json"
    state_file.write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "name": "myvm.20250101T120000_vda_a1b2c3",
                        "path": "/tmp/snap.qcow2",
                        "timestamp": "2025-01-01T12:00:00",
                        "allocation": 1024,
                        "disk": "vda",
                    },
                ],
                # Legacy: bare integer, not a per-disk dict.
                "last_allocation": 4096,
                "deferred_operations": [
                    {
                        "snapshots": ["myvm.20250101T120000_vda_a1b2c3"],
                        "reason": "apparmor",
                        "since": "2025-01-01T12:00:00",
                        "disk": "vda",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    manager = JsonStateManager(state_dir=tmp_path)

    # Must not raise.
    manager.reset_vm_disk_state("myvm", "vda")

    # Bare-integer last_allocation cannot be attributed to any disk → None.
    assert manager.get_last_allocation("myvm", "vda") is None, (
        "Legacy bare-int last_allocation must yield None after reset"
    )


def test_reset_vm_disk_state_unknown_noop(tmp_path: Path) -> None:
    """reset_vm_disk_state for non-existent VM or unknown disk is a no-op."""
    manager = JsonStateManager(state_dir=tmp_path)

    # No state file — must not raise and must not create one.
    manager.reset_vm_disk_state("nonexistent", "vda")
    assert not (tmp_path / "nonexistent.json").exists(), (
        "No state file should be created for unknown VM"
    )

    # VM with state but disk not present — must not raise.
    manager.set_last_allocation("myvm", "vda", 1000)
    manager.reset_vm_disk_state("myvm", "vdz")

    # vda allocation still intact.
    assert manager.get_last_allocation("myvm", "vda") == 1000, (
        "Unmatched disk reset must not affect existing disk state"
    )


def test_reset_vm_disk_state_atomic(tmp_path: Path) -> None:
    """Crash during os.replace leaves original state file unchanged.

    Pre-populate snapshots, last_allocation, and deferred operations for
    both vda and vdb.  Mock os.replace to raise OSError, then call
    reset_vm_disk_state for vda.  The original state file must remain
    valid JSON with all vda data intact.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    # ── populate vda + vdb state ─────────────────────────────────────
    snap_vda = SnapshotInfo(
        name="myvm.20250101T120000_vda_a1b2c3",
        path=Path("/tmp/snap_vda.qcow2"),
        timestamp=datetime(2025, 1, 1, 12, 0, 0),
        allocation=1024,
        disk="vda",
    )
    snap_vdb = SnapshotInfo(
        name="myvm.20250101T130000_vdb_111111",
        path=Path("/tmp/snap_vdb.qcow2"),
        timestamp=datetime(2025, 1, 1, 13, 0, 0),
        allocation=512,
        disk="vdb",
    )

    manager.record_snapshot("myvm", snap_vda)
    manager.record_snapshot("myvm", snap_vdb)
    manager.set_last_allocation("myvm", "vda", 1000)
    manager.set_last_allocation("myvm", "vdb", 2000)
    manager.add_deferred_blockcommit("myvm", "vda", ["snap_vda.qcow2"], "apparmor")
    manager.add_deferred_blockcommit("myvm", "vdb", ["snap_vdb.qcow2"], "vm_running")

    state_file = tmp_path / "myvm.json"
    assert state_file.exists()

    # Read original content for post-crash comparison.
    with open(state_file, encoding="utf-8") as fh:
        original_data = json.load(fh)

    # ── simulate crash during os.replace ─────────────────────────────
    with (
        patch(
            "qsnap.state.json_manager.os.replace",
            side_effect=OSError("simulated crash during rename"),
        ),
        pytest.raises(OSError, match="simulated crash"),
    ):
        manager.reset_vm_disk_state("myvm", "vda")

    # ── original state file must be intact ───────────────────────────
    assert state_file.exists(), "state file must still exist after crash"

    with open(state_file, encoding="utf-8") as fh:
        data_after = json.load(fh)
    assert data_after == original_data, "state file must be unchanged after crash"

    # Re-reading through the manager must yield original vda data.
    assert manager.get_last_allocation("myvm", "vda") == 1000
    snaps = manager.get_snapshots("myvm")
    assert len(snaps) == 2
    assert any(s.disk == "vda" for s in snaps)


# ── per-disk target state reset (reset_target_disk_state) ───────────────


def test_reset_target_disk_state_clears_only_given_vm_disk(
    tmp_path: Path,
) -> None:
    """reset_target_disk_state for (myvm, vda) removes only those records.

    Populate a target with FULLs for (myvm, vda), (myvm, vdb), and
    (othervm, vda).  After reset, only the (myvm, vda) FULLs are removed;
    the vdb and othervm FULLs persist.  Likewise for backup allocation
    baselines.
    """
    manager = JsonStateManager(state_dir=tmp_path)
    target = "/mnt/backup/shared"

    ts_vda = datetime(2025, 1, 1, 12, 0, 0)
    ts_vdb = datetime(2025, 2, 1, 12, 0, 0)
    ts_other = datetime(2025, 3, 1, 12, 0, 0)

    full_vda = "myvm.FULL.20250101T120000_vda_a1b2c3"
    full_vdb = "myvm.FULL.20250201T120000_vdb_d3e4f5"
    full_other = "othervm.FULL.20250301T120000_vda_111111"

    manager.record_full_backup(target, full_vda, ts_vda, "vda")
    manager.record_full_backup(target, full_vdb, ts_vdb, "vdb")
    manager.record_full_backup(target, full_other, ts_other, "vda")

    manager.record_incremental_dependency(target, "incr-vda-001", full_vda)
    manager.record_incremental_dependency(target, "incr-vdb-001", full_vdb)
    manager.record_incremental_dependency(target, "incr-other-001", full_other)

    manager.set_last_backup_allocation(target, "vda", 1000)
    manager.set_last_backup_allocation(target, "vdb", 2000)

    # ── pre-assertions ───────────────────────────────────────────────
    assert len(manager.get_full_backups(target)) == 3
    assert manager.get_last_backup_allocation(target, "vda") == 1000
    assert manager.get_last_backup_allocation(target, "vdb") == 2000

    # ── reset (myvm, vda) ────────────────────────────────────────────
    manager.reset_target_disk_state(target, "myvm", "vda")

    # ── (myvm, vda) FULL is gone ─────────────────────────────────────
    backups = manager.get_full_backups(target)
    backup_names = {b.name for b in backups}
    assert full_vda not in backup_names, (
        f"(myvm,vda) FULL must be removed, got names: {backup_names}"
    )
    assert full_vdb in backup_names, "(myvm,vdb) FULL must be preserved"
    assert full_other in backup_names, "(othervm,vda) FULL must be preserved"
    assert len(backups) == 2

    # ── vda allocation cleared, vdb preserved ───────────────────────
    assert manager.get_last_backup_allocation(target, "vda") is None, (
        "vda backup allocation must be None after reset"
    )
    assert manager.get_last_backup_allocation(target, "vdb") == 2000, (
        "vdb backup allocation must be preserved"
    )


def test_reset_target_disk_state_removes_only_disk_deps(
    tmp_path: Path,
) -> None:
    """reset_target_disk_state removes only the dependencies for the given disk.

    _dependencies.json holds FULL keys for (myvm, vda) and (myvm, vdb).
    After reset for (myvm, vda), only the vda FULL key is removed; the vdb
    FULL key and its incrementals remain intact.
    """
    manager = JsonStateManager(state_dir=tmp_path)
    target = "/mnt/backup/shared"

    full_vda = "myvm.FULL.20250101T120000_vda_a1b2c3"
    full_vdb = "myvm.FULL.20250201T120000_vdb_d3e4f5"

    # Record dependencies via the manager so normalisation happens.
    manager.record_incremental_dependency(target, "incr-vda-001", full_vda)
    manager.record_incremental_dependency(target, "incr-vda-002", full_vda)
    manager.record_incremental_dependency(target, "incr-vdb-001", full_vdb)

    # ── pre-assertions ───────────────────────────────────────────────
    deps_vda = manager.get_incremental_dependencies(target, full_vda)
    assert len(deps_vda) == 2
    deps_vdb = manager.get_incremental_dependencies(target, full_vdb)
    assert len(deps_vdb) == 1

    # ── reset (myvm, vda) ────────────────────────────────────────────
    manager.reset_target_disk_state(target, "myvm", "vda")

    # ── vda dependency key removed ───────────────────────────────────
    deps_vda_after = manager.get_incremental_dependencies(target, full_vda)
    assert deps_vda_after == [], f"vda deps must be empty after reset, got {deps_vda_after}"

    # ── vdb dependency key preserved ─────────────────────────────────
    deps_vdb_after = manager.get_incremental_dependencies(target, full_vdb)
    assert deps_vdb_after == ["incr-vdb-001"], (
        f"vdb deps must be preserved after reset, got {deps_vdb_after}"
    )


def test_reset_target_disk_state_unknown_noop(tmp_path: Path) -> None:
    """reset_target_disk_state for unknown target is a no-op (no error)."""
    manager = JsonStateManager(state_dir=tmp_path)

    # No state files — must not raise.
    manager.reset_target_disk_state("/nonexistent", "myvm", "vda")

    # Verify no files were created.
    for name in ("_full_backups.json", "_dependencies.json", "_target_state.json"):
        assert not (tmp_path / name).exists(), f"No {name} should be created for unknown target"


def test_reset_target_disk_state_atomic(tmp_path: Path) -> None:
    """Crash during os.replace leaves all target state files unchanged.

    Populate _full_backups.json, _dependencies.json, and _target_state.json
    with data for (myvm, vda).  Mock os.replace to raise OSError, then call
    reset_target_disk_state.  All three files must remain intact with their
    original content.
    """
    manager = JsonStateManager(state_dir=tmp_path)
    target = "/mnt/backup/shared"

    full_vda = "myvm.FULL.20250101T120000_vda_a1b2c3"
    ts_vda = datetime(2025, 1, 1, 12, 0, 0)

    manager.record_full_backup(target, full_vda, ts_vda, "vda")
    manager.record_incremental_dependency(target, "incr-vda-001", full_vda)
    manager.set_last_backup_allocation(target, "vda", 1000)

    # ── pre-assertions ───────────────────────────────────────────────
    fb_file = tmp_path / "_full_backups.json"
    dep_file = tmp_path / "_dependencies.json"
    ts_file = tmp_path / "_target_state.json"

    assert fb_file.exists()
    assert dep_file.exists()
    assert ts_file.exists()

    with open(fb_file, encoding="utf-8") as fh:
        original_fb = json.load(fh)
    with open(dep_file, encoding="utf-8") as fh:
        original_dep = json.load(fh)
    with open(ts_file, encoding="utf-8") as fh:
        original_ts = json.load(fh)

    # ── simulate crash during os.replace ─────────────────────────────
    with (
        patch(
            "qsnap.state.json_manager.os.replace",
            side_effect=OSError("simulated crash during rename"),
        ),
        pytest.raises(OSError, match="simulated crash"),
    ):
        manager.reset_target_disk_state(target, "myvm", "vda")

    # ── all files must be intact ─────────────────────────────────────
    with open(fb_file, encoding="utf-8") as fh:
        assert json.load(fh) == original_fb, "_full_backups.json must be unchanged after crash"
    with open(dep_file, encoding="utf-8") as fh:
        assert json.load(fh) == original_dep, "_dependencies.json must be unchanged after crash"
    with open(ts_file, encoding="utf-8") as fh:
        assert json.load(fh) == original_ts, "_target_state.json must be unchanged after crash"

    # Re-reading through the manager must yield original values.
    backups = manager.get_full_backups(target)
    assert len(backups) == 1
    assert backups[0].name == full_vda


# ── legacy FULL name deps cleanup regression (design D4) ─────────────────


def test_reset_target_disk_state_removes_deps_of_legacy_full_names(
    tmp_path: Path,
) -> None:
    """Legacy FULL name without parseable disk segment: deps removed via stored disk.

    Create a FULL backup entry whose name ``myvm.FULL.20250713`` has no
    parseable disk segment (parse_disk_from_snapshot_name returns None)
    but whose stored ``disk`` field is ``"vda"``.  Add dependency entries
    under that FULL key.  Also add a healthy FULL
    ``myvm.FULL.20250714T090000_vdb_b2c3d4.qcow2`` (with parseable
    ``vdb``) and its own deps.

    After ``reset_target_disk_state(target, "myvm", "vda")``, the
    legacy-named FULL and its deps MUST be removed.  The vdb FULL and its
    deps MUST survive — even though their dep key name differs from the
    stored FULL name (stem vs .qcow2), the fallback name-parsing correctly
    identifies them as ``vdb``.
    """
    target = "/mnt/backup/shared"
    legacy_full_name = "myvm.FULL.20250713"
    vdb_full_name_stem = "myvm.FULL.20250714T090000_vdb_b2c3d4"
    vdb_full_name_qcow2 = f"{vdb_full_name_stem}.qcow2"

    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)

    # ── write _full_backups.json directly ────────────────────────────
    # Legacy vda entry: name has NO parseable disk, but stored disk="vda".
    # Healthy vdb entry: name contains parseable _vdb_ segment.
    fb_data = {
        target: [
            {
                "name": legacy_full_name,
                "path": f"{target}/{legacy_full_name}",
                "timestamp": "2025-07-13T00:00:00",
                "disk": "vda",
            },
            {
                "name": vdb_full_name_qcow2,
                "path": f"{target}/{vdb_full_name_qcow2}",
                "timestamp": "2025-07-14T09:00:00",
                "disk": "vdb",
            },
        ],
    }
    (state_dir / "_full_backups.json").write_text(json.dumps(fb_data), encoding="utf-8")

    # ── write _dependencies.json directly ────────────────────────────
    # Dep keys are stem-form (as stored by record_incremental_dependency).
    dep_data = {
        target: {
            legacy_full_name: ["incr-legacy-001"],
            vdb_full_name_stem: ["incr-vdb-001"],
        },
    }
    (state_dir / "_dependencies.json").write_text(json.dumps(dep_data), encoding="utf-8")

    # ── write _target_state.json ─────────────────────────────────────
    ts_data = {
        target: {
            "last_backup_allocation": {"vda": 1000, "vdb": 2000},
        },
    }
    (state_dir / "_target_state.json").write_text(json.dumps(ts_data), encoding="utf-8")

    manager = JsonStateManager(state_dir=state_dir)

    # ── pre-assertions ───────────────────────────────────────────────
    backups = manager.get_full_backups(target)
    assert len(backups) == 2

    deps_legacy = manager.get_incremental_dependencies(target, legacy_full_name)
    assert deps_legacy == ["incr-legacy-001"]

    deps_vdb = manager.get_incremental_dependencies(target, vdb_full_name_stem)
    assert deps_vdb == ["incr-vdb-001"]

    assert manager.get_last_backup_allocation(target, "vda") == 1000
    assert manager.get_last_backup_allocation(target, "vdb") == 2000

    # ── reset (myvm, vda) ────────────────────────────────────────────
    manager.reset_target_disk_state(target, "myvm", "vda")

    # ── legacy vda FULL removed ──────────────────────────────────────
    backups_after = manager.get_full_backups(target)
    backup_names = {b.name for b in backups_after}
    assert legacy_full_name not in backup_names, "Legacy-named vda FULL must be removed"
    assert vdb_full_name_qcow2 in backup_names, "vdb FULL must be preserved"
    assert len(backups_after) == 1

    # ── legacy deps removed (via full_disk_by_name lookup) ───────────
    assert manager.get_incremental_dependencies(target, legacy_full_name) == [], (
        "Legacy FULL deps must be empty after reset — "
        "disk resolved from stored field, not parsed from name"
    )

    # ── vdb deps preserved (fallback name-parsing returns "vdb") ────
    assert manager.get_incremental_dependencies(target, vdb_full_name_stem) == ["incr-vdb-001"], (
        "vdb FULL deps must be preserved"
    )

    # ── backup allocation cleared for vda, preserved for vdb ─────────
    assert manager.get_last_backup_allocation(target, "vda") is None
    assert manager.get_last_backup_allocation(target, "vdb") == 2000


# ── State migration: legacy records without disk field ─────────────────


def test_snapshot_migration_no_disk_vdb_from_name(tmp_path: Path) -> None:
    """Legacy snapshot record lacking ``disk`` key recovers disk from name.

    A snapshot named ``testvm.20250101T000000_vdb_a1b2c3.qcow2`` lacking
    a ``disk`` field should load with ``disk="vdb"`` after migration.
    """
    state_file = tmp_path / "testvm.json"
    state_file.write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "name": "testvm.20250101T000000_vdb_a1b2c3.qcow2",
                        "path": "/snaps/testvm.20250101T000000_vdb_a1b2c3.qcow2",
                        "timestamp": "2025-01-01T00:00:00",
                        "allocation": 1024,
                        # No "disk" key — legacy.
                    },
                ],
                "last_allocation": {"vdb": 4096},
                "deferred_operations": [],
            }
        ),
        encoding="utf-8",
    )

    manager = JsonStateManager(state_dir=tmp_path)
    snapshots = manager.get_snapshots("testvm")

    assert len(snapshots) == 1
    assert snapshots[0].name == "testvm.20250101T000000_vdb_a1b2c3.qcow2"
    assert snapshots[0].disk == "vdb", (
        f"Expected disk='vdb' recovered from name, got {snapshots[0].disk!r}"
    )


def test_snapshot_migration_no_disk_fallback_vda(tmp_path: Path) -> None:
    """Legacy snapshot record with unparseable name falls back to disk='vda'.

    A snapshot named ``unparseable.qcow2`` (no timestamp/disk embedded)
    lacking a ``disk`` field should load with ``disk="vda"`` (the
    ``_LEGACY_FALLBACK_DISK`` constant).
    """
    state_file = tmp_path / "testvm.json"
    state_file.write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "name": "unparseable.qcow2",
                        "path": "/snaps/unparseable.qcow2",
                        "timestamp": "2025-01-01T00:00:00",
                        "allocation": 1024,
                        # No "disk" key — legacy, name not parseable.
                    },
                ],
                "last_allocation": {"vda": 4096},
                "deferred_operations": [],
            }
        ),
        encoding="utf-8",
    )

    manager = JsonStateManager(state_dir=tmp_path)
    snapshots = manager.get_snapshots("testvm")

    assert len(snapshots) == 1
    assert snapshots[0].name == "unparseable.qcow2"
    assert snapshots[0].disk == "vda", f"Expected fallback disk='vda', got {snapshots[0].disk!r}"


def test_deferred_migration_no_disk_recovered_from_first_snapshot(
    tmp_path: Path,
) -> None:
    """Legacy deferred_operations record lacking ``disk`` key recovers disk
    from the first snapshot name.

    The entry has snapshots ``["testvm.20250101T000000_vdc_aaa111.qcow2"]``
    but no ``disk`` field.  After migration, ``disk`` should be ``"vdc"``.
    """
    state_file = tmp_path / "testvm.json"
    state_file.write_text(
        json.dumps(
            {
                "snapshots": [],
                "last_allocation": {},
                "deferred_operations": [
                    {
                        "snapshots": ["testvm.20250101T000000_vdc_aaa111.qcow2"],
                        "reason": "apparmor",
                        "since": "2025-01-01T00:00:00",
                        # No "disk" key — legacy.
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    manager = JsonStateManager(state_dir=tmp_path)
    ops = manager.get_deferred_operations("testvm")

    assert len(ops) == 1
    assert ops[0].snapshots == ["testvm.20250101T000000_vdc_aaa111.qcow2"]
    assert ops[0].reason == "apparmor"
    assert ops[0].disk == "vdc", (
        f"Expected disk='vdc' recovered from first snapshot name, got {ops[0].disk!r}"
    )


def test_deferred_migration_no_disk_unparseable_fallback_vda(
    tmp_path: Path,
) -> None:
    """Legacy deferred_operations record with no ``disk`` key and
    unparseable snapshot names falls back to ``disk="vda"``.
    """
    state_file = tmp_path / "testvm.json"
    state_file.write_text(
        json.dumps(
            {
                "snapshots": [],
                "last_allocation": {},
                "deferred_operations": [
                    {
                        "snapshots": ["unparseable.qcow2"],
                        "reason": "selinux",
                        "since": "2025-01-01T00:00:00",
                        # No "disk" key — legacy, name not parseable.
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    manager = JsonStateManager(state_dir=tmp_path)
    ops = manager.get_deferred_operations("testvm")

    assert len(ops) == 1
    assert ops[0].disk == "vda", f"Expected fallback disk='vda', got {ops[0].disk!r}"


def test_deferred_migration_no_disk_empty_snapshots_fallback_vda(
    tmp_path: Path,
) -> None:
    """Legacy deferred_operations record with no ``disk`` key and empty
    snapshots list falls back to ``disk="vda"``.
    """
    state_file = tmp_path / "testvm.json"
    state_file.write_text(
        json.dumps(
            {
                "snapshots": [],
                "last_allocation": {},
                "deferred_operations": [
                    {
                        "snapshots": [],
                        "reason": "vm_running",
                        "since": "2025-01-01T00:00:00",
                        # No "disk" key — legacy, empty snapshots.
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    manager = JsonStateManager(state_dir=tmp_path)
    ops = manager.get_deferred_operations("testvm")

    assert len(ops) == 1
    assert ops[0].disk == "vda", (
        f"Expected fallback disk='vda' for empty snapshots, got {ops[0].disk!r}"
    )


def test_full_backup_migration_no_disk_vdb_from_name(tmp_path: Path) -> None:
    """Legacy full backup entry lacking ``disk`` key recovers disk from name.

    A FULL backup named ``vm.20250101T000000_vdb_aaa111.qcow2`` with no
    ``disk`` field loads with ``disk="vdb"`` after migration.
    """
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)
    full_backups_file = state_dir / "_full_backups.json"
    full_backups_file.write_text(
        json.dumps(
            {
                "/mnt/backup/testvm": [
                    {
                        "name": "vm.20250101T000000_vdb_aaa111.qcow2",
                        "path": "/mnt/backup/testvm/vm.20250101T000000_vdb_aaa111.qcow2",
                        "timestamp": "2025-01-01T00:00:00",
                        # No "disk" key — legacy.
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    manager = JsonStateManager(state_dir=state_dir)
    backups = manager.get_full_backups("/mnt/backup/testvm")

    assert len(backups) == 1
    assert backups[0].name == "vm.20250101T000000_vdb_aaa111.qcow2"
    assert backups[0].disk == "vdb", (
        f"Expected disk='vdb' recovered from name, got {backups[0].disk!r}"
    )


def test_full_backup_migration_no_disk_unparseable_fallback_vda(
    tmp_path: Path,
) -> None:
    """Legacy full backup entry with unparseable name falls back to ``disk="vda"``."""
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)
    full_backups_file = state_dir / "_full_backups.json"
    full_backups_file.write_text(
        json.dumps(
            {
                "/mnt/backup/testvm": [
                    {
                        "name": "old-backup-2024",
                        "path": "/mnt/backup/testvm/old-backup-2024",
                        "timestamp": "2024-01-01T00:00:00",
                        # No "disk" key — legacy, name not parseable.
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    manager = JsonStateManager(state_dir=state_dir)
    backups = manager.get_full_backups("/mnt/backup/testvm")

    assert len(backups) == 1
    assert backups[0].disk == "vda", f"Expected fallback disk='vda', got {backups[0].disk!r}"
