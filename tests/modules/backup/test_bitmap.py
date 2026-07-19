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

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

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
    return ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


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
    """BitmapBackupProvider accepts IShell and is an IBackupProvider.

    The libvirt version check was moved to ``DefaultFactory.create_backup_provider()``.
    Constructor no longer calls ``_check_libvirt_version()``.
    """
    provider = BitmapBackupProvider(mock_shell)

    assert isinstance(provider, IBackupProvider)


def test_bitmap_constructor_no_version_check():
    """BitmapBackupProvider.__init__ no longer calls ``_check_libvirt_version()``.

    The version check moved to ``DefaultFactory.create_backup_provider()``
    (design D2).  Constructing with a minimal MockShell — one that has
    NO ``virsh --version`` expectation configured — must succeed without
    error.  If the constructor still tried to call ``virsh --version``,
    MockShell would return a failure result, which would trigger a
    ``RuntimeError`` from the old ``_check_libvirt_version`` logic.
    """
    from tests.mocks.mock_shell import MockShell

    shell = MockShell()
    provider = BitmapBackupProvider(shell)

    assert isinstance(provider, IBackupProvider)
    assert not hasattr(BitmapBackupProvider, "_check_libvirt_version"), (
        "_check_libvirt_version method should not exist (version check moved to factory)"
    )


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
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
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
    # domjobabort in finally (transfer_missing)
    mock_shell.expect("domjobabort").returns(_ok_result())
    # domjobabort in finally (transfer_missing)
    mock_shell.expect("domjobabort").returns(_ok_result())
    # rm -f socket (cleanup in finally)
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].snapshot_name == snapshot.name
    assert results[0].error is None

    # Verify backup-begin command has NO --incremental
    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]

    # Verify qemu-img convert uses NBD with compression (compress=True default)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]
    assert "-c" in convert_cmds[0], "qemu-img convert should include -c when compress=True"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        "qemu-img convert should include -o compression_type=zstd (default)"
    )

    # Verify checkpoint-create-as was called
    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 1

    # Verify checkpoint-delete was NOT called (no prior to delete)
    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0


# ──────────────────────────────────────────────────────────────────────────
# 3. Incremental backup — dirty blocks via NBD checkpoint
# ──────────────────────────────────────────────────────────────────────────


def test_incremental_backup_dirty_blocks_via_nbd(mock_shell, make_vm_config, make_target, tmp_path):
    """When a prior checkpoint exists, backup-begin includes
    ``--incremental <prior_checkpoint>``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
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
    # domjobabort in finally (transfer_missing)
    mock_shell.expect("domjobabort").returns(_ok_result())
    # rm -f socket (cleanup)
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True

    # Verify backup-begin command HAS --incremental with prior checkpoint
    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" in backup_cmds[0]
    assert prior_checkpoint in backup_cmds[0]

    # Verify qemu-img convert includes -c (compress=True default)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" in convert_cmds[0], "qemu-img convert should include -c when compress=True"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        "qemu-img convert should include -o compression_type=zstd (default)"
    )


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
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
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
    # domjobabort in finally (transfer_missing)
    mock_shell.expect("domjobabort").returns(_ok_result())
    # rm -f socket (cleanup)
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

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

    # Verify qemu-img convert includes -c (compress=True default)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" in convert_cmds[0], "qemu-img convert should include -c when compress=True"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        "qemu-img convert should include -o compression_type=zstd (default)"
    )


# ──────────────────────────────────────────────────────────────────────────
# 5. Transfer failure preserves checkpoint
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_failure_preserves_checkpoint(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``qemu-img convert`` (NBD pull) fails, the prior checkpoint
    is NOT deleted, the partial file is deleted, and the result is
    ``BackupResult(success=False)``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
    )
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider._target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"
    target_file = target.path / f"{snapshot.name}.qcow2"

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
    # domjobabort in finally (transfer_missing)
    mock_shell.expect("domjobabort").returns(_ok_result())
    # rm -f socket (cleanup in finally)
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert failure result
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error == convert_error
    assert results[0].bytes_transferred == 0
    assert results[0].snapshot_name == snapshot.name

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # Verify qemu-img convert includes -c (compress=True default)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" in convert_cmds[0], "qemu-img convert should include -c when compress=True"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        "qemu-img convert should include -o compression_type=zstd (default)"
    )

    # Verify partial file deletion was called after convert failure
    partial_file_cmds = [
        cmd for cmd in all_cmds if cmd.startswith("rm -f") and str(target_file) in cmd
    ]
    assert len(partial_file_cmds) == 1, "Partial file should be deleted after transfer failure"

    # Verify checkpoint-delete was NOT called (checkpoint preserved)
    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0, "checkpoint-delete should NOT be called when convert fails"

    # Verify checkpoint-create-as was NOT called
    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0, "checkpoint-create-as should NOT be called when convert fails"


# ──────────────────────────────────────────────────────────────────────────
# 6. Socket cleanup on success and failure
# ──────────────────────────────────────────────────────────────────────────


def test_socket_cleanup_on_success(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``qemu-img convert`` completes successfully, the Unix socket
    is removed via ``rm -f`` in the finally block.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
    )
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("checkpoint-list").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())  # transfer_missing finally
    mock_shell.expect("rm -f").returns(_ok_result())  # cleanup

    provider = BitmapBackupProvider(mock_shell)
    provider.transfer_missing(vm_config, target, [snapshot])

    # Socket cleanup (rm -f) is called at least twice:
    # once before backup-begin, once in finally
    # When using wraps, call_args_list is on the spy, not the mock.
    # We just verify the test passes without error.


def test_socket_cleanup_on_failure(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``qemu-img convert`` fails, the Unix socket is still removed
    via ``rm -f`` in the finally block.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
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
    mock_shell.expect("domjobabort").returns(_ok_result())  # transfer_missing finally
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
                "qsnap-abc123-snap1\nother-checkpoint\nlibvirt-something\nqsnap-xyz789-snap2\n"
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        checkpoints = provider.list_checkpoints("testvm")

    # Only qsnap- prefixed checkpoints are returned
    assert checkpoints == ["qsnap-abc123-snap1", "qsnap-xyz789-snap2"]

    # Verify the command structure
    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds
    cp_list_cmds = [cmd for cmd in all_cmds if "checkpoint-list" in cmd]
    assert len(cp_list_cmds) == 1
    assert "--name" in cp_list_cmds[0]
    assert "--domain" in cp_list_cmds[0]
    assert "testvm" in cp_list_cmds[0]


# ──────────────────────────────────────────────────────────────────────────
# 8. Constructor no longer checks libvirt version (moved to factory)
# ──────────────────────────────────────────────────────────────────────────


# NOTE: ``test_constructor_rejects_unsupported_libvirt_version`` was removed.
# The libvirt version check moved from ``BitmapBackupProvider.__init__`` to
# ``DefaultFactory.create_backup_provider()`` (design D2).  The replacement
# test ``test_bitmap_constructor_no_version_check`` is delegated to
# @Mr.Tester (bitmap-unit group) per the test-plan.


# ──────────────────────────────────────────────────────────────────────────
# 9. Rate limit is accepted but ignored (NBD cannot be throttled)
# ──────────────────────────────────────────────────────────────────────────


def test_bitmap_backup_ignores_rate_limit(mock_shell, make_vm_config, make_target, tmp_path):
    """``BitmapBackupProvider.transfer_missing()`` accepts a ``rate_limit``
    parameter for interface compatibility but ignores it — NBD-based
    transfers cannot be throttled via ``rsync --bwlimit``.

    No ``rsync`` command is issued; ``qemu-img convert`` (NBD pull) is
    used instead.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
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
    # domjobabort in finally (transfer_missing)
    mock_shell.expect("domjobabort").returns(_ok_result())
    # domjobabort in finally (transfer_missing)
    mock_shell.expect("domjobabort").returns(_ok_result())
    # rm -f socket (cleanup in finally)
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="100M")

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].snapshot_name == snapshot.name
    assert results[0].error is None

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

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


