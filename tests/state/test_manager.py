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

from qsnap.models.results import CommitIntent, DeferredBlockcommit, FullBackupInfo, SnapshotInfo
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


def test_atomic_write_pattern(tmp_path: Path, caplog) -> None:
    """Atomic write: no .tmp remains on success; crash leaves original intact.

    This covers the CRITICAL risk in test-plan.md line 134: a crash during
    the rename step must not corrupt the existing state file.

    Since the state-recovery change (design D3), ``_save`` catches
    ``OSError`` from the rename, logs a CRITICAL naming the state path and
    the OS error, and re-raises as ``RuntimeError`` so the per-VM handler in
    ``Core._run_pipeline`` contains the failure to one VM.
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

    # Mock os.replace to raise mid-operation (the rename step).  The save
    # surfaces as RuntimeError (not OSError) per design D3.
    with (
        caplog.at_level(logging.CRITICAL, logger="qsnap.state.json_manager"),
        patch(
            "qsnap.state.json_manager.os.replace",
            side_effect=OSError("simulated crash during rename"),
        ),
        pytest.raises(RuntimeError, match="State write failed for VM crashvm"),
    ):
        manager.set_last_allocation("crashvm", "vda", 999)

    # The CRITICAL log names the state path and the OS error.
    assert any(
        r.levelno == logging.CRITICAL
        and "crashvm.json" in r.message
        and "simulated crash during rename" in r.message
        for r in caplog.records
    ), "CRITICAL log must name the state path and the OS error"

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


# ── state write resilience on ENOSPC (design D3) ────────────────────────


def test_save_oserror_raises_runtime_error_critical(tmp_path: Path, caplog) -> None:
    """``_save`` ENOSPC → CRITICAL log naming path+errno, re-raised as RuntimeError.

    Spec: "ENOSPC in state directory does not crash the process" — the
    ``OSError`` is caught, a CRITICAL names the state path and the error,
    and a ``RuntimeError`` is raised so ``Core._run_pipeline``'s per-VM
    handler contains the failure (design D3).
    """
    manager = JsonStateManager(state_dir=tmp_path)
    manager.set_last_allocation("testvm", "vda", 4096)

    enospc = OSError(28, "No space left on device")

    with (
        caplog.at_level(logging.CRITICAL, logger="qsnap.state.json_manager"),
        patch("qsnap.state.json_manager.os.replace", side_effect=enospc),
        pytest.raises(RuntimeError, match="State write failed for VM testvm"),
    ):
        manager.set_last_allocation("testvm", "vda", 8192)

    # CRITICAL log names the state path and the errno text.
    assert any(
        r.levelno == logging.CRITICAL
        and "testvm.json" in r.message
        and "No space left on device" in r.message
        for r in caplog.records
    ), "CRITICAL log must name the state path and the OS error (errno)"

    # The original state file is untouched and still readable.
    assert manager.get_last_allocation("testvm", "vda") == 4096
    with open(tmp_path / "testvm.json", encoding="utf-8") as fh:
        assert json.load(fh).get("last_allocation", {}).get("vda") == 4096


def test_save_oserror_contained_per_vm(tmp_path: Path) -> None:
    """A state-write failure is contained: other VMs' state still saves.

    Spec: "ENOSPC during save contained to one VM" — the failing save raises
    ``RuntimeError`` (which Core's per-VM try/except records as
    ``VMRunResult(success=False)`` for that VM only); vm2 and later VMs are
    still processed and writable.
    """
    manager = JsonStateManager(state_dir=tmp_path)
    manager.set_last_allocation("vm1", "vda", 100)
    manager.set_last_allocation("vm2", "vda", 200)

    # vm1's save fails with ENOSPC → RuntimeError (contained failure).
    with (
        patch(
            "qsnap.state.json_manager.os.replace",
            side_effect=OSError(28, "No space left on device"),
        ),
        pytest.raises(RuntimeError, match="State write failed for VM vm1"),
    ):
        manager.set_last_allocation("vm1", "vda", 999)

    # vm1's state file remains intact with its pre-failure value.
    assert manager.get_last_allocation("vm1", "vda") == 100
    with open(tmp_path / "vm1.json", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data.get("last_allocation", {}).get("vda") == 100

    # vm2 is still processed normally — the failure did not break the
    # manager or the state directory.
    manager.set_last_allocation("vm2", "vda", 300)
    assert manager.get_last_allocation("vm2", "vda") == 300


def test_save_partial_tmp_does_not_corrupt_state(tmp_path: Path, caplog) -> None:
    """A partial temp-file write never corrupts the existing state file.

    Spec: "Partial temp file does not corrupt existing state" — when the
    temp-file write fails before ``os.replace``, the existing ``{vm}.json``
    remains untouched and valid; the save surfaces as RuntimeError.
    """
    manager = JsonStateManager(state_dir=tmp_path)
    manager.set_last_allocation("testvm", "vda", 4096)
    manager.set_last_allocation("testvm", "vda", 8192)

    # Fail the tmp-file write itself (json.dump raises ENOSPC mid-write).
    with (
        caplog.at_level(logging.CRITICAL, logger="qsnap.state.json_manager"),
        patch(
            "qsnap.state.json_manager.json.dump",
            side_effect=OSError(28, "No space left on device"),
        ),
        pytest.raises(RuntimeError, match="State write failed for VM testvm"),
    ):
        manager.set_last_allocation("testvm", "vda", 999)

    # CRITICAL log names the path and the error.
    assert any(
        r.levelno == logging.CRITICAL
        and "testvm.json" in r.message
        and "No space left on device" in r.message
        for r in caplog.records
    )

    # Existing {vm}.json is untouched — valid JSON with the previous data.
    state_file = tmp_path / "testvm.json"
    assert state_file.exists(), "existing state file must survive a failed save"
    with open(state_file, encoding="utf-8") as fh:
        data = json.load(fh)  # must parse without error
    assert data.get("last_allocation", {}).get("vda") == 8192, (
        "state file content must be unchanged after a failed save"
    )

    # Reads through the manager return the pre-failure value.
    assert manager.get_last_allocation("testvm", "vda") == 8192


def test_save_failed_write_does_not_rotate_backups(tmp_path: Path) -> None:
    """A failed save must not rotate the state backup chain.

    Spec (state-recovery, "Partial temp file does not corrupt existing
    state"): "no rotation has been performed for this failed save".  When
    the temp-file write fails, the ``.1``/``.2`` backup artifacts must be
    exactly as they were before the failed save.

    NOTE: this encodes the spec clause verbatim.  The current ``_save``
    implementation rotates BEFORE the temp-file write, so a failed write
    still shifts the backup chain — this test documents that gap (see QA
    report).
    """
    manager = JsonStateManager(state_dir=tmp_path, state_backup_count=2)
    manager.set_last_allocation("testvm", "vda", 4096)
    manager.set_last_allocation("testvm", "vda", 8192)

    backup_files_before = sorted(p.name for p in tmp_path.glob("testvm.json.*"))

    # Fail the tmp-file write (json.dump raises ENOSPC mid-write).
    with (
        patch(
            "qsnap.state.json_manager.json.dump",
            side_effect=OSError(28, "No space left on device"),
        ),
        pytest.raises(RuntimeError, match="State write failed for VM testvm"),
    ):
        manager.set_last_allocation("testvm", "vda", 999)

    backup_files_after = sorted(p.name for p in tmp_path.glob("testvm.json.*"))
    assert backup_files_after == backup_files_before, (
        f"failed save must not rotate backups: {backup_files_before} -> {backup_files_after}"
    )


# ── deferred operations tests ────────────────────────────────────────────


def test_add_and_retrieve_deferred_blockcommit(tmp_path: Path) -> None:
    """add_deferred_blockcommit stores entry; get_deferred_operations returns it with correct fields.

    The deferred queue is per-disk (deferred-operations spec): the entry
    must carry the disk it was queued for, and ``last_warned_at`` starts
    as ``None`` for a fresh entry.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "apparmor")

    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 1

    op = ops[0]
    assert isinstance(op, DeferredBlockcommit)
    assert op.snapshots == ["snap1.qcow2"]
    assert op.reason == "apparmor"
    assert op.disk == "vda"
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
    assert op.disk == "vda"
    assert isinstance(op.since, datetime)
    # New entries have no warning timestamp yet.
    assert op.last_warned_at is None


