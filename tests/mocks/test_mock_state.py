"""Mock verification: InMemoryStateManager implements IStateManager."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.interfaces.state import IStateManager
from qsnap.models.results import DeferredBlockcommit, SnapshotInfo
from tests.mocks.mock_state import InMemoryStateManager


def test_inmemory_state_is_istatemanager():
    """InMemoryStateManager passes isinstance against IStateManager and all
    basic state operations (set/get allocation, record/list snapshots) work."""
    state_manager = InMemoryStateManager()
    assert isinstance(state_manager, IStateManager)

    # Allocation: initially None, then set and get.
    assert state_manager.get_last_allocation("testvm") is None

    state_manager.set_last_allocation("testvm", 1048576)
    assert state_manager.get_last_allocation("testvm") == 1048576

    # Snapshots: initially empty, then record and retrieve.
    assert state_manager.get_snapshots("testvm") == []

    info = SnapshotInfo(
        name="testvm.20240101T000000",
        path=Path("/var/lib/libvirt/snapshots/testvm/testvm.20240101T000000.qcow2"),
        timestamp=datetime(2024, 1, 1, 0, 0, 0),
        allocation=65536,
    )
    state_manager.record_snapshot("testvm", info)

    snapshots = state_manager.get_snapshots("testvm")
    assert len(snapshots) == 1
    assert snapshots[0].name == info.name
    assert snapshots[0].allocation == info.allocation


def test_in_memory_state_manager_implements_deferred_operations():
    """InMemoryStateManager is still a valid IStateManager after the
    deferred blockcommit methods were added to the ABC."""
    state_manager = InMemoryStateManager()
    assert isinstance(state_manager, IStateManager)

    # The deferred-operation methods must exist and be callable.
    assert callable(state_manager.add_deferred_blockcommit)
    assert callable(state_manager.get_deferred_operations)
    assert callable(state_manager.clear_deferred_operations)

    # A VM with no deferred operations returns an empty list (never None).
    assert state_manager.get_deferred_operations("testvm") == []


def test_in_memory_state_manager_add_get_clear_deferred():
    """Round-trip: add_deferred_blockcommit → get returns it → clear → get
    returns [].
    """
    state_manager = InMemoryStateManager()

    # Initially empty.
    assert state_manager.get_deferred_operations("testvm") == []

    # Add a deferred blockcommit.
    state_manager.add_deferred_blockcommit(
        "testvm",
        snapshots=["snap1", "snap2"],
        reason="MAC blocks blockcommit while VM is running",
    )

    # Retrieve — should contain exactly one DeferredBlockcommit.
    deferred = state_manager.get_deferred_operations("testvm")
    assert len(deferred) == 1
    assert isinstance(deferred[0], DeferredBlockcommit)
    assert deferred[0].snapshots == ["snap1", "snap2"]
    assert deferred[0].reason == "MAC blocks blockcommit while VM is running"
    assert deferred[0].since is not None

    # Clear and verify empty.
    state_manager.clear_deferred_operations("testvm")
    assert state_manager.get_deferred_operations("testvm") == []
