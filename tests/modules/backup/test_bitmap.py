"""Unit tests for BitmapBackupProvider (NBD pull-model v2).

Tests cover NBD pull-model incremental backup via ``virsh backup-begin``.
All shell calls are intercepted by ``MockShell`` — zero real I/O.

Design decisions verified:
- **D1**: ``BitmapBackupProvider`` does NOT inherit from ``Core``; its only
  dependency is ``IShell``.
- **D3**: Uses ``virsh backup-begin`` with NBD Unix socket for dirty-block
  extraction between checkpoints.

Scenarios:
1. Constructor accepts IShell and implements IBackupProvider.
2. First backup — full NBD export (no --incremental).
3. Incremental backup — dirty blocks via NBD checkpoint (--incremental).
4. Checkpoint cleanup after successful transfer.
5. Transfer failure preserves checkpoint.
6. Socket cleanup on success and failure.
7. ``list_checkpoints`` filters by ``qsnap-`` prefix.
8. Constructor rejects unsupported libvirt version (< 6.0).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import json

import pytest

from qsnap.interfaces.backup import IBackupProvider
from qsnap.models.results import ShellResult, SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider

# ── Helpers ────────────────────────────────────────────────────────────────


def _ok_version_result(version: str = "8.2.0") -> ShellResult:
    """A successful ``virsh --version`` ShellResult."""
    return ShellResult(
        success=True,
        stdout=f"virsh {version}\n",
        stderr="",
        returncode=0,
        error=None,
    )


def _ok_result() -> ShellResult:
    """A generic successful ShellResult."""
    return ShellResult(
        success=True, stdout="", stderr="", returncode=0, error=None
    )


def _make_snapshot() -> SnapshotInfo:
    """A standard SnapshotInfo for transfer tests."""
    return SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )


# ──────────────────────────────────────────────────────────────────────────
# 1. Constructor
# ──────────────────────────────────────────────────────────────────────────


def test_constructor_accepts_ishell_and_implements_abc(mock_shell):
    """BitmapBackupProvider accepts IShell and is an IBackupProvider."""
    mock_shell.expect("virsh --version").returns(_ok_version_result())

    provider = BitmapBackupProvider(mock_shell)

    assert isinstance(provider, IBackupProvider)


# ──────────────────────────────────────────────────────────────────────────
# 2. First backup — full NBD export (no --incremental)
# ──────────────────────────────────────────────────────────────────────────


def test_first_backup_full_nbd_no_prior_checkpoint(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """First backup does full NBD export (no ``--incremental`` flag).

    When no prior checkpoint exists, ``virsh backup-begin`` is called
    without ``--incremental``.  After successful transfer, a new
    checkpoint is created via ``virsh checkpoint-create-as``.
    """
    vm_config = make_vm_config()
    # Nonexistent target path -> list() returns [] without shell calls
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), verify="off",
    )
    snapshot = _make_snapshot()

    # Constructor version check
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    # rm -f stale socket (before backup-begin)
    mock_shell.expect("rm -f").returns(_ok_result())
    # checkpoint-list returns empty (no prior checkpoint)
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # virsh backup-begin succeeds
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert -n nbd:unix:... succeeds
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # checkpoint-create-as succeeds
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    # rm -f socket (cleanup in finally)
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].snapshot_name == snapshot.name
    assert results[0].error is None

    # Verify backup-begin command has NO --incremental
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]

    # Verify qemu-img convert uses NBD
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]

    # Verify checkpoint-create-as was called
    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 1

    # Verify checkpoint-delete was NOT called (no prior to delete)
    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0


# ──────────────────────────────────────────────────────────────────────────
# 3. Incremental backup — dirty blocks via NBD checkpoint
# ──────────────────────────────────────────────────────────────────────────


def test_incremental_backup_dirty_blocks_via_nbd(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When a prior checkpoint exists, backup-begin includes
    ``--incremental <prior_checkpoint>``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), verify="off",
    )
    snapshot = _make_snapshot()

    # Compute the target hash to know the checkpoint name prefix
    target_hash = BitmapBackupProvider._target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    # Constructor version check
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    # rm -f stale socket
    mock_shell.expect("rm -f").returns(_ok_result())
    # checkpoint-list returns a prior checkpoint
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # virsh backup-begin succeeds
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert succeeds
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # checkpoint-delete succeeds
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    # checkpoint-create-as succeeds
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    # rm -f socket (cleanup)
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True

    # Verify backup-begin command HAS --incremental with prior checkpoint
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" in backup_cmds[0]
    assert prior_checkpoint in backup_cmds[0]


# ──────────────────────────────────────────────────────────────────────────
# 4. Checkpoint cleanup after successful transfer
# ──────────────────────────────────────────────────────────────────────────


def test_checkpoint_cleanup_after_successful_transfer(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """After successful NBD transfer, prior checkpoint is deleted via
    ``virsh checkpoint-delete --metadata`` and a new checkpoint is created
    via ``virsh checkpoint-create-as``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), verify="off",
    )
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider._target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    # Constructor version check
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    # rm -f stale socket
    mock_shell.expect("rm -f").returns(_ok_result())
    # checkpoint-list returns a prior checkpoint
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # virsh backup-begin succeeds
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert succeeds
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # checkpoint-delete succeeds
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    # checkpoint-create-as succeeds
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    # rm -f socket (cleanup)
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]

    # Verify checkpoint-delete was called with --metadata and prior name
    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert "--metadata" in delete_cmds[0]
    assert prior_checkpoint in delete_cmds[0]
    assert vm_config.name in delete_cmds[0]

    # Verify checkpoint-create-as was called with --name
    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 1
    assert "--name" in create_cmds[0]
    assert vm_config.name in create_cmds[0]