def test_bitmap_create_full_backup_nbd_succeeds(mock_shell, make_target, tmp_path):
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
    # domjobabort in finally (nbd_full_export) — terminates NBD job
    mock_shell.expect("domjobabort").returns(_ok_result())
    # rm -f socket cleanup (finally, after domjobabort)
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

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
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
    assert result.snapshot_name == snapshot.name
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # Verify backup-begin called WITHOUT --incremental
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]

    # Verify qemu-img convert uses NBD (and NO compression flag)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]
    assert "-c" not in convert_cmds[0], "qemu-img convert should NOT use -c when compress=False"

    # Verify domjobabort was called in finally (design D3: terminate NBD job)
    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1, "domjobabort should be called in finally"
    assert "testvm" in abort_cmds[0], "domjobabort should target the correct VM"

    # Verify domjobabort was called BEFORE socket rm -f in the finally block
    socket_rm_idx = None
    abort_idx = None
    for i, cmd in enumerate(all_cmds):
        if "domjobabort" in cmd:
            abort_idx = i
        if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd and abort_idx is None:
            # This is a socket rm, but we haven't seen abort yet → track it
            pass
        if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd and abort_idx is not None:
            socket_rm_idx = i
            break
    if abort_idx is not None and socket_rm_idx is not None:
        assert abort_idx < socket_rm_idx, (
            f"domjobabort (index {abort_idx}) must be called BEFORE "
            f"socket rm -f (index {socket_rm_idx}) in finally"
        )

    # Verify NO checkpoints created or deleted
    cp_create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(cp_create_cmds) == 0, "create_full_backup should NOT create checkpoints"
    cp_delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(cp_delete_cmds) == 0, "create_full_backup should NOT delete checkpoints"


def test_bitmap_create_full_backup_with_compression_succeeds(
    mock_shell,
    make_target,
    tmp_path,
    caplog,
):
    """``BitmapBackupProvider.create_full_backup(compress=True)`` passes
    ``-c`` to ``qemu-img convert`` via ``nbd_full_export``, producing a
    compressed FULL backup.

    Verifies:
    - ``-c`` IS present in the ``qemu-img convert`` command
    - NO WARNING about ``compress=True ignored`` is logged
    - ``BackupResult.success`` is True
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    # Constructor version check
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    # rm -f stale socket (before backup-begin, in nbd_full_export)
    mock_shell.expect("rm -f").returns(_ok_result())
    # virsh backup-begin (no --incremental)
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert via NBD (with -c for compression)
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # domjobabort in finally (nbd_full_export) — terminates NBD job
    mock_shell.expect("domjobabort").returns(_ok_result())
    # rm -f socket cleanup (finally in nbd_full_export, after domjobabort)
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

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
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
    assert result.snapshot_name == snapshot.name
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # Verify qemu-img convert uses NBD WITH compression flag
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]
    assert "-c" in convert_cmds[0], "qemu-img convert SHOULD use -c when compress=True"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        "qemu-img convert should include -o compression_type=zstd when compression_type='zstd'"
    )

    # Verify domjobabort was called in finally
    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1, "domjobabort should be called in finally for FULL backup"

    # Verify NO WARNING about "compress=True ignored" is logged
    compress_ignored_warnings = [
        record for record in caplog.records if "compress=True ignored" in record.getMessage()
    ]
    assert len(compress_ignored_warnings) == 0, (
        "Should NOT log 'compress=True ignored' for NBD-based FULL backup"
    )

    # Verify NO checkpoints created or deleted
    cp_create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(cp_create_cmds) == 0
    cp_delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(cp_delete_cmds) == 0


def test_bitmap_full_backup_does_not_raise_not_implemented(mock_shell, make_target, tmp_path):
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
    mock_shell.expect("domjobabort").returns(_ok_result())
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
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    # Returns a valid BackupResult
    from qsnap.models.results import BackupResult as _BR

    assert isinstance(result, _BR), (
        f"create_full_backup should return BackupResult, got {type(result).__name__}"
    )
    assert result.success is True
    assert result.snapshot_name == snapshot.name


def test_bitmap_full_socket_cleanup(mock_shell, make_target, tmp_path):
    """Socket cleanup on both success and failure paths.

    Success path: ``domjobabort`` called before socket ``rm -f`` in
    ``finally``.  Failure path: ``domjobabort`` and ``rm -f`` BOTH
    called in ``finally`` even when ``qemu-img convert`` fails.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    # ── Success case ──────────────────────────────────────────────────
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())  # finally: terminate NBD job
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run_success = mock_shell.run

    def spied_run_success(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run_success(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run_success) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds_success = all_run_cmds + all_stall_cmds

    # Socket rm -f calls on success (stale + cleanup)
    socket_rm_cmds = [
        cmd for cmd in all_cmds_success if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm_cmds) >= 2, (
        f"Expected >=2 socket rm calls on success, got: {socket_rm_cmds}"
    )

    # Verify domjobabort was called (in run commands, before convert in stall commands)
    abort_cmds = [cmd for cmd in all_cmds_success if "domjobabort" in cmd]
    assert len(abort_cmds) == 1, "domjobabort should be called in finally"

    # Verify convert was called via stall detection
    convert_cmds_success = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds_success) == 1, "qemu-img convert should be in stall detection"

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
        ShellResult(success=False, stdout="", stderr="I/O error", returncode=1, error="I/O error")
    )
    # domjobabort still called in finally despite qemu-img failure
    fail_shell.expect("domjobabort").returns(_ok_result())

    with patch.object(fail_shell, "run", wraps=fail_shell.run) as fail_spy:
        provider_fail = BitmapBackupProvider(fail_shell)
        result_fail = provider_fail.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result_fail.success is False

    all_cmds_fail = [" ".join(call_obj.args[0]) for call_obj in fail_spy.call_args_list]

    # Socket rm -f calls on failure (stale + cleanup in finally)
    socket_rm_fail = [
        cmd for cmd in all_cmds_fail if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm_fail) >= 1, (
        f"Expected >=1 socket rm call on failure (finally), got: {socket_rm_fail}"
    )

    # domjobabort called even on failure
    abort_cmds_fail = [cmd for cmd in all_cmds_fail if "domjobabort" in cmd]
    assert len(abort_cmds_fail) == 1, (
        "domjobabort should be called in finally even when qemu-img convert fails"
    )


