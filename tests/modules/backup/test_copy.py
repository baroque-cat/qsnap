"""Unit tests for FileCopyBackupProvider.

Tests cover the three ``IBackupProvider`` methods (``transfer_missing``,
``list``, ``delete``) using ``MockShell`` to simulate ``rsync``/``qemu-img``/
``rm`` commands.  No real I/O occurs — all shell calls are intercepted by
``MockShell``.

Design decisions verified:
- **D1**: ``FileCopyBackupProvider`` does NOT inherit from ``Core``; its
  dependencies are ``IShell`` (required) and ``IStateManager`` (optional).
- **D3**: All transfers use ``rsync`` exclusively (no ``cp`` fallback).
- **D4**: When ``copy_base`` is False (default) and target is empty,
  ``create_full_backup()`` is called instead of rsync for the first snapshot.
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
7. copy_base=false triggers create_full_backup on empty target.
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
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qsnap.models.results import ShellResult, SnapshotInfo
from qsnap.modules.backup.file_copy import FileCopyBackupProvider
from tests.mocks.mock_shell import MockShell


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
        path=str(tmp_path / "nonexistent_target"), incremental=False,
        verify="off", copy_base=True,
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
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    # Side effect: simulate rsync creating the target file so stat() works.
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(
            vm_config, target, [snapshot], rate_limit="no"
        )

    # Assert successful result with correct file size
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].bytes_transferred == 65536
    assert results[0].error is None
    assert results[0].snapshot_name == snapshot.name
    assert results[0].source_path == snapshot.path
    assert results[0].target_path == expected_target_file

    # Assert rsync command copies to target.path/<snapshot.name>.qcow2
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert str(snapshot.path) in rsync_cmds[0]
    assert str(expected_target_file) in rsync_cmds[0]
    # No rate limit → no --bwlimit
    assert "--bwlimit" not in rsync_cmds[0]
    # Has --partial for resumability
    assert "--partial" in rsync_cmds[0]
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

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Snapshot is skipped — no results returned
    assert len(results) == 0

    # rsync is NOT called
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
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
        path=str(tmp_path / "nonexistent_target"), incremental=True,
        verify="off", copy_base=True,
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
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
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
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(
            vm_config, target, [snapshot], rate_limit="no"
        )

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True

    # Verify rebase command
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 1
    rebase_cmd = rebase_cmds[0]

    # CRITICAL: -u flag is present (unsafe, metadata-only update)
    assert " -u " in rebase_cmd
    # CRITICAL: backing path is the bare filename, not the full source path
    assert "-b backing.qcow2" in rebase_cmd
    assert "/source/path/backing.qcow2" not in rebase_cmd
    # Verify target file is in the rebase command
    assert str(expected_target_file) in rebase_cmd


def test_transfer_non_incremental_no_rebase(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``target.incremental`` is False, the snapshot is copied without
    calling ``qemu-img rebase``.  The backing path remains as-is.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=False,
        verify="off", copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True

    # Verify rebase is NOT called
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 0


def test_transfer_rsync_fails_disk_full(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``rsync`` returns a non-zero exit code (e.g. disk full), the
    provider returns ``BackupResult(success=False, error=<stderr>)``.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=False,
        verify="off", copy_base=True,
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


def test_rsync_unavailable_transfer_fails_no_cp_fallback(
    make_vm_config, make_target, tmp_path
):
    """When rsync is not available (``MockShell`` returns failure for
    ``rsync``), the transfer fails with no ``cp`` fallback (design D3).

    A fresh ``MockShell`` is used (without conftest's pre-configured
    expectations) so the rsync command returns failure.
    """
    shell = MockShell()
    # rsync → failure (not installed or unavailable)
    error_msg = "rsync: command not found"
    shell.expect(r"^rsync").returns(
        ShellResult(
            success=False, stdout="", stderr=error_msg,
            returncode=127, error=error_msg,
        )
    )

    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=False,
        verify="off", copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    with patch.object(shell, "run", wraps=shell.run) as shell_spy:
        provider = FileCopyBackupProvider(shell)
        results = provider.transfer_missing(
            vm_config, target, [snapshot], rate_limit="no"
        )

    # Assert failure with the rsync error
    assert len(results) == 1
    assert results[0].success is False
    assert "rsync" in results[0].error

    # No cp was called — no fallback
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    cp_cmds = [cmd for cmd in all_cmds if cmd.startswith("cp ")]
    assert len(cp_cmds) == 0, (
        "cp should NOT be used as fallback when rsync fails (design D3)"
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
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
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
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
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
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``qemu-img rebase`` fails (MockShell returns failure),
    ``transfer_missing`` returns a ``BackupResult`` with ``success=False``
    and an error message containing ``"rebase failed"``.

    The rebase step is part of the incremental backup flow: after copying
    the snapshot file via rsync, the backing path is rebased.  If the
    rebase command itself fails, the provider must report the failure
    rather than silently returning success.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=True,
        verify="off", copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # Mock rsync returns success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )
    # Mock qemu-img info returns JSON with backing-filename
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

    provider = FileCopyBackupProvider(mock_shell)
    results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False
    assert "rebase failed" in results[0].error


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
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
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
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run
    ) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    # Verify qemu-img info was called (for verification: source + target)
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    info_cmds = [cmd for cmd in all_cmds if "qemu-img info" in cmd]
    assert len(info_cmds) >= 2


def test_transfer_missing_full_verification(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``target.verify`` is ``"full"``, after the metadata check,
    ``qemu-img compare`` is called to verify byte-level integrity.

    With both metadata and compare succeeding, ``transfer_missing``
    returns ``BackupResult(success=True)``.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"), incremental=False, verify="full",
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
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
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
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    # Side effect: simulate rsync creating the target file so stat() works.
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run
    ) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    # Verify qemu-img compare was called
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    compare_cmds = [cmd for cmd in all_cmds if "qemu-img compare" in cmd]
    assert len(compare_cmds) == 1


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
        path=str(tmp_path / "backups"), incremental=False, verify="off",
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
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    # Side effect: simulate rsync creating the target file so stat() works.
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run
    ) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    # Verify NO qemu-img commands were called
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    qemu_cmds = [cmd for cmd in all_cmds if "qemu-img" in cmd]
    assert len(qemu_cmds) == 0


# ──────────────────────────────────────────────────────────────────────────
# Full backup creation (qemu-img convert)
# ──────────────────────────────────────────────────────────────────────────


def test_create_full_backup_uncompressed(mock_shell, make_target, tmp_path):
    """``create_full_backup(compress=False, bucket_level="monthly")`` calls
    ``qemu-img convert`` WITHOUT the ``-c`` flag and returns
    ``BackupResult(success=True)``.

    The command pattern is ``qemu-img convert -f qcow2 -O qcow2 <src> <tmp>``.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # Mock qemu-img convert returns success
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )
    # Mock mv (atomic rename) returns success
    mock_shell.expect(r"^mv ").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    # Side effect: simulate mv creating the final file so stat() works
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run
    ) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            snapshot, target, compress=False, bucket_level="monthly",
        )

    # Assert successful result
    assert result.success is True
    assert result.error is None
    assert result.bytes_transferred == 65536
    assert result.snapshot_name == snapshot.name

    # Verify qemu-img convert was called WITHOUT -c flag
    convert_calls = [
        call_obj
        for call_obj in shell_spy.call_args_list
        if "qemu-img convert" in " ".join(call_obj.args[0])
    ]
    assert len(convert_calls) == 1
    convert_args = convert_calls[0].args[0]
    assert "-c" not in convert_args, (
        "qemu-img convert should NOT contain -c when compress=False"
    )
    # Verify required format flags are present
    assert "-f" in convert_args
    assert "qcow2" in convert_args
    assert "-O" in convert_args