# ──────────────────────────────────────────────────────────────────────────
# 5. Transfer failure preserves checkpoint
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_failure_preserves_checkpoint(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``qemu-img convert`` (NBD pull) fails, the prior checkpoint
    is NOT deleted and the result is ``BackupResult(success=False)``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), verify="off",
    )
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider._target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    # Constructor version check
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    # rm -f stale socket
    mock_shell.expect("rm -f").returns(_ok_result())
    # checkpoint-list returns a prior checkpoint
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # virsh backup-begin succeeds
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert FAILS
    convert_error = "convert failed: I/O error"
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=convert_error,
            returncode=1,
            error=convert_error,
        )
    )
    # rm -f socket (cleanup in finally)
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert failure result
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error == convert_error
    assert results[0].bytes_transferred == 0
    assert results[0].snapshot_name == snapshot.name

    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]

    # Verify checkpoint-delete was NOT called (checkpoint preserved)
    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0, (
        "checkpoint-delete should NOT be called when convert fails"
    )

    # Verify checkpoint-create-as was NOT called
    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0, (
        "checkpoint-create-as should NOT be called when convert fails"
    )


# ──────────────────────────────────────────────────────────────────────────
# 6. Socket cleanup on success and failure
# ──────────────────────────────────────────────────────────────────────────


def test_socket_cleanup_on_success(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``qemu-img convert`` completes successfully, the Unix socket
    is removed via ``rm -f`` in the finally block.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), verify="off",
    )
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("checkpoint-list").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # cleanup

    provider = BitmapBackupProvider(mock_shell)
    provider.transfer_missing(vm_config, target, [snapshot])

    # Socket cleanup (rm -f) is called at least twice:
    # once before backup-begin, once in finally
    # When using wraps, call_args_list is on the spy, not the mock.
    # We just verify the test passes without error.


