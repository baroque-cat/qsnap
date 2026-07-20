"""Unit tests for FileCopyBackupProvider.

Tests cover the three ``IBackupProvider`` methods (``transfer_missing``,
``list``, ``delete``) using ``MockShell`` to simulate ``rsync``/``qemu-img``/
``rm`` commands.  No real I/O occurs — all shell calls are intercepted by
``MockShell``.

Design decisions verified:
- **D1**: ``FileCopyBackupProvider`` does NOT inherit from ``Core``; its
  dependencies are ``IShell`` (required) and ``IStateManager`` (optional).
- **D3**: All transfers use ``rsync`` exclusively (no ``cp`` fallback).
- **D4**: REMOVED.  ``transfer_missing`` no longer calls
  ``create_full_backup()``.  All snapshots (including the first to an
  empty target) are transferred via ``rsync`` — the ``copy_base``
  config flag is now advisory and no longer triggers FULL creation.
- **D5**: For incremental backups, ``qemu-img rebase -u -b <bare_filename>``
  is used to update the backing path to a bare filename in the target
  directory (metadata-only update, no data copy).

Scenarios (from ``specs/backup-provider/spec.md``):
Transfer Missing:
1. New snapshot copied to empty target via rsync.
2. Snapshot already exists on target — skipped.
3. Incremental backup — rebase backing path.
4. Non-incremental backup — no rebase.
5. Transfer fails — rsync error.
6. Rate-limited transfer via rsync --bwlimit.
7. copy_base=false with empty target — rsync used (no create_full_backup).
8. copy_base=true allows direct rsync transfer.
List Backups:
9. Target directory exists with backups.
10. Target directory does not exist.
11. Target directory exists but is empty.
Delete Backups:
12. Successful backup deletion.
13. Backup file does not exist — rm -f is idempotent.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.models.results import ShellResult, SnapshotInfo
from qsnap.modules.backup.file_copy import FileCopyBackupProvider
from tests.mocks.mock_shell import MockShell

# ──────────────────────────────────────────────────────────────────────────
# Autouse fixture: ensure fake snapshot paths pass os.path.exists check
# ──────────────────────────────────────────────────────────────────────────


# ── Module-level: track ALL MockShell calls ──
# Both run() and run_with_stall_detection() calls are logged in mock_shell._calls
_orig_mock_run = MockShell.run
_orig_mock_sd = MockShell.run_with_stall_detection


def _tracked_run(self, cmd, timeout, check=False):
    self._calls = getattr(self, "_calls", [])
    self._calls.append(cmd)
    return _orig_mock_run(self, cmd, timeout, check=check)


def _tracked_sd(self, cmd, output_file=None, stall_timeout=1800, check=False):
    self._calls = getattr(self, "_calls", [])
    self._calls.append(cmd)
    return _orig_mock_sd(
        self, cmd, output_file=output_file, stall_timeout=stall_timeout, check=check
    )


MockShell.run = _tracked_run
MockShell.run_with_stall_detection = _tracked_sd


def _all_cmds(mock_shell):
    """Get all tracked commands from a MockShell instance."""
    return [" ".join(c) for c in getattr(mock_shell, "_calls", [])]


def _clear_calls(mock_shell):
    """Clear tracked calls (call before each test)."""
    mock_shell._calls = []


def _wrap_sd_with_side_effect(mock_shell, side_effect_fn):
    """Wrap run_with_stall_detection with a side-effect callback.

    The side_effect_fn receives the command list and is called before
    the original method.  Useful for creating target files so stat() works.
    """
    original_sd = mock_shell.run_with_stall_detection

    def _wrapped(cmd, output_file=None, stall_timeout=1800, check=False):
        side_effect_fn(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = _wrapped


def _rsync_side_effect(cmd):
    """Create target file for rsync commands."""
    cmd_str = " ".join(cmd)
    if cmd_str.startswith("rsync "):
        tgt = Path(cmd[-1])
        tgt.parent.mkdir(parents=True, exist_ok=True)
        tgt.write_bytes(b"\x00" * 65536)


@pytest.fixture(autouse=True)
def _ensure_snapshot_paths_exist(monkeypatch):
    """Make os.path.exists return True for fake snapshot paths in tests.

    The stale state guard in ``transfer_missing`` checks
    ``os.path.exists(snapshot.path)`` before rsync.  Most tests use
    fake paths like ``/snapshots/testvm.X.qcow2`` that don't exist on
    disk.  This fixture patches ``os.path.exists`` to return True for
    those paths while preserving real behavior for everything else.
    """
    real_exists = os.path.exists

    def _fake_exists(path):
        path_str = str(path)
        if "/snapshots/" in path_str:
            return True
        return real_exists(path)

    monkeypatch.setattr(os.path, "exists", _fake_exists)


# ──────────────────────────────────────────────────────────────────────────
# Transfer Missing — rsync-based (no cp fallback — design D3)
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_missing_new_snapshot_rsync_empty_target(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When the target path does not exist (``list()`` returns ``[]``) and
    there is one snapshot to copy, the provider copies it via ``rsync`` to
    ``target.path/<snapshot.name>.qcow2`` and returns
    ``BackupResult(success=True, bytes_transferred=<file_size>)``.

    ``copy_base=True`` is set so the empty-target FULL-creation path is
    skipped and rsync is used directly.
    """
    vm_config = make_vm_config()
    # Target path does not exist -> list() returns [] with no shell calls
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate rsync creating the target file so stat() works.
    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    # Assert successful result with correct file size
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].bytes_transferred == 65536
    assert results[0].error is None
    assert results[0].snapshot_name == snapshot.name
    assert results[0].source_path == snapshot.path
    assert results[0].target_path == expected_target_file

    # Assert rsync command copies to target.path/<snapshot.name>.qcow2
    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert str(snapshot.path) in rsync_cmds[0]
    assert str(expected_target_file) in rsync_cmds[0]
    # No rate limit → no --bwlimit
    assert "--bwlimit" not in rsync_cmds[0]
    # Has --partial for resumability
    assert "--partial" in rsync_cmds[0]
    # --compress should appear because target.compress defaults to True
    assert "--compress" in rsync_cmds[0]
    assert "--compress-choice=zstd" in rsync_cmds[0], (
        f"--compress-choice=zstd should be in rsync command (default zstd), got: {rsync_cmds[0]}"
    )
    assert "--compress" in rsync_cmds[0].split("--partial")[0], (
        f"--compress should appear before --partial in: {rsync_cmds[0]}"
    )
    # No cp fallback
    cp_cmds = [cmd for cmd in all_cmds if cmd.startswith("cp ")]
    assert len(cp_cmds) == 0


def test_transfer_missing_existing_snapshot_skipped(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When the target already contains a backup with the same name as the
    snapshot, the snapshot is NOT copied (``rsync`` is NOT called) and does
    not appear in the returned ``BackupResult`` list.
    """
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path), incremental=False, verify="off")

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # Create the target file so list() finds it as an existing backup
    target_file = tmp_path / f"{snapshot.name}.qcow2"
    target_file.touch()

    # Mock qemu-img info for list() to return success with actual-size
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 65536}),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Snapshot is skipped — no results returned
    assert len(results) == 0

    # rsync is NOT called
    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 0


def test_transfer_incremental_rebase_backing_path(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``target.incremental`` is True and the copied snapshot has a
    backing file, ``qemu-img rebase -u -b <bare_filename> <target_file>``
    is executed.

    CRITICAL (design D5):
    - The ``-u`` flag MUST be present (unsafe, metadata-only update).
    - The backing path MUST be the bare filename (e.g. ``backing.qcow2``),
      NOT the full source path — because the backing file is in the same
      target directory on a different filesystem (XFS).

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=True,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Mock qemu-img info on source returns JSON with backing-filename
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "actual-size": 65536,
                    "backing-filename": "/source/path/backing.qcow2",
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Mock qemu-img rebase returns success
    mock_shell.expect("qemu-img rebase").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True

    # Verify rebase command
    all_cmds = _all_cmds(mock_shell)
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 1
    rebase_cmd = rebase_cmds[0]

    # CRITICAL: -u flag is present (unsafe, metadata-only update)
    assert " -u " in rebase_cmd
    # CRITICAL: backing path is the bare filename, not the full source path
    assert "-b backing.qcow2" in rebase_cmd
    assert "/source/path/backing.qcow2" not in rebase_cmd
    # CRITICAL: -B qcow2 flag is present (design D3: renamed from -F in QEMU 11.0)
    assert "-B qcow2" in rebase_cmd
    # Verify target file is in the rebase command
    assert str(expected_target_file) in rebase_cmd

    # Verify --compress in rsync command (target.compress defaults to True)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--compress" in rsync_cmds[0]
    assert "--compress-choice=zstd" in rsync_cmds[0], (
        f"--compress-choice=zstd should be in rsync command (default zstd), got: {rsync_cmds[0]}"
    )
    assert "--compress" in rsync_cmds[0].split("--partial")[0], (
        f"--compress should appear before --partial in: {rsync_cmds[0]}"
    )


def test_transfer_non_incremental_no_rebase(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``target.incremental`` is False, the snapshot is copied without
    calling ``qemu-img rebase``.  The backing path remains as-is.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True

    # Verify rebase is NOT called
    all_cmds = _all_cmds(mock_shell)
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 0

    # Verify --compress in rsync command (target.compress defaults to True)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--compress" in rsync_cmds[0]
    assert "--compress-choice=zstd" in rsync_cmds[0], (
        f"--compress-choice=zstd should be in rsync command (default zstd), got: {rsync_cmds[0]}"
    )
    assert "--compress" in rsync_cmds[0].split("--partial")[0], (
        f"--compress should appear before --partial in: {rsync_cmds[0]}"
    )


def test_transfer_rsync_fails_disk_full(mock_shell, make_vm_config, make_target, tmp_path, caplog):
    """When ``rsync`` returns a non-zero exit code (e.g. disk full), the
    provider returns ``BackupResult(success=False, error=<stderr>)``.

    A WARNING log is emitted before returning the failure result so that
    silent failures are impossible to miss in production logs.

    The partial file left by ``rsync --partial`` is cleaned up via
    ``rm -f`` before returning the failure result (design D2).

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # Mock rsync returns failure (disk full)
    error_msg = "No space left on device"
    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=error_msg,
            returncode=1,
            error=error_msg,
        )
    )
    # Mock rm -f for partial file cleanup
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.WARNING, logger="qsnap.modules.backup.file_copy")

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert failure with error message
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error == error_msg
    assert results[0].bytes_transferred == 0
    assert results[0].snapshot_name == snapshot.name
    assert results[0].source_path == snapshot.path
    assert results[0].target_path == expected_target_file

    # Assert WARNING was logged before returning failure
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("rsync failed for testvm.20250101T000000" in msg for msg in warnings), (
        f"Expected 'rsync failed' WARNING, got: {warnings}"
    )

    # Assert rm -f was called to clean up the partial target file (design D2)
    all_cmds = _all_cmds(mock_shell)
    rm_cmds = [cmd for cmd in all_cmds if cmd.startswith("rm -f")]
    assert len(rm_cmds) >= 1, (
        f"Expected rm -f to clean up partial file after rsync failure, got: {all_cmds}"
    )
    assert any(str(expected_target_file) in cmd for cmd in rm_cmds), (
        f"rm -f should target {expected_target_file}, got: {rm_cmds}"
    )