def test_create_full_backup_compressed(mock_shell, make_target, tmp_path):
    """``create_full_backup(compress=True, bucket_level="monthly")`` calls
    ``qemu-img convert`` WITH the ``-c`` flag and returns
    ``BackupResult(success=True)``.

    The command pattern is ``qemu-img convert -c -f qcow2 -O qcow2 <src> <tmp>``.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # Mock qemu-img convert returns success
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )
    # Mock mv (atomic rename) returns success
    mock_shell.expect(r"^mv ").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    # Side effect: simulate mv creating the final file so stat() works
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run
    ) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            snapshot, target, compress=True, bucket_level="monthly",
        )

    # Assert successful result
    assert result.success is True
    assert result.error is None
    assert result.bytes_transferred == 65536

    # Verify qemu-img convert was called WITH -c flag
    convert_calls = [
        call_obj
        for call_obj in shell_spy.call_args_list
        if "qemu-img convert" in " ".join(call_obj.args[0])
    ]
    assert len(convert_calls) == 1
    convert_args = convert_calls[0].args[0]
    assert "-c" in convert_args, (
        "qemu-img convert should contain -c when compress=True"
    )
    # Verify -c appears before -f (flag ordering in source)
    assert convert_args.index("-c") < convert_args.index("-f")


# ──────────────────────────────────────────────────────────────────────────
# FULL anchor rebase in transfer_missing
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_missing_rebases_to_full_anchor(
    mock_shell, make_vm_config, make_target, tmp_path
):
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
        path=str(tmp_path / "backups"), incremental=True, verify="off",
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

    # list() calls qemu-img info on the existing anchor file
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 1024}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # rsync succeeds
    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )
    # qemu-img rebase succeeds
    mock_shell.expect(r"qemu-img rebase").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    # Side effect: simulate rsync creating the target file so stat() works
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run
    ) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(
            vm_config, target, [snapshot], rate_limit="no"
        )

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None
    assert results[0].target_path == expected_target_file

    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]

    # Verify rebase command rebases to the FULL anchor
    rebase_cmds = [cmd for cmd in all_cmds if "qemu-img rebase" in cmd]
    assert len(rebase_cmds) == 1
    rebase_cmd = rebase_cmds[0]

    # CRITICAL: -u flag (unsafe, metadata-only)
    assert " -u " in rebase_cmd
    # CRITICAL: backing path is the bare anchor filename prefixed with ./
    assert f"./{anchor_name}" in rebase_cmd
    # Verify target file is in the rebase command
    assert str(expected_target_file) in rebase_cmd

    # CRITICAL: qemu-img info was NOT called on the source snapshot
    # (the FULL-anchor path skips the source backing-filename query)
    source_info_cmds = [
        cmd
        for cmd in all_cmds
        if "qemu-img info" in cmd and str(snapshot.path) in cmd
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
        path=str(tmp_path / "nonexistent_target"), incremental=True,
        verify="off", copy_base=True,
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
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
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
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    # Side effect: simulate rsync creating the target file so stat() works
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run
    ) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]

    # Verify qemu-img info WAS called on the source (to get backing-filename)
    source_info_cmds = [
        cmd
        for cmd in all_cmds
        if "qemu-img info" in cmd and str(snapshot.path) in cmd
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
    # Verify target file is in the rebase command
    assert str(expected_target_file) in rebase_cmd


# ──────────────────────────────────────────────────────────────────────────
# Rate-limited transfer (rsync --bwlimit)
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_with_rate_limit_uses_rsync(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``rate_limit`` is set, the provider uses
    ``rsync --bwlimit=<kib> --partial --progress``.

    ``rate_limit="100M"`` → ``rate_limit_to_kib("100M") == 102400`` →
    ``--bwlimit=102400``.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=False,
        verify="off", rate_limit="100M", copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # rsync → success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run
    ) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(
            vm_config, target, [snapshot], rate_limit="100M"
        )

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].bytes_transferred == 65536

    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--bwlimit=102400" in rsync_cmds[0]
    assert "--partial" in rsync_cmds[0]
    assert "--progress" in rsync_cmds[0]
    assert str(snapshot.path) in rsync_cmds[0]


def test_partial_file_resumes_with_rsync(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When a partial file exists in the target and ``rate_limit`` is set,
    rsync is invoked with ``--partial`` to resume the interrupted transfer.

    The partial file is an incomplete ``.qcow2`` — ``qemu-img info`` fails
    on it, so ``list()`` skips it and the snapshot is treated as missing.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"), incremental=False,
        verify="off", rate_limit="100M", copy_base=True,
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
            success=False, stdout="", stderr="corrupt file",
            returncode=1, error="corrupt file",
        )
    )
    # rsync → success
    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run
    ) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(
            vm_config, target, [snapshot], rate_limit="100M"
        )

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    assert "--partial" in rsync_cmds[0]


def test_pre_transfer_info_log(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
):
    """An INFO log is emitted before the transfer, mentioning the rate
    limit when one is configured.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=False,
        verify="off", rate_limit="100M", copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    caplog.set_level(logging.INFO, logger="qsnap.modules.backup.file_copy")

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        provider.transfer_missing(
            vm_config, target, [snapshot], rate_limit="100M"
        )

    info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        "Transferring" in msg and "rate limit: 100M" in msg
        for msg in info_msgs
    ), f"Expected pre-transfer INFO log mentioning rate limit, got: {info_msgs}"