def test_add_deferred_blockcommit_active_layer_reason(tmp_path: Path) -> None:
    """add_deferred_blockcommit stores entry with "active_layer" reason."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", "vdb", ["snap3.qcow2"], "active_layer")

    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 1

    op = ops[0]
    assert isinstance(op, DeferredBlockcommit)
    assert op.snapshots == ["snap3.qcow2"]
    assert op.reason == "active_layer"
    assert op.disk == "vdb"
    assert isinstance(op.since, datetime)
    # New entries have no warning timestamp yet.
    assert op.last_warned_at is None


def test_add_deferred_blockcommit_blockjob_active_reason(tmp_path: Path) -> None:
    """add_deferred_blockcommit stores entry with "blockjob_active" reason.

    When a foreign/in-flight block job is detected on a disk (probe
    before commit or stale-intent recovery), the commit is deferred with
    reason ``"blockjob_active"`` (blockjob-protocol spec).  The reason
    must round-trip through persistent state per disk.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "blockjob_active")

    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 1

    op = ops[0]
    assert isinstance(op, DeferredBlockcommit)
    assert op.snapshots == ["snap1.qcow2"]
    assert op.reason == "blockjob_active"
    assert op.disk == "vda"
    assert isinstance(op.since, datetime)
    assert op.last_warned_at is None

    # The reason survives a full state round-trip (re-load).
    manager2 = JsonStateManager(state_dir=tmp_path)
    ops2 = manager2.get_deferred_operations("vm1")
    assert len(ops2) == 1
    assert ops2[0].reason == "blockjob_active"
    assert ops2[0].disk == "vda"


def test_add_deferred_blockcommit_vm_state_unknown_reason(tmp_path: Path) -> None:
    """add_deferred_blockcommit stores entry with "vm_state_unknown" reason.

    When the fail-closed offline race guard cannot re-check the VM state
    (e.g. the recheck probe fails), the commit is deferred with reason
    ``"vm_state_unknown"`` (fail-closed offline guard spec).  The reason
    must round-trip through persistent state per disk.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit(
        "vm1", "vda", ["snap1.qcow2", "snap2.qcow2"], "vm_state_unknown"
    )

    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 1

    op = ops[0]
    assert isinstance(op, DeferredBlockcommit)
    assert op.snapshots == ["snap1.qcow2", "snap2.qcow2"]
    assert op.reason == "vm_state_unknown"
    assert op.disk == "vda"
    assert isinstance(op.since, datetime)
    assert op.last_warned_at is None

    # The reason survives a full state round-trip (re-load).
    manager2 = JsonStateManager(state_dir=tmp_path)
    ops2 = manager2.get_deferred_operations("vm1")
    assert len(ops2) == 1
    assert ops2[0].reason == "vm_state_unknown"
    assert ops2[0].disk == "vda"


def test_multiple_disks_separate_deferred_entries(tmp_path: Path) -> None:
    """Multiple disks of one VM hold independent deferred entries.

    Each ``add_deferred_blockcommit`` call appends a separate entry
    carrying its own disk, so the queue is per-disk (deferred-operations
    spec scenario "Multiple disks can have separate deferred entries").
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "apparmor")
    manager.add_deferred_blockcommit("vm1", "vdb", ["snap2.qcow2"], "vm_running")

    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 2

    by_disk = {op.disk: op for op in ops}
    assert set(by_disk) == {"vda", "vdb"}
    assert by_disk["vda"].snapshots == ["snap1.qcow2"]
    assert by_disk["vda"].reason == "apparmor"
    assert by_disk["vdb"].snapshots == ["snap2.qcow2"]
    assert by_disk["vdb"].reason == "vm_running"

    # Both entries survive a state round-trip with their disks intact.
    manager2 = JsonStateManager(state_dir=tmp_path)
    ops2 = manager2.get_deferred_operations("vm1")
    assert len(ops2) == 2
    assert {op.disk for op in ops2} == {"vda", "vdb"}


def test_add_deferred_blockcommit_enospc_reason(tmp_path: Path) -> None:
    """add_deferred_blockcommit stores entry with "enospc" reason.

    Blockcommit failures classified as space errors (is_space_error) are
    deferred with reason="enospc" instead of aborting the VM (spec:
    deferred-operations scenario "Add deferred blockcommit with enospc
    reason"; design D4).  The reason must round-trip through persistent
    state.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "enospc")

    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 1

    op = ops[0]
    assert isinstance(op, DeferredBlockcommit)
    assert op.snapshots == ["snap1.qcow2"]
    assert op.reason == "enospc"
    assert op.disk == "vda"
    assert isinstance(op.since, datetime)
    # New entries have no warning timestamp yet.
    assert op.last_warned_at is None

    # The enospc reason must survive a full state round-trip (re-load).
    manager2 = JsonStateManager(state_dir=tmp_path)
    ops2 = manager2.get_deferred_operations("vm1")
    assert len(ops2) == 1
    assert ops2[0].reason == "enospc"
    assert ops2[0].disk == "vda"


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
    assert op.disk == "vda"
    assert isinstance(op.since, datetime)


def test_clear_deferred_operations(tmp_path: Path) -> None:
    """clear_deferred_operations removes all queued operations for a VM."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "apparmor")
    manager.add_deferred_blockcommit("vm1", "vdb", ["snap2.qcow2", "snap3.qcow2"], "selinux")

    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 2
    # Per-disk entries are tracked independently before the clear.
    assert {op.disk for op in ops} == {"vda", "vdb"}

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
    manager1.add_deferred_blockcommit("vm1", "vdb", ["snap2.qcow2"], "selinux")

    # New manager instance, same state directory — must load persisted data.
    manager2 = JsonStateManager(state_dir=tmp_path)
    ops = manager2.get_deferred_operations("vm1")

    assert len(ops) == 2
    assert ops[0].snapshots == ["snap1.qcow2"]
    assert ops[0].reason == "apparmor"
    assert ops[0].disk == "vda"
    assert isinstance(ops[0].since, datetime)
    assert ops[1].snapshots == ["snap2.qcow2"]
    assert ops[1].reason == "selinux"
    assert ops[1].disk == "vdb"
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
    """_deferred_to_dict / _dict_to_deferred preserve last_warned_at and disk."""
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
    assert d["disk"] == "vda"

    restored = JsonStateManager._dict_to_deferred(d)
    assert restored.last_warned_at == warned
    assert restored.disk == "vda"

    # The full file round-trip also preserves both fields.
    manager = JsonStateManager(state_dir=tmp_path)
    manager.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "apparmor")
    manager.update_deferred_warning("vm1", 0, warned)

    reloaded = JsonStateManager(state_dir=tmp_path)
    op = reloaded.get_deferred_operations("vm1")[0]
    assert op.disk == "vda"
    assert op.last_warned_at == warned


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
    # Legacy records without a disk field fall back to the legacy disk.
    assert restored.disk == "vda"

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
    assert ops[0].disk == "vda"


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


# ── commit intent journal tests ─────────────────────────────────────────


def test_commit_intent_set_get_clear(tmp_path: Path) -> None:
    """set_commit_in_progress stores one CommitIntent; clear removes it.

    commit-intent-journal scenario "Set, read, and clear an intent
    record": the returned record carries the exact fields written, and
    clearing the disk yields an empty list.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_commit_in_progress("vm1", "vda", ["s1"], "/data/img.qcow2", "20260812T150126")

    intents = manager.get_commit_in_progress("vm1")
    assert len(intents) == 1
    intent = intents[0]
    assert isinstance(intent, CommitIntent)
    assert intent.disk == "vda"
    assert intent.snapshots == ["s1"]
    assert intent.base == "/data/img.qcow2"
    assert intent.started_ts == "20260812T150126"

    manager.clear_commit_in_progress("vm1", "vda")
    assert manager.get_commit_in_progress("vm1") == []


def test_commit_intent_upsert_same_disk(tmp_path: Path) -> None:
    """A second set for the same disk replaces the record (at most one per disk).

    commit-intent-journal scenario "Upsert replaces the record for the
    same disk": the merged snapshot list holds the LATEST values, and
    exactly one record remains.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_commit_in_progress("vm1", "vda", ["s1"], "/data/img.qcow2", "20260812T150126")
    manager.set_commit_in_progress("vm1", "vda", ["s1", "s2"], "/data/img.qcow2", "20260812T160000")

    intents = manager.get_commit_in_progress("vm1")
    assert len(intents) == 1
    intent = intents[0]
    assert intent.disk == "vda"
    assert intent.snapshots == ["s1", "s2"]
    assert intent.base == "/data/img.qcow2"
    assert intent.started_ts == "20260812T160000"