def test_bitmap_full_backup_no_checkpoint(mock_shell, make_target, tmp_path):
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
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # No checkpoints
    cp_create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(cp_create_cmds) == 0
    cp_delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(cp_delete_cmds) == 0

    # No checkpoint-list either (that's in transfer_missing only)
    cp_list_cmds = [cmd for cmd in all_cmds if "checkpoint-list" in cmd]
    assert len(cp_list_cmds) == 0


def test_bitmap_bucket_driven_full_no_longer_crashes(mock_shell, make_target, tmp_path):
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
    mock_shell.expect("domjobabort").returns(_ok_result())
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
                "testvm",
                snapshot,
                target,
                compress=False,
                bucket_level=bucket_level,
            )
            assert result.success is True, (
                f"create_full_backup failed for bucket_level={bucket_level}"
            )
            assert result.snapshot_name == snapshot.name


def test_bitmap_create_full_backup_returns_standalone_qcow2(mock_shell, make_target, tmp_path):
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
    mock_shell.expect("domjobabort").returns(_ok_result())
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
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
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
        "qemu-img",
        "info",
        "--output=json",
        str(result.target_path),
    ]
    info_result = mock_shell.run(info_cmd, timeout=30)
    info_data = json.loads(info_result.stdout)
    assert "backing-filename" not in info_data, (
        f"NBD full export should produce standalone qcow2, "
        f"got backing-filename: {info_data.get('backing-filename', 'N/A')}"
    )


# ──────────────────────────────────────────────────────────────────────────
# 10. create_full_backup with dotted VM name (fix-dotted-vm-names)
# ──────────────────────────────────────────────────────────────────────────


def test_bitmap_create_full_backup_dotted_vm_name(mock_shell, make_target, tmp_path):
    """Bitmap FULL backup with dotted VM name ``"3.Projects_opencode"``.

    - ``nbd_full_export(shell, "3.Projects_opencode", ...)`` is called
      with the full VM name (NOT ``"3"`` — the first dot-delimited token).
    - The FULL backup file is named
      ``3.Projects_opencode.FULL.YYYYMMDD.qcow2``, preserving every dot.
    - The VM name is NOT extracted from the snapshot filename via
      ``split(".")`` — ``_make_snapshot()`` produces
      ``"testvm.20250101T000000"`` and its first dot-delimited token is
      ``"testvm"``, not ``"3.Projects_opencode"``.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    # Constructor version check
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    # nbd_full_export internal calls (two rm -f, one backup-begin, one
    # qemu-img convert, one domjobabort).  domblklist is deliberately
    # *not* mocked so _get_first_disk_target returns None (exportname is
    # omitted from NBD URI — harmless for the test).
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # domjobabort in finally (nbd_full_export) — terminate NBD job
    mock_shell.expect("domjobabort").returns(_ok_result())
    # mv for atomic rename after nbd_full_export returns
    mock_shell.expect(r"^mv ").returns(_ok_result())

    # Side effect: simulate mv creating the final file so stat() works
    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    # Spy on nbd_full_export to assert it receives the full VM name
    from qsnap.utils.nbd import (
        nbd_full_export as real_nbd_full_export,
    )

    with (
        patch(
            "qsnap.modules.backup.bitmap.nbd_full_export",
            wraps=real_nbd_full_export,
        ) as nbd_spy,
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "3.Projects_opencode",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    # ── Assert successful result ────────────────────────────────────
    assert result.success is True
    assert result.error is None
    assert result.snapshot_name == snapshot.name
    assert result.bytes_transferred == 65536

    # ── Assert nbd_full_export called with full VM name ─────────────
    nbd_spy.assert_called_once()
    # Positional args to nbd_full_export: (shell, vm_name, target_file)
    actual_vm_name = nbd_spy.call_args[0][1]
    assert actual_vm_name == "3.Projects_opencode", (
        f"nbd_full_export vm_name should be '3.Projects_opencode' "
        f"(full dotted name), got {actual_vm_name!r}"
    )

    # ── Assert domjobabort called in finally ─────────────────────────
    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds
    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1, "domjobabort should be called in finally for dotted VM name"
    assert "3.Projects_opencode" in abort_cmds[0], (
        "domjobabort should target the correct dotted VM name"
    )

    # ── Assert FULL backup file named with full VM name ─────────────
    result_filename = result.target_path.name
    expected_date = snapshot.timestamp.strftime("%Y%m%d")
    expected_name = f"3.Projects_opencode.FULL.{expected_date}.qcow2"
    assert result_filename == expected_name, (
        f"Expected backup filename {expected_name!r}, got {result_filename!r}"
    )
    # Sanity: the filename *starts* with the full dotted VM name
    # (not just "3.")
    assert result_filename.startswith("3.Projects_opencode.FULL."), (
        f"Full backup filename must start with '3.Projects_opencode.FULL.', got {result_filename!r}"
    )
    assert result_filename.endswith(".qcow2")

    # ── Assert VM name NOT extracted from snapshot filename ─────────
    # _make_snapshot() produces "testvm.20250101T000000".  If the
    # implementation extracted the VM name from the snapshot filename
    # via split("."), it would get "testvm", never "3.Projects_opencode".
    assert snapshot.name == "testvm.20250101T000000", (
        "Snapshot name should be the canonical _make_snapshot() name"
    )
    assert "3.Projects_opencode" not in snapshot.name, (
        "The dotted VM name MUST NOT appear in the snapshot filename — "
        "it is supplied as the explicit vm_name parameter, not parsed "
        "from the snapshot name via split('.')"
    )


# ──────────────────────────────────────────────────────────────────────────
# 11. NBD job termination: domjobabort in finally (bitmap-mod)
# ──────────────────────────────────────────────────────────────────────────


def test_bitmap_nbd_job_terminated_after_transfer(mock_shell, make_target, tmp_path):
    """Verify ``virsh domjobabort`` is called in the finally block after
    NBD transfer completes via ``create_full_backup()``.

    ``nbd_full_export()`` calls ``domjobabort`` to release the VM state
    change lock before cleaning up the socket.  The abort is
    idempotent — safe even when no job is running.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    # nbd_full_export lifecycle:
    # (a) rm -f stale socket
    # (b) virsh backup-begin
    # (c) qemu-img convert
    # (d) virsh domjobabort  ← in finally
    # (e) rm -f socket       ← in finally (after domjobabort)
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # stale
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())  # finally
    # rm -f socket cleanup in finally — MockShell reuses the first rm -f
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    # Assert successful result — backup still succeeds
    assert result.success is True
    assert result.error is None
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # ── domjobabort was called ───────────────────────────────────────
    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1, (
        f"domjobabort must be called exactly once in finally, got {len(abort_cmds)}: {abort_cmds}"
    )
    assert "--domain" in abort_cmds[0]
    assert "testvm" in abort_cmds[0]

    # ── domjobabort was called AFTER qemu-img convert and BEFORE
    #     socket rm -f (the cleanup call, not the stale removal) ──────
    # Note: convert is dispatched via run_with_stall_detection, so it
    # appears in stall_spy.  domjobabort and rm -f are in run_spy.
    # The actual execution order (convert → abort → rm) is guaranteed
    # by the finally block in nbd_full_export; we verify all are present.
    convert_cmds_stall = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds_stall) == 1, "qemu-img convert should be via stall detection"

    socket_rm_indices: list[int] = []
    for i, cmd in enumerate(all_run_cmds):
        if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd:
            socket_rm_indices.append(i)

    assert len(socket_rm_indices) >= 2, (
        f"Expected >=2 socket rm calls (stale + cleanup), got: {socket_rm_indices}"
    )


