"""Tests for InMemoryStateManager — reset_vm_state and reset_target_state."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from qsnap.models.results import SnapshotInfo
from tests.mocks.mock_state import InMemoryStateManager


def _make_snapshot(
    name: str,
    ts: datetime,
    allocation: int = 1024,
    path: str = "/tmp/snap.qcow2",
) -> SnapshotInfo:
    return SnapshotInfo(name=name, path=Path(path), timestamp=ts, allocation=allocation, disk="vda")


# ── reset_vm_state tests ───────────────────────────────────────────────


@pytest.mark.mock
def test_inmemory_reset_vm_state_clears_snapshots() -> None:
    """After reset_vm_state, get_snapshots returns an empty list."""
    mgr = InMemoryStateManager()

    snap1 = _make_snapshot("snap1", datetime(2024, 1, 1, 11, 0, 0))
    snap2 = _make_snapshot("snap2", datetime(2024, 1, 1, 12, 0, 0))
    mgr.record_snapshot("testvm", snap1)
    mgr.record_snapshot("testvm", snap2)

    assert len(mgr.get_snapshots("testvm")) == 2

    mgr.reset_vm_state("testvm")

    assert mgr.get_snapshots("testvm") == []


@pytest.mark.mock
def test_inmemory_reset_vm_state_clears_last_allocation() -> None:
    """After reset_vm_state, get_last_allocation returns None."""
    mgr = InMemoryStateManager()

    mgr.set_last_allocation("testvm", "vda", 4096)
    assert mgr.get_last_allocation("testvm", "vda") == 4096

    mgr.reset_vm_state("testvm")

    assert mgr.get_last_allocation("testvm", "vda") is None


@pytest.mark.mock
def test_inmemory_reset_vm_state_clears_deferred_operations() -> None:
    """After reset_vm_state, get_deferred_operations returns an empty list."""
    mgr = InMemoryStateManager()

    mgr.add_deferred_blockcommit("testvm", "vda", ["snap1.qcow2"], "apparmor")
    mgr.add_deferred_blockcommit("testvm", "vda", ["snap2.qcow2"], "vm_running")

    assert len(mgr.get_deferred_operations("testvm")) == 2

    mgr.reset_vm_state("testvm")

    assert mgr.get_deferred_operations("testvm") == []


@pytest.mark.mock
def test_inmemory_reset_vm_state_nonexistent_vm_no_error() -> None:
    """resetting a VM that has no recorded state does not raise."""
    mgr = InMemoryStateManager()

    # vm with no state — should be a no-op.
    mgr.reset_vm_state("nonexistent")

    # No error was raised — state remains empty.
    assert mgr.get_snapshots("nonexistent") == []
    assert mgr.get_last_allocation("nonexistent", "vda") is None
    assert mgr.get_deferred_operations("nonexistent") == []


# ── reset_target_state tests ───────────────────────────────────────────


@pytest.mark.mock
def test_inmemory_reset_target_state_removes_from_full_backups() -> None:
    """After reset_target_state, get_full_backups returns an empty list."""
    mgr = InMemoryStateManager()

    target = "/mnt/backup/testvm"
    mgr.record_full_backup(target, "full-2024-01-01", datetime(2024, 1, 1, 12, 0, 0), "vda")
    mgr.record_full_backup(target, "full-2024-02-01", datetime(2024, 2, 1, 12, 0, 0), "vda")

    assert len(mgr.get_full_backups(target)) == 2

    mgr.reset_target_state(target)

    assert mgr.get_full_backups(target) == []


@pytest.mark.mock
def test_inmemory_reset_target_state_removes_from_dependencies() -> None:
    """After reset_target_state, get_incremental_dependencies returns an empty list."""
    mgr = InMemoryStateManager()

    target = "/mnt/backup/testvm"
    mgr.record_incremental_dependency(target, "incr-001", "full-2024-01-01")
    mgr.record_incremental_dependency(target, "incr-002", "full-2024-01-01")

    assert len(mgr.get_incremental_dependencies(target, "full-2024-01-01")) == 2

    mgr.reset_target_state(target)

    assert mgr.get_incremental_dependencies(target, "full-2024-01-01") == []


@pytest.mark.mock
def test_inmemory_reset_target_state_removes_from_target_state() -> None:
    """After reset_target_state, get_last_backup_allocation returns None."""
    mgr = InMemoryStateManager()

    target = "/mnt/backup/testvm"
    mgr.set_last_backup_allocation(target, "vda", 12345)

    assert mgr.get_last_backup_allocation(target, "vda") == 12345

    mgr.reset_target_state(target)

    assert mgr.get_last_backup_allocation(target, "vda") is None


@pytest.mark.mock
def test_inmemory_reset_target_state_nonexistent_target_no_error() -> None:
    """resetting a target that has no recorded state does not raise."""
    mgr = InMemoryStateManager()

    # Target with no state — should be a no-op.
    mgr.reset_target_state("/nonexistent/target")

    # No error was raised — state remains empty.
    assert mgr.get_full_backups("/nonexistent/target") == []
    assert mgr.get_incremental_dependencies("/nonexistent/target", "any") == []
    assert mgr.get_last_backup_allocation("/nonexistent/target", "vda") is None


# ── FULL backup name normalization (mock parity, design D1/D4) ──────────


@pytest.mark.mock
def test_inmemory_record_full_backup_normalizes_stem() -> None:
    """record_full_backup stores stem names in extended (.qcow2) form."""
    mgr = InMemoryStateManager()
    target = "/mnt/backup/testvm"
    stem = "testvm.FULL.20260101_vda_a1b2c3"

    mgr.record_full_backup(
        target,
        stem,
        datetime(2026, 1, 1, 12, 0, 0),
        "vda",
    )

    recorded = mgr.get_last_full_backup(target)
    assert recorded is not None
    assert recorded.name == stem + ".qcow2"


@pytest.mark.mock
def test_inmemory_record_full_backup_derives_extended_path() -> None:
    """record_full_backup derives path from the normalized (.qcow2) name."""
    mgr = InMemoryStateManager()
    target = "/mnt/backup/testvm"
    stem = "testvm.FULL.20260101_vda_a1b2c3"

    mgr.record_full_backup(
        target,
        stem,
        datetime(2026, 1, 1, 12, 0, 0),
        "vda",
    )

    recorded = mgr.get_last_full_backup(target)
    assert recorded is not None
    assert recorded.path == Path(target) / (stem + ".qcow2")


@pytest.mark.mock
def test_inmemory_remove_full_backup_accepts_stem_lookup() -> None:
    """remove_full_backup with a stem lookup removes the extended record."""
    mgr = InMemoryStateManager()
    target = "/mnt/backup/testvm"
    extended = "testvm.FULL.20260101_vda_a1b2c3.qcow2"

    mgr.record_full_backup(
        target,
        extended,
        datetime(2026, 1, 1, 12, 0, 0),
        "vda",
    )
    assert len(mgr.get_full_backups(target)) == 1

    removed = mgr.remove_full_backup(target, "testvm.FULL.20260101_vda_a1b2c3")

    assert removed is True
    assert mgr.get_full_backups(target) == []


@pytest.mark.mock
def test_inmemory_remove_full_backup_non_matching_returns_false() -> None:
    """remove_full_backup for an unrecorded name returns False, keeps others."""
    mgr = InMemoryStateManager()
    target = "/mnt/backup/testvm"

    mgr.record_full_backup(
        target,
        "testvm.FULL.20260101_vda_a1b2c3",
        datetime(2026, 1, 1, 12, 0, 0),
        "vda",
    )

    assert mgr.remove_full_backup(target, "testvm.FULL.20260101_vdb_ffff00") is False
    assert len(mgr.get_full_backups(target)) == 1


# ── commit intent journal (harden-blockcommit-races) ─────────────────────
# Mock parity for the JsonStateManager intent-journal tests in
# tests/state/test_manager.py (commit-intent-journal spec scenarios):
# set/read/clear, upsert on the same disk, per-disk independence, and
# reset interactions.


def test_inmemory_commit_intent_set_get_clear() -> None:
    """set_commit_in_progress → get_commit_in_progress → clear round-trips."""
    mgr = InMemoryStateManager()

    # Nothing recorded yet.
    assert mgr.get_commit_in_progress("testvm") == []

    mgr.set_commit_in_progress(
        "testvm", "vda", ["snap1.qcow2", "snap2.qcow2"], "/base/testvm.qcow2", "20260808T160000"
    )

    intents = mgr.get_commit_in_progress("testvm")
    assert len(intents) == 1
    intent = intents[0]
    assert intent.disk == "vda"
    assert intent.snapshots == ["snap1.qcow2", "snap2.qcow2"]
    assert intent.base == "/base/testvm.qcow2"
    assert intent.started_ts == "20260808T160000"

    # Clearing removes the record; other VMs are unaffected.
    mgr.clear_commit_in_progress("testvm", "vda")
    assert mgr.get_commit_in_progress("testvm") == []


def test_inmemory_commit_intent_upsert_same_disk() -> None:
    """A second set_commit_in_progress for the same disk replaces the record."""
    mgr = InMemoryStateManager()

    mgr.set_commit_in_progress(
        "testvm", "vda", ["snap1.qcow2"], "/base/testvm.qcow2", "20260808T160000"
    )
    mgr.set_commit_in_progress(
        "testvm", "vda", ["snap1.qcow2", "snap2.qcow2"], "/base/testvm.qcow2", "20260808T170000"
    )

    intents = mgr.get_commit_in_progress("testvm")
    assert len(intents) == 1, f"Upsert must keep one record, got {len(intents)}"
    assert intents[0].snapshots == ["snap1.qcow2", "snap2.qcow2"]
    assert intents[0].started_ts == "20260808T170000"


def test_inmemory_commit_intent_multiple_disks_independent() -> None:
    """Different disks hold independent intent records for the same VM."""
    mgr = InMemoryStateManager()

    mgr.set_commit_in_progress(
        "testvm", "vda", ["snap1.qcow2"], "/base/testvm.qcow2", "20260808T160000"
    )
    mgr.set_commit_in_progress(
        "testvm", "vdb", ["snap1-vdb.qcow2"], "/base/testvm-vdb.qcow2", "20260808T160000"
    )

    intents = mgr.get_commit_in_progress("testvm")
    assert len(intents) == 2
    assert {i.disk for i in intents} == {"vda", "vdb"}

    # Clearing one disk leaves the other intact.
    mgr.clear_commit_in_progress("testvm", "vda")
    remaining = mgr.get_commit_in_progress("testvm")
    assert len(remaining) == 1
    assert remaining[0].disk == "vdb"


def test_inmemory_commit_intent_clear_noop_when_absent() -> None:
    """clear_commit_in_progress for an unrecorded disk is a no-op."""
    mgr = InMemoryStateManager()

    mgr.set_commit_in_progress(
        "testvm", "vda", ["snap1.qcow2"], "/base/testvm.qcow2", "20260808T160000"
    )

    mgr.clear_commit_in_progress("testvm", "vdz")
    intents = mgr.get_commit_in_progress("testvm")
    assert len(intents) == 1
    assert intents[0].disk == "vda"


def test_inmemory_reset_vm_disk_state_clears_per_disk_intent() -> None:
    """reset_vm_disk_state removes the intent for the reset disk only."""
    mgr = InMemoryStateManager()

    mgr.set_commit_in_progress(
        "testvm", "vda", ["snap1.qcow2"], "/base/testvm.qcow2", "20260808T160000"
    )
    mgr.set_commit_in_progress(
        "testvm", "vdb", ["snap1-vdb.qcow2"], "/base/testvm-vdb.qcow2", "20260808T160000"
    )

    mgr.reset_vm_disk_state("testvm", "vda")

    remaining = mgr.get_commit_in_progress("testvm")
    assert len(remaining) == 1
    assert remaining[0].disk == "vdb"


def test_inmemory_reset_vm_state_clears_all_intents() -> None:
    """reset_vm_state clears every commit-intent record for the VM."""
    mgr = InMemoryStateManager()

    mgr.set_commit_in_progress(
        "testvm", "vda", ["snap1.qcow2"], "/base/testvm.qcow2", "20260808T160000"
    )
    mgr.set_commit_in_progress(
        "testvm", "vdb", ["snap1-vdb.qcow2"], "/base/testvm-vdb.qcow2", "20260808T160000"
    )
    mgr.set_commit_in_progress(
        "othervm", "vda", ["snap-other.qcow2"], "/base/othervm.qcow2", "20260808T160000"
    )

    mgr.reset_vm_state("testvm")

    assert mgr.get_commit_in_progress("testvm") == []
    # Other VMs' intents are untouched.
    other = mgr.get_commit_in_progress("othervm")
    assert len(other) == 1
    assert other[0].disk == "vda"