def test_commit_intent_multiple_disks_independent(tmp_path: Path) -> None:
    """vda and vdb hold independent intent records; clearing vda leaves vdb.

    commit-intent-journal scenario "Multiple disks hold independent
    intent records": two disks yield two records and per-disk clear
    removes only the target disk's record.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_commit_in_progress("vm1", "vda", ["s1"], "/data/img-a.qcow2", "20260812T150126")
    manager.set_commit_in_progress("vm1", "vdb", ["s2"], "/data/img-b.qcow2", "20260812T150200")

    intents = manager.get_commit_in_progress("vm1")
    assert len(intents) == 2
    by_disk = {i.disk: i for i in intents}
    assert by_disk["vda"].snapshots == ["s1"]
    assert by_disk["vdb"].snapshots == ["s2"]

    manager.clear_commit_in_progress("vm1", "vda")

    remaining = manager.get_commit_in_progress("vm1")
    assert len(remaining) == 1
    assert remaining[0].disk == "vdb"
    assert remaining[0].snapshots == ["s2"]


def test_commit_intent_survives_round_trip(tmp_path: Path) -> None:
    """A written intent record survives re-instantiating the manager.

    commit-intent-journal scenario "Intent survives a state round-trip":
    a fresh JsonStateManager over the same state file returns the
    identical record.
    """
    manager1 = JsonStateManager(state_dir=tmp_path)
    manager1.set_commit_in_progress(
        "vm1", "vda", ["s1", "s2"], "/data/img.qcow2", "20260812T150126"
    )

    manager2 = JsonStateManager(state_dir=tmp_path)
    intents = manager2.get_commit_in_progress("vm1")
    assert len(intents) == 1
    intent = intents[0]
    assert intent.disk == "vda"
    assert intent.snapshots == ["s1", "s2"]
    assert intent.base == "/data/img.qcow2"
    assert intent.started_ts == "20260812T150126"


def test_commit_intent_json_round_trip(tmp_path: Path) -> None:
    """The journal round-trips through the JSON state file as a top-level list.

    state-management scenario "Journal round-trip through JSON state":
    the record is persisted under the ``commit_in_progress`` top-level
    key of ``{vm}.json`` as a list of objects with the exact fields.
    """
    manager = JsonStateManager(state_dir=tmp_path)
    manager.set_commit_in_progress("vm1", "vda", ["s1"], "/data/img.qcow2", "20260812T150126")

    state_file = tmp_path / "vm1.json"
    assert state_file.exists()
    with open(state_file, encoding="utf-8") as fh:
        data = json.load(fh)

    journal = data.get("commit_in_progress")
    assert journal == [
        {
            "disk": "vda",
            "snapshots": ["s1"],
            "base": "/data/img.qcow2",
            "started_ts": "20260812T150126",
        }
    ]


def test_commit_intent_atomic_with_other_state(tmp_path: Path) -> None:
    """The intent journal is written in the same atomic save as other state.

    state-management scenario "Journal write is atomic with other state":
    writing an intent must not clobber existing per-VM state — both the
    journal and pre-existing keys (last_allocation, deferred_operations,
    snapshots) survive in one coherent file, and no ``.tmp`` remains.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_last_allocation("vm1", "vda", 4096)
    manager.add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "apparmor")
    manager.set_commit_in_progress("vm1", "vda", ["s1"], "/data/img.qcow2", "20260812T150126")

    # The pre-existing state is untouched by the journal write.
    assert manager.get_last_allocation("vm1", "vda") == 4096
    ops = manager.get_deferred_operations("vm1")
    assert len(ops) == 1
    assert ops[0].reason == "apparmor"
    intents = manager.get_commit_in_progress("vm1")
    assert len(intents) == 1
    assert intents[0].snapshots == ["s1"]

    # One atomic save produced one coherent file; no .tmp lingers.
    assert not (tmp_path / "vm1.json.tmp").exists()
    with open(tmp_path / "vm1.json", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["last_allocation"] == {"vda": 4096}
    assert len(data["deferred_operations"]) == 1
    assert data["commit_in_progress"][0]["disk"] == "vda"

    # A fresh manager sees the combined state.
    manager2 = JsonStateManager(state_dir=tmp_path)
    assert manager2.get_last_allocation("vm1", "vda") == 4096
    assert len(manager2.get_deferred_operations("vm1")) == 1
    assert len(manager2.get_commit_in_progress("vm1")) == 1


def test_legacy_state_file_loads_cleanly(tmp_path: Path) -> None:
    """A legacy state file without commit_in_progress loads as an empty list.

    state-management scenario "Legacy state file loads cleanly": no
    migration is required and the read does not rewrite the file.
    """
    legacy_data = {
        "last_allocation": {"vda": 4096},
        "snapshots": [],
        "deferred_operations": [],
    }
    state_file = tmp_path / "vm1.json"
    state_file.write_text(json.dumps(legacy_data), encoding="utf-8")

    with open(state_file, encoding="utf-8") as fh:
        original_raw = fh.read()

    manager = JsonStateManager(state_dir=tmp_path)

    assert manager.get_commit_in_progress("vm1") == []

    # Existing legacy fields still work.
    assert manager.get_last_allocation("vm1", "vda") == 4096

    # Reads must not rewrite the legacy file (no migration pass).
    with open(state_file, encoding="utf-8") as fh:
        after_raw = fh.read()
    assert after_raw == original_raw


# ── full backup tracking tests ───────────────────────────────────────────


def test_set_last_full_backup_roundtrips_with_disk(tmp_path: Path) -> None:
    """set_last_full_backup then get_last_full_backup round-trips name, timestamp, disk.

    The recorded name carries the ``.qcow2`` extension and the derived
    path resolves to the backup file (spec scenario "Full backup state
    saved and retrieved with disk").
    """
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    name = "full-2024-01-01.qcow2"
    ts = datetime(2024, 1, 1, 12, 0, 0)

    manager.set_last_full_backup(target, name, ts, "vda")

    result = manager.get_last_full_backup(target)

    assert result is not None
    assert isinstance(result, FullBackupInfo)
    assert result.name == name
    assert result.timestamp == ts
    assert result.disk == "vda"
    assert result.path == Path(target) / name


def test_full_backup_state_saved_and_retrieved(tmp_path: Path) -> None:
    """Full backup state survives across JsonStateManager instances (disk reload)."""
    target = "/mnt/backup/testvm"
    name = "full-2024-06-01.qcow2"
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
    assert result.disk == "vda"
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
    name = "full-2024-01-01.qcow2"
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

    manager.record_full_backup(target, "full-2024-01-01.qcow2", ts1, "vda")
    manager.record_full_backup(target, "full-2024-02-01.qcow2", ts2, "vda")
    manager.record_full_backup(target, "full-2024-03-01.qcow2", ts3, "vda")

    backups = manager.get_full_backups(target)

    assert len(backups) == 3
    assert backups[0].name == "full-2024-01-01.qcow2"
    assert backups[1].name == "full-2024-02-01.qcow2"
    assert backups[2].name == "full-2024-03-01.qcow2"


# ── FULL name-extension invariant tests (design D1/D2/D3) ────────────


def test_record_full_backup_extends_name_and_derives_path(tmp_path: Path) -> None:
    """Recorded FULL names carry the .qcow2 extension; path derives from the name.

    Spec: "Recorded name carries the .qcow2 extension and path resolves
    to the file".  Recording an already-extended name must not
    double-append.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    ts = datetime(2024, 1, 1, 12, 0, 0)

    manager.record_full_backup(target, "full-2024-01-01.qcow2", ts, "vda")

    backups = manager.get_full_backups(target)
    assert len(backups) == 1
    assert backups[0].name == "full-2024-01-01.qcow2"
    assert backups[0].path == Path(target) / "full-2024-01-01.qcow2"

    # The persisted entry stores the same extended name and derived path.
    with open(tmp_path / "_full_backups.json", encoding="utf-8") as fh:
        stored = json.load(fh)[target]
    assert stored[0]["name"] == "full-2024-01-01.qcow2"
    assert stored[0]["path"] == str(Path(target) / "full-2024-01-01.qcow2")


def test_record_full_backup_normalizes_stem_defensively(tmp_path: Path) -> None:
    """A stem name passed to record_full_backup is normalized defensively.

    The state manager enforces the .qcow2 invariant caller-independently
    (design D1): even a stem name from a future call site cannot regress
    the storage format.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    ts = datetime(2024, 1, 1, 12, 0, 0)

    manager.record_full_backup(target, "full-2024-01-01", ts, "vda")

    backups = manager.get_full_backups(target)
    assert len(backups) == 1
    assert backups[0].name == "full-2024-01-01.qcow2"
    assert backups[0].path == Path(target) / "full-2024-01-01.qcow2"
    assert backups[0].timestamp == ts
    assert backups[0].disk == "vda"


def test_record_full_backup_idempotent_no_double_append(tmp_path: Path) -> None:
    """Recording the same extended name twice never produces .qcow2.qcow2.

    The per-field guard checks ``endswith(".qcow2")`` before appending, so
    the persisted name is always the exact extended form.  On read, the
    load-time dedup (design D4) collapses the two identical records into
    one.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_full_backup(
        target, "full-2024-01-01.qcow2", datetime(2024, 1, 1, 12, 0, 0), "vda"
    )
    manager.record_full_backup(
        target, "full-2024-01-01.qcow2", datetime(2024, 1, 2, 12, 0, 0), "vda"
    )

    # Both persisted records carry the exact extended name — never a
    # double-append (.qcow2.qcow2).
    with open(tmp_path / "_full_backups.json", encoding="utf-8") as fh:
        stored = json.load(fh)[target]
    assert len(stored) == 2
    assert [e["name"] for e in stored] == [
        "full-2024-01-01.qcow2",
        "full-2024-01-01.qcow2",
    ]

    # On read, load-time dedup collapses the identical records to one.
    backups = manager.get_full_backups(target)
    assert len(backups) == 1
    assert backups[0].name == "full-2024-01-01.qcow2"
    assert not backups[0].name.endswith(".qcow2.qcow2")


def test_get_full_backups_returns_all_per_disk_fulls(tmp_path: Path) -> None:
    """get_full_backups returns every FULL recorded per disk for a target.

    FULLs are tracked per (target, disk): records for different disks of
    the same VM are all returned, oldest first (spec scenario
    "get_full_backups returns all per-disk FULLs").
    """
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    ts1 = datetime(2024, 1, 1, 12, 0, 0)
    ts2 = datetime(2024, 2, 1, 12, 0, 0)
    ts3 = datetime(2024, 3, 1, 12, 0, 0)

    manager.record_full_backup(target, "testvm.FULL.20240101T120000_vda_a1b2c3.qcow2", ts1, "vda")
    manager.record_full_backup(target, "testvm.FULL.20240201T120000_vda_d3e4f5.qcow2", ts2, "vda")
    manager.record_full_backup(target, "testvm.FULL.20240301T120000_vdb_b2c3d4.qcow2", ts3, "vdb")

    backups = manager.get_full_backups(target)

    assert len(backups) == 3
    assert [b.disk for b in backups] == ["vda", "vda", "vdb"]
    assert [b.name for b in backups] == [
        "testvm.FULL.20240101T120000_vda_a1b2c3.qcow2",
        "testvm.FULL.20240201T120000_vda_d3e4f5.qcow2",
        "testvm.FULL.20240301T120000_vdb_b2c3d4.qcow2",
    ]


def test_load_normalizes_stem_entry_on_load(tmp_path: Path) -> None:
    """A stem entry written by the buggy version is repaired on load.

    The load-time migration (design D2) normalizes the name to extended
    form, rebuilds the path from the target, AND persists the repaired
    state back to disk so the fix is one-time.
    """
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)
    target = "/mnt/backup/testvm"

    stem_data = {
        target: [
            {
                "name": "full-2024-01-01",
                "path": f"{target}/full-2024-01-01",
                "timestamp": "2024-01-01T12:00:00",
                "disk": "vda",
            },
        ],
    }
    full_backups_file = state_dir / "_full_backups.json"
    full_backups_file.write_text(json.dumps(stem_data), encoding="utf-8")

    manager = JsonStateManager(state_dir=state_dir)
    backups = manager.get_full_backups(target)

    # The loaded record is fully extended.
    assert len(backups) == 1
    assert backups[0].name == "full-2024-01-01.qcow2"
    assert backups[0].path == Path(target) / "full-2024-01-01.qcow2"

    # The repaired state is persisted back to disk (one-time migration).
    with open(full_backups_file, encoding="utf-8") as fh:
        stored = json.load(fh)[target]
    assert stored[0]["name"] == "full-2024-01-01.qcow2"
    assert stored[0]["path"] == str(Path(target) / "full-2024-01-01.qcow2")


def test_load_mixed_stem_extended_twins_deduplicate_to_one(tmp_path: Path) -> None:
    """A stem entry and its extended twin collapse into one record on load.

    Normalization runs BEFORE dedup (design D2): the stem twin is
    extended first, then the duplicate (name, target_path) tuple is
    removed.  Exactly one extended record survives.
    """
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)
    target = "/mnt/backup/testvm"

    mixed_data = {
        target: [
            {
                "name": "full-2024-01-01",
                "path": f"{target}/full-2024-01-01",
                "timestamp": "2024-01-01T12:00:00",
                "disk": "vda",
            },
            {
                "name": "full-2024-01-01.qcow2",
                "path": f"{target}/full-2024-01-01.qcow2",
                "timestamp": "2024-01-01T12:00:00",
                "disk": "vda",
            },
        ],
    }
    full_backups_file = state_dir / "_full_backups.json"
    full_backups_file.write_text(json.dumps(mixed_data), encoding="utf-8")

    manager = JsonStateManager(state_dir=state_dir)
    backups = manager.get_full_backups(target)

    assert len(backups) == 1
    assert backups[0].name == "full-2024-01-01.qcow2"
    assert backups[0].path == Path(target) / "full-2024-01-01.qcow2"


def test_load_already_extended_entries_unchanged_no_rewrite(tmp_path: Path) -> None:
    """Already-extended entries load unchanged; the file is not rewritten.

    Pre-regression production state passes through with zero migration
    cost: loading twice leaves the file byte-identical (design D2).
    """
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)
    target = "/mnt/backup/testvm"

    extended_data = {
        target: [
            {
                "name": "full-2024-01-01.qcow2",
                "path": f"{target}/full-2024-01-01.qcow2",
                "timestamp": "2024-01-01T12:00:00",
                "disk": "vda",
            },
            {
                "name": "full-2024-03-01.qcow2",
                "path": f"{target}/full-2024-03-01.qcow2",
                "timestamp": "2024-03-01T12:00:00",
                "disk": "vda",
            },
        ],
    }
    full_backups_file = state_dir / "_full_backups.json"
    full_backups_file.write_text(json.dumps(extended_data), encoding="utf-8")

    with open(full_backups_file, encoding="utf-8") as fh:
        original_raw = fh.read()

    manager = JsonStateManager(state_dir=state_dir)
    backups = manager.get_full_backups(target)
    assert len(backups) == 2
    assert backups[0].name == "full-2024-01-01.qcow2"
    assert backups[1].name == "full-2024-03-01.qcow2"

    # Load again through a second instance — still no rewrite.
    JsonStateManager(state_dir=state_dir).get_full_backups(target)

    with open(full_backups_file, encoding="utf-8") as fh:
        after_raw = fh.read()
    assert after_raw == original_raw, "already-extended file must not be rewritten"


def test_load_repairs_asymmetric_entry_field_by_field(tmp_path: Path) -> None:
    """name and path are repaired independently (per-field guard).

    Covers both asymmetry directions: (1) stem name + extended path —
    only the name is fixed; (2) extended name + stem path — the path is
    rebuilt from the (unchanged) extended name.
    """
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)
    target = "/mnt/backup/testvm"

    asymmetric_data = {
        target: [
            # Direction 1: stem name, extended path.
            {
                "name": "full-2024-01-01",
                "path": f"{target}/full-2024-01-01.qcow2",
                "timestamp": "2024-01-01T12:00:00",
                "disk": "vda",
            },
            # Direction 2: extended name, stem path.
            {
                "name": "full-2024-02-01.qcow2",
                "path": f"{target}/full-2024-02-01",
                "timestamp": "2024-02-01T12:00:00",
                "disk": "vda",
            },
        ],
    }
    full_backups_file = state_dir / "_full_backups.json"
    full_backups_file.write_text(json.dumps(asymmetric_data), encoding="utf-8")

    manager = JsonStateManager(state_dir=state_dir)
    backups = manager.get_full_backups(target)

    assert len(backups) == 2
    # Direction 1: name extended, path untouched.
    assert backups[0].name == "full-2024-01-01.qcow2"
    assert backups[0].path == Path(target) / "full-2024-01-01.qcow2"
    # Direction 2: name untouched, path rebuilt from the extended name.
    assert backups[1].name == "full-2024-02-01.qcow2"
    assert backups[1].path == Path(target) / "full-2024-02-01.qcow2"


def test_remove_full_backup_stem_lookup_removes_extended_record(tmp_path: Path) -> None:
    """remove_full_backup with a stem name removes the extended record.

    Design D3: stem callers (e.g. ``Core._cleanup_backups`` passing
    ``BackupInfo.name``) and extended callers remove the same record.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_full_backup(
        target, "full-2024-01-01.qcow2", datetime(2024, 1, 1, 12, 0, 0), "vda"
    )

    assert manager.remove_full_backup(target, "full-2024-01-01") is True
    assert manager.get_full_backups(target) == []


def test_remove_full_backup_extended_lookup_removes_record(tmp_path: Path) -> None:
    """remove_full_backup with the extended name removes the same record."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_full_backup(
        target, "full-2024-01-01.qcow2", datetime(2024, 1, 1, 12, 0, 0), "vda"
    )

    assert manager.remove_full_backup(target, "full-2024-01-01.qcow2") is True
    assert manager.get_full_backups(target) == []


def test_remove_full_backup_non_matching_returns_false(tmp_path: Path) -> None:
    """A non-matching name leaves state untouched and returns False."""
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.record_full_backup(
        target, "full-2024-01-01.qcow2", datetime(2024, 1, 1, 12, 0, 0), "vda"
    )

    # Neither the stem nor the extended form matches the stored name.
    assert manager.remove_full_backup(target, "full-2024-03-01") is False
    assert manager.remove_full_backup(target, "full-2024-03-01.qcow2") is False

    backups = manager.get_full_backups(target)
    assert len(backups) == 1
    assert backups[0].name == "full-2024-01-01.qcow2"


def test_remove_full_backup_after_set_last_full_backup_delegation(
    tmp_path: Path,
) -> None:
    """The set_last_full_backup delegation path inherits name normalization.

    ``set_last_full_backup`` delegates to ``record_full_backup`` (design
    D1), so a stem passed through the legacy setter is stored extended —
    and the tolerant remove path then finds it via the stem form.
    """
    manager = JsonStateManager(state_dir=tmp_path)

    target = "/mnt/backup/testvm"
    manager.set_last_full_backup(target, "full-2024-01-01", datetime(2024, 1, 1, 12, 0, 0), "vda")

    backups = manager.get_full_backups(target)
    assert len(backups) == 1
    assert backups[0].name == "full-2024-01-01.qcow2"

    assert manager.remove_full_backup(target, "full-2024-01-01") is True
    assert manager.get_full_backups(target) == []


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


# ── mixed-generation dependency key formats (state-management spec) ─────


def test_mixed_generation_dependencies_counted_together(tmp_path: Path) -> None:
    """Legacy snapshot-name keys and backup-name (freeze-ts) keys count together.

    Spec (state-management): ``record_incremental_dependency`` accepts
    incremental names in both legacy snapshot format (``vm.20260807T152956
    _vda_ec1148``) and backup-name format (``vm.20260808T031542_vda_a1b2c3``),
    and ``get_incremental_dependencies`` returns ALL of them for a FULL so
    the chain-length decision sees the total count without inspecting key
    formats.  No migration pass is required — mixed generations coexist.
    """
    manager = JsonStateManager(state_dir=tmp_path)
    target = "/mnt/backup/testvm"

    full_name = "vm.FULL.20260801T030000_vda_a1b2c3"

    # 3 legacy snapshot-keyed incrementals (named after snapshots).
    legacy_keys = [
        "vm.20260802T010000_vda_b2c3d4",
        "vm.20260802T020000_vda_c3d4e5",
        "vm.20260802T030000_vda_d4e5f6",
    ]
    # 2 backup-name (freeze-ts) incrementals (named after freeze points).
    backup_name_keys = [
        "vm.20260808T031542_vda_a1b2c3",
        "vm.20260808T041542_vda_b2c3d4",
    ]

    for incremental in legacy_keys + backup_name_keys:
        manager.record_incremental_dependency(target, incremental, full_name)

    deps = manager.get_incremental_dependencies(target, full_name)

    # Both formats are returned — the chain-length decision sees count 5.
    assert len(deps) == 5, f"Expected 5 mixed-format deps, got {deps}"
    for incremental in legacy_keys + backup_name_keys:
        assert incremental in deps

    # Lookup via the .qcow2 form of the FULL name finds the same 5 entries
    # (full_name normalization is orthogonal to incremental key formats).
    deps_qcow2 = manager.get_incremental_dependencies(target, f"{full_name}.qcow2")
    assert len(deps_qcow2) == 5

    # No migration rewrite happens: the stored keys are the given formats.
    with open(tmp_path / "_dependencies.json", encoding="utf-8") as fh:
        stored = json.load(fh)[target]
    assert sorted(stored) == [full_name]


def test_dependency_keys_accepted_in_both_formats(tmp_path: Path) -> None:
    """record/get accept legacy snapshot-name and backup-name FULL keys.

    The FULL anchor itself may be stored under either a legacy-style key
    (stem, no disk segment, e.g. ``vm.FULL.20260727``) or a new freeze-ts
    key with a disk segment (``vm.FULL.20260808T030000_vda_abc123``).
    ``record_incremental_dependency`` and ``get_incremental_dependencies``
    must handle both without error.
    """
    manager = JsonStateManager(state_dir=tmp_path)
    target = "/mnt/backup/testvm"

    legacy_full = "vm.FULL.20260727"
    freeze_ts_full = "vm.FULL.20260808T030000_vda_abc123"

    # Legacy-style anchor key, incremental named in backup-name format.
    manager.record_incremental_dependency(target, "vm.20260808T031542_vda_a1b2c3", legacy_full)
    # Freeze-ts anchor key (with .qcow2 extension, as on disk), legacy
    # snapshot-name incremental.
    manager.record_incremental_dependency(target, "vm.20260807T152956_vda_ec1148", freeze_ts_full)

    # Both lookups resolve regardless of anchor key format.
    assert manager.get_incremental_dependencies(target, legacy_full) == [
        "vm.20260808T031542_vda_a1b2c3"
    ]
    assert manager.get_incremental_dependencies(target, freeze_ts_full) == [
        "vm.20260807T152956_vda_ec1148"
    ]
    # The .qcow2 form of the freeze-ts anchor resolves too (stem-normalized).
    assert manager.get_incremental_dependencies(target, f"{freeze_ts_full}.qcow2") == [
        "vm.20260807T152956_vda_ec1148"
    ]


@pytest.mark.mock
def test_inmemory_mixed_generation_dependencies_counted_together() -> None:
    """InMemoryStateManager: mixed-format dependency keys counted together.

    Mock parity for ``test_mixed_generation_dependencies_counted_together``
    (TESTING.md: mocks mirror the production hierarchy).
    """
    manager = InMemoryStateManager()
    target = "/mnt/backup/testvm"

    full_name = "vm.FULL.20260801T030000_vda_a1b2c3"
    legacy_keys = [
        "vm.20260802T010000_vda_b2c3d4",
        "vm.20260802T020000_vda_c3d4e5",
        "vm.20260802T030000_vda_d4e5f6",
    ]
    backup_name_keys = [
        "vm.20260808T031542_vda_a1b2c3",
        "vm.20260808T041542_vda_b2c3d4",
    ]

    for incremental in legacy_keys + backup_name_keys:
        manager.record_incremental_dependency(target, incremental, full_name)

    deps = manager.get_incremental_dependencies(target, full_name)
    assert len(deps) == 5
    for incremental in legacy_keys + backup_name_keys:
        assert incremental in deps

    # .qcow2 lookup of the FULL name finds the same entries.
    assert len(manager.get_incremental_dependencies(target, f"{full_name}.qcow2")) == 5


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
    """Old-format _full_backups.json (dict values + stem entries) is auto-migrated on load.

    Two migrations apply together: dict→list (old single-dict format) AND
    stem→extended (name-extension normalization, design D2).  The loaded
    record carries a normalized ``.qcow2`` name and the path derived from
    it.
    """
    # Write old format: {target_path: {name, path, timestamp}} (dict, not
    # list), with stem-format name/path as written by the buggy version.
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

    # get_full_backups should auto-migrate the dict to a list and the
    # stem name/path to the extended form.
    backups = manager.get_full_backups("/mnt/backup/testvm")
    assert len(backups) == 1
    assert backups[0].name == "full-2024-01-01.qcow2"
    assert backups[0].timestamp == datetime(2024, 1, 1, 12, 0, 0)
    assert backups[0].path == Path("/mnt/backup/testvm/full-2024-01-01.qcow2")

    # get_last_full_backup should also work (returns last from list).
    last = manager.get_last_full_backup("/mnt/backup/testvm")
    assert last is not None
    assert last.name == "full-2024-01-01.qcow2"


def test_full_backups_json_new_format_loaded_as_is(tmp_path: Path) -> None:
    """New-format _full_backups.json (list values, extended names) is loaded as-is.

    The deprecated ``bucket_level`` field is silently ignored, and the
    already-extended entries trigger no migration rewrite — the file on
    disk stays byte-identical (design D2).
    """
    new_data = {
        "/mnt/backup/testvm": [
            {
                "name": "full-2024-01-01.qcow2",
                "path": "/mnt/backup/testvm/full-2024-01-01.qcow2",
                "timestamp": "2024-01-01T12:00:00",
                "bucket_level": "monthly",
            },
            {
                "name": "full-2024-03-01.qcow2",
                "path": "/mnt/backup/testvm/full-2024-03-01.qcow2",
                "timestamp": "2024-03-01T12:00:00",
                "bucket_level": "weekly",
            },
        ],
    }
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)
    full_backups_file = state_dir / "_full_backups.json"
    full_backups_file.write_text(json.dumps(new_data), encoding="utf-8")

    with open(full_backups_file, encoding="utf-8") as fh:
        original_raw = fh.read()

    manager = JsonStateManager(state_dir=state_dir)

    backups = manager.get_full_backups("/mnt/backup/testvm")
    assert len(backups) == 2
    assert backups[0].name == "full-2024-01-01.qcow2"
    assert backups[1].name == "full-2024-03-01.qcow2"
    assert backups[0].path == Path("/mnt/backup/testvm/full-2024-01-01.qcow2")

    # No migration rewrite: the file is byte-identical after load.
    with open(full_backups_file, encoding="utf-8") as fh:
        after_raw = fh.read()
    assert after_raw == original_raw, (
        "Already-extended _full_backups.json must not be rewritten (idempotent)"
    )


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

    Normalization runs BEFORE dedup (design D2): the duplicate stem
    entries are first extended to ``.qcow2`` form, then the twin is
    removed.  The surviving entries carry the extended name.
    """
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)

    # Write a state file with duplicate stem-format entries.
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

    # Only the first occurrence should remain (the duplicate removed),
    # and names/paths carry the .qcow2 extension.
    assert len(backups) == 2
    assert backups[0].name == "full-2024-01-01.qcow2"
    assert backups[1].name == "full-2024-02-01.qcow2"
    assert backups[0].path == Path("/mnt/backup/testvm/full-2024-01-01.qcow2")

    # Deduplication log should have been emitted with the normalized name.
    assert (
        "Deduplicated FULL backup entry: full-2024-01-01.qcow2 for target /mnt/backup/testvm"
        in caplog.text
    )