def test_rsync_unavailable_transfer_fails_no_cp_fallback(
    make_vm_config, make_target, tmp_path, caplog
):
    """When rsync is not available (``MockShell`` returns failure for
    ``rsync``), the transfer fails with no ``cp`` fallback (design D3).

    A fresh ``MockShell`` is used (without conftest's pre-configured
    expectations) so the rsync command returns failure.  A WARNING log is
    emitted to ensure the failure is visible in production logs.

    The partial file left by ``rsync --partial`` is cleaned up via
    ``rm -f`` before returning the failure result (design D2).
    """
    shell = MockShell()
    # rsync → failure (not installed or unavailable)
    error_msg = "rsync: command not found"
    shell.expect(r"^rsync").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=error_msg,
            returncode=127,
            error=error_msg,
        )
    )
    # rm -f for partial file cleanup
    shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    caplog.set_level(logging.WARNING, logger="qsnap.modules.backup.file_copy")

    _clear_calls(shell)
    with patch.object(shell, "run", wraps=shell.run):
        provider = FileCopyBackupProvider(shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    # Assert failure with the rsync error
    assert len(results) == 1
    assert results[0].success is False
    assert "rsync" in results[0].error

    # No cp was called — no fallback
    all_cmds = _all_cmds(shell)
    cp_cmds = [cmd for cmd in all_cmds if cmd.startswith("cp ")]
    assert len(cp_cmds) == 0, "cp should NOT be used as fallback when rsync fails (design D3)"

    # Assert WARNING was logged before returning failure
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("rsync failed for testvm.20250101T000000" in msg for msg in warnings), (
        f"Expected 'rsync failed' WARNING, got: {warnings}"
    )

    # Assert rm -f was called to clean up the partial target file (design D2)
    rm_cmds = [cmd for cmd in all_cmds if cmd.startswith("rm -f")]
    assert len(rm_cmds) >= 1, (
        f"Expected rm -f to clean up partial file after rsync failure, got: {all_cmds}"
    )
    assert any(str(expected_target_file) in cmd for cmd in rm_cmds), (
        f"rm -f should target {expected_target_file}, got: {rm_cmds}"
    )


# ──────────────────────────────────────────────────────────────────────────
# List Backups
# ──────────────────────────────────────────────────────────────────────────


def test_list_backups_target_exists(mock_shell, make_target, tmp_path):
    """When ``target.path`` contains ``.qcow2`` files, ``list()`` returns
    ``SnapshotInfo`` objects with metadata from ``qemu-img info``, sorted
    by timestamp.
    """
    target = make_target(path=str(tmp_path))

    # Create .qcow2 files in the target directory
    file1 = tmp_path / "vm.20250101T000000.qcow2"
    file2 = tmp_path / "vm.20250102T000000.qcow2"
    file1.touch()
    file2.touch()

    # Mock qemu-img info returns JSON with actual-size
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 65536}),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    provider = FileCopyBackupProvider(mock_shell)
    snapshots = provider.list(target)

    # Assert 2 SnapshotInfo returned, sorted by timestamp
    assert len(snapshots) == 2
    assert snapshots[0].name == "vm.20250101T000000"
    assert snapshots[1].name == "vm.20250102T000000"
    assert snapshots[0].timestamp == datetime(2025, 1, 1, 0, 0, 0)
    assert snapshots[1].timestamp == datetime(2025, 1, 2, 0, 0, 0)
    assert snapshots[0].timestamp < snapshots[1].timestamp


def test_list_backups_target_not_exists(mock_shell, make_target, tmp_path):
    """When ``target.path`` does not exist, ``list()`` returns an empty
    list and no shell commands are executed.
    """
    target = make_target(path=str(tmp_path / "nonexistent"))

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        snapshots = provider.list(target)

    assert len(snapshots) == 0
    assert shell_spy.call_count == 0


def test_list_backups_target_empty(mock_shell, make_target, tmp_path):
    """When ``target.path`` exists but contains no ``.qcow2`` files,
    ``list()`` returns an empty list.
    """
    target = make_target(path=str(tmp_path))

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        snapshots = provider.list(target)

    assert len(snapshots) == 0
    assert shell_spy.call_count == 0


# ──────────────────────────────────────────────────────────────────────────
# Delete Backups
# ──────────────────────────────────────────────────────────────────────────


def test_delete_backup_success(mock_shell):
    """When ``rm -f`` completes successfully, ``delete()`` returns
    ``ShellResult(success=True)``.
    """
    mock_shell.expect("rm").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/backups/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    provider = FileCopyBackupProvider(mock_shell)
    result = provider.delete(snapshot)

    assert result.success is True
    assert result.returncode == 0
    assert result.error is None


def test_delete_backup_file_not_found(mock_shell):
    """When the backup file does not exist, ``rm -f`` is idempotent and
    still returns success.  ``delete()`` therefore returns
    ``ShellResult(success=True)``.
    """
    mock_shell.expect("rm").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/backups/nonexistent.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    provider = FileCopyBackupProvider(mock_shell)
    result = provider.delete(snapshot)

    assert result.success is True
    assert result.error is None


# ──────────────────────────────────────────────────────────────────────────
# Rebase failure & shared parser imports
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_rebase_failure_returns_backup_result_failure(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
):
    """When ``qemu-img rebase`` fails (MockShell returns failure) while
    rebasing to a FULL anchor, ``transfer_missing`` returns a
    ``BackupResult`` with ``success=False`` and an error message
    containing ``"rebase failed"``.

    A ``WARNING`` log is emitted before returning the failure so that
    silent rebase failures are visible in production logs.

    This test targets the *FULL-anchor rebase* path (``rebase to FULL
    failed``), which runs when a valid FULL anchor exists in the target
    directory.  The anchor passes M1 verification and the rebase command
    itself fails.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"),
        incremental=True,
        verify="off",
        copy_base=True,
    )
    target.path.mkdir(parents=True, exist_ok=True)

    # Pre-create a FULL anchor file so the rebase-to-FULL path is taken
    anchor_name = "testvm.FULL.20250101.qcow2"
    anchor_file = target.path / anchor_name
    anchor_file.write_bytes(b"\x00" * 1024)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # qemu-img info returns valid qcow2 metadata for:
    #   - list() on the anchor file
    #   - verify_full_backup() M1 check on the anchor
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1073741824,
                    "actual-size": 1024,
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Mock qemu-img rebase returns FAILURE
    rebase_error = "rebase error: backing file not found"
    mock_shell.expect("qemu-img rebase").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=rebase_error,
            returncode=1,
            error=rebase_error,
        )
    )

    caplog.set_level(logging.WARNING, logger="qsnap.modules.backup.file_copy")

    provider = FileCopyBackupProvider(mock_shell)
    results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False
    assert "rebase failed" in results[0].error

    # Assert WARNING was logged for rebase-to-FULL failure
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("rebase to FULL failed for testvm.20250101T000000" in msg for msg in warnings), (
        f"Expected 'rebase to FULL failed' WARNING, got: {warnings}"
    )


def test_transfer_verify_failure_deletes_file_and_logs_warning(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
):
    """When ``verify_backup()`` returns an error (e.g. metadata mismatch),
    a ``WARNING`` log is emitted AND the partially-transferred target file
    is deleted via ``rm -f`` before returning ``BackupResult(success=False)``.

    The file deletion (design D2) ensures retention cleanup does not find a
    broken backup and log a misleading ``[delete] removed backup`` message.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="metadata",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Mock rm -f for partial file cleanup
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    verify_error = "verification failed: metadata mismatch — virtual-size differs"

    caplog.set_level(logging.WARNING, logger="qsnap.modules.backup.file_copy")

    with (
        patch(
            "qsnap.modules.backup.file_copy.verify_backup",
            return_value=verify_error,
        ),
        patch.object(mock_shell, "run", wraps=mock_shell.run),
    ):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert failure with the verify error
    assert len(results) == 1
    assert results[0].success is False
    assert verify_error in results[0].error

    # Assert WARNING was logged before returning failure
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "backup verification failed for testvm.20250101T000000" in msg for msg in warnings
    ), f"Expected 'backup verification failed' WARNING, got: {warnings}"

    # Assert rm -f was called to clean up the partial target file
    all_cmds = _all_cmds(mock_shell)
    rm_cmds = [cmd for cmd in all_cmds if cmd.startswith("rm -f")]
    assert len(rm_cmds) >= 1, (
        f"Expected rm -f to be called for partial file cleanup, got: {all_cmds}"
    )
    target_rm_cmds = [cmd for cmd in rm_cmds if str(expected_target_file) in cmd]
    assert len(target_rm_cmds) >= 1, (
        f"Expected rm -f {expected_target_file}, got rm commands: {rm_cmds}"
    )


def test_transfer_json_decode_failure_logs_warning(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
):
    """When ``qemu-img info --output=json`` returns invalid JSON in the
    no-FULL-anchor fallback rebase path, a ``WARNING`` log is emitted
    before returning ``BackupResult(success=False)``.

    This covers the ``json.JSONDecodeError`` exception path in
    ``transfer_missing()`` — the backing info parse failure is logged at
    WARNING level so operators can detect corrupted qemu-img output.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=True,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Mock qemu-img info returns success but with invalid JSON
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout="not valid json {{{",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    caplog.set_level(logging.WARNING, logger="qsnap.modules.backup.file_copy")

    provider = FileCopyBackupProvider(mock_shell)
    results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert failure due to JSON parse error
    assert len(results) == 1
    assert results[0].success is False
    assert "rebase failed" in results[0].error

    # Assert WARNING was logged before returning failure
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("backing info parse failed for testvm.20250101T000000" in msg for msg in warnings), (
        f"Expected 'backing info parse failed' WARNING, got: {warnings}"
    )


def test_file_copy_provider_imports_shared_parsers():
    """Verify ``file_copy.py`` imports ``parse_timestamp`` from
    ``qsnap.utils.parsing`` (shared parser, not a local duplicate).
    """
    from qsnap.modules.backup import file_copy
    from qsnap.utils.parsing import parse_timestamp

    assert hasattr(file_copy, "parse_timestamp")
    assert file_copy.parse_timestamp is parse_timestamp


# ──────────────────────────────────────────────────────────────────────────
# Verification (metadata / full / off)
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_missing_metadata_verification_default(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``target.verify`` is ``"metadata"`` (the default), after the
    ``rsync`` command, ``qemu-img info`` is called on both source and
    target to verify format, virtual-size, and actual-size.

    With matching metadata, ``transfer_missing`` returns
    ``BackupResult(success=True)``.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"),
        incremental=False,
        copy_base=True,
        # verify defaults to "metadata"
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    qcow2_info = json.dumps(
        {
            "format": "qcow2",
            "virtual-size": 1073741824,
            "actual-size": 1048576,
        }
    )

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Mock qemu-img info (used by verification for both source and target)
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=qcow2_info,
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Side effect: simulate rsync creating the target file so stat() works.
    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    # Verify qemu-img info was called (for verification: source + target)
    all_cmds = _all_cmds(mock_shell)
    info_cmds = [cmd for cmd in all_cmds if "qemu-img info" in cmd]
    assert len(info_cmds) >= 2

    # Verify --compress in rsync command (target.compress defaults to True)
    # NOTE: At the dataclass level, default verify is "metadata", but
    # ConfigFacade would resolve to "hash" for file-copy mode.
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--compress" in rsync_cmds[0]
    assert "--compress-choice=zstd" in rsync_cmds[0], (
        f"--compress-choice=zstd should be in rsync command (default zstd), got: {rsync_cmds[0]}"
    )
    assert "--compress" in rsync_cmds[0].split("--partial")[0], (
        f"--compress should appear before --partial in: {rsync_cmds[0]}"
    )


def test_transfer_missing_full_verification(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``target.verify`` is ``"full"``, after the metadata check,
    ``qemu-img compare`` is called to verify byte-level integrity.

    With both metadata and compare succeeding, ``transfer_missing``
    returns ``BackupResult(success=True)``.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"),
        incremental=False,
        verify="full",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    qcow2_info = json.dumps(
        {
            "format": "qcow2",
            "virtual-size": 1073741824,
            "actual-size": 1048576,
        }
    )

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Mock qemu-img info (for verification)
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=qcow2_info,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Mock qemu-img compare returns success
    mock_shell.expect(r"qemu-img compare").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate rsync creating the target file so stat() works.
    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    # Verify qemu-img compare was called
    all_cmds = _all_cmds(mock_shell)
    compare_cmds = [cmd for cmd in all_cmds if "qemu-img compare" in cmd]
    assert len(compare_cmds) == 1

    # Verify --compress in rsync command (target.compress defaults to True)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--compress" in rsync_cmds[0]
    assert "--compress-choice=zstd" in rsync_cmds[0], (
        f"--compress-choice=zstd should be in rsync command (default zstd), got: {rsync_cmds[0]}"
    )
    assert "--compress" in rsync_cmds[0].split("--partial")[0], (
        f"--compress should appear before --partial in: {rsync_cmds[0]}"
    )


def test_transfer_missing_no_verification_when_off(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``target.verify`` is ``"off"``, no ``qemu-img`` commands are
    called after ``rsync``.  Only ``rsync`` is executed (no rebase since
    ``incremental=False``), and ``transfer_missing`` returns
    ``BackupResult(success=True)``.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate rsync creating the target file so stat() works.
    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    # Verify NO qemu-img commands were called
    all_cmds = _all_cmds(mock_shell)
    qemu_cmds = [cmd for cmd in all_cmds if "qemu-img" in cmd]
    assert len(qemu_cmds) == 0

    # Verify --compress in rsync command (target.compress defaults to True)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--compress" in rsync_cmds[0]
    assert "--compress-choice=zstd" in rsync_cmds[0], (
        f"--compress-choice=zstd should be in rsync command (default zstd), got: {rsync_cmds[0]}"
    )
    assert "--compress" in rsync_cmds[0].split("--partial")[0], (
        f"--compress should appear before --partial in: {rsync_cmds[0]}"
    )


# ──────────────────────────────────────────────────────────────────────────
# Full backup creation (qemu-img convert)
# ──────────────────────────────────────────────────────────────────────────


def test_create_full_backup_uncompressed_stopped_vm(mock_shell, make_target, tmp_path):
    """``create_full_backup(compress=False, bucket_level="monthly")`` with
    a stopped VM calls ``qemu-img convert`` WITHOUT the ``-c`` flag and
    returns ``BackupResult(success=True)``.

    The command pattern is ``qemu-img convert -f qcow2 -O qcow2 <src> <tmp>``.
    The direct ``qemu-img convert`` path is used (no NBD, no ``virsh
    backup-begin``).
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # VM is stopped → direct qemu-img convert
    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Mock qemu-img convert returns success
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Mock mv (atomic rename) returns success
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate mv creating the final file so stat() works
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    # Assert successful result
    assert result.success is True
    assert result.error is None
    assert result.bytes_transferred == 65536
    assert result.snapshot_name == snapshot.name

    # Verify qemu-img convert was called WITHOUT -c flag
    all_cmds = _all_cmds(mock_shell)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    convert_args_list = [c for c in mock_shell._calls if "qemu-img convert" in " ".join(c)]
    convert_args = convert_args_list[0]
    assert "-c" not in convert_args, "qemu-img convert should NOT contain -c when compress=False"
    # Verify required format flags are present
    assert "-f" in convert_args
    assert "qcow2" in convert_args
    assert "-O" in convert_args

    # Verify direct convert path used, no NBD
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 0, "virsh backup-begin should NOT be called for stopped VM"
    nbd_cmds = [cmd for cmd in all_cmds if "nbd:unix:" in cmd]
    assert len(nbd_cmds) == 0, "NBD should NOT be used for stopped VM"


