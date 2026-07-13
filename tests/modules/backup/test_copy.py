"""Unit tests for FileCopyBackupProvider.

Tests cover the three ``IBackupProvider`` methods (``transfer_missing``,
``list``, ``delete``) using ``MockShell`` to simulate ``cp``/``qemu-img``/
``rm`` commands.  No real I/O occurs — all shell calls are intercepted by
``MockShell``.

Design decisions verified:
- **D1**: ``FileCopyBackupProvider`` does NOT inherit from ``Core``; its only
  dependency is ``IShell``.
- **D5**: For incremental backups, ``qemu-img rebase -u -b <bare_filename>``
  is used to update the backing path to a bare filename in the target
  directory (metadata-only update, no data copy).

Scenarios (from ``specs/backup-provider/spec.md``):
Transfer Missing:
1. New snapshot copied to empty target.
2. Snapshot already exists on target — skipped.
3. Incremental backup — rebase backing path.
4. Non-incremental backup — no rebase.
5. Copy fails — disk full or permission error.
List Backups:
6. Target directory exists with backups.
7. Target directory does not exist.
8. Target directory exists but is empty.
Delete Backups:
9. Successful backup deletion.
10. Backup file does not exist — rm -f is idempotent.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qsnap.models.results import BackupResult, ShellResult, SnapshotInfo
from qsnap.modules.backup.file_copy import FileCopyBackupProvider


# ──────────────────────────────────────────────────────────────────────────
# Transfer Missing
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_missing_new_snapshot_empty_target(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When the target path does not exist (``list()`` returns ``[]``) and
    there is one snapshot to copy, the provider copies it via ``cp`` to
    ``target.path/<snapshot.name>.qcow2`` and returns
    ``BackupResult(success=True, bytes_transferred=<file_size>)``.
    """
    vm_config = make_vm_config()
    # Target path does not exist -> list() returns [] with no shell calls
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=False
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # Mock cp returns success
    mock_shell.expect("cp").returns(
        ShellResult(
            success=True, stdout="", stderr="", returncode=0, error=None
        )
    )

    # Side effect: simulate cp creating the target file so stat() works.
    # We wrap the original MockShell.run, creating the file on cp commands,
    # then delegating to the expectation-based mock for the return value.
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("cp "):
            target_file = Path(cmd[-1])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run) as shell_spy:
        provider = FileCopyBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result with correct file size
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].bytes_transferred == 65536
    assert results[0].error is None
    assert results[0].snapshot_name == snapshot.name
    assert results[0].source_path == snapshot.path
    assert results[0].target_path == expected_target_file

    # Assert cp command copies to target.path/<snapshot.name>.qcow2
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    cp_cmds = [cmd for cmd in all_cmds if cmd.startswith("cp ")]
    assert len(cp_cmds) == 1
    assert str(snapshot.path) in cp_cmds[0]
    assert str(expected_target_file) in cp_cmds[0]


def test_transfer_missing_existing_snapshot_skipped(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When the target already contains a backup with the same name as the
    snapshot, the snapshot is NOT copied (``cp`` is NOT called) and does not
    appear in the returned ``BackupResult`` list.
    """
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path), incremental=False)

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

    # cp is NOT called
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    cp_cmds = [cmd for cmd in all_cmds if cmd.startswith("cp ")]
    assert len(cp_cmds) == 0


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
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=True
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # Mock cp returns success
    mock_shell.expect("cp").returns(
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
        results = provider.transfer_missing(vm_config, target, [snapshot])

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
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=False
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    # Mock cp returns success
    mock_shell.expect("cp").returns(
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


def test_transfer_copy_fails_disk_full(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``cp`` returns a non-zero exit code (e.g. disk full), the
    provider returns ``BackupResult(success=False, error=<stderr>)``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), incremental=False
    )

    snapshot = SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    expected_target_file = target.path / f"{snapshot.name}.qcow2"

    # Mock cp returns failure (disk full)
    error_msg = "No space left on device"
    mock_shell.expect("cp").returns(
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