def test_deduplicate_no_duplicates_noop(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """No duplicate entries → all preserved, no deduplication log.

    Load-time normalization still applies to the stem fixtures, so the
    returned entries (and the persisted state) carry the extended
    ``.qcow2`` name.
    """
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)

    # Write a state file with only unique stem-format entries.
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

    # All entries should be preserved, with extended names.
    assert len(backups) == 3
    names = [b.name for b in backups]
    assert names == [
        "full-2024-01-01.qcow2",
        "full-2024-02-01.qcow2",
        "full-2024-03-01.qcow2",
    ]

    # No deduplication log should have been emitted.
    assert "Deduplicated FULL backup entry:" not in caplog.text


def test_deduplicate_is_idempotent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Second load after deduplication is a no-op (idempotent).

    After the first load, the persisted state carries the normalized
    extended names — so the second load finds neither an extension fix
    nor duplicates to remove.
    """

    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)

    # Write a state file with duplicate stem-format entries.
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

    # First load — deduplication should occur and names normalize.
    manager1 = JsonStateManager(state_dir=state_dir)
    backups1 = manager1.get_full_backups("/mnt/backup/testvm")
    assert len(backups1) == 2
    assert (
        "Deduplicated FULL backup entry: full-2024-01-01.qcow2 for target /mnt/backup/testvm"
        in caplog.text
    )

    # The state file should have been rewritten with the deduplicated
    # list carrying extended names.
    with open(full_backups_file, encoding="utf-8") as fh:
        rewritten_data = json.load(fh)
    assert len(rewritten_data["/mnt/backup/testvm"]) == 2
    names_on_disk = [e["name"] for e in rewritten_data["/mnt/backup/testvm"]]
    assert names_on_disk == ["full-2024-01-01.qcow2", "full-2024-02-01.qcow2"]

    # Clear caplog before second load.
    caplog.clear()

    # Second load — should be a no-op (already deduplicated + extended).
    manager2 = JsonStateManager(state_dir=state_dir)
    backups2 = manager2.get_full_backups("/mnt/backup/testvm")
    assert len(backups2) == 2
    assert backups2[0].name == "full-2024-01-01.qcow2"
    assert backups2[1].name == "full-2024-02-01.qcow2"

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

    NOTE: this path routes through ``_save_target_state``, which the
    state-recovery implementer left UNWRAPPED (only ``_save`` catches
    ``OSError`` → ``RuntimeError``, design D3).  The OSError expectation is
    therefore kept — verified against qsnap/state/json_manager.py:638.
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


def test_reset_vm_state_clears_commit_intents(tmp_path: Path) -> None:
    """After reset_vm_state, the commit intent journal is empty (parity with
    InMemoryStateManager — a stale intent must not survive a VM reset)."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_commit_in_progress(
        "testvm", "vda", ["snap1.qcow2"], "/images/testvm.qcow2", "20260808T160000"
    )
    manager.set_commit_in_progress(
        "testvm", "vdb", ["snap2.qcow2"], "/images/testvm-vdb.qcow2", "20260808T160000"
    )
    assert len(manager.get_commit_in_progress("testvm")) == 2

    manager.reset_vm_state("testvm")

    assert manager.get_commit_in_progress("testvm") == []


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
    manager.record_full_backup(
        target, "full-2024-01-01.qcow2", datetime(2024, 1, 1, 12, 0, 0), "vda"
    )
    manager.record_full_backup(
        target, "full-2024-02-01.qcow2", datetime(2024, 2, 1, 12, 0, 0), "vda"
    )

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
    manager.record_full_backup(
        target, "full-2024-01-01.qcow2", datetime(2024, 1, 1, 12, 0, 0), "vda"
    )
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


