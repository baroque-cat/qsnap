"""Mock verification: InMemoryStateManager implements IStateManager."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.interfaces.state import IStateManager
from qsnap.models.results import SnapshotInfo
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
