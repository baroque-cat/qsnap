"""Unit tests for JsonStateManager — concrete IStateManager implementation.

Tests verify allocation read/write, missing-state handling, snapshot
recording/listing with timestamp sorting, and the atomic write pattern
(crash-safety).  No source code is modified.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.models.results import DeferredBlockcommit, FullBackupInfo, SnapshotInfo
from qsnap.state.json_manager import JsonStateManager

# ── helpers ──────────────────────────────────────────────────────────────


def _make_snapshot(
    name: str,
    ts: datetime,
    allocation: int = 1024,
    path: str = "/tmp/snap.qcow2",
    content_hash: str | None = None,
) -> SnapshotInfo:
    return SnapshotInfo(
        name=name,
        path=Path(path),
        timestamp=ts,
        allocation=allocation,
        content_hash=content_hash,
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
    """DeferredBlockcommit is a frozen dataclass with snapshots, reason, since fields."""
    item = DeferredBlockcommit(
        snapshots=["snap1.qcow2"],
        reason="apparmor",
        since=datetime(2024, 1, 1, 12, 0, 0),
    )

    # Fields exist and hold correct values.
    assert item.snapshots == ["snap1.qcow2"]
    assert item.reason == "apparmor"
    assert item.since == datetime(2024, 1, 1, 12, 0, 0)

    # Frozen: mutation raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.reason = "selinux"  # type: ignore[misc]


# ── content_hash persistence tests ───────────────────────────────────────


def test_record_snapshot_with_content_hash_restored(tmp_path: Path) -> None:
    """record_snapshot stores content_hash; get_snapshots returns it preserved."""
    manager = JsonStateManager(state_dir=tmp_path)

    snap = _make_snapshot(
        "snap_hash",
        datetime(2024, 1, 1, 12, 0, 0),
        content_hash="abc123",
    )
    manager.record_snapshot("testvm", snap)

    snapshots = manager.get_snapshots("testvm")

    assert len(snapshots) == 1
    assert snapshots[0].content_hash == "abc123"


def test_snapshot_content_hash_persists_across_runs(tmp_path: Path) -> None:
    """content_hash survives across JsonStateManager instances (disk reload)."""
    manager1 = JsonStateManager(state_dir=tmp_path)
    snap = _make_snapshot(
        "snap_persist",
        datetime(2024, 1, 1, 12, 0, 0),
        content_hash="deadbeef",
    )
    manager1.record_snapshot("testvm", snap)

    # New manager instance, same state directory — must load persisted data.
    manager2 = JsonStateManager(state_dir=tmp_path)
    snapshots = manager2.get_snapshots("testvm")

    assert len(snapshots) == 1
    assert snapshots[0].content_hash == "deadbeef"


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