def test_reset_vm_disk_state_clears_only_that_disks_intent(tmp_path: Path) -> None:
    """reset_vm_disk_state filters the commit intent journal per disk: the
    reset disk's intent is removed, other disks' intents are preserved."""
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_commit_in_progress(
        "myvm", "vda", ["snap_vda.qcow2"], "/images/myvm-vda.qcow2", "20260808T160000"
    )
    manager.set_commit_in_progress(
        "myvm", "vdb", ["snap_vdb.qcow2"], "/images/myvm-vdb.qcow2", "20260808T160000"
    )
    assert len(manager.get_commit_in_progress("myvm")) == 2

    manager.reset_vm_disk_state("myvm", "vda")

    intents = manager.get_commit_in_progress("myvm")
    assert len(intents) == 1, f"Expected only the vdb intent, got {intents}"
    assert intents[0].disk == "vdb"
    assert intents[0].snapshots == ["snap_vdb.qcow2"]


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


def test_reset_vm_disk_state_atomic(tmp_path: Path, caplog) -> None:
    """Crash during os.replace leaves original state file unchanged.

    Pre-populate snapshots, last_allocation, and deferred operations for
    both vda and vdb.  Mock os.replace to raise OSError, then call
    reset_vm_disk_state for vda.  The original state file must remain
    valid JSON with all vda data intact.  The save surfaces as RuntimeError
    with a CRITICAL log naming the path (design D3).
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
        caplog.at_level(logging.CRITICAL, logger="qsnap.state.json_manager"),
        patch(
            "qsnap.state.json_manager.os.replace",
            side_effect=OSError("simulated crash during rename"),
        ),
        pytest.raises(RuntimeError, match="State write failed for VM myvm"),
    ):
        manager.reset_vm_disk_state("myvm", "vda")

    # The CRITICAL log names the state path and the OS error.
    assert any(
        r.levelno == logging.CRITICAL
        and "myvm.json" in r.message
        and "simulated crash during rename" in r.message
        for r in caplog.records
    ), "CRITICAL log must name the state path and the OS error"

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

    full_vda = "myvm.FULL.20250101T120000_vda_a1b2c3.qcow2"
    full_vdb = "myvm.FULL.20250201T120000_vdb_d3e4f5.qcow2"
    full_other = "othervm.FULL.20250301T120000_vda_111111.qcow2"

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

    NOTE: this path routes through ``_save_full_backups``/``_save_dependencies``/
    ``_save_target_state``, which the state-recovery implementer left
    UNWRAPPED (only ``_save`` catches ``OSError`` → ``RuntimeError``, design
    D3).  The OSError expectation is therefore kept — verified against
    qsnap/state/json_manager.py:413/533/638.
    """
    manager = JsonStateManager(state_dir=tmp_path)
    target = "/mnt/backup/shared"

    full_vda = "myvm.FULL.20250101T120000_vda_a1b2c3.qcow2"
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