def test_create_full_backup_compressed_stopped_vm(mock_shell, make_target, tmp_path):
    """``create_full_backup(compress=True, bucket_level="monthly")`` with
    a stopped VM calls ``qemu-img convert`` WITH the ``-c`` flag and
    returns ``BackupResult(success=True)``.

    The command pattern is ``qemu-img convert -c -f qcow2 -O qcow2 <src> <tmp>``.
    The direct ``qemu-img convert`` path is used (no NBD, no ``virsh
    backup-begin``).
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # VM is stopped → direct qemu-img convert
    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Mock qemu-img convert returns success
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Mock mv (atomic rename) returns success
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate mv creating the final file so stat() works
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=True,
            bucket_level="monthly",
        )

    # Assert successful result
    assert result.success is True
    assert result.error is None
    assert result.bytes_transferred == 65536

    # Verify qemu-img convert was called WITH -c flag
    all_cmds = _all_cmds(mock_shell)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    convert_args_list = [c for c in mock_shell._calls if "qemu-img convert" in " ".join(c)]
    convert_args = convert_args_list[0]
    assert "-c" in convert_args, "qemu-img convert should contain -c when compress=True"
    assert "-o compression_type=zstd" in " ".join(convert_args), (
        f"qemu-img convert should contain -o compression_type=zstd (default zstd), "
        f"got: {' '.join(convert_args)}"
    )
    # Verify -c appears before -f (flag ordering in source)
    assert convert_args.index("-c") < convert_args.index("-f")

    # Verify direct convert path used, no NBD
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 0, "virsh backup-begin should NOT be called for stopped VM"
    nbd_cmds = [cmd for cmd in all_cmds if "nbd:unix:" in cmd]
    assert len(nbd_cmds) == 0, "NBD should NOT be used for stopped VM"


# ──────────────────────────────────────────────────────────────────────────
# NBD-based full backup (running VM, libvirt >= 6.0)
# ──────────────────────────────────────────────────────────────────────────


def test_create_full_backup_nbd_running_vm_succeeds(mock_shell, make_target, tmp_path):
    """When the VM is running and libvirt >= 6.0, ``create_full_backup``
    uses NBD pull-model (``virsh backup-begin`` + ``qemu-img convert -n
    nbd:unix:``) and returns ``BackupResult(success=True)``.

    Verifies:
    - ``virsh backup-begin`` called WITHOUT ``--incremental``
    - ``qemu-img convert -n nbd:unix:...`` used
    - No direct ``qemu-img convert -f qcow2 -O qcow2``
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # NBD path: libvirt >= 6.0 required
    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # rm -f stale socket (before backup-begin)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # virsh backup-begin succeeds (no --incremental)
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # qemu-img convert via NBD succeeds
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # rm -f socket cleanup (in finally)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # mv (atomic rename) returns success
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate mv creating the final file so stat() works
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    # Assert successful result
    assert result.success is True
    assert result.error is None
    assert result.bytes_transferred == 65536
    assert result.snapshot_name == snapshot.name

    all_cmds = _all_cmds(mock_shell)

    # Verify backup-begin called WITHOUT --incremental
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]

    # Verify qemu-img convert uses NBD
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]

    # Verify NO direct qemu-img convert path (no -f -O flags)
    direct_cmds = [cmd for cmd in convert_cmds if "-f" in cmd and "-O" in cmd]
    assert len(direct_cmds) == 0, "Direct qemu-img convert (-f/-O) should NOT be used for NBD path"


def test_create_full_backup_direct_stopped_vm_succeeds(mock_shell, make_target, tmp_path):
    """When the VM is stopped (``State: shut off``), ``create_full_backup``
    uses direct ``qemu-img convert`` with no NBD and returns
    ``BackupResult(success=True)``.

    Verifies:
    - ``virsh dominfo`` returns ``State: shut off``
    - Direct ``qemu-img convert -f qcow2 -O qcow2`` called
    - No ``virsh backup-begin``
    - No ``nbd:unix:``
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # VM is stopped
    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Direct qemu-img convert
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # mv (atomic rename)
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True
    assert result.error is None
    assert result.bytes_transferred == 65536

    all_cmds = _all_cmds(mock_shell)

    # Verify direct convert with format flags
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-f qcow2" in convert_cmds[0]
    assert "-O qcow2" in convert_cmds[0]

    # Verify NO NBD
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 0
    nbd_cmds = [cmd for cmd in all_cmds if "nbd:unix:" in cmd]
    assert len(nbd_cmds) == 0


def test_create_full_backup_vm_state_detection_fails_falls_back(
    mock_shell, make_target, tmp_path, caplog
):
    """When ``virsh dominfo`` fails, a WARNING is logged and the provider
    falls back to direct ``qemu-img convert`` (best-effort).

    No NBD is attempted since the VM state is unknown.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # virsh dominfo FAILS → VM state unknown
    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="virsh: error: failed to connect",
            returncode=1,
            error="virsh: error: failed to connect",
        )
    )

    # Direct convert fallback
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    caplog.set_level(logging.WARNING, logger="qsnap.utils.nbd")

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    # Falls back to direct convert — succeeds
    assert result.success is True

    # WARNING about VM state detection failure
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Failed to detect VM running state" in msg for msg in warnings), (
        f"Expected VM state detection failure warning, got: {warnings}"
    )

    all_cmds = _all_cmds(mock_shell)

    # Direct convert used
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-f qcow2" in convert_cmds[0]

    # No NBD attempted
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 0


def test_nbd_full_export_produces_standalone_qcow2(mock_shell, make_target, tmp_path):
    """After ``create_full_backup`` via NBD, the resulting qcow2 has no
    backing file (``qemu-img info`` shows no ``backing-filename``).

    NBD full export creates a standalone qcow2 because it exports the
    entire disk state through the socket — there is no backing chain on
    the target.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # NBD path mocks
    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Simulate mv creating the final file
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    # Now verify the result is standalone: mock qemu-img info on the
    # target file to return no backing filename.
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1073741824,
                    "actual-size": 1048576,
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    info_cmd = [
        "qemu-img",
        "info",
        "--output=json",
        str(result.target_path),
    ]
    info_result = mock_shell.run(info_cmd, timeout=30)
    info_data = json.loads(info_result.stdout)
    assert "backing-filename" not in info_data, (
        f"NBD full export should produce a standalone qcow2 with no "
        f"backing file, but got backing-filename: "
        f"{info_data.get('backing-filename', 'N/A')}"
    )


def test_nbd_socket_cleanup_on_success(mock_shell, make_target, tmp_path):
    """When ``create_full_backup`` via NBD succeeds, the Unix socket is
    removed via ``rm -f`` in the ``finally`` block.

    ``rm -f`` is called at least twice: once before ``backup-begin``
    (stale socket removal) and once in ``finally`` (cleanup).
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate mv creating the final file so stat() works
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    # Count rm -f commands (socket cleanup before + after)
    all_cmds = _all_cmds(mock_shell)
    rm_cmds = [cmd for cmd in all_cmds if cmd.startswith("rm -f")]
    # At least 2: stale socket removal + finally cleanup
    assert len(rm_cmds) >= 2, f"Expected >= 2 rm -f calls (stale socket + finally), got: {rm_cmds}"
    # Verify socket paths in rm commands
    socket_rm_cmds = [cmd for cmd in rm_cmds if "/tmp/qsnap-backup-" in cmd]
    assert len(socket_rm_cmds) == 2, f"Expected 2 socket rm -f calls, got: {socket_rm_cmds}"


def test_nbd_socket_cleanup_on_failure(mock_shell, make_target, tmp_path):
    """When ``qemu-img convert`` (NBD pull) fails, the Unix socket is
    still removed via ``rm -f`` in the ``finally`` block.

    Socket cleanup is guaranteed even when the NBD export fails.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # qemu-img convert FAILS
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="NBD connection reset",
            returncode=1,
            error="NBD connection reset",
        )
    )
    # rm -f .tmp file (in create_full_backup error handling)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is False

    # Verify socket cleanup rm -f was called despite failure
    all_cmds = _all_cmds(mock_shell)
    rm_cmds = [cmd for cmd in all_cmds if cmd.startswith("rm -f")]
    socket_rm_cmds = [cmd for cmd in rm_cmds if "/tmp/qsnap-backup-" in cmd]
    assert len(socket_rm_cmds) >= 1, (
        f"Socket cleanup rm -f should be called even on failure, got: {socket_rm_cmds}"
    )


def test_nbd_full_file_copy_no_checkpoint_created(mock_shell, make_target, tmp_path):
    """``create_full_backup`` via NBD for ``FileCopyBackupProvider`` does
    NOT create or delete any checkpoints.

    No ``virsh checkpoint-create-as`` or ``virsh checkpoint-delete``
    calls should appear.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    all_cmds = _all_cmds(mock_shell)

    # No checkpoints
    cp_create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(cp_create_cmds) == 0, "FileCopyBackupProvider should NOT create checkpoints"
    cp_delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(cp_delete_cmds) == 0, "FileCopyBackupProvider should NOT delete checkpoints"


def test_nbd_full_timestamp_matches_snapshot_not_export_time(
    mock_shell, make_target, tmp_path, mock_state
):
    """``create_full_backup()`` no longer calls ``record_full_backup()``
    itself — that responsibility has moved to callers
    (``transfer_missing`` D4 path, Core's ``_backup_target``).

    The FULL backup result still uses ``source_snapshot.timestamp`` for
    retention bucket alignment — callers pass that timestamp to
    ``record_full_backup()``.  Here we verify that ``create_full_backup``
    returns the correct timestamp (embedded in the filename) but does NOT
    record state itself.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell, state=mock_state)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    # Verify create_full_backup does NOT record state itself — callers do it.
    full_backups = mock_state.get_full_backups(str(target.path))
    assert len(full_backups) == 0, (
        "create_full_backup should NOT call record_full_backup — "
        "callers are responsible for recording state"
    )

    # Verify the result filename still embeds the snapshot timestamp
    # (callers use source_snapshot.timestamp for retention alignment).
    assert "20250101" in str(result.target_path), (
        f"FULL filename should embed snapshot date 20250101, got: {result.target_path}"
    )


def test_nbd_full_old_libvirt_falls_back_direct_convert(mock_shell, make_target, tmp_path, caplog):
    """When libvirt < 6.0 and VM is running, a WARNING is logged and the
    provider falls back to direct ``qemu-img convert`` (best-effort).
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # VM is running (from conftest), but libvirt is OLD
    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 5.9.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Direct convert fallback
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    caplog.set_level(logging.WARNING, logger="qsnap.modules.backup.file_copy")

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    # WARNING about old libvirt → fallback
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("libvirt < 6.0" in msg for msg in warnings), (
        f"Expected old-libvirt warning, got: {warnings}"
    )

    all_cmds = _all_cmds(mock_shell)

    # Direct convert used
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-f qcow2" in convert_cmds[0]

    # No NBD attempted
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 0