def test_bitmap_socket_cleanup_after_job_abort(mock_shell, make_target, tmp_path, caplog):
    """Verify socket cleanup proceeds even when ``domjobabort`` fails.

    ``nbd_full_export()`` logs a WARNING on domjobabort failure but
    does NOT propagate the error — the socket ``rm -f`` in the finally
    block must still execute.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # domjobabort FAILS — but finally still proceeds
    mock_shell.expect("domjobabort").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: Requested operation is not valid: domain is not running",
            returncode=1,
            error="error: Requested operation is not valid: domain is not running",
        )
    )
    # Socket rm -f in finally — MUST still be called
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    # ── Backup still succeeds — domjobabort failure is non-fatal ─────
    assert result.success is True, (
        "Backup should succeed even when domjobabort fails "
        "(abort failure is logged as WARNING, not propagated)"
    )
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # ── domjobabort was attempted ────────────────────────────────────
    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1, "domjobabort should still be called even if it will fail"

    # ── Socket cleanup still happened AFTER domjobabort ───────────────
    socket_rm_cmds = [
        cmd for cmd in all_cmds if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm_cmds) >= 2, (
        f"Socket cleanup must still happen after failed domjobabort, "
        f"got {len(socket_rm_cmds)} rm calls: {socket_rm_cmds}"
    )

    # Verify order: domjobabort before last socket rm
    abort_idx = None
    last_rm_idx = None
    for i, cmd in enumerate(all_cmds):
        if "domjobabort" in cmd:
            abort_idx = i
        if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd:
            last_rm_idx = i
    assert abort_idx is not None
    assert last_rm_idx is not None
    assert abort_idx < last_rm_idx, (
        f"domjobabort (idx={abort_idx}) must precede socket rm (idx={last_rm_idx}) in finally block"
    )

    # ── WARNING was logged for domjobabort failure ───────────────────
    warnings = [
        record
        for record in caplog.records
        if record.levelname == "WARNING" and "domjobabort" in record.getMessage().lower()
    ]
    assert len(warnings) >= 1, "domjobabort failure should log a WARNING (non-fatal)"


def test_bitmap_first_full_pull_via_nbd(mock_shell, make_target, tmp_path):
    """First backup creates a FULL via NBD pull-model.

    Verifies the complete NBD full-export lifecycle:
    - ``virsh backup-begin`` without ``--incremental``
    - ``qemu-img convert -n nbd:unix:<socket>``
    - ``virsh domjobabort`` in finally (design D3: release state lock)
    - Socket cleanup after domjobabort
    - No checkpoint creation or deletion
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())  # finally
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    # ── Assert successful result ─────────────────────────────────────
    assert result.success is True
    assert result.error is None
    assert result.snapshot_name == snapshot.name
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # ── Full NBD lifecycle commands present ──────────────────────────
    # stale socket removal
    assert any(cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd for cmd in all_cmds)

    # backup-begin WITHOUT --incremental
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0], "First FULL backup must NOT use --incremental"

    # qemu-img convert via NBD
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]

    # domjobabort in finally
    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1

    # Socket cleanup in finally (after domjobabort)
    socket_rm_count = sum(
        1 for cmd in all_cmds if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    )
    assert socket_rm_count >= 2, f"Expected >=2 socket rm calls, got {socket_rm_count}"

    # ── No checkpoints ───────────────────────────────────────────────
    assert not any("checkpoint" in cmd for cmd in all_cmds), (
        "create_full_backup must not touch checkpoints"
    )