# ── reset_target_disk_state with backup-name keys (freeze-ts format) ─────


def test_reset_target_disk_state_backup_name_keys(tmp_path: Path) -> None:
    """reset_target_disk_state handles freeze-ts (backup-name) keys.

    FULL backups recorded under the new freeze-ts names — as written to
    disk: ``{vm}.FULL.{freeze_ts}_{disk}_{hex6}.qcow2`` — plus their
    stem-form dependency keys must be removed for the restored (vm, disk)
    only.  Other disks and other VMs on the same target are preserved
    (restore-command: "Restore resets only the restored disk's state").
    """
    manager = JsonStateManager(state_dir=tmp_path)
    target = "/mnt/backup/shared"

    ts_vda = datetime(2026, 8, 8, 3, 0, 0)
    ts_vdb = datetime(2026, 8, 8, 3, 30, 0)

    # New-format freeze-ts FULL names with the .qcow2 extension on disk.
    full_vda = "myvm.FULL.20260808T030000_vda_abc123.qcow2"
    full_vdb = "myvm.FULL.20260808T033000_vdb_d4e5f6.qcow2"

    manager.record_full_backup(target, full_vda, ts_vda, "vda")
    manager.record_full_backup(target, full_vdb, ts_vdb, "vdb")

    # Deltas recorded under freeze-ts delta names; dependency keys are
    # stored stem-form by record_incremental_dependency.
    manager.record_incremental_dependency(target, "myvm.20260808T031542_vda_a1b2c3", full_vda)
    manager.record_incremental_dependency(target, "myvm.20260808T040000_vdb_b2c3d4", full_vdb)

    manager.set_last_backup_allocation(target, "vda", 1000)
    manager.set_last_backup_allocation(target, "vdb", 2000)

    # ── pre-assertions ───────────────────────────────────────────────
    assert len(manager.get_full_backups(target)) == 2
    assert manager.get_incremental_dependencies(target, full_vda) == [
        "myvm.20260808T031542_vda_a1b2c3"
    ]
    assert manager.get_incremental_dependencies(target, full_vdb) == [
        "myvm.20260808T040000_vdb_b2c3d4"
    ]
    assert manager.get_last_backup_allocation(target, "vda") == 1000
    assert manager.get_last_backup_allocation(target, "vdb") == 2000

    # ── reset (myvm, vda) ────────────────────────────────────────────
    manager.reset_target_disk_state(target, "myvm", "vda")

    # ── only the restored disk's state is reset ──────────────────────
    backups = manager.get_full_backups(target)
    backup_names = {b.name for b in backups}
    assert full_vda not in backup_names, "restored-disk FULL must be removed"
    assert full_vdb in backup_names, "other-disk FULL must be preserved"
    assert len(backups) == 1

    # Deps of the removed FULL are gone; other-disk deps survive.  The
    # stem-form dep key matches the normalized map built from the .qcow2
    # FULL record name.
    assert manager.get_incremental_dependencies(target, full_vda) == [], (
        "restored-disk deps must be removed"
    )
    assert manager.get_incremental_dependencies(target, full_vdb) == [
        "myvm.20260808T040000_vdb_b2c3d4"
    ], "other-disk deps must be preserved"

    # Backup allocation cleared for vda, preserved for vdb.
    assert manager.get_last_backup_allocation(target, "vda") is None
    assert manager.get_last_backup_allocation(target, "vdb") == 2000

    # Dependency keys on disk: only the vdb stem key remains.
    with open(tmp_path / "_dependencies.json", encoding="utf-8") as fh:
        stored = json.load(fh)[target]
    assert "myvm.FULL.20260808T030000_vda_abc123" not in stored
    assert "myvm.FULL.20260808T033000_vdb_d4e5f6" in stored


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