def test_nbd_full_creates_tmp_then_renames(mock_shell, make_target, tmp_path):
    """``create_full_backup`` via NBD writes data to a ``.tmp`` file, then
    atomically renames it to the final name on success.

    Verifies:
    - ``qemu-img convert`` target is ``.tmp``
    - ``mv`` from ``.tmp`` to final name
    - ``BackupResult.path`` is the final (non-``.tmp``) path
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    all_cmds = _all_cmds(mock_shell)

    # Verify qemu-img convert writes to .tmp
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert ".tmp" in convert_cmds[0], (
        f"Expected convert target to be .tmp file, got: {convert_cmds[0]}"
    )

    # Verify mv from .tmp to final
    mv_cmds = [cmd for cmd in all_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 1
    assert ".tmp" in mv_cmds[0], f"Expected mv from .tmp, got: {mv_cmds[0]}"

    # Verify result path is the FINAL (non-.tmp) path
    assert ".tmp" not in str(result.target_path), (
        f"Result path should be final (non-.tmp), got: {result.target_path}"
    )
    assert ".FULL." in str(result.target_path)


def test_nbd_full_failure_leaves_no_final_file(mock_shell, make_target, tmp_path):
    """When ``create_full_backup`` via NBD fails (``qemu-img convert``
    error), the ``.tmp`` file is removed and no final ``.FULL.*.qcow2``
    file is created.

    Result is ``BackupResult(success=False)``.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # qemu-img convert FAILS
    convert_error = "qemu-img: NBD I/O error"
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=convert_error,
            returncode=1,
            error=convert_error,
        )
    )

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is False
    assert result.error == convert_error

    all_cmds = _all_cmds(mock_shell)

    # Verify .tmp file is removed
    tmp_rm_cmds = [cmd for cmd in all_cmds if cmd.startswith("rm -f") and ".tmp" in cmd]
    assert len(tmp_rm_cmds) >= 1, f"Expected rm -f on .tmp file after failure, got: {all_cmds}"

    # Verify no mv was called (no final file rename)
    mv_cmds = [cmd for cmd in all_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 0

    # Verify no final .FULL. file exists
    full_files = list(target.path.glob("*.FULL.*.qcow2"))
    assert len(full_files) == 0, f"No final FULL file should exist after failure, got: {full_files}"


def test_nbd_full_backup_with_compression_succeeds(mock_shell, make_target, tmp_path, caplog):
    """When ``compress=True`` and NBD is selected, the ``-c`` flag IS
    passed to ``qemu-img convert`` (compression is now supported over
    NBD).  No WARNING about ``compress=True ignored`` is logged.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="myvm.20250101T000000",
        path=Path("/snapshots/myvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    caplog.set_level(logging.WARNING, logger="qsnap.modules.backup.file_copy")

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "myvm",
            snapshot,
            target,
            compress=True,
            bucket_level="daily",
        )

    assert result.success is True
    assert result.error is None
    assert result.bytes_transferred == 65536
    assert result.snapshot_name == snapshot.name

    # NBD path used WITH -c (compression supported over NBD)
    all_cmds = _all_cmds(mock_shell)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0], "NBD path should be used for running VM"
    assert "-c" in convert_cmds[0], "NBD path should use -c when compress=True"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        f"NBD convert should include -o compression_type=zstd (default zstd), "
        f"got: {convert_cmds[0]}"
    )

    # NO WARNING about compress=True ignored
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    compress_ignored = [msg for msg in warnings if "compress=True ignored" in msg]
    assert len(compress_ignored) == 0, (
        f"Should NOT log 'compress=True ignored' warning (compression "
        f"is now supported over NBD). Got: {warnings}"
    )


def test_nbd_full_no_force_share_on_convert(mock_shell, make_target, tmp_path):
    """``create_full_backup`` via NBD does NOT use ``--force-share`` on
    the ``qemu-img convert`` command.

    The NBD pull-model avoids the lock conflict by design — data is read
    through the Unix socket served by QEMU, not by opening the qcow2 file
    directly.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    all_cmds = _all_cmds(mock_shell)

    # No --force-share anywhere
    force_share_cmds = [cmd for cmd in all_cmds if "--force-share" in cmd]
    assert len(force_share_cmds) == 0, (
        f"--force-share should NOT appear in any NBD convert command, got: {force_share_cmds}"
    )

    # NBD used (not direct convert with --force-share)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]


# ──────────────────────────────────────────────────────────────────────────
# FULL anchor rebase in transfer_missing
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_missing_rebases_to_full_anchor(mock_shell, make_vm_config, make_target, tmp_path):
    """When a FULL anchor file (``*.FULL.*.qcow2``) exists in the target
    directory, ``transfer_missing`` rebases the copied snapshot to the
    anchor's bare filename (``./<anchor_name>``) instead of querying the
    source's backing-filename.

    This is the FULL-anchor rebase path (design D5 extension): the
    incremental backup points to the standalone FULL backup in the target
    directory, not to the source backing chain.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"),
        incremental=True,
        verify="off",
    )
    target.path.mkdir(parents=True, exist_ok=True)

    # Pre-create a FULL anchor file in the target directory
    anchor_name = "testvm.FULL.20250101.qcow2"
    anchor_file = target.path / anchor_name
    anchor_file.write_bytes(b"\x00" * 1024)

    snapshot = SnapshotInfo(
        name="testvm.20250102T000000",
        path=Path("/snapshots/testvm.20250102T000000.qcow2"),
        timestamp=datetime(2025, 1, 2, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # list() calls qemu-img info on the existing anchor file.
    # Also used by verify_full_backup() for M1 on the anchor candidate.
    # Must include format/virtual-size for M1 to pass.
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1073741824,
                    "actual-size": 1024,
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # rsync succeeds
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # qemu-img rebase succeeds
    mock_shell.expect(r"qemu-img rebase").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate rsync creating the target file so stat() works
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None
    assert results[0].target_path == expected_target_file

    all_cmds = _all_cmds(mock_shell)

    # Verify rebase command rebases to the FULL anchor
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 1
    rebase_cmd = rebase_cmds[0]

    # CRITICAL: -u flag (unsafe, metadata-only)
    assert " -u " in rebase_cmd
    # CRITICAL: backing path is the bare anchor filename prefixed with ./
    assert f"./{anchor_name}" in rebase_cmd
    # CRITICAL: -B qcow2 flag is present (design D3: renamed from -F in QEMU 11.0)
    assert "-B qcow2" in rebase_cmd
    # Verify target file is in the rebase command
    assert str(expected_target_file) in rebase_cmd

    # CRITICAL: qemu-img info was NOT called on the source snapshot
    # (the FULL-anchor path skips the source backing-filename query)
    source_info_cmds = [
        cmd for cmd in all_cmds if "qemu-img info" in cmd and str(snapshot.path) in cmd
    ]
    assert len(source_info_cmds) == 0, (
        "qemu-img info should NOT be called on the source when a FULL "
        "anchor exists — the rebase should go directly to the anchor"
    )


def test_transfer_missing_no_full_anchor_uses_source_backing(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When NO FULL anchor file exists in the target directory,
    ``transfer_missing`` uses the source backing-filename for the rebase
    (the existing/legacy behavior before FULL anchors were introduced).

    This verifies that the ``qemu-img info`` call on the source snapshot
    IS made (to retrieve ``backing-filename``), and the rebase uses the
    bare basename of that backing path.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    # Target path does not exist → list() returns [], _find_full_anchor returns None
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=True,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # rsync succeeds
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # qemu-img info on source returns JSON with backing-filename
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "actual-size": 65536,
                    "backing-filename": "/source/path/backing.qcow2",
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # qemu-img rebase succeeds
    mock_shell.expect(r"qemu-img rebase").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate rsync creating the target file so stat() works
    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    all_cmds = _all_cmds(mock_shell)

    # Verify qemu-img info WAS called on the source (to get backing-filename)
    source_info_cmds = [
        cmd for cmd in all_cmds if "qemu-img info" in cmd and str(snapshot.path) in cmd
    ]
    assert len(source_info_cmds) == 1, (
        "qemu-img info should be called on the source snapshot when no "
        "FULL anchor exists (to retrieve backing-filename)"
    )

    # Verify rebase command uses the source backing basename
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 1
    rebase_cmd = rebase_cmds[0]

    # CRITICAL: -u flag (unsafe, metadata-only)
    assert " -u " in rebase_cmd
    # CRITICAL: backing path is the bare filename from source, not full path
    assert "-b backing.qcow2" in rebase_cmd
    assert "/source/path/backing.qcow2" not in rebase_cmd
    # CRITICAL: -B qcow2 flag is present (design D3: renamed from -F in QEMU 11.0)
    assert "-B qcow2" in rebase_cmd
    # Verify target file is in the rebase command
    assert str(expected_target_file) in rebase_cmd


# ──────────────────────────────────────────────────────────────────────────
# Rate-limited transfer (rsync --bwlimit)
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_with_rate_limit_uses_rsync(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``rate_limit`` is set, the provider uses
    ``rsync --bwlimit=<kib> --partial --progress``.

    ``rate_limit="100M"`` → ``rate_limit_to_kib("100M") == 102400`` →
    ``--bwlimit=102400``.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        rate_limit="100M",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # rsync → success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="100M")

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].bytes_transferred == 65536

    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--bwlimit=102400" in rsync_cmds[0]
    assert "--partial" in rsync_cmds[0]
    assert "--progress" in rsync_cmds[0]
    assert str(snapshot.path) in rsync_cmds[0]


def test_partial_file_resumes_with_rsync(mock_shell, make_vm_config, make_target, tmp_path):
    """When a partial file exists in the target and ``rate_limit`` is set,
    rsync is invoked with ``--partial`` to resume the interrupted transfer.

    The partial file is an incomplete ``.qcow2`` — ``qemu-img info`` fails
    on it, so ``list()`` skips it and the snapshot is treated as missing.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"),
        incremental=False,
        verify="off",
        rate_limit="100M",
        copy_base=True,
    )
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # Pre-create a partial file (incomplete qcow2)
    partial_file = target.path / f"{snapshot.name}.qcow2"
    partial_file.write_bytes(b"\x00" * 32768)

    # qemu-img info on the partial file fails → list() skips it
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="corrupt file",
            returncode=1,
            error="corrupt file",
        )
    )
    # rsync → success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="100M")

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--partial" in rsync_cmds[0]


def test_pre_transfer_info_log(mock_shell, make_vm_config, make_target, tmp_path, caplog):
    """An INFO log is emitted before the transfer, mentioning the rate
    limit when one is configured.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        rate_limit="100M",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    caplog.set_level(logging.INFO, logger="qsnap.modules.backup.file_copy")

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        provider.transfer_missing(vm_config, target, [snapshot], rate_limit="100M")

    info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any("Transferring" in msg and "rate limit: 100M" in msg for msg in info_msgs), (
        f"Expected pre-transfer INFO log mentioning rate limit, got: {info_msgs}"
    )


def test_post_transfer_info_log_throughput(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
):
    """An INFO log is emitted after the transfer with bytes transferred
    and elapsed time (throughput).

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        rate_limit="100M",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    caplog.set_level(logging.INFO, logger="qsnap.modules.backup.file_copy")

    # Mock time.monotonic to guarantee a positive, deterministic elapsed
    with (
        patch(
            "qsnap.modules.backup.file_copy.time.monotonic",
            side_effect=[100.0, 101.0],
        ),
        patch.object(mock_shell, "run", side_effect=spied_run),
    ):
        provider = FileCopyBackupProvider(mock_shell)
        provider.transfer_missing(vm_config, target, [snapshot], rate_limit="100M")

    info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any("Transferred" in msg and "bytes" in msg and "MiB/s" in msg for msg in info_msgs), (
        f"Expected post-transfer INFO log with throughput, got: {info_msgs}"
    )