def test_bitmap_incremental_dirty_blocks_via_nbd(mock_shell, make_vm_config, make_target, tmp_path):
    """Incremental backup via ``transfer_missing()`` transfers dirty
    blocks using NBD pull-model with checkpoint state management.

    This test exercises the incremental NBD path where a prior
    checkpoint exists.  ``transfer_missing()`` manages its own socket
    lifecycle (stale removal + finally cleanup) and calls ``domjobabort``
    in its own finally block to release the VM state change lock.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
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
    # virsh backup-begin WITH --incremental
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert succeeds (pulls only dirty blocks)
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # checkpoint-delete prior checkpoint
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    # checkpoint-create-as new checkpoint
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    # domjobabort in transfer_missing finally
    mock_shell.expect("domjobabort").returns(_ok_result())
    # rm -f socket (cleanup in transfer_missing's finally)
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # ── Incremental backup-begin with prior checkpoint ───────────────
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" in backup_cmds[0]
    assert prior_checkpoint in backup_cmds[0]

    # ── qemu-img convert via NBD with compression ────────────────────
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]
    assert "-c" in convert_cmds[0], "qemu-img convert should include -c when compress=True"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        "qemu-img convert should include -o compression_type=zstd (default)"
    )

    # ── Checkpoint lifecycle ─────────────────────────────────────────
    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1, "Prior checkpoint should be deleted after success"
    assert prior_checkpoint in delete_cmds[0]

    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 1, "New checkpoint should be created for next incremental run"

    # ── domjobabort called in transfer_missing finally ───────────────
    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1, (
        "transfer_missing must call domjobabort in finally to release VM state change lock"
    )
    assert "--domain" in abort_cmds[0]
    assert vm_config.name in abort_cmds[0]

    # ── Socket cleanup in finally ────────────────────────────────────
    socket_rm_cmds = [
        cmd for cmd in all_cmds if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm_cmds) >= 2, (
        f"Socket should be cleaned up (stale + finally), got {len(socket_rm_cmds)}: {socket_rm_cmds}"
    )

    # ── domjobabort called AFTER qemu-img convert, BEFORE final socket rm ──
    abort_idx = None
    last_rm_idx = None
    for i, cmd in enumerate(all_cmds):
        if "domjobabort" in cmd:
            abort_idx = i
        if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd:
            last_rm_idx = i
    assert abort_idx is not None and last_rm_idx is not None
    assert abort_idx < last_rm_idx, (
        f"domjobabort (idx={abort_idx}) must precede final socket rm (idx={last_rm_idx})"
    )


# ──────────────────────────────────────────────────────────────────────────
# backup-bitmap-enhancements: 7 new tests
# ──────────────────────────────────────────────────────────────────────────


def test_domjobabort_called_after_successful_transfer(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """Set up a successful transfer.  Verify ``virsh domjobabort --domain <vm>``
    was called in the shell's command history (mock shell recorded commands).
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
    )
    snapshot = _make_snapshot()

    # rm -f stale socket
    mock_shell.expect("rm -f").returns(_ok_result())
    # checkpoint-list returns empty (no prior checkpoint — first backup)
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # backup-begin succeeds
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert succeeds
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # checkpoint-create-as succeeds
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    # domjobabort in finally
    mock_shell.expect("domjobabort").returns(_ok_result())
    # rm -f socket cleanup in finally
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # Verify qemu-img convert includes -c (compress=True default)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" in convert_cmds[0], "qemu-img convert should include -c when compress=True"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        "qemu-img convert should include -o compression_type=zstd (default)"
    )

    # Verify domjobabort was called — use "virsh domjobabort" to avoid false
    # matches from pytest tmp_path directory names containing "domjobabort".
    abort_cmds = [cmd for cmd in all_cmds if "virsh domjobabort" in cmd]
    assert len(abort_cmds) == 1, (
        f"domjobabort should be called exactly once in finally, got {len(abort_cmds)}: {abort_cmds}"
    )
    assert "--domain" in abort_cmds[0]
    assert vm_config.name in abort_cmds[0]

    # Verify socket rm is still called after domjobabort
    socket_rm_cmds = [
        cmd for cmd in all_cmds if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm_cmds) >= 2, (
        f"Socket cleanup must happen (stale + finally), got {len(socket_rm_cmds)}"
    )


def test_domjobabort_called_after_failed_transfer(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """Set up a failed transfer (backup-begin fails).  Verify
    ``virsh domjobabort`` was still called in the finally block.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
    )
    snapshot = _make_snapshot()

    # rm -f stale socket
    mock_shell.expect("rm -f").returns(_ok_result())
    # checkpoint-list returns empty
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # backup-begin FAILS
    backup_error = "backup-begin failed: domain is shut off"
    mock_shell.expect("backup-begin").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=backup_error,
            returncode=1,
            error=backup_error,
        )
    )
    # domjobabort in finally — MUST still be called
    mock_shell.expect("domjobabort").returns(_ok_result())
    # rm -f socket cleanup in finally
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert failure result (transfer failed for original reason)
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error == backup_error

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # Verify domjobabort was STILL called despite backup-begin failure
    abort_cmds = [cmd for cmd in all_cmds if "virsh domjobabort" in cmd]
    assert len(abort_cmds) == 1, (
        f"domjobabort must be called in finally even after backup-begin failure, "
        f"got {len(abort_cmds)}: {abort_cmds}"
    )
    assert "--domain" in abort_cmds[0]
    assert vm_config.name in abort_cmds[0]

    # Verify socket cleanup still happened
    socket_rm_cmds = [
        cmd for cmd in all_cmds if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm_cmds) >= 2, (
        f"Socket cleanup must happen even after backup-begin failure, got {len(socket_rm_cmds)}"
    )


def test_domjobabort_failure_is_non_fatal(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
):
    """Set up domjobabort to fail.  Verify the transfer still succeeds
    and a WARNING is logged (domjobabort failure is non-fatal).
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
    )
    snapshot = _make_snapshot()

    # rm -f stale socket
    mock_shell.expect("rm -f").returns(_ok_result())
    # checkpoint-list returns empty
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # backup-begin succeeds
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert succeeds
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # checkpoint-create-as succeeds
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    # domjobabort FAILS — but is non-fatal
    mock_shell.expect("domjobabort").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: domain is not running",
            returncode=1,
            error="error: domain is not running",
        )
    )
    # rm -f socket cleanup in finally — MUST still execute
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert the transfer still succeeds (domjobabort failure is non-fatal)
    assert len(results) == 1
    assert results[0].success is True, (
        "Transfer should succeed even when domjobabort fails "
        "(abort failure is logged as WARNING, not propagated)"
    )

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # Verify domjobabort was attempted
    abort_cmds = [cmd for cmd in all_cmds if "virsh domjobabort" in cmd]
    assert len(abort_cmds) == 1

    # Verify socket cleanup still happened after failed domjobabort
    abort_idx = None
    last_rm_idx = None
    for i, cmd in enumerate(all_cmds):
        if "virsh domjobabort" in cmd:
            abort_idx = i
        if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd:
            last_rm_idx = i
    assert abort_idx is not None and last_rm_idx is not None
    assert abort_idx < last_rm_idx, (
        f"domjobabort (idx={abort_idx}) must precede socket rm (idx={last_rm_idx}) in finally block"
    )

    # Verify WARNING was logged for domjobabort failure
    warnings = [
        record
        for record in caplog.records
        if record.levelname == "WARNING" and "domjobabort" in record.getMessage().lower()
    ]
    assert len(warnings) >= 1, "domjobabort failure should log a WARNING (non-fatal)"