# ── crash-evidence state fields: boot_id / last_commit_ts ─────────────────
# recover-lost-checkpoint-bitmaps (state-management spec): the per-VM state
# file gains two OPTIONAL fields — ``boot_id`` (host boot identifier,
# recorded after each fully successful run) and a per-disk
# ``last_commit_ts`` map (written after every successful blockcommit /
# ``qemu-img commit``).  Both are additive: legacy state files without
# them load with readers returning ``None`` (unknown), and no migration
# pass rewrites existing files.  Resets keep the new fields coherent.


def test_boot_id_round_trips_through_json(tmp_path: Path) -> None:
    """set_boot_id then get_boot_id round-trips through the JSON state file.

    The ``boot_id`` key is serialized in ``{vm}.json`` and survives a
    fresh manager instance (state-management scenario "Boot id recorded
    on successful run").
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_boot_id("testvm", "boot-A")
    assert manager.get_boot_id("testvm") == "boot-A"

    # Serialized as an optional key in the per-VM JSON file.
    state_file = tmp_path / "testvm.json"
    assert state_file.exists()
    with open(state_file, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data.get("boot_id") == "boot-A"

    # Round-trip through a fresh instance.
    manager2 = JsonStateManager(state_dir=tmp_path)
    assert manager2.get_boot_id("testvm") == "boot-A"


def test_boot_id_absent_in_legacy_state_returns_none(tmp_path: Path) -> None:
    """Legacy state files without ``boot_id`` load with get_boot_id() == None.

    Absence is "unknown", never an error, and no migration rewrite
    happens (state-management scenario "Missing boot id is unknown, not
    an error").
    """
    state_file = tmp_path / "testvm.json"
    legacy_data = {
        "last_allocation": {"vda": 4096},
        "snapshots": [],
        "deferred_operations": [],
    }
    state_file.write_text(json.dumps(legacy_data), encoding="utf-8")

    with open(state_file, encoding="utf-8") as fh:
        original_raw = fh.read()

    manager = JsonStateManager(state_dir=tmp_path)

    assert manager.get_boot_id("testvm") is None
    assert manager.get_last_allocation("testvm", "vda") == 4096

    # No migration rewrite on read.
    with open(state_file, encoding="utf-8") as fh:
        assert fh.read() == original_raw


def test_last_commit_ts_round_trips_through_json(tmp_path: Path) -> None:
    """set_last_commit_ts then get_last_commit_ts round-trips per disk.

    The per-disk ``last_commit_ts`` map survives a fresh manager
    instance and disks are independent (state-management scenario
    "Marker written after successful blockcommit").
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_last_commit_ts("testvm", "vda", "20260808T160000")
    manager.set_last_commit_ts("testvm", "vdb", "20260808T170000")

    # Serialized as an optional per-disk map in the per-VM JSON file.
    state_file = tmp_path / "testvm.json"
    with open(state_file, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data.get("last_commit_ts") == {
        "vda": "20260808T160000",
        "vdb": "20260808T170000",
    }

    # Round-trip through a fresh instance, per-disk independent.
    manager2 = JsonStateManager(state_dir=tmp_path)
    assert manager2.get_last_commit_ts("testvm", "vda") == "20260808T160000"
    assert manager2.get_last_commit_ts("testvm", "vdb") == "20260808T170000"

    # Overwrite one disk — the other is unaffected.
    manager2.set_last_commit_ts("testvm", "vda", "20260809T000000")
    assert manager2.get_last_commit_ts("testvm", "vda") == "20260809T000000"
    assert manager2.get_last_commit_ts("testvm", "vdb") == "20260808T170000"


def test_last_commit_ts_absent_in_legacy_state_returns_none(tmp_path: Path) -> None:
    """Legacy state files without ``last_commit_ts`` load with get() == None.

    Absent marker is "unknown" — recovery gate G1 treats it
    conservatively as failed (state-management scenario "Absent marker
    is conservative").
    """
    state_file = tmp_path / "testvm.json"
    state_file.write_text(
        json.dumps({"last_allocation": {"vda": 4096}}),
        encoding="utf-8",
    )

    manager = JsonStateManager(state_dir=tmp_path)

    assert manager.get_last_commit_ts("testvm", "vda") is None
    assert manager.get_last_commit_ts("testvm", "vdb") is None
    # A VM with no state file at all also reads None.
    assert manager.get_last_commit_ts("never_seen", "vda") is None


def test_boot_id_and_last_commit_ts_persist_across_boot_change(
    tmp_path: Path,
) -> None:
    """boot_id change is detectable across a simulated host restart.

    State holds boot-A; a later run records boot-B.  Readers observe the
    new value — crash-evidence consumers can conclude the host restarted
    since the last successful run.  ``last_commit_ts`` markers survive
    the boot change (they are VM/disk state, not host state).
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_boot_id("testvm", "boot-A")
    manager.set_last_commit_ts("testvm", "vda", "20260808T160000")

    # Host restarts — next successful run records the new boot_id.
    manager.set_boot_id("testvm", "boot-B")

    assert manager.get_boot_id("testvm") == "boot-B"
    assert manager.get_boot_id("testvm") != "boot-A"
    # Per-disk commit marker survives the reboot.
    assert manager.get_last_commit_ts("testvm", "vda") == "20260808T160000"


def test_reset_vm_disk_state_keeps_new_fields_coherent(tmp_path: Path) -> None:
    """reset_vm_disk_state leaves boot_id and last_commit_ts coherent.

    The per-disk reset removes the disk's snapshots/allocation/deferred
    state; the crash-evidence fields (host ``boot_id`` and the per-disk
    ``last_commit_ts`` markers) must remain structurally intact and
    readable for every disk (state-management spec: additive optional
    fields, no migration).
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_boot_id("testvm", "boot-A")
    manager.set_last_commit_ts("testvm", "vda", "20260808T160000")
    manager.set_last_commit_ts("testvm", "vdb", "20260808T170000")
    manager.set_last_allocation("testvm", "vda", 1000)
    manager.set_last_allocation("testvm", "vdb", 2000)

    manager.reset_vm_disk_state("testvm", "vda")

    # New fields are coherent and preserved.
    assert manager.get_boot_id("testvm") == "boot-A"
    assert manager.get_last_commit_ts("testvm", "vda") == "20260808T160000"
    assert manager.get_last_commit_ts("testvm", "vdb") == "20260808T170000"

    # Legacy per-disk state was cleared for the reset disk only.
    assert manager.get_last_allocation("testvm", "vda") is None
    assert manager.get_last_allocation("testvm", "vdb") == 2000

    # On-disk structure remains valid JSON with the new fields intact.
    with open(tmp_path / "testvm.json", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data.get("boot_id") == "boot-A"
    assert data.get("last_commit_ts") == {
        "vda": "20260808T160000",
        "vdb": "20260808T170000",
    }


def test_reset_target_disk_state_keeps_new_fields_coherent(tmp_path: Path) -> None:
    """reset_target_disk_state leaves per-VM boot_id/last_commit_ts alone.

    Target resets operate on the per-target files only; the per-VM
    crash-evidence fields must not be affected (state-management spec:
    additive optional fields).
    """
    manager = JsonStateManager(state_dir=tmp_path)

    manager.set_boot_id("testvm", "boot-A")
    manager.set_last_commit_ts("testvm", "vda", "20260808T160000")
    manager.record_full_backup(
        "/mnt/backup/shared",
        "testvm.FULL.20260808T030000_vda_a1b2c3.qcow2",
        datetime(2026, 8, 8, 3, 0, 0),
        "vda",
    )

    manager.reset_target_disk_state("/mnt/backup/shared", "testvm", "vda")

    # Per-VM crash-evidence fields are untouched.
    assert manager.get_boot_id("testvm") == "boot-A"
    assert manager.get_last_commit_ts("testvm", "vda") == "20260808T160000"
    # Target state was cleared for the disk.
    assert manager.get_full_backups("/mnt/backup/shared") == []


# ── stale collapse_in_progress key (removed phase API) ────────────────────
# bulk-collapse-blockcommit (state-management REMOVED requirement): the
# hysteresis ``collapse_in_progress`` phase key is gone — no reader or
# writer exists anymore (the collapse is a single uncapped bulk blockcommit
# per run).  A stale key persisted by an older qsnap version is tolerated
# on load (existing state intact, no rewrite on read) and survives
# ``reset_vm_state`` / ``reset_vm_disk_state`` untouched (nothing reads or
# writes the key anymore).


def test_stale_collapse_in_progress_key_tolerated_on_load(tmp_path: Path) -> None:
    """A stale ``collapse_in_progress`` key loads cleanly and is never read.

    bulk-collapse-blockcommit (state-management REMOVED requirement): a
    state file written by an older qsnap version may carry the
    ``collapse_in_progress`` key and other unknown keys; loading must
    succeed, existing state must stay intact, and reads must not rewrite
    the file (no migration pass).  There is NO reader method for the key
    anymore — it is never read back.
    """
    state_file = tmp_path / "testvm.json"
    state_file.write_text(
        json.dumps(
            {
                "last_allocation": {"vda": 4096},
                "snapshots": [],
                "deferred_operations": [],
                # Written by an older version that understood the phase key.
                "collapse_in_progress": ["vda"],
                # A hypothetical future key — must be tolerated too.
                "some_future_key": {"nested": True},
            }
        ),
        encoding="utf-8",
    )
    with open(state_file, encoding="utf-8") as fh:
        original_raw = fh.read()

    manager = JsonStateManager(state_dir=tmp_path)

    # Existing state intact; no error raised.
    assert manager.get_last_allocation("testvm", "vda") == 4096

    # No reader method exists — the key is never read back.
    assert not hasattr(manager, "get_collapse_in_progress"), (
        "the collapse-phase reader was removed from JsonStateManager"
    )

    # Reads must not rewrite the file (no migration pass).
    with open(state_file, encoding="utf-8") as fh:
        assert fh.read() == original_raw


def test_reset_vm_state_leaves_stale_collapse_key_untouched(tmp_path: Path) -> None:
    """Stale ``collapse_in_progress`` keys survive both state resets untouched.

    bulk-collapse-blockcommit (state-management REMOVED requirement):
    ``reset_vm_state`` / ``reset_vm_disk_state`` stop touching the
    ``collapse_in_progress`` key — a stale persisted key survives the
    reset byte-for-byte because nothing reads or writes the key anymore.
    """
    state_file = tmp_path / "testvm.json"
    # Seed with the same formatting the manager's atomic save produces
    # (``indent=2``) so the byte-for-byte fragment check is exact.
    state_file.write_text(
        json.dumps(
            {
                "last_allocation": {"vda": 4096, "vdb": 8192},
                "snapshots": [],
                "deferred_operations": [],
                "collapse_in_progress": ["vda"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    stale_fragment = '"collapse_in_progress": [\n    "vda"\n  ]'

    manager = JsonStateManager(state_dir=tmp_path)

    # ── whole-VM reset ────────────────────────────────────────────────
    manager.reset_vm_state("testvm")

    with open(state_file, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["collapse_in_progress"] == ["vda"], (
        "reset_vm_state must leave the stale collapse_in_progress value intact"
    )
    with open(state_file, encoding="utf-8") as fh:
        assert stale_fragment in fh.read(), (
            "the stale key's serialized form must survive reset_vm_state byte-for-byte"
        )

    # ── per-disk reset ─────────────────────────────────────────────────
    # Re-seed (the whole-VM reset above rewrote the other fields) and
    # verify the per-disk reset also leaves the key untouched.
    state_file.write_text(
        json.dumps(
            {
                "last_allocation": {"vda": 4096, "vdb": 8192},
                "snapshots": [],
                "deferred_operations": [],
                "collapse_in_progress": ["vda", "vdb"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    stale_fragment_multi = '"collapse_in_progress": [\n    "vda",\n    "vdb"\n  ]'

    manager.reset_vm_disk_state("testvm", "vda")

    with open(state_file, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["collapse_in_progress"] == ["vda", "vdb"], (
        "reset_vm_disk_state must leave the stale collapse_in_progress value intact"
    )
    with open(state_file, encoding="utf-8") as fh:
        assert stale_fragment_multi in fh.read(), (
            "the stale key's serialized form must survive reset_vm_disk_state byte-for-byte"
        )