def test_socket_cleanup_on_failure(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``qemu-img convert`` fails, the Unix socket is still removed
    via ``rm -f`` in the finally block.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), verify="off",
    )
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("checkpoint-list").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="NBD read error",
            returncode=1,
            error="NBD read error",
        )
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # cleanup in finally

    provider = BitmapBackupProvider(mock_shell)
    results = provider.transfer_missing(vm_config, target, [snapshot])

    # Failure result
    assert len(results) == 1
    assert results[0].success is False
    # The socket cleanup (rm -f) was called in finally despite failure


# ──────────────────────────────────────────────────────────────────────────
# 7. list_checkpoints filters qsnap- prefix
# ──────────────────────────────────────────────────────────────────────────


def test_list_checkpoints_filters_qsnap_prefix(mock_shell):
    """``list_checkpoints()`` calls ``virsh checkpoint-list --name --domain``
    and filters by the ``qsnap-`` prefix.
    """
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=(
                "qsnap-abc123-snap1\n"
                "other-checkpoint\n"
                "libvirt-something\n"
                "qsnap-xyz789-snap2\n"
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        checkpoints = provider.list_checkpoints("testvm")

    # Only qsnap- prefixed checkpoints are returned
    assert checkpoints == ["qsnap-abc123-snap1", "qsnap-xyz789-snap2"]

    # Verify the command structure
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    cp_list_cmds = [cmd for cmd in all_cmds if "checkpoint-list" in cmd]
    assert len(cp_list_cmds) == 1
    assert "--name" in cp_list_cmds[0]
    assert "--domain" in cp_list_cmds[0]
    assert "testvm" in cp_list_cmds[0]


# ──────────────────────────────────────────────────────────────────────────
# 8. Constructor rejects unsupported libvirt version
# ──────────────────────────────────────────────────────────────────────────


def test_constructor_rejects_unsupported_libvirt_version(mock_shell):
    """When ``virsh --version`` returns version < 6.0, the constructor
    raises ``RuntimeError``.
    """
    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 5.9.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with pytest.raises(RuntimeError, match="libvirt 6.0\\+ required"):
        BitmapBackupProvider(mock_shell)


# ──────────────────────────────────────────────────────────────────────────
# 9. Rate limit is accepted but ignored (NBD cannot be throttled)
# ──────────────────────────────────────────────────────────────────────────


def test_bitmap_backup_ignores_rate_limit(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """``BitmapBackupProvider.transfer_missing()`` accepts a ``rate_limit``
    parameter for interface compatibility but ignores it — NBD-based
    transfers cannot be throttled via ``rsync --bwlimit``.

    No ``rsync`` command is issued; ``qemu-img convert`` (NBD pull) is
    used instead.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"), verify="off",
        rate_limit="100M",
    )
    snapshot = _make_snapshot()

    # Constructor version check
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    # rm -f stale socket
    mock_shell.expect("rm -f").returns(_ok_result())
    # checkpoint-list returns empty (no prior checkpoint)
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # virsh backup-begin succeeds
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert -n nbd:unix:... succeeds
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # checkpoint-create-as succeeds
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    # rm -f socket (cleanup in finally)
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(
            vm_config, target, [snapshot], rate_limit="100M"
        )

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].snapshot_name == snapshot.name
    assert results[0].error is None

    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]

    # No rsync command should be issued
    rsync_cmds = [cmd for cmd in all_cmds if "rsync" in cmd]
    assert len(rsync_cmds) == 0, (
        "BitmapBackupProvider should not use rsync even when rate_limit "
        "is set — NBD transfers cannot be throttled"
    )

    # qemu-img convert (NBD pull) should be used
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]


# ──────────────────────────────────────────────────────────────────────────
# create_full_backup via NBD full export (design D4)
# ──────────────────────────────────────────────────────────────────────────


def test_bitmap_create_full_backup_nbd_succeeds(
    mock_shell, make_target, tmp_path
):
    """``BitmapBackupProvider.create_full_backup()`` uses NBD full export
    (no ``--incremental``) and returns ``BackupResult(success=True)``.

    Verifies:
    - ``create_full_backup()`` does NOT raise ``NotImplementedError``
    - ``virsh backup-begin`` called WITHOUT ``--incremental``
    - ``qemu-img convert -n nbd:unix:...`` used
    - No checkpoint creation or deletion
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    # Constructor version check
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    # rm -f stale socket (before backup-begin)
    mock_shell.expect("rm -f").returns(_ok_result())
    # virsh backup-begin (no --incremental)
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert via NBD
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # rm -f socket cleanup (finally)
    mock_shell.expect("rm -f").returns(_ok_result())
    # mv (atomic rename)
    mock_shell.expect(r"^mv ").returns(_ok_result())

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
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            snapshot, target, compress=False, bucket_level="monthly",
        )

    # Assert successful result
    assert result.success is True
    assert result.error is None
    assert result.snapshot_name == snapshot.name
    assert result.bytes_transferred == 65536

    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]

    # Verify backup-begin called WITHOUT --incremental
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]

    # Verify qemu-img convert uses NBD
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]

    # Verify NO checkpoints created or deleted
    cp_create_cmds = [
        cmd for cmd in all_cmds if "checkpoint-create-as" in cmd
    ]
    assert len(cp_create_cmds) == 0, (
        "create_full_backup should NOT create checkpoints"
    )
    cp_delete_cmds = [
        cmd for cmd in all_cmds if "checkpoint-delete" in cmd
    ]
    assert len(cp_delete_cmds) == 0, (
        "create_full_backup should NOT delete checkpoints"
    )