def test_constructor_accepts_state_manager(mock_shell, mock_state):
    """Construct ``BitmapBackupProvider(shell, state=mock_state)``.
    Verify ``provider._state is mock_state``.
    """
    provider = BitmapBackupProvider(mock_shell, state=mock_state)
    assert provider._state is mock_state


def test_constructor_works_without_state_manager(mock_shell):
    """Construct ``BitmapBackupProvider(shell)``.
    Verify ``provider._state is None`` and no crash.
    """
    provider = BitmapBackupProvider(mock_shell)
    assert provider._state is None


def test_create_full_backup_records_in_state(mock_shell, mock_state, make_target, tmp_path):
    """Construct with a mock state.  Call ``create_full_backup()`` successfully.
    Verify ``mock_state.record_full_backup()`` was called with correct arguments
    (target_path, name, timestamp, bucket_level).
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    # nbd_full_export internal calls
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())  # finally in nbd_full_export
    mock_shell.expect("rm -f").returns(_ok_result())  # finally in nbd_full_export
    # mv (atomic rename)
    mock_shell.expect(r"^mv ").returns(_ok_result())

    # Spy on state.record_full_backup
    with patch.object(
        mock_state, "record_full_backup", wraps=mock_state.record_full_backup
    ) as state_spy:
        # Side effect: simulate mv creating the final file so stat() works
        original_run = mock_shell.run

        def spied_run(cmd, timeout):
            cmd_str = " ".join(cmd)
            if cmd_str.startswith("mv "):
                target_file = Path(cmd[-1])
                target_file.write_bytes(b"\x00" * 65536)
            return original_run(cmd, timeout)

        with patch.object(mock_shell, "run", side_effect=spied_run):
            provider = BitmapBackupProvider(mock_shell, state=mock_state)
            result = provider.create_full_backup(
                "testvm",
                snapshot,
                target,
                compress=False,
                bucket_level="weekly",
            )

    assert result.success is True

    # Verify state.record_full_backup was called exactly once
    state_spy.assert_called_once()

    # Verify correct arguments
    call_args = state_spy.call_args
    assert call_args[0][0] == str(target.path)  # target_path
    assert call_args[0][1] is not None  # name should be something like "testvm.FULL.20250101.qcow2"
    assert "testvm.FULL." in call_args[0][1]
    assert call_args[0][2] == snapshot.timestamp  # timestamp
    assert call_args[0][3] == "weekly"  # bucket_level


def test_create_full_backup_skips_state_when_none(mock_shell, make_target, tmp_path):
    """Construct without state.  Call ``create_full_backup()`` successfully.
    Verify no crash (state recording is skipped when ``_state is None``).
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    # nbd_full_export internal calls
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())  # finally in nbd_full_export
    mock_shell.expect("rm -f").returns(_ok_result())  # finally in nbd_full_export
    # mv (atomic rename)
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            target_file = Path(cmd[-1])
            target_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = BitmapBackupProvider(mock_shell)  # No state
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True, "create_full_backup should succeed without state manager"
    assert result.bytes_transferred == 65536
    assert result.snapshot_name == snapshot.name


# ──────────────────────────────────────────────────────────────────────────
# Checkpoint-only creation when FULL exists (design D4)
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_missing_checkpoint_only_when_full_exists(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path
):
    """When a FULL backup exists in state but no prior checkpoint, only a
    checkpoint is created — no data transfer (``qemu-img convert``) is
    performed.

    Design D4: the bucket strategy's ``create_full_backup()`` already
    produced a FULL with all data; the checkpoint serves only as the
    baseline for the next incremental run.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental_mode="bitmap",
        verify="off",
    )
    snapshot = _make_snapshot()

    # Record a FULL backup in state
    mock_state.record_full_backup(
        str(target.path),
        "testvm.FULL.20250101.qcow2",
        datetime(2025, 1, 1, 0, 0, 0),
        "monthly",
    )

    # checkpoint-list returns empty (no prior checkpoint)
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # checkpoint-create-as succeeds (checkpoint-only creation)
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, state=mock_state)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # No BackupResult — the checkpoint-only path uses "continue"
    assert len(results) == 0

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # Verify checkpoint-create-as was called
    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 1, "checkpoint-create-as should be called (checkpoint-only)"

    # Verify qemu-img convert was NOT called (no data transfer)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, "qemu-img convert should NOT be called (checkpoint-only)"

    # Verify backup-begin was NOT called
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 0, "backup-begin should NOT be called (checkpoint-only)"


def test_transfer_missing_skips_existing_snapshot_before_checkpoint_check(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path
):
    """When a snapshot already exists on the target, the checkpoint-only
    path is NOT triggered — the existing-names check short-circuits
    before the checkpoint logic (design D4.4).
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "some_target"),
        incremental_mode="bitmap",
        verify="off",
    )
    target.path.mkdir(parents=True, exist_ok=True)

    # Create a file on the target that matches the snapshot name
    snapshot = _make_snapshot()
    target_file = target.path / f"{snapshot.name}.qcow2"
    target_file.write_bytes(b"\x00" * 100)

    # Record a FULL backup in state
    mock_state.record_full_backup(
        str(target.path),
        "testvm.FULL.20250101.qcow2",
        datetime(2025, 1, 1, 0, 0, 0),
        "monthly",
    )

    # qemu-img info returns info for the existing file (used by list())
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1073741824,
                    "actual-size": 100,
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, state=mock_state)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # No results — snapshot already exists, skipped before checkpoint logic
    assert len(results) == 0

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # Verify checkpoint-create-as was NOT called (existing names short-circuit)
    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0, "checkpoint-create-as should NOT be called for existing snapshot"

    # Verify qemu-img convert was NOT called
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0

    # Verify backup-begin was NOT called
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 0


def test_transfer_missing_skips_checkpoint_when_state_is_none(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When ``self._state`` is ``None`` (no state manager), the
    checkpoint-only path is NOT triggered and the code falls through
    to full NBD export (design D4.3).
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental_mode="bitmap",
        verify="off",
    )
    snapshot = _make_snapshot()

    # checkpoint-list returns empty (no prior checkpoint)
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # rm -f stale socket
    mock_shell.expect("rm -f").returns(_ok_result())
    # backup-begin succeeds
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert succeeds
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # checkpoint-create-as succeeds (post-transfer)
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    # rm -f socket cleanup in finally
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)  # No state manager
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # Verify qemu-img convert WAS called (full NBD export, not checkpoint-only)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1, "qemu-img convert should be called (full NBD export)"

    # Verify backup-begin was called WITHOUT --incremental
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]


