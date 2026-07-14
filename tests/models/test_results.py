"""Tests for immutable result dataclasses.

Covers SnapshotResult, BackupResult, CommitResult, RetentionResult,
ShellResult, and ChangeResult -- verifying field values, success/failure
semantics, and frozen immutability where specified.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import VMRunResult
from qsnap.models.results import (
    BackupResult,
    ChangeResult,
    CommitResult,
    FullBackupInfo,
    RestoreResult,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
    SnapshotResult,
)


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


def test_snapshot_result_content_hash_defaults_none():
    """SnapshotResult.content_hash defaults to None when not provided."""
    result = SnapshotResult(
        success=True,
        name="testvm.20250101",
        path=Path("/snapshots/testvm.20250101"),
        new_allocation=1024,
        error=None,
    )
    assert result.content_hash is None


def test_snapshot_result_content_hash_set():
    """SnapshotResult.content_hash can be set to a hash string."""
    result = SnapshotResult(
        success=True,
        name="testvm.20250101",
        path=Path("/snapshots/testvm.20250101"),
        new_allocation=1024,
        error=None,
        content_hash="abc123",
    )
    assert result.content_hash == "abc123"


def test_snapshot_info_content_hash_defaults_none():
    """SnapshotInfo.content_hash defaults to None when not provided."""
    info = SnapshotInfo(
        name="testvm.20250101",
        path=Path("/snapshots/testvm.20250101"),
        timestamp=datetime(2025, 1, 1, 12, 0, 0),
        allocation=1024,
    )
    assert info.content_hash is None


def test_full_backup_info_dataclass_fields_and_frozen():
    """FullBackupInfo has name (str), path (Path), timestamp (datetime) and is frozen."""
    ts = datetime(2025, 1, 1, 12, 0, 0)
    info = FullBackupInfo(
        name="full-backup-20250101",
        path=Path("/mnt/backup/full-backup-20250101.qcow2"),
        timestamp=ts,
    )
    # Verify field values.
    assert info.name == "full-backup-20250101"
    assert info.path == Path("/mnt/backup/full-backup-20250101.qcow2")
    assert info.timestamp == ts
    # Verify field types via isinstance on the actual values.
    assert isinstance(info.name, str)
    assert isinstance(info.path, Path)
    assert isinstance(info.timestamp, datetime)
    # Verify the exact set of field names.
    field_names = {f.name for f in dataclasses.fields(FullBackupInfo)}
    assert field_names == {"name", "path", "timestamp"}
    # Verify the dataclass is declared frozen.
    assert info.__dataclass_params__.frozen is True
    # Verify mutation raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.name = "mutated"
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.path = Path("/other")
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.timestamp = datetime(2025, 1, 2, 12, 0, 0)