def test_debug_log_contains_rsync_command(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
):
    """A DEBUG log is emitted with the full rsync command string.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        rate_limit="100M",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    caplog.set_level(logging.DEBUG, logger="qsnap.modules.backup.file_copy")

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        provider.transfer_missing(vm_config, target, [snapshot], rate_limit="100M")

    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any(
        "Transfer command: rsync" in msg and "--bwlimit=102400" in msg for msg in debug_msgs
    ), f"Expected DEBUG log with rsync command, got: {debug_msgs}"


def test_slow_transfer_triggers_warning(mock_shell, make_vm_config, make_target, tmp_path, caplog):
    """When throughput is less than 10% of the configured rate limit, a
    WARNING is logged mentioning ``'slower than expected'`` and
    ``'Check target disk health'``.

    Setup: ``rate_limit="100M"`` → configured 104_857_600 B/s.
    10% threshold = 10_485_760 B/s.
    bytes_transferred=65536, elapsed=100s → 655 B/s < threshold → WARNING.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        rate_limit="100M",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    caplog.set_level(logging.WARNING, logger="qsnap.modules.backup.file_copy")

    # Mock time.monotonic: start=100.0, end=200.0 → elapsed=100.0s
    # throughput = 65536 / 100 = 655.36 B/s < 10_485_760 → WARNING
    with (
        patch(
            "qsnap.modules.backup.file_copy.time.monotonic",
            side_effect=[100.0, 200.0],
        ),
        patch.object(mock_shell, "run", side_effect=spied_run),
    ):
        provider = FileCopyBackupProvider(mock_shell)
        provider.transfer_missing(vm_config, target, [snapshot], rate_limit="100M")

    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "slower than expected" in msg and "Check target disk health" in msg for msg in warnings
    ), f"Expected slow-transfer WARNING, got: {warnings}"


def test_full_backup_ignores_rate_limit(mock_shell, make_target, tmp_path):
    """``create_full_backup()`` uses ``qemu-img convert`` regardless of
    the ``rate_limit`` setting on the target — rsync is never used for
    full (anchor) backups.
    """
    target = make_target(
        path=str(tmp_path / "backups"),
        rate_limit="100M",
    )
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 0
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1


# ──────────────────────────────────────────────────────────────────────────
# copy_base behavior (design D4)
# ──────────────────────────────────────────────────────────────────────────


def test_copy_base_false_prevents_base_copy(
    mock_shell, make_vm_config, make_target, tmp_path, mock_state
):
    """When ``copy_base=False`` (default) and the target is empty,
    ``transfer_missing`` does NOT call ``create_full_backup()`` — the D4
    code path has been removed.

    Instead, the snapshot is transferred via ``rsync`` (normal transfer
    behavior), just like the ``copy_base=True`` case.

    Verifies that ``qemu-img convert`` is never called, ``rsync`` is used
    instead, and no FULL backup state is recorded.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=False,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate rsync creating the target file so stat() works.
    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell, state=mock_state)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    # Only ONE result: the rsync transfer.  No FULL creation.
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].target_path == expected_target_file

    # rsync was called (normal transfer behavior)
    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1

    # qemu-img convert is NOT called — D4 path is removed
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, (
        "qemu-img convert should NOT be called from transfer_missing — "
        "D4 code path has been removed"
    )

    # No FULL backup state was recorded
    full_backups = mock_state.get_full_backups(str(target.path))
    assert len(full_backups) == 0, (
        "transfer_missing should NOT record FULL backup state — D4 code path has been removed"
    )


def test_transfer_missing_does_not_create_full_when_empty_target(
    mock_shell, make_vm_config, make_target, tmp_path, mock_state
):
    """When the target is empty (``list()`` returns ``[]``),
    ``transfer_missing`` does NOT call ``create_full_backup()`` — the D4
    code path has been removed.

    The snapshot is transferred via ``rsync`` normally.  No
    ``qemu-img convert`` command is ever issued, and no FULL backup
    state is recorded.  This applies regardless of the ``copy_base``
    setting.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=False,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate rsync creating the target file so stat() works.
    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell, state=mock_state)

        # Spy on create_full_backup to verify it is never called
        with patch.object(
            provider, "create_full_backup", wraps=provider.create_full_backup
        ) as full_spy:
            results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    # Only ONE result: the rsync transfer.  No FULL creation.
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None
    assert results[0].bytes_transferred == 65536
    assert results[0].snapshot_name == snapshot.name
    assert results[0].target_path == expected_target_file

    # create_full_backup was NEVER called
    assert full_spy.call_count == 0, (
        f"create_full_backup should NOT be called from transfer_missing — "
        f"D4 code path has been removed.  Got {full_spy.call_count} call(s)."
    )

    # rsync was called (normal transfer behavior)
    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1

    # qemu-img convert is NOT called — D4 path is removed
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, (
        "qemu-img convert should NOT be called from transfer_missing — "
        "D4 code path has been removed"
    )

    # verify commands don't include mv (no FULL creation rename)
    mv_cmds = [cmd for cmd in all_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 0, "mv should not be called — no FULL creation"

    # No FULL backup state was recorded
    full_backups = mock_state.get_full_backups(str(target.path))
    assert len(full_backups) == 0, (
        "transfer_missing should NOT record FULL backup state — D4 code path has been removed"
    )


def test_copy_base_true_allows_base_copy(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``copy_base=True`` and the target is empty,
    ``transfer_missing`` uses rsync directly instead of calling
    ``create_full_backup()``.

    This verifies that setting ``copy_base=True`` preserves the legacy
    behavior where the base disk image is copied (via rsync) rather than
    creating a standalone FULL backup via ``qemu-img convert``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].target_path == expected_target_file

    # rsync was called (not qemu-img convert)
    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, "qemu-img convert should NOT be called when copy_base=True"


# ──────────────────────────────────────────────────────────────────────────
# Retry-unawareness (fault-tolerance-and-safety)
# ──────────────────────────────────────────────────────────────────────────


def test_provider_remains_retry_unaware(mock_shell, make_vm_config, make_target, tmp_path):
    """The ``FileCopyBackupProvider`` does NOT perform any retry logic
    on its own — that is Core's responsibility.

    When ``transfer_missing()`` encounters a transient failure (e.g.
    "Connection refused"), the transfer command is attempted exactly ONCE
    and the error is returned in the ``BackupResult`` without retrying.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    error_msg = "Connection refused"
    # rsync transfer fails — simulating a transient error
    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=error_msg,
            returncode=1,
            error=error_msg,
        )
    )

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    # Assert failure result with the original error string
    assert len(results) == 1
    assert results[0].success is False
    assert "Connection refused" in results[0].error
    assert results[0].bytes_transferred == 0
    assert results[0].snapshot_name == snapshot.name
    assert results[0].source_path == snapshot.path
    assert results[0].target_path == expected_target_file

    # The transfer command (rsync) was attempted exactly ONCE — no retry loop
    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1, (
        f"Expected exactly 1 rsync attempt (no retry), got {len(rsync_cmds)}"
    )


def test_backup_result_error_structured_for_retry_detection(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """The ``BackupResult.error`` field returned by ``transfer_missing()``
    is a plain string (not None, not an exception), which can be passed
    directly to ``is_retryable()`` for pattern-matching.

    This ensures the error format produced by the provider is compatible
    with Core's retry logic.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    from qsnap.utils.retry import is_retryable

    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    error_msg = "No route to host"
    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=error_msg,
            returncode=1,
            error=error_msg,
        )
    )

    provider = FileCopyBackupProvider(mock_shell)
    results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    # Assert error field is a proper string
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error is not None
    assert isinstance(results[0].error, str)

    # The error string is structured so that is_retryable() can
    # pattern-match it (Core's responsibility to act on the result)
    assert is_retryable(results[0].error) is True


# ──────────────────────────────────────────────────────────────────────────
# Dotted VM name support (fix-dotted-vm-names)
# ──────────────────────────────────────────────────────────────────────────


def test_create_full_backup_dotted_vm_name(mock_shell, make_target, tmp_path):
    """Spec: ``create_full_backup("3.Projects_opencode", ...)`` passes the
    full dotted VM name untruncated to ``virsh dominfo`` and uses it in
    the FULL backup filename.

    Verifies:
    - ``virsh dominfo --domain 3.Projects_opencode`` is called (NOT
      ``--domain 3``).
    - The FULL backup file is named ``3.Projects_opencode.FULL.YYYYMMDD.qcow2``.
    - The VM name is NOT extracted from the snapshot filename via
      ``split(".")`` — the snapshot name is ``"testvm.<timestamp>"``
      but the FULL uses ``"3.Projects_opencode"``.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # VM is stopped → direct qemu-img convert
    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: 3.Projects_opencode\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate mv creating the final file so stat() works.
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "3.Projects_opencode",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True
    assert result.error is None

    # Assert dominfo called with full dotted name — NOT truncated to "3"
    all_cmds = _all_cmds(mock_shell)
    dominfo_cmds = [cmd for cmd in all_cmds if "virsh dominfo" in cmd]
    assert len(dominfo_cmds) >= 1
    assert "--domain 3.Projects_opencode" in dominfo_cmds[0], (
        f"Expected '--domain 3.Projects_opencode' in dominfo command, got: {dominfo_cmds[0]}"
    )

    # Assert FULL backup file name uses full VM name
    assert result.target_path is not None
    assert "3.Projects_opencode.FULL." in str(result.target_path), (
        f"Full backup file should use dotted VM name, got: {result.target_path}"
    )
    # FULL name uses vm_name arg, NOT extracted from snapshot filename
    assert "testvm" not in str(result.target_path), (
        f"FULL backup name should use vm_name arg ('3.Projects_opencode'), "
        f"not extracted from snapshot name. Got: {result.target_path}"
    )


def test_transfer_missing_passes_vm_name_to_create_full(
    mock_shell, make_vm_config, make_target, tmp_path, mock_state
):
    """Spec: ``transfer_missing`` no longer calls ``create_full_backup``
    — the D4 code path has been removed (see design-D4-removal).

    When ``copy_base=False`` (default) and the target is empty,
    ``transfer_missing`` now uses ``rsync`` for the snapshot instead.
    ``create_full_backup()`` is NOT called from within
    ``transfer_missing()`` — it is only invoked by Core orchestration
    (e.g. ``_backup_target`` for bucket-triggered FULL anchors).

    Verifies that ``create_full_backup`` is never called, regardless of
    ``vm_config.name`` content (including dotted names like
    ``"3.Projects_opencode"``).
    """
    vm_config = make_vm_config(name="3.Projects_opencode")
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=False,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate rsync creating the target file so stat() works.
    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell, state=mock_state)

        # Spy on create_full_backup to verify it is never called
        with patch.object(
            provider, "create_full_backup", wraps=provider.create_full_backup
        ) as full_spy:
            results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    # transfer_missing returns exactly 1 result: the rsync transfer.
    # No FULL creation result is included.
    assert len(results) == 1
    assert results[0].success is True

    # create_full_backup was NOT called — D4 path is removed
    assert full_spy.call_count == 0, (
        f"create_full_backup should NOT be called from transfer_missing — "
        f"D4 code path has been removed. Got {full_spy.call_count} call(s)."
    )

    # No FULL backup state was recorded
    full_backups = mock_state.get_full_backups(str(target.path))
    assert len(full_backups) == 0, (
        "transfer_missing should NOT record FULL backup state — D4 code path has been removed"
    )


def test_create_full_backup_dotted_vm_name_passed_to_is_vm_running(
    mock_shell, make_target, tmp_path
):
    """Spec: ``create_full_backup`` passes the full dotted VM name to
    ``is_vm_running`` and ``nbd_full_export``.

    When the VM is running, the NBD pull-model path is selected.  The
    dotted VM name (e.g. ``"3.Projects_opencode"``) must be passed
    untruncated to:
    - ``virsh dominfo --domain 3.Projects_opencode`` (NOT ``--domain 3``)
    - ``virsh backup-begin --domain 3.Projects_opencode`` (NOT truncated)
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # NBD path: libvirt >= 6.0 required
    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # rm -f stale socket (before backup-begin)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # virsh backup-begin succeeds (no --incremental)
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # qemu-img convert via NBD succeeds
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # rm -f socket cleanup (in finally)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # mv (atomic rename) returns success
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate mv creating the final file so stat() works.
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "3.Projects_opencode",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True
    assert result.error is None

    all_cmds = _all_cmds(mock_shell)

    # Assert dominfo called with the full dotted name, not truncated
    dominfo_cmds = [cmd for cmd in all_cmds if "virsh dominfo" in cmd]
    assert len(dominfo_cmds) >= 1
    assert "--domain 3.Projects_opencode" in dominfo_cmds[0], (
        f"is_vm_running should receive full dotted VM name. "
        f"Expected '--domain 3.Projects_opencode', got: {dominfo_cmds[0]}"
    )

    # Assert backup-begin uses the full dotted VM name
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--domain 3.Projects_opencode" in backup_cmds[0], (
        f"nbd_full_export should receive full dotted VM name. "
        f"Expected '--domain 3.Projects_opencode' in backup-begin, "
        f"got: {backup_cmds[0]}"
    )

    # Assert NBD path was used (qemu-img convert via nbd:)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0], "NBD path should be used for running VM"

    # Assert FULL backup file uses full VM name
    assert "3.Projects_opencode.FULL." in str(result.target_path), (
        f"Full backup file should use dotted VM name, got: {result.target_path}"
    )


