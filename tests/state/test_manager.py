"""Unit tests for JsonStateManager — concrete IStateManager implementation.

Tests verify allocation read/write, missing-state handling, snapshot
recording/listing with timestamp sorting, and the atomic write pattern
(crash-safety).  No source code is modified.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.models.results import SnapshotInfo
from qsnap.state.json_manager import JsonStateManager

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
