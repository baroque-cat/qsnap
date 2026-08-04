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
    return SnapshotInfo(
        name=name,
        path=Path(path),
        timestamp=ts,
        allocation=allocation,
        disk="vda"
    )


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
    assert (
        mgr.get_incremental_dependencies("/nonexistent/target", "any") == []
    )
    assert mgr.get_last_backup_allocation("/nonexistent/target", "vda") is None
