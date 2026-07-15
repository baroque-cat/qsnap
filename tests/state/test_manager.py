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
    assert not state_file.exists(), (
        "Original state file should be renamed away after corruption"
    )

    # A broken file should exist.
    broken_files = list(tmp_path.glob("testvm.json.broken.*"))
    assert len(broken_files) == 1, (
        f"Expected exactly one broken file, got {len(broken_files)}: "
        f"{[f.name for f in broken_files]}"
    )
    assert broken_files[0].name.startswith("testvm.json.broken."), (
        f"Broken file name should start with 'testvm.json.broken.', "
        f"got {broken_files[0].name}"
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
        assert not backup.exists(), (
            f"Backup file {backup.name} should NOT exist on first save"
        )

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
    assert not backup3.exists(), (
        "testvm.json.3 should NOT exist — backup count limit is 2"
    )


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
            f"Backup file {backup.name} should NOT exist when "
            f"state_backup_count=0"
        )