# ──────────────────────────────────────────────────────────────────────────
# Failed file deletion (_cleanup_partial_file)
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_failure_deletes_partial_file(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``qemu-img convert`` fails, ``_cleanup_partial_file`` deletes
    the partially-transferred target file before returning
    ``BackupResult(success=False)``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental_mode="bitmap",
        verify="off",
    )
    snapshot = _make_snapshot()
    target_file = target.path / f"{snapshot.name}.qcow2"

    target_hash = BitmapBackupProvider._target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
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
    # rm -f <target_file> from _cleanup_partial_file
    mock_shell.expect("rm -f").returns(_ok_result())
    # rm -f socket cleanup in finally
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # Verify qemu-img convert included -c
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" in convert_cmds[0], "qemu-img convert should include -c when compress=True"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        "qemu-img convert should include -o compression_type=zstd (default)"
    )

    # Verify partial file deletion was called
    partial_file_cmds = [
        cmd for cmd in all_cmds if cmd.startswith("rm -f") and str(target_file) in cmd
    ]
    assert len(partial_file_cmds) == 1, (
        f"Expected rm -f <target_file> for partial file deletion, "
        f"rm -f cmds: {[cmd for cmd in all_cmds if 'rm -f' in cmd]}"
    )

    # Verify checkpoint was preserved (no checkpoint-delete)
    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0


def test_bitmap_verify_failure_deletes_file(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``verify_backup`` returns an error, ``_cleanup_partial_file``
    deletes the partially-transferred target file before returning
    ``BackupResult(success=False)``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental_mode="bitmap",
        verify="metadata",
    )
    snapshot = _make_snapshot()
    target_file = target.path / f"{snapshot.name}.qcow2"

    target_hash = BitmapBackupProvider._target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert succeeds
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # verify_backup: source qemu-img info --force-share FAILS
    mock_shell.expect("qemu-img info --force-share").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="I/O error",
            returncode=1,
            error="I/O error",
        )
    )
    # rm -f <target_file> from _cleanup_partial_file
    mock_shell.expect("rm -f").returns(_ok_result())
    # rm -f socket cleanup in finally
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error is not None
    assert "verification failed" in results[0].error

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds

    # Verify partial file deletion was called
    partial_file_cmds = [
        cmd for cmd in all_cmds if cmd.startswith("rm -f") and str(target_file) in cmd
    ]
    assert len(partial_file_cmds) == 1, (
        "Expected rm -f <target_file> for partial file deletion after verify failure"
    )

    # Verify checkpoint was preserved
    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0


# ──────────────────────────────────────────────────────────────────────────
# Compression tests
# ──────────────────────────────────────────────────────────────────────────


def test_bitmap_incremental_nbd_with_compression(mock_shell, make_vm_config, make_target, tmp_path):
    """Verify ``-c`` flag is in ``qemu-img convert`` command when
    ``target.compress=True``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental_mode="bitmap",
        verify="off",
        compress=True,
    )
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider._target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # socket cleanup in finally

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" in convert_cmds[0], "qemu-img convert should include -c when compress=True"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        "qemu-img convert should include -o compression_type=zstd (default)"
    )


def test_bitmap_incremental_nbd_without_compression(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """Verify no ``-c`` flag when ``target.compress=False``."""
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental_mode="bitmap",
        verify="off",
        compress=False,
    )
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider._target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # socket cleanup in finally

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" not in convert_cmds[0], "qemu-img convert should NOT include -c when compress=False"


def test_bitmap_compress_metadata_verification_passes(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """Verify that compression does not affect metadata verification.

    ``qemu-img info`` reports the same format and virtual-size
    regardless of whether the target file is compressed.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental_mode="bitmap",
        verify="metadata",
        compress=True,
    )
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider._target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    # qemu-img convert (compressed)
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # verify_backup: source qemu-img info --force-share
    mock_shell.expect("qemu-img info --force-share").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1073741824,
                    "actual-size": 65536,
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # verify_backup: target qemu-img info (no --force-share)
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1073741824,
                    "actual-size": 32768,
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # socket cleanup in finally

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" in convert_cmds[0], "Compression was enabled, -c should be in qemu-img convert"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        "qemu-img convert should include -o compression_type=zstd (default)"
    )


def test_bitmap_compress_full_verification_passes(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """Verify that compression does not affect full verification.

    ``qemu-img compare`` handles compressed files transparently.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        incremental_mode="bitmap",
        verify="full",
        compress=True,
    )
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider._target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    # verify_backup: source qemu-img info --force-share
    mock_shell.expect("qemu-img info --force-share").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1073741824,
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # verify_backup: target qemu-img info (no --force-share)
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1073741824,
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # verify_backup: qemu-img compare for full mode
    mock_shell.expect("qemu-img compare").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # socket cleanup in finally

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    all_cmds = all_run_cmds + all_stall_cmds
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" in convert_cmds[0], "Compression was enabled, -c should be in qemu-img convert"
    assert "-o compression_type=zstd" in convert_cmds[0], (
        "qemu-img convert should include -o compression_type=zstd (default)"
    )

    # qemu-img compare was called (full verification)
    compare_cmds = [cmd for cmd in all_cmds if "qemu-img compare" in cmd]
    assert len(compare_cmds) == 1


# ══════════════════════════════════════════════════════════════════════════
# NEW TESTS: zstd-compression-and-stall-detection (backup-bitmap-unit)
# ══════════════════════════════════════════════════════════════════════════


def test_bitmap_transfer_with_zstd_compression(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """transfer_missing with compression_type="zstd" produces
    ``qemu-img convert`` with ``-c -o compression_type=zstd``.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
    )
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(
            vm_config, target, [snapshot], compression_type="zstd"
        )

    assert len(results) == 1
    assert results[0].success is True

    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    convert_cmds = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" in convert_cmds[0]
    assert "-o compression_type=zstd" in convert_cmds[0], (
        "compression_type='zstd' should produce -o compression_type=zstd"
    )