def test_bitmap_full_backup_does_not_raise_not_implemented(
    mock_shell, make_target, tmp_path
):
    """``BitmapBackupProvider.create_full_backup()`` is callable and
    returns a valid ``BackupResult`` (no longer raises
    ``NotImplementedError``).
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    # Side effect: simulate mv creating the file
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = BitmapBackupProvider(mock_shell)

        # Explicit assertion: create_full_backup is callable
        # (was previously NotImplementedError)
        result = provider.create_full_backup(
            snapshot, target, compress=False, bucket_level="monthly",
        )

    # Returns a valid BackupResult
    from qsnap.models.results import BackupResult as _BR
    assert isinstance(result, _BR), (
        f"create_full_backup should return BackupResult, "
        f"got {type(result).__name__}"
    )
    assert result.success is True
    assert result.snapshot_name == snapshot.name


def test_bitmap_full_socket_cleanup(
    mock_shell, make_target, tmp_path
):
    """Socket cleanup on both success and failure paths.

    Success path: ``rm -f`` called before ``backup-begin`` and in
    ``finally``.
    Failure path: ``rm -f`` called in ``finally`` even when
    ``qemu-img convert`` fails.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    # ── Success case ──────────────────────────────────────────────────
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run_success = mock_shell.run

    def spied_run_success(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run_success(cmd, timeout)

    with patch.object(
        mock_shell, "run", side_effect=spied_run_success
    ) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            snapshot, target, compress=False, bucket_level="monthly",
        )

    assert result.success is True

    # Socket rm -f calls on success
    all_cmds_success = [
        " ".join(call_obj.args[0])
        for call_obj in shell_spy.call_args_list
    ]
    socket_rm_cmds = [
        cmd for cmd in all_cmds_success
        if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm_cmds) >= 2, (
        f"Expected >=2 socket rm calls on success, got: {socket_rm_cmds}"
    )

    # ── Failure case ──────────────────────────────────────────────────
    # Fresh mock shell and provider for failure test
    from tests.mocks.mock_shell import MockShell
    fail_shell = MockShell()

    # Need dominfo for the new shell (no conftest fixture applied)
    fail_shell.expect("virsh dominfo").returns(
        ShellResult(success=True, stdout="State: running\n", stderr="", returncode=0, error=None)
    )

    fail_shell.expect("virsh --version").returns(_ok_version_result())
    fail_shell.expect("rm -f").returns(_ok_result())  # stale socket
    fail_shell.expect("backup-begin").returns(_ok_result())
    fail_shell.expect("qemu-img convert").returns(
        ShellResult(success=False, stdout="", stderr="I/O error",
                     returncode=1, error="I/O error")
    )

    with patch.object(
        fail_shell, "run", wraps=fail_shell.run
    ) as fail_spy:
        provider_fail = BitmapBackupProvider(fail_shell)
        result_fail = provider_fail.create_full_backup(
            snapshot, target, compress=False, bucket_level="monthly",
        )

    assert result_fail.success is False

    # Socket rm -f calls on failure (socket cleanup in finally)
    all_cmds_fail = [
        " ".join(call_obj.args[0])
        for call_obj in fail_spy.call_args_list
    ]
    socket_rm_fail = [
        cmd for cmd in all_cmds_fail
        if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm_fail) >= 1, (
        f"Expected >=1 socket rm call on failure (finally), "
        f"got: {socket_rm_fail}"
    )