# ──────────────────────────────────────────────────────────────────────────
# -B qcow2 flag (design D3: renamed from -F in QEMU 11.0)
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_incremental_rebase_with_B_qcow2(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """The rebase command includes ``-B qcow2`` (backing file format)
    when rebasing an incremental snapshot to a FULL anchor or source
    backing file.

    Design D3: ``-B qcow2`` (renamed from ``-F`` in QEMU 11.0) ensures
    ``qemu-img rebase -u`` can resolve the backing file regardless of
    security context or metadata format ambiguity.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=True,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # rsync succeeds
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # qemu-img info on source returns JSON with backing-filename
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "actual-size": 65536,
                    "backing-filename": "/source/path/backing.qcow2",
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # qemu-img rebase succeeds
    mock_shell.expect(r"qemu-img rebase").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate rsync creating the target file so stat() works
    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 1
    rebase_cmd = rebase_cmds[0]

    # CRITICAL: -B qcow2 flag MUST be present
    assert "-B" in rebase_cmd
    assert "qcow2" in rebase_cmd
    # Verify -B qcow2 appears as a contiguous pair
    assert "-B qcow2" in rebase_cmd
    assert "-u" in rebase_cmd
    assert "-b" in rebase_cmd


def test_transfer_no_full_anchor_rebase_with_B_flag(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When no FULL anchor exists and the rebase uses the source backing
    filename, the ``-B qcow2`` flag is STILL included.

    Both rebase code paths (FULL-anchor and source-backing fallback) use
    the ``-B qcow2`` flag consistently.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=True,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # rsync succeeds
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # qemu-img info on source returns backing-filename
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "actual-size": 65536,
                    "backing-filename": "/var/lib/qemu/base.qcow2",
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # qemu-img rebase succeeds
    mock_shell.expect(r"qemu-img rebase").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 1
    rebase_cmd = rebase_cmds[0]

    # Both -u and -B qcow2 are present (no-FULL-anchor fallback path)
    assert "-B qcow2" in rebase_cmd
    assert "-u" in rebase_cmd
    assert "-b base.qcow2" in rebase_cmd


# ──────────────────────────────────────────────────────────────────────────
# FULL anchor M1 verification before rebase
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_missing_rebases_to_full_anchor_m1_passes(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When a FULL anchor exists and M1 verification
    (``verify_full_backup(shell, anchor, "metadata")``) passes, the
    incremental snapshot is rebased to the anchor.

    M1 verification checks via ``qemu-img info`` that the anchor is
    valid qcow2 (format="qcow2").
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"),
        incremental=True,
        verify="off",
    )
    target.path.mkdir(parents=True, exist_ok=True)

    # Pre-create a FULL anchor file
    anchor_name = "testvm.FULL.20250101.qcow2"
    anchor_file = target.path / anchor_name
    anchor_file.write_bytes(b"\x00" * 1024)

    snapshot = SnapshotInfo(
        name="testvm.20250102T000000",
        path=Path("/snapshots/testvm.20250102T000000.qcow2"),
        timestamp=datetime(2025, 1, 2, 0, 0, 0),
        allocation=65536,
    )

    # qemu-img info for list() on the anchor AND for M1 verification
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1073741824,
                    "actual-size": 1024,
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # rsync succeeds
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # qemu-img rebase succeeds
    mock_shell.expect(r"qemu-img rebase").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)

    # Verify rebase command uses the FULL anchor
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 1
    assert f"./{anchor_name}" in rebase_cmds[0]
    assert "-B qcow2" in rebase_cmds[0]

    # qemu-img info was NOT called on the source (anchor path skips source query)
    source_info_cmds = [
        cmd for cmd in all_cmds if "qemu-img info" in cmd and str(snapshot.path) in cmd
    ]
    assert len(source_info_cmds) == 0


def test_transfer_missing_rebase_uses_alternative_full_on_m1_fail(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When M1 verification fails on the newest FULL anchor, the system
    tries the next older anchor.  If that one passes, the rebase uses
    the older anchor.

    M1 failure is simulated via ``qemu-img info`` returning a non-qcow2
    format (e.g. ``"raw"``) for the newer anchor, causing
    ``verify_full_backup`` to report ``"expected format qcow2"``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"),
        incremental=True,
        verify="off",
    )
    target.path.mkdir(parents=True, exist_ok=True)

    # Pre-create TWO FULL anchors — newer (M1 FAILS) and older (M1 PASSES)
    older_anchor_name = "testvm.FULL.20250101.qcow2"
    newer_anchor_name = "testvm.FULL.20250102.qcow2"
    (target.path / newer_anchor_name).write_bytes(b"\x00" * 1024)
    (target.path / older_anchor_name).write_bytes(b"\x00" * 1024)

    snapshot = SnapshotInfo(
        name="testvm.20250103T000000",
        path=Path("/snapshots/testvm.20250103T000000.qcow2"),
        timestamp=datetime(2025, 1, 3, 0, 0, 0),
        allocation=65536,
    )

    # ── qemu-img info mocks ──
    # Newer anchor → M1 FAILS (format is "raw", not "qcow2")
    mock_shell.expect_first(rf"qemu-img info.*{newer_anchor_name}").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "raw", "virtual-size": 1073741824, "actual-size": 1024}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Older anchor → M1 PASSES (format is "qcow2")
    mock_shell.expect_first(rf"qemu-img info.*{older_anchor_name}").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 1073741824, "actual-size": 1024}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Generic fallback for any other qemu-img info calls
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 65536}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # rsync
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # qemu-img rebase
    mock_shell.expect(r"qemu-img rebase").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)

    # Verify rebase uses the OLDER anchor (the one that passed M1)
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 1
    assert f"./{older_anchor_name}" in rebase_cmds[0], (
        f"Rebase should use older anchor '{older_anchor_name}' "
        f"(the one that passed M1), got: {rebase_cmds[0]}"
    )
    assert f"./{newer_anchor_name}" not in rebase_cmds[0], (
        f"Rebase should NOT use newer anchor '{newer_anchor_name}' (it failed M1)"
    )
    assert "-B qcow2" in rebase_cmds[0]


def test_transfer_missing_no_rebase_when_no_valid_full(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When all FULL anchors fail M1 verification, no rebase is performed.
    The snapshot transfer succeeds but no ``qemu-img rebase`` command is
    executed.

    The old behavior was to fall back to the source backing file, but
    the new logic deliberately skips rebase when no valid FULL anchor
    can be found (avoids linking incrementals to a potentially corrupt
    anchor chain).
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"),
        incremental=True,
        verify="off",
    )
    target.path.mkdir(parents=True, exist_ok=True)

    # Pre-create a FULL anchor that will FAIL M1
    anchor_name = "testvm.FULL.20250101.qcow2"
    anchor_file = target.path / anchor_name
    anchor_file.write_bytes(b"\x00" * 1024)

    snapshot = SnapshotInfo(
        name="testvm.20250102T000000",
        path=Path("/snapshots/testvm.20250102T000000.qcow2"),
        timestamp=datetime(2025, 1, 2, 0, 0, 0),
        allocation=65536,
    )

    # Anchor → M1 FAILS (format is not qcow2)
    mock_shell.expect_first(rf"qemu-img info.*{anchor_name}").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "raw", "virtual-size": 1073741824, "actual-size": 1024}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Fallback for any other qemu-img info calls
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 65536}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # rsync succeeds
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    # Transfer succeeds — no rebase performed (all anchors failed M1)
    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 0, "No rebase should be performed when all FULL anchors fail M1"


# ──────────────────────────────────────────────────────────────────────────
# Stale state self-healing
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_missing_stale_snapshot_skipped(
    mock_shell, make_vm_config, make_target, tmp_path, mock_state
):
    """When a snapshot exists in state but the file has been removed from
    disk (e.g. blockcommitted by a prior run), it is silently skipped,
    removed from state, and no transfer attempt is made.

    A WARNING is logged about the stale state entry.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        # Path deliberately outside /snapshots/ so autouse fixture does NOT
        # intercept os.path.exists — the stale guard SHOULD fire.
        path=Path("/stale_snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # Pre-populate state so we can verify removal
    mock_state.record_snapshot(vm_config.name, snapshot)
    assert len(mock_state.get_snapshots(vm_config.name)) == 1

    # The file does NOT exist on disk → stale guard fires.
    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run):
        provider = FileCopyBackupProvider(mock_shell, state=mock_state)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Stale snapshot skipped — no results
    assert len(results) == 0

    # No rsync or qemu-img commands were called
    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 0, "No rsync should be called for stale snapshot"
    qemu_cmds = [cmd for cmd in all_cmds if "qemu-img" in cmd]
    assert len(qemu_cmds) == 0

    # State entry was removed
    remaining = mock_state.get_snapshots(vm_config.name)
    assert len(remaining) == 0, (
        f"Stale snapshot should be removed from state, got {len(remaining)} entries"
    )


# ──────────────────────────────────────────────────────────────────────────
# NBD: domjobabort + socket cleanup
# ──────────────────────────────────────────────────────────────────────────


def test_nbd_socket_and_domjobabort_on_success(mock_shell, make_target, tmp_path):
    """When ``create_full_backup`` via NBD succeeds, ``virsh domjobabort``
    is called in the ``finally`` block before the socket ``rm -f``.

    The domjobabort releases the VM state change lock held by the
    ``virsh backup-begin`` job.  Socket cleanup follows abort.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # domjobabort (called in finally)
    mock_shell.expect("domjobabort").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    all_cmds = _all_cmds(mock_shell)

    # Verify domjobabort was called
    abort_cmds = [cmd for cmd in all_cmds if "virsh domjobabort" in cmd]
    assert len(abort_cmds) == 1, f"Expected 1 domjobabort call in finally block, got: {abort_cmds}"
    assert "--domain testvm" in abort_cmds[0]

    # Verify socket rm -f was called (before backup-begin AND in finally)
    rm_cmds = [cmd for cmd in all_cmds if cmd.startswith("rm -f")]
    socket_rm_cmds = [cmd for cmd in rm_cmds if "/tmp/qsnap-backup-" in cmd]
    assert len(socket_rm_cmds) == 2, (
        f"Expected 2 socket rm -f calls (stale removal + finally), got: {socket_rm_cmds}"
    )

    # domjobabort runs AFTER backup-begin but BEFORE final socket rm
    backup_cmds_list = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    backup_idx = all_cmds.index(backup_cmds_list[0]) if backup_cmds_list else -1
    abort_idx = all_cmds.index(abort_cmds[0])
    assert abort_idx > backup_idx, "domjobabort should run after backup-begin"


def test_nbd_cleanup_on_failure_domjobabort(mock_shell, make_target, tmp_path):
    """When ``qemu-img convert`` (NBD pull) fails, ``virsh domjobabort``
    is still called in the ``finally`` block.

    The VM state change lock must be released even when the export fails.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # qemu-img convert FAILS
    convert_error = "NBD connection reset"
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=convert_error,
            returncode=1,
            error=convert_error,
        )
    )
    # domjobabort STILL called (in finally)
    mock_shell.expect("domjobabort").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is False

    all_cmds = _all_cmds(mock_shell)

    # domjobabort was called despite qemu-img convert failure
    abort_cmds = [cmd for cmd in all_cmds if "virsh domjobabort" in cmd]
    assert len(abort_cmds) == 1, (
        f"domjobabort should be called in finally even on failure, got: {abort_cmds}"
    )

    # Socket rm -f was still called (cleanup in finally)
    rm_cmds = [cmd for cmd in all_cmds if cmd.startswith("rm -f")]
    socket_rm_cmds = [cmd for cmd in rm_cmds if "/tmp/qsnap-backup-" in cmd]
    # At least 1 socket rm (stale removal before backup-begin) + final rm
    assert len(socket_rm_cmds) >= 1, (
        f"Socket rm -f should run in finally even on failure, got: {socket_rm_cmds}"
    )