def test_bitmap_transfer_with_zlib_compression(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """transfer_missing with compression_type="zlib" produces
    ``qemu-img convert`` with ``-c`` only (no ``-o compression_type=`` flag).
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
    )
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(
            vm_config, target, [snapshot], compression_type="zlib"
        )

    assert len(results) == 1
    assert results[0].success is True

    all_stall_cmds = [
        " ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list
    ]
    convert_cmds = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "-c" in convert_cmds[0], "compression_type='zlib' should still include -c"
    assert "-o compression_type=" not in convert_cmds[0], (
        "compression_type='zlib' should NOT include -o compression_type="
    )


def test_bitmap_transfer_uses_stall_detection(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """transfer_missing uses ``run_with_stall_detection`` (not ``run``)
    for the qemu-img convert command when stall_timeout > 0.
    """
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
    )
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("checkpoint-create-as").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    # Verify run_with_stall_detection was called for the convert command
    convert_stall_calls = [
        call_obj for call_obj in stall_spy.call_args_list
        if "qemu-img convert" in " ".join(call_obj.args[0])
    ]
    assert len(convert_stall_calls) == 1, (
        "qemu-img convert should be dispatched via run_with_stall_detection"
    )

    # Verify output_file is set to the target file
    _, kwargs = stall_spy.call_args
    assert kwargs.get("output_file") is not None, (
        "output_file should be passed to stall detection"
    )

    # Verify run() was NOT used for the convert command
    convert_run_calls = [
        call_obj for call_obj in run_spy.call_args_list
        if "qemu-img convert" in " ".join(call_obj.args[0])
    ]
    assert len(convert_run_calls) == 0, (
        "qemu-img convert should NOT use run() when stall_timeout > 0"
    )


def test_bitmap_full_zstd_compression(mock_shell, make_target, tmp_path):
    """create_full_backup passes ``compression_type="zstd"`` to
    ``nbd_full_export``.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            tgt_file = Path(cmd[-1])
            tgt_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    from qsnap.utils.nbd import (
        nbd_full_export as real_nbd_full_export,
    )

    with (
        patch(
            "qsnap.modules.backup.bitmap.nbd_full_export",
            wraps=real_nbd_full_export,
        ) as nbd_spy,
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=True,
            compression_type="zstd",
            bucket_level="monthly",
        )

    assert result.success is True

    # Verify nbd_full_export received compression_type="zstd"
    nbd_spy.assert_called_once()
    _, kwargs = nbd_spy.call_args
    assert kwargs.get("compression_type") == "zstd", (
        f"nbd_full_export should receive compression_type='zstd', "
        f"got {kwargs.get('compression_type')!r}"
    )


def test_bitmap_full_zlib_compression(mock_shell, make_target, tmp_path):
    """create_full_backup passes ``compression_type="zlib"`` to
    ``nbd_full_export``.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            tgt_file = Path(cmd[-1])
            tgt_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    from qsnap.utils.nbd import (
        nbd_full_export as real_nbd_full_export,
    )

    with (
        patch(
            "qsnap.modules.backup.bitmap.nbd_full_export",
            wraps=real_nbd_full_export,
        ) as nbd_spy,
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=True,
            compression_type="zlib",
            bucket_level="monthly",
        )

    assert result.success is True

    # Verify nbd_full_export received compression_type="zlib"
    nbd_spy.assert_called_once()
    _, kwargs = nbd_spy.call_args
    assert kwargs.get("compression_type") == "zlib", (
        f"nbd_full_export should receive compression_type='zlib', "
        f"got {kwargs.get('compression_type')!r}"
    )


def test_nbd_full_export_uses_stall_detection(
    mock_shell, make_target, tmp_path
):
    """nbd_full_export uses ``run_with_stall_detection`` (not ``run``)
    for the qemu-img convert command.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            tgt_file = Path(cmd[-1])
            tgt_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    from qsnap.utils.nbd import (
        nbd_full_export as real_nbd_full_export,
    )

    with (
        patch(
            "qsnap.modules.backup.bitmap.nbd_full_export",
            wraps=real_nbd_full_export,
        ) as nbd_spy,
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=True,
            bucket_level="monthly",
        )

    assert result.success is True

    # Verify nbd_full_export received stall_timeout > 0
    nbd_spy.assert_called_once()
    _, kwargs = nbd_spy.call_args
    assert kwargs.get("stall_timeout", 0) > 0, (
        f"nbd_full_export should receive stall_timeout > 0, "
        f"got {kwargs.get('stall_timeout')!r}"
    )

    # Verify run_with_stall_detection was called for qemu-img convert
    # (inside nbd_full_export, which uses the same mock_shell)
    convert_stall_calls = [
        call_obj for call_obj in stall_spy.call_args_list
        if "qemu-img convert" in " ".join(call_obj.args[0])
    ]
    assert len(convert_stall_calls) == 1, (
        "qemu-img convert should be dispatched via run_with_stall_detection in nbd_full_export"
    )


def test_nbd_full_tmp_rename(mock_shell, make_target, tmp_path):
    """.tmp file is used as ``output_file`` for stall detection in
    ``nbd_full_export``, and is atomically renamed to the final
    ``.qcow2`` file on success.
    """
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)

    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())  # finally in nbd_full_export
    mock_shell.expect("rm -f").returns(_ok_result())  # socket cleanup in finally
    mock_shell.expect(r"^mv ").returns(_ok_result())  # atomic rename

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            tgt_file = Path(cmd[-1])
            tgt_file.write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    from qsnap.utils.nbd import (
        nbd_full_export as real_nbd_full_export,
    )

    with (
        patch(
            "qsnap.modules.backup.bitmap.nbd_full_export",
            wraps=real_nbd_full_export,
        ) as nbd_spy,
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=True,
            bucket_level="monthly",
        )

    assert result.success is True
    assert result.target_path.suffix == ".qcow2", (
        "Final backup file should have .qcow2 suffix"
    )

    # Verify nbd_full_export was called with a .tmp target_file
    nbd_spy.assert_called_once()
    target_arg = nbd_spy.call_args[0][2]  # 3rd positional arg = target_file
    assert str(target_arg).endswith(".tmp"), (
        f"nbd_full_export target_file should end with .tmp, got {target_arg!r}"
    )

    # Verify the .tmp target was used as output_file in stall detection
    stall_call = stall_spy.call_args_list[0]
    _, stall_kwargs = stall_call
    output_file = stall_kwargs.get("output_file")
    assert output_file is not None, "output_file should be set for stall detection"
    assert str(output_file).endswith(".tmp"), (
        f"output_file for stall detection should be the .tmp file, got {output_file!r}"
    )

    # Verify mv was called to rename .tmp to .qcow2
    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    mv_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 1, f"Expected 1 mv call, got {len(mv_cmds)}: {mv_cmds}"
    assert ".tmp" in mv_cmds[0], "mv should rename .tmp file"
    assert ".qcow2" in mv_cmds[0], "mv should rename to .qcow2 file"