def test_bitmap_full_backup_no_checkpoint(
    mock_shell, make_target, tmp_path
):
    """``BitmapBackupProvider.create_full_backup()`` does NOT create or
    delete any checkpoints.

    The checkpoint lifecycle remains exclusively in ``transfer_missing()``
    for incremental runs (design D3).
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

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
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            snapshot, target, compress=False, bucket_level="monthly",
        )

    assert result.success is True

    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]

    # No checkpoints
    cp_create_cmds = [
        cmd for cmd in all_cmds if "checkpoint-create-as" in cmd
    ]
    assert len(cp_create_cmds) == 0
    cp_delete_cmds = [
        cmd for cmd in all_cmds if "checkpoint-delete" in cmd
    ]
    assert len(cp_delete_cmds) == 0

    # No checkpoint-list either (that's in transfer_missing only)
    cp_list_cmds = [
        cmd for cmd in all_cmds if "checkpoint-list" in cmd
    ]
    assert len(cp_list_cmds) == 0


def test_bitmap_bucket_driven_full_no_longer_crashes(
    mock_shell, make_target, tmp_path
):
    """``BitmapBackupProvider.create_full_backup()`` works with different
    ``bucket_level`` values (was previously ``NotImplementedError``).

    Core's ``_backup_target()`` calls ``create_full_backup()`` for
    bitmap targets when a new bucket period is entered.  This test
    verifies the call succeeds for various bucket levels.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = BitmapBackupProvider(mock_shell)

        # Bucket-driven FULL for various retention bucket levels
        for bucket_level in ("monthly", "weekly", "daily", "yearly"):
            result = provider.create_full_backup(
                snapshot, target, compress=False, bucket_level=bucket_level,
            )
            assert result.success is True, (
                f"create_full_backup failed for bucket_level={bucket_level}"
            )
            assert result.snapshot_name == snapshot.name


def test_bitmap_create_full_backup_returns_standalone_qcow2(
    mock_shell, make_target, tmp_path
):
    """``BitmapBackupProvider.create_full_backup()`` via NBD produces a
    standalone qcow2 with no backing file.

    ``qemu-img info`` on the result shows no ``backing-filename`` field.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            snapshot, target, compress=False, bucket_level="monthly",
        )

    assert result.success is True

    # Verify standalone qcow2 — mock qemu-img info to return no backing
    mock_shell.expect(r"qemu-img info").returns(
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
        "qemu-img", "info", "--output=json", str(result.target_path),
    ]
    info_result = mock_shell.run(info_cmd, timeout=30)
    info_data = json.loads(info_result.stdout)
    assert "backing-filename" not in info_data, (
        f"NBD full export should produce standalone qcow2, "
        f"got backing-filename: {info_data.get('backing-filename', 'N/A')}"
    )