def test_risk_domjobabort_fails_gracefully(mock_shell, make_target, tmp_path, caplog):
    """When ``virsh domjobabort`` itself fails (e.g. job already
    terminated), a WARNING is logged but the failure does NOT propagate
    — socket cleanup proceeds and the backup result is still returned.

    The domjobabort is best-effort; its failure should not mask the
    underlying backup operation result.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # domjobabort FAILS (job already terminated)
    domjob_error = "error: Requested operation is not valid: domain is not running"
    mock_shell.expect("domjobabort").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=domjob_error,
            returncode=1,
            error=domjob_error,
        )
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    caplog.set_level(logging.WARNING, logger="qsnap.utils.nbd")

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    # Verify the expected behaviors:
    # 1. WARNING logged about domjobabort failure
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("domjobabort failed" in msg for msg in warnings), (
        f"Expected 'domjobabort failed' WARNING, got: {warnings}"
    )

    # 2. Socket cleanup STILL ran after the WARNING (finally block completed)
    all_cmds = _all_cmds(mock_shell)
    abort_cmds = [cmd for cmd in all_cmds if "virsh domjobabort" in cmd]
    assert len(abort_cmds) == 1, (
        f"domjobabort should be called (then WARNING on failure), got: {abort_cmds}"
    )
    rm_cmds = [cmd for cmd in all_cmds if cmd.startswith("rm -f")]
    socket_rm_cmds = [cmd for cmd in rm_cmds if "/tmp/qsnap-backup-" in cmd]
    assert len(socket_rm_cmds) == 2, (
        f"Socket rm -f should still proceed after domjobabort failure, got: {socket_rm_cmds}"
    )

    # 3. The domjobabort failure is non-fatal — the mv (atomic rename)
    # completed successfully after the NBD export, proving the finally
    # block ran without crashing the operation.
    mv_cmds = [cmd for cmd in all_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 1, "mv should still complete after domjobabort warning"


# ──────────────────────────────────────────────────────────────────────────
# Architecture — import-path verification
# ──────────────────────────────────────────────────────────────────────────


def test_nbd_imports_from_utils():
    """``qsnap.modules.backup.file_copy`` imports NBD functions from
    ``qsnap.utils.nbd`` (shared utility, not a backup sub-module).

    The module-level import ``from qsnap.utils.nbd import is_vm_running``
    means ``qsnap.modules.backup.file_copy.is_vm_running`` is the same
    function object as ``qsnap.utils.nbd.is_vm_running``.
    """
    from qsnap.modules.backup import file_copy
    from qsnap.utils.nbd import (
        is_libvirt_new_enough as nbd_libvirt_check,
    )
    from qsnap.utils.nbd import (
        is_vm_running as nbd_vm_running,
    )
    from qsnap.utils.nbd import (
        nbd_full_export as nbd_export,
    )

    assert hasattr(file_copy, "is_vm_running"), "file_copy must import is_vm_running"
    assert file_copy.is_vm_running is nbd_vm_running, (
        "file_copy.is_vm_running must be qsnap.utils.nbd.is_vm_running"
    )
    assert file_copy.is_libvirt_new_enough is nbd_libvirt_check, (
        "file_copy.is_libvirt_new_enough must be qsnap.utils.nbd.is_libvirt_new_enough"
    )
    assert file_copy.nbd_full_export is nbd_export, (
        "file_copy.nbd_full_export must be qsnap.utils.nbd.nbd_full_export"
    )


def test_verify_backup_imported_from_utils():
    """``qsnap.modules.backup.file_copy`` imports verification functions
    from ``qsnap.utils.verification`` (shared utility, not a backup
    sub-module).

    The module-level import ``from qsnap.utils.verification import
    verify_backup`` means ``qsnap.modules.backup.file_copy.verify_backup``
    is the same function object as ``qsnap.utils.verification.verify_backup``.
    """
    from qsnap.modules.backup import file_copy
    from qsnap.utils.verification import (
        verify_backup as vfy_backup,
    )
    from qsnap.utils.verification import (
        verify_full_backup as vfy_full,
    )

    assert hasattr(file_copy, "verify_backup"), "file_copy must import verify_backup"
    assert file_copy.verify_backup is vfy_backup, (
        "file_copy.verify_backup must be qsnap.utils.verification.verify_backup"
    )
    assert file_copy.verify_full_backup is vfy_full, (
        "file_copy.verify_full_backup must be qsnap.utils.verification.verify_full_backup"
    )


# ──────────────────────────────────────────────────────────────────────────
# rsync --compress flag
# ──────────────────────────────────────────────────────────────────────────


def test_rsync_with_compress_flag(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``target.compress=True`` (default), the rsync command includes
    the ``--compress`` flag BEFORE ``--partial``.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
        # compress defaults to True
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--compress" in rsync_cmds[0], (
        f"--compress should be in rsync command when compress=True, got: {rsync_cmds[0]}"
    )
    # verify ordering: --compress before --partial
    assert "--compress" in rsync_cmds[0].split("--partial")[0], (
        f"--compress should appear before --partial in: {rsync_cmds[0]}"
    )


def test_rsync_compress_with_rate_limit(mock_shell, make_vm_config, make_target, tmp_path):
    """When both ``compress=True`` and ``rate_limit`` is set, the rsync
    command includes both ``--bwlimit`` and ``--compress``, with
    ``--compress`` appearing before ``--partial``.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        rate_limit="100M",
        copy_base=True,
        # compress defaults to True
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="100M")

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    rsync_cmd = rsync_cmds[0]
    assert "--bwlimit=102400" in rsync_cmd, (
        f"--bwlimit should be present when rate_limit set, got: {rsync_cmd}"
    )
    assert "--compress" in rsync_cmd, (
        f"--compress should coexist with --bwlimit when compress=True, got: {rsync_cmd}"
    )
    # verify ordering: --compress before --partial
    assert "--compress" in rsync_cmd.split("--partial")[0], (
        f"--compress should appear before --partial in: {rsync_cmd}"
    )


def test_rsync_without_compress(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``target.compress=False``, the rsync command does NOT include
    the ``--compress`` flag.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        compress=False,
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    with patch.object(mock_shell, "run", wraps=mock_shell.run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--compress" not in rsync_cmds[0], (
        f"--compress should NOT be in rsync command when compress=False, got: {rsync_cmds[0]}"
    )
    # --partial should still be present
    assert "--partial" in rsync_cmds[0]


def test_rsync_compress_hash_verification_passes(mock_shell, make_vm_config, make_target, tmp_path):
    """Verify that ``--compress`` does not affect hash/byte-level
    verification.  When ``verify="full"`` and ``compress=True``, the
    transfer succeeds even though rsync uses compression — the resulting
    files are byte-identical after decompression by rsync.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"),
        incremental=False,
        verify="full",
        copy_base=True,
        # compress defaults to True
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    qcow2_info = json.dumps(
        {
            "format": "qcow2",
            "virtual-size": 1073741824,
            "actual-size": 1048576,
        }
    )

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Mock qemu-img info (for metadata verification)
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=qcow2_info,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Mock qemu-img compare returns success (full byte-level verification)
    mock_shell.expect(r"qemu-img compare").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate rsync creating the target file so stat() works.
    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result — --compress did not break verification
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    # Verify rsync command includes --compress
    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--compress" in rsync_cmds[0]
    assert "--compress-choice=zstd" in rsync_cmds[0], (
        f"--compress-choice=zstd should be in rsync command (default zstd), got: {rsync_cmds[0]}"
    )

    # Verify qemu-img compare was called (full verification passed)
    compare_cmds = [cmd for cmd in all_cmds if "qemu-img compare" in cmd]
    assert len(compare_cmds) == 1, (
        "qemu-img compare should be called for verify='full', "
        "proving --compress does not break byte-level verification"
    )


def test_failed_backup_deletion_before_retention_cleanup(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When verification fails, the partially-transferred backup file is
    deleted via ``rm -f`` IMMEDIATELY, before ``BackupResult(success=False)``
    is returned — ensuring retention cleanup does not find a broken backup
    and log a misleading ``[delete] removed backup`` message (design D2).

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="metadata",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Mock rm -f for partial file cleanup
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    verify_error = "verification failed: virtual-size mismatch"

    with (
        patch(
            "qsnap.modules.backup.file_copy.verify_backup",
            return_value=verify_error,
        ),
        patch.object(mock_shell, "run", wraps=mock_shell.run),
    ):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert failure result — deletion already happened
    assert len(results) == 1
    assert results[0].success is False
    assert verify_error in results[0].error

    # Verify rm -f was called: the deletion happens inside transfer_missing
    # (before the function returns), NOT in the caller's retention cleanup
    all_cmds = _all_cmds(mock_shell)
    rm_cmds = [cmd for cmd in all_cmds if cmd.startswith("rm -f")]
    assert len(rm_cmds) >= 1, (
        f"Expected rm -f for failed backup cleanup before function returns, got: {all_cmds}"
    )
    assert any(str(expected_target_file) in cmd for cmd in rm_cmds), (
        f"rm -f should target {expected_target_file}, ensuring retention "
        f"cleanup never sees the partial file. Got: {rm_cmds}"
    )


# ──────────────────────────────────────────────────────────────────────────
# New tests: zstd compression & stall detection (zstd-compression-and-stall-detection)
# ──────────────────────────────────────────────────────────────────────────


def test_create_full_backup_compressed_zstd_stopped_vm(mock_shell, make_target, tmp_path):
    """``create_full_backup(compress=True)`` with stopped VM uses
    ``-c -o compression_type=zstd`` (default zstd).

    Verifies both ``-c`` flag and ``-o compression_type=zstd`` are present.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    original_sd = mock_shell.run_with_stall_detection

    def _sd_mv(cmd, output_file=None, stall_timeout=1800, check=False):
        # qemu-img convert creates the temp file
        cmd_str = " ".join(cmd)
        if "qemu-img convert" in cmd_str:
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    original_run = mock_shell.run

    def _run_mv(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run_with_stall_detection", side_effect=_sd_mv),
        patch.object(mock_shell, "run", side_effect=_run_mv),
    ):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=True,
            bucket_level="monthly",
        )

    assert result.success is True
    assert result.bytes_transferred == 65536

    all_cmds = _all_cmds(mock_shell)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" in convert_cmds[0], "Should contain -c for compression"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        f"Should contain -o compression_type=zstd (default zstd), got: {convert_cmds[0]}"
    )


def test_create_full_backup_compressed_zlib_stopped_vm(mock_shell, make_target, tmp_path):
    """``create_full_backup(compress=True, compression_type='zlib')`` uses
    ``-c`` but NOT ``-o compression_type=`` (zlib is the default)."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    original_sd = mock_shell.run_with_stall_detection

    def _sd_mv(cmd, output_file=None, stall_timeout=1800, check=False):
        cmd_str = " ".join(cmd)
        if "qemu-img convert" in cmd_str:
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    original_run = mock_shell.run

    def _run_mv(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run_with_stall_detection", side_effect=_sd_mv),
        patch.object(mock_shell, "run", side_effect=_run_mv),
    ):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=True,
            bucket_level="monthly",
            compression_type="zlib",
        )

    assert result.success is True

    all_cmds = _all_cmds(mock_shell)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" in convert_cmds[0], "Should contain -c for compression"
    assert "-o compression_type=zstd" not in convert_cmds[0], (
        f"Should NOT contain -o compression_type=zstd for zlib, got: {convert_cmds[0]}"
    )
    assert "-o compression_type=" not in convert_cmds[0], (
        f"Should NOT contain any -o compression_type= for zlib (default), got: {convert_cmds[0]}"
    )


def test_rsync_uses_stall_detection(mock_shell, make_vm_config, make_target, tmp_path):
    """rsync transfers use ``run_with_stall_detection`` (not ``run``),
    with output_file and stall_timeout parameters."""
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as sd_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    # Verify run_with_stall_detection was called for rsync
    assert sd_spy.call_count >= 1, "run_with_stall_detection should be called for rsync"
    # Verify the call includes output_file and stall_timeout
    rsync_calls = [c for c in sd_spy.call_args_list if "rsync" in " ".join(c.args[0])]
    assert len(rsync_calls) >= 1, "rsync should be called via run_with_stall_detection"
    # Check keyword arguments: output_file and stall_timeout
    first_call = rsync_calls[0]
    assert first_call.kwargs.get("output_file") is not None, "output_file should be set"
    assert first_call.kwargs.get("stall_timeout") == 1800, "stall_timeout should be default 1800"


def test_nbd_full_uses_stall_detection(mock_shell, make_target, tmp_path):
    """NBD convert uses ``run_with_stall_detection`` (not ``run``)."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as sd_spy:
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True
    # NBD convert should go through run_with_stall_detection
    convert_calls = [c for c in sd_spy.call_args_list if "qemu-img convert" in " ".join(c.args[0])]
    assert len(convert_calls) >= 1, (
        f"NBD convert should be called via run_with_stall_detection, got {len(convert_calls)} calls"
    )