def test_post_transfer_info_log_throughput(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
):
    """An INFO log is emitted after the transfer with bytes transferred
    and elapsed time (throughput).

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=False,
        verify="off", rate_limit="100M", copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    caplog.set_level(logging.INFO, logger="qsnap.modules.backup.file_copy")

    # Mock time.monotonic to guarantee a positive, deterministic elapsed
    with patch(
        "qsnap.modules.backup.file_copy.time.monotonic",
        side_effect=[100.0, 101.0],
    ):
        with patch.object(mock_shell, "run", side_effect=spied_run):
            provider = FileCopyBackupProvider(mock_shell)
            provider.transfer_missing(
                vm_config, target, [snapshot], rate_limit="100M"
            )

    info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        "Transferred" in msg and "bytes" in msg and "MiB/s" in msg
        for msg in info_msgs
    ), f"Expected post-transfer INFO log with throughput, got: {info_msgs}"


def test_debug_log_contains_rsync_command(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
):
    """A DEBUG log is emitted with the full rsync command string.

    Uses ``copy_base=True`` to skip the empty-target FULL creation path.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=False,
        verify="off", rate_limit="100M", copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    caplog.set_level(logging.DEBUG, logger="qsnap.modules.backup.file_copy")

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = FileCopyBackupProvider(mock_shell)
        provider.transfer_missing(
            vm_config, target, [snapshot], rate_limit="100M"
        )

    debug_msgs = [
        r.message for r in caplog.records if r.levelno == logging.DEBUG
    ]
    assert any(
        "Transfer command: rsync" in msg and "--bwlimit=102400" in msg
        for msg in debug_msgs
    ), f"Expected DEBUG log with rsync command, got: {debug_msgs}"


