"""Tests for immutable result dataclasses.

Covers SnapshotResult, BackupResult, CommitResult, RetentionResult,
ShellResult, and ChangeResult -- verifying field values, success/failure
semantics, and frozen immutability where specified.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import VMRunResult
from qsnap.models.results import (
    ActionRecord,
    BackupResult,
    ChainVerifyResult,
    ChangeResult,
    CommitResult,
    DeferredBlockcommit,
    ReconcileResult,
    RestoreResult,
    RetentionResult,
    ShellResult,
    SnapshotResult,
)
from qsnap.state.json_manager import JsonStateManager


def test_snapshot_result_success():
    """A successful SnapshotResult carries all fields and is frozen."""
    result = SnapshotResult(
        success=True,
        name="testvm.20250101",
        path=Path("/snapshots/testvm.20250101"),
        new_allocation=1024,
        error=None,
    )
    assert result.success is True
    assert result.name == "testvm.20250101"
    assert result.path == Path("/snapshots/testvm.20250101")
    assert result.new_allocation == 1024
    assert result.error is None
    # Verify the dataclass is declared frozen.
    assert result.__dataclass_params__.frozen is True


def test_snapshot_result_failure():
    """A failed SnapshotResult has success=False and a non-None error string."""
    result = SnapshotResult(
        success=False,
        name="testvm.20250101",
        path=Path(""),
        new_allocation=0,
        error="virsh timed out",
    )
    assert result.success is False
    assert result.error == "virsh timed out"


def test_backup_result_success():
    """A successful BackupResult carries all fields."""
    result = BackupResult(
        success=True,
        snapshot_name="snap1",
        source_path=Path("/src/snap1"),
        target_path=Path("/dst/snap1"),
        bytes_transferred=4096,
        error=None,
    )
    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.source_path == Path("/src/snap1")
    assert result.target_path == Path("/dst/snap1")
    assert result.bytes_transferred == 4096
    assert result.error is None


def test_commit_result_success():
    """A successful CommitResult carries all fields."""
    result = CommitResult(
        success=True,
        committed_snapshot="snap1",
        error=None,
    )
    assert result.success is True
    assert result.committed_snapshot == "snap1"
    assert result.error is None


def test_retention_result_keep_remove():
    """RetentionResult holds keep/remove lists and is frozen."""
    result = RetentionResult(
        keep=["snap1", "snap2"],
        remove=["snap3"],
    )
    assert result.keep == ["snap1", "snap2"]
    assert result.remove == ["snap3"]
    # Verify the dataclass is declared frozen.
    assert result.__dataclass_params__.frozen is True


def test_shell_result_success():
    """A successful ShellResult carries all fields."""
    result = ShellResult(
        success=True,
        stdout="output",
        stderr="",
        returncode=0,
        error=None,
    )
    assert result.success is True
    assert result.stdout == "output"
    assert result.stderr == ""
    assert result.returncode == 0
    assert result.error is None


def test_shell_result_failure():
    """A failed ShellResult has success=False and a non-None error string."""
    result = ShellResult(
        success=False,
        stdout="",
        stderr="command not found",
        returncode=127,
        error="FileNotFoundError",
    )
    assert result.success is False
    assert result.error == "FileNotFoundError"


def test_change_result_disk_grown():
    """ChangeResult with changed=True and differing allocation values."""
    result = ChangeResult(
        changed=True,
        last_allocation=1024,
        current_allocation=2048,
    )
    assert result.changed is True
    assert result.last_allocation == 1024
    assert result.current_allocation == 2048


def test_restore_result_success_fields_and_frozen():
    """RestoreResult has all required fields and is frozen."""
    result = RestoreResult(
        success=True,
        snapshot_name="snap1",
        restored_path=Path("/restore"),
        chain_files=[Path("/restore/base.qcow2"), Path("/restore/snap1.qcow2")],
        error=None,
    )
    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.restored_path == Path("/restore")
    assert len(result.chain_files) == 2
    assert result.chain_files[0] == Path("/restore/base.qcow2")
    assert result.chain_files[1] == Path("/restore/snap1.qcow2")
    assert result.error is None
    # Verify the dataclass is declared frozen.
    assert result.__dataclass_params__.frozen is True
    # Verify mutation raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.success = False
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.error = "mutated"


def test_vm_run_result_backup_failed_field():
    """VMRunResult has backup_failed field (default False) and is frozen."""
    result = VMRunResult(vm_name="testvm", success=True)
    assert result.vm_name == "testvm"
    assert result.success is True
    assert result.backup_failed is False
    assert result.error is None
    # Verify the dataclass is declared frozen.
    assert result.__dataclass_params__.frozen is True
    # Verify mutation raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.backup_failed = True


def test_old_json_bucket_level_loaded(tmp_path):
    """JsonStateManager loads JSON with a legacy ``bucket_level`` field without error.

    The field is silently ignored — old state files should not crash the
    manager nor leak the bucket_level into FullBackupInfo.
    """
    manager = JsonStateManager(state_dir=tmp_path)
    ts = datetime(2025, 1, 15, 12, 0, 0)
    manager.record_full_backup("/mnt/backup", "full-backup-20250115.qcow2", ts)

    # Inject a legacy ``bucket_level`` field into the raw JSON.
    fulls_path = tmp_path / "_full_backups.json"
    with open(fulls_path, encoding="utf-8") as fh:
        data = json.load(fh)
    for entry in data.get("/mnt/backup", []):
        entry["bucket_level"] = "monthly"
    with open(fulls_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)

    # Reload — must not error, must not expose bucket_level.
    result = manager.get_last_full_backup("/mnt/backup")
    assert result is not None
    assert result.name == "full-backup-20250115.qcow2"
    assert not hasattr(result, "bucket_level"), "bucket_level must not leak into FullBackupInfo"

    # get_full_backups must also be read-tolerant.
    backups = manager.get_full_backups("/mnt/backup")
    assert len(backups) == 1
    assert not hasattr(backups[0], "bucket_level")


def test_old_json_bucket_level_read_tolerant(tmp_path):
    """JsonStateManager is read-tolerant: old JSON with ``bucket_level`` loads.

    Multiple entries with the legacy field must all load without error
    and the field must be silently dropped.
    """
    manager = JsonStateManager(state_dir=tmp_path)
    ts1 = datetime(2025, 1, 10, 0, 0, 0)
    ts2 = datetime(2025, 2, 10, 0, 0, 0)

    manager.record_full_backup("/mnt/backup", "full-backup-20250110.qcow2", ts1)
    manager.record_full_backup("/mnt/backup", "full-backup-20250210.qcow2", ts2)

    # Inject legacy bucket_level into both entries.
    fulls_path = tmp_path / "_full_backups.json"
    with open(fulls_path, encoding="utf-8") as fh:
        data = json.load(fh)
    for entry in data.get("/mnt/backup", []):
        entry["bucket_level"] = "yearly"
    with open(fulls_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)

    # get_full_backups returns both entries, neither has bucket_level.
    backups = manager.get_full_backups("/mnt/backup")
    assert len(backups) == 2
    for b in backups:
        assert not hasattr(b, "bucket_level"), f"{b.name} must not expose bucket_level"


def test_full_backup_state_saved_retrieved(tmp_path):
    """record_full_backup(target_path, name, timestamp) saves state,
    get_last_full_backup retrieves it."""
    manager = JsonStateManager(state_dir=tmp_path)
    ts = datetime(2025, 6, 1, 12, 0, 0)

    manager.record_full_backup("/mnt/backup", "vm.FULL.20250601.qcow2", ts)

    result = manager.get_last_full_backup("/mnt/backup")
    assert result is not None
    assert result.name == "vm.FULL.20250601.qcow2"
    assert isinstance(result.path, Path)
    assert result.timestamp == ts


def test_no_full_backup_returns_none(tmp_path):
    """get_last_full_backup returns None when no FULL has been recorded."""
    manager = JsonStateManager(state_dir=tmp_path)
    assert manager.get_last_full_backup("/never/seen/path") is None


def test_full_backup_recorded_and_retrieved(tmp_path):
    """record_full_backup then get_full_backups returns a list with the entry."""
    manager = JsonStateManager(state_dir=tmp_path)
    ts = datetime(2025, 7, 1, 12, 0, 0)

    manager.record_full_backup("/mnt/backup", "vm.FULL.20250701.qcow2", ts)

    backups = manager.get_full_backups("/mnt/backup")
    assert len(backups) == 1
    assert backups[0].name == "vm.FULL.20250701.qcow2"
    assert backups[0].timestamp == ts


# ── DeferredBlockcommit (last_warned_at) ──────────────────────────────────


def test_deferred_blockcommit_defaults_last_warned_at_none():
    """DeferredBlockcommit.last_warned_at defaults to None when not provided."""
    item = DeferredBlockcommit(
        snapshots=[],
        reason="test",
        since=datetime.now(),
    )
    assert item.last_warned_at is None


def test_deferred_blockcommit_explicit_last_warned_at():
    """DeferredBlockcommit stores an explicit last_warned_at value."""
    warned = datetime(2025, 1, 1, 12, 0, 0)
    item = DeferredBlockcommit(
        snapshots=["snap1.qcow2"],
        reason="apparmor",
        since=datetime(2024, 1, 1, 12, 0, 0),
        last_warned_at=warned,
    )
    assert item.last_warned_at == warned


# ── ActionRecord ──────────────────────────────────────────────────────────


def test_action_record_is_immutable():
    """ActionRecord is a frozen dataclass; mutation raises FrozenInstanceError."""
    record = ActionRecord(
        action="snapshot_create",
        vm_name="testvm",
        name="testvm.20250101",
        path=Path("/snapshots/testvm.20250101"),
    )

    # Verify the dataclass is declared frozen.
    assert record.__dataclass_params__.frozen is True

    # Verify setting any attribute raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.action = "snapshot_delete"
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.vm_name = "mutated"
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.name = "mutated"
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.path = Path("/other")
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.size = 999
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.duration = 5.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.error = "mutated"


def test_action_record_defaults_zero():
    """Constructing ActionRecord with only required fields gives zero/none defaults."""
    record = ActionRecord(
        action="backup_transfer",
        vm_name="testvm",
        name="backup-snap1.qcow2",
        path=Path("/mnt/backup/backup-snap1.qcow2"),
    )

    # Verify explicit fields.
    assert record.action == "backup_transfer"
    assert record.vm_name == "testvm"
    assert record.name == "backup-snap1.qcow2"
    assert record.path == Path("/mnt/backup/backup-snap1.qcow2")

    # Verify default values.
    assert record.size == 0
    assert record.duration == 0.0
    assert record.error is None


def test_action_record_handles_unicode_error():
    """ActionRecord with a Unicode error string does not crash on repr/str."""
    record = ActionRecord(
        action="error",
        vm_name="testvm",
        name="",
        path=Path(""),
        error="失败 — disk full 💾",
    )

    # Verify the error is stored correctly.
    assert record.error == "失败 — disk full 💾"

    # Verify repr and str do not crash (they must work without exception).
    r = repr(record)
    s = str(record)
    assert isinstance(r, str)
    assert isinstance(s, str)
    # Sanity: the unicode error text is present in the repr.
    assert "失败 — disk full 💾" in r


# ── ReconcileResult ─────────────────────────────────────────────────────────


def test_reconcile_result_is_frozen():
    """ReconcileResult is a frozen dataclass with all required fields and defaults."""
    result = ReconcileResult(
        vm_name="testvm",
        phantom_snapshots_removed=3,
        phantom_fulls_removed=1,
        stale_deps_removed=2,
        baselines_cleared=1,
        orphan_checkpoints_deleted=0,
        orphan_files_removed=5,
        state_supplemented=2,
        xml_refreshed=True,
        allocation_fixed=False,
        errors=["state file corrupted", "permission denied"],
    )

    # Verify the dataclass is declared frozen.
    assert result.__dataclass_params__.frozen is True

    # Verify mutation raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.vm_name = "mutated"
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.phantom_snapshots_removed = 99
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.phantom_fulls_removed = 99
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.stale_deps_removed = 99
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.baselines_cleared = 99
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.orphan_checkpoints_deleted = 99
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.orphan_files_removed = 99
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.errors = ["mutated"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.broken_chains = ["mutated"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.state_supplemented = 99
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.xml_refreshed = False
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.allocation_fixed = True

    # Verify the exact set of field names.
    field_names = {f.name for f in dataclasses.fields(ReconcileResult)}
    assert field_names == {
        "vm_name",
        "phantom_snapshots_removed",
        "phantom_fulls_removed",
        "stale_deps_removed",
        "baselines_cleared",
        "orphan_checkpoints_deleted",
        "orphan_files_removed",
        "state_supplemented",
        "xml_refreshed",
        "allocation_fixed",
        "errors",
        "broken_chains",
    }

    # Verify field values.
    assert result.vm_name == "testvm"
    assert result.phantom_snapshots_removed == 3
    assert result.phantom_fulls_removed == 1
    assert result.stale_deps_removed == 2
    assert result.baselines_cleared == 1
    assert result.orphan_checkpoints_deleted == 0
    assert result.orphan_files_removed == 5
    assert result.state_supplemented == 2
    assert result.xml_refreshed is True
    assert result.allocation_fixed is False
    assert result.errors == ["state file corrupted", "permission denied"]

    # Verify field types via isinstance on the actual values.
    assert isinstance(result.vm_name, str)
    assert isinstance(result.phantom_snapshots_removed, int)
    assert isinstance(result.phantom_fulls_removed, int)
    assert isinstance(result.stale_deps_removed, int)
    assert isinstance(result.baselines_cleared, int)
    assert isinstance(result.orphan_checkpoints_deleted, int)
    assert isinstance(result.orphan_files_removed, int)
    assert isinstance(result.state_supplemented, int)
    assert isinstance(result.xml_refreshed, bool)
    assert isinstance(result.allocation_fixed, bool)
    assert isinstance(result.errors, list)


def test_reconcile_result_defaults_and_equality():
    """ReconcileResult can be constructed with only vm_name; defaults are zero/empty."""
    result = ReconcileResult(vm_name="defaults-test")

    # Verify default values.
    assert result.phantom_snapshots_removed == 0
    assert result.phantom_fulls_removed == 0
    assert result.stale_deps_removed == 0
    assert result.baselines_cleared == 0
    assert result.orphan_checkpoints_deleted == 0
    assert result.orphan_files_removed == 0
    assert result.state_supplemented == 0
    assert result.xml_refreshed is False
    assert result.allocation_fixed is False
    assert result.errors == []  # empty list, not None
    assert result.broken_chains == []  # empty list, not None

    # Two instances with same args are equal.
    a = ReconcileResult(vm_name="eq-test")
    b = ReconcileResult(vm_name="eq-test")
    assert a == b
    assert a == b  # __eq__ and __ne__ are consistent

    # Two instances with different vm_name are not equal.
    c = ReconcileResult(vm_name="other")
    assert a != c


# ── ChainVerifyResult ─────────────────────────────────────────────────────


def test_chain_verify_result_broken_file_field():
    """ChainVerifyResult has broken_file (Path | None) defaulting to None, and is frozen."""
    # 1. broken_file defaults to None when not specified.
    result = ChainVerifyResult(success=True, error=None)
    assert result.broken_file is None

    # 2. broken_file can be set to a Path value.
    path = Path("/var/lib/libvirt/images/vm.qcow2")
    result_with = ChainVerifyResult(success=False, error="missing backing file", broken_file=path)
    assert result_with.broken_file == path
    assert isinstance(result_with.broken_file, Path)

    # 3. Verify the exact set of field names.
    field_names = {f.name for f in dataclasses.fields(ChainVerifyResult)}
    assert field_names == {"success", "error", "broken_file"}

    # 4. Verify the dataclass is frozen (immutable).
    assert result.__dataclass_params__.frozen is True

    # 5. Mutation raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.broken_file = Path("/mutated")
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.success = False
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.error = "mutated"