def test_stall_timeout_zero_falls_back(mock_shell, make_vm_config, make_target, tmp_path):
    """When stall_timeout=0, ``run()`` with timeout=3600 is used instead of
    ``run_with_stall_detection``."""
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as sd_spy,
    ):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(
            vm_config,
            target,
            [snapshot],
            stall_timeout=0,
        )

    assert len(results) == 1
    assert results[0].success is True

    # rsync should use run(), not run_with_stall_detection
    rsync_run_calls = [c for c in run_spy.call_args_list if "rsync" in " ".join(c.args[0])]
    assert len(rsync_run_calls) >= 1, (
        f"rsync should use run() when stall_timeout=0, got {len(rsync_run_calls)} run calls"
    )
    # verify timeout=3600 was passed to run()
    first_call = rsync_run_calls[0]
    timeout_value = first_call.kwargs.get(
        "timeout", first_call.args[1] if len(first_call.args) > 1 else None
    )
    assert timeout_value == 3600, (
        f"run() timeout should be 3600 when stall_timeout=0, got {timeout_value}"
    )
    # run_with_stall_detection should NOT be called for rsync
    rsync_sd_calls = [c for c in sd_spy.call_args_list if "rsync" in " ".join(c.args[0])]
    assert len(rsync_sd_calls) == 0, (
        "run_with_stall_detection should NOT be called when stall_timeout=0"
    )


def test_rsync_with_zstd_compression(mock_shell, make_vm_config, make_target, tmp_path):
    """rsync uses ``--compress-choice=zstd`` when compression_type='zstd' and compress=True."""
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    _wrap_sd_with_side_effect(mock_shell, _rsync_side_effect)

    provider = FileCopyBackupProvider(mock_shell)
    results = provider.transfer_missing(
        vm_config,
        target,
        [snapshot],
        compression_type="zstd",
    )

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--compress" in rsync_cmds[0]
    assert "--compress-choice=zstd" in rsync_cmds[0], (
        f"--compress-choice=zstd should be present for zstd compression, got: {rsync_cmds[0]}"
    )


def test_rsync_with_zlib_compression(mock_shell, make_vm_config, make_target, tmp_path):
    """rsync uses ``--compress`` only when compression_type='zlib'.

    zlib is rsync's default compression algorithm, so the implementation
    emits ``--compress`` without any ``--compress-choice=`` flag.  The
    ``--compress-choice=`` flag is only added for non-zlib compression
    types (e.g. zstd).
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    _wrap_sd_with_side_effect(mock_shell, _rsync_side_effect)

    provider = FileCopyBackupProvider(mock_shell)
    results = provider.transfer_missing(
        vm_config,
        target,
        [snapshot],
        compression_type="zlib",
    )

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--compress" in rsync_cmds[0]
    assert "--compress-choice=" not in rsync_cmds[0], (
        f"--compress-choice= should NOT be present for zlib compression "
        f"(zlib is rsync's default), got: {rsync_cmds[0]}"
    )


def test_rsync_zstd_with_rate_limit(mock_shell, make_vm_config, make_target, tmp_path):
    """rsync with rate limit and zstd compression includes both
    ``--bwlimit`` and ``--compress-choice=zstd``."""
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        rate_limit="100M",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    _wrap_sd_with_side_effect(mock_shell, _rsync_side_effect)

    provider = FileCopyBackupProvider(mock_shell)
    results = provider.transfer_missing(
        vm_config,
        target,
        [snapshot],
        rate_limit="100M",
        compression_type="zstd",
    )

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--bwlimit=102400" in rsync_cmds[0], "Should have --bwlimit"
    assert "--compress-choice=zstd" in rsync_cmds[0], (
        f"Should have --compress-choice=zstd with zstd compression, got: {rsync_cmds[0]}"
    )


def test_rsync_no_compression(mock_shell, make_vm_config, make_target, tmp_path):
    """When compress=False, rsync has no ``--compress`` or ``--compress-choice=`` flags."""
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        compress=False,
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    _wrap_sd_with_side_effect(mock_shell, _rsync_side_effect)

    provider = FileCopyBackupProvider(mock_shell)
    results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--compress" not in rsync_cmds[0], (
        f"--compress should NOT be present when compress=False, got: {rsync_cmds[0]}"
    )
    assert "--compress-choice=" not in rsync_cmds[0], (
        f"--compress-choice= should NOT be present when compress=False, got: {rsync_cmds[0]}"
    )


def test_nbd_full_zstd_compression(mock_shell, make_target, tmp_path):
    """NBD convert with ``compress=True`` and ``compression_type='zstd'``
    includes ``-c -o compression_type=zstd``."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    original_sd = mock_shell.run_with_stall_detection

    def _sd_create(cmd, output_file=None, stall_timeout=1800, check=False):
        cmd_str = " ".join(cmd)
        if "qemu-img convert" in cmd_str:
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    original_run = mock_shell.run

    def _run_mv(cmd, timeout):
        if " ".join(cmd).startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run_with_stall_detection", side_effect=_sd_create),
        patch.object(mock_shell, "run", side_effect=_run_mv),
    ):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=True,
            bucket_level="daily",
            compression_type="zstd",
        )

    assert result.success is True
    assert result.bytes_transferred == 65536

    all_cmds = _all_cmds(mock_shell)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0], "NBD path should be used"
    assert "-c" in convert_cmds[0], "Should contain -c"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        f"Should contain -o compression_type=zstd, got: {convert_cmds[0]}"
    )


def test_nbd_full_zlib_compression(mock_shell, make_target, tmp_path):
    """NBD convert with ``compress=True`` and ``compression_type='zlib'``
    includes ``-c`` only (no ``-o compression_type=`` flag)."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    _clear_calls(mock_shell)
    original_sd = mock_shell.run_with_stall_detection

    def _sd_create(cmd, output_file=None, stall_timeout=1800, check=False):
        cmd_str = " ".join(cmd)
        if "qemu-img convert" in cmd_str:
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    original_run = mock_shell.run

    def _run_mv(cmd, timeout):
        if " ".join(cmd).startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run_with_stall_detection", side_effect=_sd_create),
        patch.object(mock_shell, "run", side_effect=_run_mv),
    ):
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=True,
            bucket_level="daily",
            compression_type="zlib",
        )

    assert result.success is True

    all_cmds = _all_cmds(mock_shell)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0], "NBD path should be used"
    assert "-c" in convert_cmds[0], "Should contain -c"
    assert "-o compression_type=" not in convert_cmds[0], (
        f"Should NOT contain -o compression_type= for zlib (default), got: {convert_cmds[0]}"
    )


def test_full_backup_uses_stall_detection(mock_shell, make_target, tmp_path):
    """``create_full_backup`` (direct convert) passes output_file and
    stall_timeout to ``run_with_stall_detection``."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as sd_spy:
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    # Verify run_with_stall_detection was called for the convert
    convert_calls = [c for c in sd_spy.call_args_list if "qemu-img convert" in " ".join(c.args[0])]
    assert len(convert_calls) >= 1, (
        f"qemu-img convert should use run_with_stall_detection, got {len(convert_calls)} calls"
    )
    # Verify output_file is set (tmp file) and stall_timeout is 1800
    first_call = convert_calls[0]
    assert first_call.kwargs.get("output_file") is not None, "output_file should be set"
    assert ".tmp" in str(first_call.kwargs["output_file"]), "output_file should be .tmp file"
    assert first_call.kwargs.get("stall_timeout") == 1800, "stall_timeout should be default 1800"


# ──────────────────────────────────────────────────────────────────────────
# New tests: fix-bitmap-incremental-via-xml — file-copy-unit delegation group
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_with_rate_limit(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``rate_limit`` is set on the target config, the provider uses
    ``rsync --bwlimit=<kib> --partial`` for the transfer.

    This verifies that the rate limit passed through ``transfer_missing()``
    is correctly translated to the ``--bwlimit`` rsync flag.  The
    ``rate_limit_to_kib()`` utility converts human-readable values
    (e.g. ``"50M"`` → ``51200``) for the rsync argument.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental=False,
        verify="off",
        copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate rsync creating the target file so stat() works.
    _clear_calls(mock_shell)
    original_run = mock_shell.run
    original_sd = mock_shell.run_with_stall_detection

    def _side_effect(cmd, is_rsync=True):
        cmd_str = " ".join(cmd)
        if is_rsync and cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)

    def spied_run(cmd, timeout):
        _side_effect(cmd)
        return original_run(cmd, timeout)

    def spied_sd(cmd, output_file=None, stall_timeout=1800, check=False):
        _side_effect(cmd)
        return original_sd(cmd, output_file=output_file, stall_timeout=stall_timeout, check=check)

    mock_shell.run_with_stall_detection = spied_sd

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="50M")

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].bytes_transferred == 65536
    assert results[0].error is None
    assert results[0].snapshot_name == snapshot.name
    assert results[0].source_path == snapshot.path
    assert results[0].target_path == expected_target_file

    # Verify rsync uses --bwlimit=51200 (50M → rate_limit_to_kib)
    all_cmds = _all_cmds(mock_shell)
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--bwlimit=51200" in rsync_cmds[0], (
        f"Expected --bwlimit=51200 for rate_limit='50M', got: {rsync_cmds[0]}"
    )
    assert "--partial" in rsync_cmds[0]
    assert str(snapshot.path) in rsync_cmds[0]
    assert str(expected_target_file) in rsync_cmds[0]


def test_transfer_rebase_to_full_anchor(mock_shell, make_vm_config, make_target, tmp_path):
    """When a FULL anchor file (``*.FULL.*.qcow2``) exists in the target
    directory and ``target.incremental=True``, the rebase command MUST
    target the FULL anchor's bare filename WITH the ``-B qcow2`` flag
    (NOT ``-F qcow2``).

    QEMU 11.0 renamed the ``rebase`` subcommand's ``--backing-format``
    flag from ``-F`` to ``-B`` (design D3).  This test explicitly
    verifies:
    - The rebase ``-b`` argument points to ``./<anchor_name>``.
    - The format flag is ``-B qcow2``, not ``-F qcow2``.
    - ``qemu-img info`` is NOT called on the source (bypassed when a
      valid anchor exists).
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"),
        incremental=True,
        verify="off",
    )
    target.path.mkdir(parents=True, exist_ok=True)

    # Pre-create a FULL anchor file in the target directory
    anchor_name = "testvm.FULL.20250101.qcow2"
    anchor_file = target.path / anchor_name
    anchor_file.write_bytes(b"\x00" * 1024)

    snapshot = SnapshotInfo(
        name="testvm.20250102T000000",
        path=Path("/snapshots/testvm.20250102T000000.qcow2"),
        timestamp=datetime(2025, 1, 2, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # qemu-img info for list() on the anchor AND M1 verification
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1073741824,
                    "actual-size": 1024,
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # rsync succeeds
    mock_shell.expect(r"^rsync").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # qemu-img rebase succeeds
    mock_shell.expect(r"qemu-img rebase").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Side effect: simulate rsync creating the target file so stat() works
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="no")

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None
    assert results[0].target_path == expected_target_file

    all_cmds = _all_cmds(mock_shell)

    # Verify rebase command uses -B qcow2 (NOT -F) for backing format
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 1
    rebase_cmd = rebase_cmds[0]

    # CRITICAL (design D3): -B qcow2 is present (QEMU 11.0 renamed from -F)
    assert "-B qcow2" in rebase_cmd, (
        f"Expected -B qcow2 (QEMU 11.0 rebase format flag), got: {rebase_cmd}"
    )
    # CRITICAL: -F qcow2 must NOT appear (old flag, removed in QEMU 11.0 for rebase)
    assert "-F qcow2" not in rebase_cmd, (
        f"-F qcow2 should NOT appear in rebase command (renamed to -B in QEMU 11.0), "
        f"got: {rebase_cmd}"
    )
    # CRITICAL: -u flag (unsafe, metadata-only)
    assert " -u " in rebase_cmd
    # CRITICAL: backing path is the bare anchor filename prefixed with ./
    assert f"./{anchor_name}" in rebase_cmd
    # Verify target file is in the rebase command
    assert str(expected_target_file) in rebase_cmd

    # qemu-img info was NOT called on the source (anchor path skips source query)
    source_info_cmds = [
        cmd for cmd in all_cmds if "qemu-img info" in cmd and str(snapshot.path) in cmd
    ]
    assert len(source_info_cmds) == 0, (
        "qemu-img info should NOT be called on the source when a FULL "
        "anchor exists — the rebase should go directly to the anchor"
    )