def test_slow_transfer_triggers_warning(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
):
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
        path=str(tmp_path / "nonexistent_target"), incremental=False,
        verify="off", rate_limit="100M", copy_base=True,
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    caplog.set_level(logging.WARNING, logger="qsnap.modules.backup.file_copy")

    # Mock time.monotonic: start=100.0, end=200.0 → elapsed=100.0s
    # throughput = 65536 / 100 = 655.36 B/s < 10_485_760 → WARNING
    with patch(
        "qsnap.modules.backup.file_copy.time.monotonic",
        side_effect=[100.0, 200.0],
    ):
        with patch.object(mock_shell, "run", side_effect=spied_run):
            provider = FileCopyBackupProvider(mock_shell)
            provider.transfer_missing(
                vm_config, target, [snapshot], rate_limit="100M"
            )

    warnings = [
        r.message for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert any(
        "slower than expected" in msg and "Check target disk health" in msg
        for msg in warnings
    ), f"Expected slow-transfer WARNING, got: {warnings}"


def test_full_backup_ignores_rate_limit(
    mock_shell, make_target, tmp_path
):
    """``create_full_backup()`` uses ``qemu-img convert`` regardless of
    the ``rate_limit`` setting on the target — rsync is never used for
    full (anchor) backups.
    """
    target = make_target(
        path=str(tmp_path / "backups"), rate_limit="100M",
    )
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect(r"^mv ").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run
    ) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        result = provider.create_full_backup(
            snapshot, target, compress=False, bucket_level="monthly",
        )

    assert result.success is True

    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
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
    ``transfer_missing`` triggers ``create_full_backup()`` for the
    first (most recent) snapshot instead of rsync.  No rsync is called
    for that snapshot.

    Verifies design D4: first backup to empty target is always a FULL via
    ``qemu-img convert``, and ``record_full_backup()`` is called on state.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"), incremental=False,
        verify="off", copy_base=False,
    )
    target.path.mkdir(parents=True, exist_ok=True)

    snapshots = [
        SnapshotInfo(
            name="testvm.20250101T000000",
            path=Path("/snapshots/testvm.20250101T000000.qcow2"),
            timestamp=datetime(2025, 1, 1, 0, 0, 0),
            allocation=65536,
        ),
    ]

    # Mock qemu-img convert returns success
    mock_shell.expect(r"qemu-img convert").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )
    # Mock mv (atomic rename) returns success
    mock_shell.expect(r"^mv ").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )
    # Mock rsync (for subsequent incremental transfer after FULL)
    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    # Side effect: simulate mv creating the final file so stat() works
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        elif cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run
    ) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell, state=mock_state)
        results = provider.transfer_missing(
            vm_config, target, snapshots, rate_limit="no"
        )

    # First result is from create_full_backup
    # Second result is from rsync of the same snapshot (FULL name ≠ snapshot name)
    assert len(results) == 2
    assert results[0].success is True  # FULL creation
    assert results[1].success is True  # rsync transfer

    # Both qemu-img convert and rsync were called
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1, (
        "rsync should be called for subsequent transfer after FULL creation"
    )
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1

    # State was notified: record_full_backup was called
    full_backups = mock_state.get_full_backups(str(target.path))
    assert len(full_backups) == 1
    assert full_backups[0].bucket_level == "monthly"


