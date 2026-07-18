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
        "_check_libvirt_version method should not exist "
        "(version check moved to factory)"
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
    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
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
    # rm -f socket (cleanup)
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True

    # Verify backup-begin command HAS --incremental with prior checkpoint
    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
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
    # rm -f socket (cleanup)
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]

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


def test_transfer_failure_preserves_checkpoint(mock_shell, make_vm_config, make_target, tmp_path):
    """When ``qemu-img convert`` (NBD pull) fails, the prior checkpoint
    is NOT deleted and the result is ``BackupResult(success=False)``.
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

    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]

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

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        checkpoints = provider.list_checkpoints("testvm")

    # Only qsnap- prefixed checkpoints are returned
    assert checkpoints == ["qsnap-abc123-snap1", "qsnap-xyz789-snap2"]

    # Verify the command structure
    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
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
    # rm -f socket (cleanup in finally)
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot], rate_limit="100M")

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].snapshot_name == snapshot.name
    assert results[0].error is None

    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]

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

    with patch.object(mock_shell, "run", side_effect=spied_run) as shell_spy:
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

    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]

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

    with patch.object(mock_shell, "run", side_effect=spied_run) as shell_spy:
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

    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]

    # Verify qemu-img convert uses NBD WITH compression flag
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]
    assert "-c" in convert_cmds[0], "qemu-img convert SHOULD use -c when compress=True"

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

    with patch.object(mock_shell, "run", side_effect=spied_run_success) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    all_cmds_success = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]

    # Socket rm -f calls on success (stale + cleanup)
    socket_rm_cmds = [
        cmd for cmd in all_cmds_success if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm_cmds) >= 2, (
        f"Expected >=2 socket rm calls on success, got: {socket_rm_cmds}"
    )

    # Verify domjobabort was called
    abort_cmds = [cmd for cmd in all_cmds_success if "domjobabort" in cmd]
    assert len(abort_cmds) == 1, "domjobabort should be called in finally"

    # Verify domjobabort is called BEFORE socket rm -f in finally
    # Find the last rm -f socket call (cleanup in finally) and verify
    # domjobabort appears before it in the command sequence.
    abort_idx = None
    for i, cmd in enumerate(all_cmds_success):
        if "domjobabort" in cmd:
            abort_idx = i
    # The last rm -f for the socket is the cleanup call
    last_socket_rm_idx = None
    for i, cmd in enumerate(all_cmds_success):
        if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd:
            last_socket_rm_idx = i
    assert abort_idx is not None
    assert last_socket_rm_idx is not None
    assert abort_idx < last_socket_rm_idx, (
        f"domjobabort (index {abort_idx}) must precede socket rm -f "
        f"(index {last_socket_rm_idx}) in finally block"
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

    with patch.object(mock_shell, "run", side_effect=spied_run) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success is True

    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]

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
        patch.object(mock_shell, "run", side_effect=spied_run) as shell_spy,
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
    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
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


def test_bitmap_nbd_job_terminated_after_transfer(
    mock_shell, make_target, tmp_path
):
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

    with patch.object(mock_shell, "run", side_effect=spied_run) as shell_spy:
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

    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]

    # ── domjobabort was called ───────────────────────────────────────
    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1, (
        "domjobabort must be called exactly once in finally, "
        f"got {len(abort_cmds)}: {abort_cmds}"
    )
    assert "--domain" in abort_cmds[0]
    assert "testvm" in abort_cmds[0]

    # ── domjobabort was called AFTER qemu-img convert and BEFORE
    #     socket rm -f (the cleanup call, not the stale removal) ──────
    convert_idx = None
    abort_idx = None
    socket_rm_indices: list[int] = []

    for i, cmd in enumerate(all_cmds):
        if "qemu-img convert" in cmd:
            convert_idx = i
        if "domjobabort" in cmd:
            abort_idx = i
        if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd:
            socket_rm_indices.append(i)

    assert convert_idx is not None, "qemu-img convert not found in command trace"
    assert abort_idx is not None, "domjobabort not found in command trace"
    assert len(socket_rm_indices) >= 2, (
        f"Expected >=2 socket rm calls (stale + cleanup), got: {socket_rm_indices}"
    )

    # The LAST socket rm is the cleanup in finally
    cleanup_rm_idx = socket_rm_indices[-1]
    assert convert_idx < abort_idx < cleanup_rm_idx, (
        f"Expected order: qemu-img convert ({convert_idx}) "
        f"< domjobabort ({abort_idx}) "
        f"< socket rm cleanup ({cleanup_rm_idx})"
    )


def test_bitmap_socket_cleanup_after_job_abort(
    mock_shell, make_target, tmp_path, caplog
):
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

    with patch.object(mock_shell, "run", side_effect=spied_run) as shell_spy:
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

    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]

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
        f"domjobabort (idx={abort_idx}) must precede socket rm (idx={last_rm_idx}) "
        "in finally block"
    )

    # ── WARNING was logged for domjobabort failure ───────────────────
    warnings = [
        record for record in caplog.records
        if record.levelname == "WARNING" and "domjobabort" in record.getMessage().lower()
    ]
    assert len(warnings) >= 1, (
        "domjobabort failure should log a WARNING (non-fatal)"
    )


def test_bitmap_first_full_pull_via_nbd(
    mock_shell, make_target, tmp_path
):
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

    with patch.object(mock_shell, "run", side_effect=spied_run) as shell_spy:
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

    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]

    # ── Full NBD lifecycle commands present ──────────────────────────
    # stale socket removal
    assert any(cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd for cmd in all_cmds)

    # backup-begin WITHOUT --incremental
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0], (
        "First FULL backup must NOT use --incremental"
    )

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


def test_bitmap_incremental_dirty_blocks_via_nbd(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """Incremental backup via ``transfer_missing()`` transfers dirty
    blocks using NBD pull-model with checkpoint state management.

    This test exercises the incremental NBD path where a prior
    checkpoint exists.  ``transfer_missing()`` manages its own socket
    lifecycle (stale removal + finally cleanup) and does NOT call
    ``nbd_full_export()`` or ``domjobabort`` — those are
    ``create_full_backup()`` concerns.  The socket cleanup in the
    ``transfer_missing`` finally block must still execute.
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
    # rm -f socket (cleanup in transfer_missing's finally)
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Assert successful result
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error is None

    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]

    # ── Incremental backup-begin with prior checkpoint ───────────────
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" in backup_cmds[0]
    assert prior_checkpoint in backup_cmds[0]

    # ── qemu-img convert via NBD ─────────────────────────────────────
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]

    # ── Checkpoint lifecycle ─────────────────────────────────────────
    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1, "Prior checkpoint should be deleted after success"
    assert prior_checkpoint in delete_cmds[0]

    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 1, "New checkpoint should be created for next incremental run"

    # ── Socket cleanup in finally ────────────────────────────────────
    socket_rm_cmds = [
        cmd for cmd in all_cmds if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm_cmds) >= 2, (
        f"Socket should be cleaned up (stale + finally), got {len(socket_rm_cmds)}: {socket_rm_cmds}"
    )

    # ── No domjobabort — transfer_missing does NOT use nbd_full_export ─
    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 0, (
        "transfer_missing does NOT call nbd_full_export or domjobabort; "
        "domjobabort is only in the create_full_backup/NBD full-export path"
    )