def test_copy_base_true_allows_base_copy(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``copy_base=True`` and the target is empty,
    ``transfer_missing`` uses rsync directly instead of calling
    ``create_full_backup()``.

    This verifies that setting ``copy_base=True`` preserves the legacy
    behavior where the base disk image is copied (via rsync) rather than
    creating a standalone FULL backup via ``qemu-img convert``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=False,
        verify="off", copy_base=True,
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
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("rsync "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run
    ) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(
            vm_config, target, [snapshot], rate_limit="no"
        )

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].target_path == expected_target_file

    # rsync was called (not qemu-img convert)
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    rsync_cmds = [cmd for cmd in all_cmds if cmd.startswith("rsync ")]
    assert len(rsync_cmds) == 1
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, (
        "qemu-img convert should NOT be called when copy_base=True"
    )


# ──────────────────────────────────────────────────────────────────────────
# Retry-unawareness (fault-tolerance-and-safety)
# ──────────────────────────────────────────────────────────────────────────


def test_provider_remains_retry_unaware(
    mock_shell, make_vm_config, make_target, tmp_path
):
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
    mock_shell.expect(r"^rsync").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=error_msg,
            returncode=1,
            error=error_msg,
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(
            vm_config, target, [snapshot], rate_limit="no"
        )

    # Assert failure result with the original error string
    assert len(results) == 1
    assert results[0].success is False
    assert "Connection refused" in results[0].error
    assert results[0].bytes_transferred == 0
    assert results[0].snapshot_name == snapshot.name
    assert results[0].source_path == snapshot.path
    assert results[0].target_path == expected_target_file

    # The transfer command (rsync) was attempted exactly ONCE — no retry loop
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
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
    results = provider.transfer_missing(
        vm_config, target, [snapshot], rate_limit="no"
    )

    # Assert error field is a proper string
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error is not None
    assert isinstance(results[0].error, str)

    # The error string is structured so that is_retryable() can
    # pattern-match it (Core's responsibility to act on the result)
    assert is_retryable(results[0].error) is True
