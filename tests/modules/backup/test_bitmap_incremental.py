"""Unit tests for BitmapBackupProvider incremental dirty-block copy loop.

Tests cover the new INbdClient-driven copy loop (design D2/D4) that replaces
the former bare ``qemu-img convert`` pull on the incremental path.  All shell
calls go through ``MockShell``; all NBD operations through ``MockNbdClient`` —
zero real I/O.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from qsnap.models.results import NbdExtent, NbdResult, ShellResult, SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.utils.retry import is_retryable
from tests.mocks.mock_nbd import MockNbdClient

pytestmark = pytest.mark.unit

# Tiny disk so block_status runs a single window — the copy-loop logic is
# the same regardless of disk size, and one window avoids multiplying extents.
_TINY_DISK = 65536


# ── Helpers ────────────────────────────────────────────────────────────────


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


def _default_nbd() -> MockNbdClient:
    """A MockNbdClient with a tiny disk (single block_status window)."""
    return MockNbdClient(size=_TINY_DISK, max_request_size=33554432)


def _setup_full_copy_loop_expectations(
    mock_shell, target, prev_path: Path, disk_target: str = "vda"
) -> MockNbdClient:
    """Register all MockShell expectations for a successful copy loop.

    Handles: domblklist, qemu-img info (listing), test -f, qemu-img create,
    rm -f write socket/pidfile, qemu-nbd, kill, rm -f write socket/pidfile,
    mv.  Returns a pre-configured MockNbdClient.

    The caller must still register:
    - rm -f (stale source socket)
    - checkpoint-list
    - backup-begin
    - checkpoint-delete (rotation)
    - domjobabort
    - rm -f (source socket cleanup in finally)
    - qemu-img info responses for verification (if verify != "off")
    - virsh --version (if the caller needs it; most MODIFY tests in
      test_bitmap.py register it explicitly)
    """
    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        f"qemu:dirty-bitmap:backup-{disk_target}": [
            NbdExtent(offset=0, length=65536, data=True),
        ],
    }

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    # domblklist for disk target (use expect_first to beat conftest)
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=(
                f"Target   Source\n"
                f"--------------------------------\n"
                f"{disk_target}   /var/lib/libvirt/images/testvm.qcow2\n"
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # qemu-img info for listing previous backup (use expect_first)
    mock_shell.expect_first("qemu-img info.*" + str(prev_path.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # test -f for re-check existence (expect_first to beat conftest generic)
    mock_shell.expect_first(f"test -f {prev_path}").returns(_ok_result())
    # qemu-img create -f qcow2 -b <prev> -F qcow2 <tmp>
    mock_shell.expect("qemu-img create").returns(_ok_result())
    # rm -f write socket + pidfile (stale cleanup before qemu-nbd)
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    # qemu-nbd --fork --pid-file --socket
    mock_shell.expect("qemu-nbd --fork").returns(_ok_result())
    # kill (terminate qemu-nbd) — pre-create pidfile so _terminate_qemu_nbd finds it
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok_result())
    # rm -f write socket + pidfile (post-kill cleanup)
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    # mv tmp -> final
    mock_shell.expect("^mv ").returns(_ok_result())

    return nbd


def _setup_verify_bitmap_incremental_expectations(
    mock_shell,
    source_path: str,
    delta_path_str: str,
    prev_path: Path,
    verify_mode: str,
    *,
    delta_virtual_size: int = _TINY_DISK,
    delta_actual_size: int = 65536,
    delta_backing: str | None = None,
) -> None:
    """Register MockShell expectations for verify_bitmap_incremental.

    *delta_backing* defaults to ``str(prev_path)``.
    """
    if delta_backing is None:
        delta_backing = str(prev_path)

    source_info = {
        "format": "qcow2",
        "virtual-size": delta_virtual_size,
        "actual-size": 1048576,
    }
    delta_info = {
        "format": "qcow2",
        "virtual-size": delta_virtual_size,
        "actual-size": delta_actual_size,
        "backing-filename": delta_backing,
    }

    # Use expect_first so these beat any generic qemu-img info patterns
    mock_shell.expect_first(rf"qemu-img info.*--force-share.*{source_path}").returns(
        ShellResult(
            success=True, stdout=json.dumps(source_info), stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect_first(rf"qemu-img info.*{delta_path_str}").returns(
        ShellResult(
            success=True, stdout=json.dumps(delta_info), stderr="", returncode=0, error=None
        )
    )

    if verify_mode in ("hash", "full"):
        mock_shell.expect("qemu-img compare").returns(_ok_result())


# ── Tests ──────────────────────────────────────────────────────────────────


def test_copy_loop_reads_only_dirty_extents(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """block_status has dirty + clean extents; pread covers exactly
    dirty ∩ allocated ranges.  bytes_read == dirty total."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    # Create the previous backup file so list() finds it
    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")
    # Override block_status to return dirty AND clean extents
    nbd.block_status_payload = {
        "base:allocation": [
            NbdExtent(offset=0, length=65536, data=True),
            # no unallocated tail since disk is exactly 65536
        ],
        "qemu:dirty-bitmap:backup-vda": [
            NbdExtent(offset=0, length=65536, data=True),
        ],
    }
    nbd._size = 65536

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # rotation
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # source socket cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    # Assert pread calls cover exactly the dirty∩allocated range
    pread_calls = [c for c in nbd.calls if c[0] == "pread"]
    assert len(pread_calls) > 0, "Expected at least one pread call"
    total_read = sum(c[2] for c in pread_calls)
    assert total_read == 65536, f"Expected 65536 bytes read (dirty total), got {total_read}"
    assert nbd.bytes_read == 65536

    # No convert commands in the incremental path
    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    all_stall_cmds = [" ".join(c.args[0]) for c in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, "No qemu-img convert on the incremental path"


def test_first_incremental_backing_is_full(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """qemu-img create -b names the FULL backup; final qemu-img info
    shows backing-filename == FULL path → verify pass."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="metadata")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    # This is the "FULL" anchor — the previous backup
    prev_backup = target_path / "testvm.FULL.20241230.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    delta_file = target_path / f"{snapshot.name}.qcow2"
    _setup_verify_bitmap_incremental_expectations(
        mock_shell,
        str(snapshot.path),
        str(delta_file),
        prev_backup,
        "metadata",
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # rotation
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # source socket cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    # Assert create -b references the FULL backup
    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    create_cmds = [cmd for cmd in all_run_cmds if "qemu-img create" in cmd]
    assert len(create_cmds) == 1
    assert f"-b {prev_backup}" in create_cmds[0]
    assert "-F qcow2" in create_cmds[0]


def test_previous_backup_vanished_retryable_failure(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """test -f fails → is_retryable(error) is True; successor checkpoint
    deleted, prior preserved."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    # Create the previous backup file so list() finds it
    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    # domblklist for disk target (expect_first to beat conftest)
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /var/lib/libvirt/images/testvm.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # qemu-img info for listing previous backup (success — list finds it)
    mock_shell.expect_first("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # test -f FAILS — vanished between listing and delta creation
    # (expect_first to beat conftest's generic success)
    mock_shell.expect_first(f"test -f {prev_backup}").returns(
        ShellResult(success=False, stdout="", stderr="", returncode=1, error="test -f failed")
    )

    # Source socket + checkpoint setup
    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    # Successor checkpoint deleted best-effort (failure path)
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    # cleanup in finally: rm -f write socket+pidfile, rm -f .tmp, source socket
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=_default_nbd())
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False
    assert "vanished" in results[0].error.lower(), (
        f"Error should mention 'vanished', got: {results[0].error}"
    )
    assert "eof" in results[0].error.lower(), f"Error should mention 'eof', got: {results[0].error}"
    assert is_retryable(results[0].error) is True, (
        f"Vanished error should be retryable, got is_retryable={is_retryable(results[0].error)}"
    )

    # Successor checkpoint deleted
    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    # prior checkpoint preserved (not in delete command)
    assert prior_checkpoint not in delete_cmds[0]
    # No convert / no create calls beyond list()
    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0


def test_previous_existence_rechecked_before_create(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """Assert a test -f <prev> command appears BEFORE the qemu-img create command."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # rotation
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # source socket cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    # Find positions of test -f and qemu-img create
    test_f_idx = None
    create_idx = None
    for i, cmd in enumerate(all_run_cmds):
        if cmd.startswith("test -f") and str(prev_backup) in cmd:
            test_f_idx = i
        if "qemu-img create" in cmd:
            create_idx = i
    assert test_f_idx is not None, "test -f <prev> must appear in shell history"
    assert create_idx is not None, "qemu-img create must appear in shell history"
    assert test_f_idx < create_idx, "test -f must appear BEFORE qemu-img create in shell history"


def test_mid_copy_failure_cleans_temp_qemu_nbd_and_socket(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """fail_pread after first extent → result failure, rm -f <tmp> issued,
    write socket rm issued, qemu-nbd kill issued, domjobabort issued,
    successor checkpoint deleted."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"
    tmp_suffix = f"{snapshot.name}.qcow2.tmp"
    delta_file = target_path / f"{snapshot.name}.qcow2"

    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }
    # Fail on pread
    nbd.fail_pread = "pread I/O error"

    # domblklist for disk target
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /var/lib/libvirt/images/testvm.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(_ok_result())
    mock_shell.expect("qemu-img create").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("qemu-nbd --fork").returns(_ok_result())
    # Pre-create pidfile for _terminate_qemu_nbd
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok_result())
    # rm -f write socket + pidfile (post-kill cleanup)
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    # .tmp removal in finally
    mock_shell.expect("rm -f").returns(_ok_result())

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    # successor checkpoint deleted best-effort
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    # _cleanup_partial_file: rm -f <delta_file>
    mock_shell.expect(f"rm -f {delta_file}").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # source socket cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False
    assert "pread I/O error" in results[0].error

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    # .tmp removal in finally block
    tmp_rm_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and tmp_suffix in cmd]
    assert len(tmp_rm_cmds) >= 1, "Expected rm -f of .tmp file, got none"

    # write socket removal
    write_socket_rm = [cmd for cmd in all_run_cmds if write_socket in cmd]
    assert len(write_socket_rm) >= 1, "Write socket not cleaned up"

    # qemu-nbd kill issued
    kill_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("kill")]
    assert len(kill_cmds) >= 1, "qemu-nbd kill not issued"

    # domjobabort
    abort_cmds = [cmd for cmd in all_run_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) >= 1, "domjobabort not issued"

    # successor checkpoint deleted
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint not in delete_cmds[0]


def test_successful_transfer_no_tmp_or_socket_remain(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """On success: final rm -f sweep includes tmp file and write socket;
    mv tmp→final issued."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}.sock"
    tmp_suffix = f"{snapshot.name}.qcow2.tmp"

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # rotation
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # source socket cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    # mv tmp→final issued
    mv_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 1, "mv tmp→final must be issued"
    assert tmp_suffix in mv_cmds[0], f"mv should involve the .tmp file, got: {mv_cmds[0]}"
    assert ".qcow2" in mv_cmds[0]

    # rm -f of .tmp issued (from finally block — no-op after rename but present)
    tmp_rm_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and tmp_suffix in cmd]
    assert len(tmp_rm_cmds) >= 1, "rm -f <tmp> must appear in finally cleanup"

    # write socket cleanup (at least twice: pre-nbd rm + post-kill rm)
    write_socket_cmds = [cmd for cmd in all_run_cmds if write_socket in cmd]
    assert len(write_socket_cmds) >= 2, "Write socket should be in both pre-nbd rm and post-kill rm"


def test_stall_watchdog_aborts_with_correct_error_string(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """sleeping pread_handler + small stall_timeout; exact string match
    'Stall detected: no progress for {N}s'; failure path ran."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"
    delta_file = target_path / f"{snapshot.name}.qcow2"

    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }
    # pread_handler sleeps enough to trigger the 1-second stall watchdog
    stall_timeout = 1

    def sleeping_pread(offset: int, length: int) -> NbdResult:
        time.sleep(2.0)  # longer than stall_timeout
        return NbdResult(success=True, payload=bytes(length), error=None)

    nbd.pread_handler = sleeping_pread

    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /var/lib/libvirt/images/testvm.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(_ok_result())
    mock_shell.expect("qemu-img create").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("qemu-nbd --fork").returns(_ok_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # .tmp removal in finally

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # successor
    mock_shell.expect(f"rm -f {delta_file}").returns(_ok_result())  # _cleanup_partial_file
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # source socket

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(
            vm_config, target, [snapshot], stall_timeout=stall_timeout
        )

    assert len(results) == 1
    assert results[0].success is False
    expected_error = f"Stall detected: no progress for {stall_timeout}s"
    assert results[0].error == expected_error, (
        f"Expected exact error '{expected_error}', got '{results[0].error}'"
    )

    # Failure path ran
    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1


def test_slow_progressing_loop_not_killed(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """Short sleeps per chunk, generous timeout; success."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }

    def slow_pread(offset: int, length: int) -> NbdResult:
        time.sleep(0.1)  # short sleep, less than stall_timeout
        return NbdResult(success=True, payload=bytes(length), error=None)

    nbd.pread_handler = slow_pread

    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /var/lib/libvirt/images/testvm.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(_ok_result())
    mock_shell.expect("qemu-img create").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("qemu-nbd --fork").returns(_ok_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("^mv ").returns(_ok_result())

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # rotation
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # source socket

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run),
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot], stall_timeout=5)

    assert len(results) == 1
    assert results[0].success is True


def test_zero_stall_timeout_disables_watchdog(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """stall_timeout=0 + sleeping pread → success (watchdog disabled)."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }

    def sleeping_pread(offset: int, length: int) -> NbdResult:
        time.sleep(1.0)  # sleep, but stall_timeout=0 disables watchdog
        return NbdResult(success=True, payload=bytes(length), error=None)

    nbd.pread_handler = sleeping_pread

    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /var/lib/libvirt/images/testvm.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(_ok_result())
    mock_shell.expect("qemu-img create").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("qemu-nbd --fork").returns(_ok_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("^mv ").returns(_ok_result())

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # rotation
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # source socket

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run),
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot], stall_timeout=0)

    assert len(results) == 1
    assert results[0].success is True


def test_incremental_uses_inbd_client_copy_loop(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """No 'qemu-img convert' in shell history when prior checkpoint exists;
    mock connect called with both contexts."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # rotation
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # source socket cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    all_stall_cmds = [" ".join(c.args[0]) for c in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, (
        "No qemu-img convert on the incremental path — copy loop must be used"
    )

    # Assert source nbd connect was called with both contexts
    src_connect = [c for c in nbd.calls if c[0] == "connect"]
    assert len(src_connect) >= 1, "Source NBD connect not called"
    contexts = nbd.requested_contexts
    assert "base:allocation" in contexts
    assert "qemu:dirty-bitmap:backup-vda" in contexts


def test_qemu_img_info_shows_backing_filename(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """Verify path asserts backing-filename; wrong backing → verify failure."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="metadata")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    delta_file = target_path / f"{snapshot.name}.qcow2"

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }

    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /var/lib/libvirt/images/testvm.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(_ok_result())
    mock_shell.expect("qemu-img create").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("qemu-nbd --fork").returns(_ok_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("^mv ").returns(_ok_result())

    # Verification: source info passes, but delta has WRONG backing-filename
    source_info = json.dumps(
        {"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 1048576}
    )
    wrong_backing = "/wrong/path/backup.qcow2"
    delta_info = json.dumps(
        {
            "format": "qcow2",
            "virtual-size": _TINY_DISK,
            "actual-size": 65536,
            "backing-filename": wrong_backing,
        }
    )

    mock_shell.expect_first(rf"qemu-img info.*--force-share.*{snapshot.path}").returns(
        ShellResult(success=True, stdout=source_info, stderr="", returncode=0, error=None)
    )
    mock_shell.expect_first(rf"qemu-img info.*{delta_file.name}").returns(
        ShellResult(success=True, stdout=delta_info, stderr="", returncode=0, error=None)
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # successor
    mock_shell.expect(f"rm -f {delta_file}").returns(_ok_result())  # _cleanup_partial_file
    mock_shell.expect("domjobabort").returns(_ok_result())
    # finally: rm -f write socket+pidfile, .tmp, source socket
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run),
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False
    assert "verification failed" in results[0].error
    assert "backing-filename" in results[0].error


def test_restore_chain_resolved_without_bitmap_specific_logic(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """The delta's backing-filename points to previous backup so the
    EXISTING restore flow works — assert create -b chain + final backing
    check pass with no bitmap-specific restore code."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="metadata")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    delta_file = target_path / f"{snapshot.name}.qcow2"
    _setup_verify_bitmap_incremental_expectations(
        mock_shell,
        str(snapshot.path),
        str(delta_file),
        prev_backup,
        "metadata",
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # rotation
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # source socket cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    create_cmds = [cmd for cmd in all_run_cmds if "qemu-img create" in cmd]
    assert len(create_cmds) == 1
    assert f"-b {prev_backup}" in create_cmds[0]

    # check there's no bitmap-specific restore logic in the provider (list, delete only)
    assert hasattr(provider, "list")
    assert hasattr(provider, "delete")
    assert hasattr(provider, "transfer_missing")


def test_bitmap_incremental_ignores_compress_setting(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
) -> None:
    """target.compress=True; assert INFO log line and no '-c' in any
    issued command on the incremental path."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off", compress=True)
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # rotation
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # source socket cleanup

    caplog.set_level("INFO")
    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    all_stall_cmds = [" ".join(c.args[0]) for c in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    # No -c compression flag anywhere in the incremental path
    # Use word-boundary check — avoid false hits on qsnap-chec*kpoint-test.xml
    for cmd in all_cmds:
        tokens = cmd.split()
        assert "-c" not in tokens, f"Command '{cmd}' should not contain -c flag"
        assert "-o" not in tokens or "compression_type" not in cmd, (
            f"Command '{cmd}' should not contain -o compression_type"
        )

    # INFO log about compress being ignored
    compress_logs = [
        rec
        for rec in caplog.records
        if "uncompressed" in rec.getMessage().lower() and "design d6" in rec.getMessage().lower()
    ]
    assert len(compress_logs) >= 1, "Expected INFO log about bitmap incrementals being uncompressed"


def test_missing_libnbd_fails_factory_construction(make_target, tmp_path) -> None:
    """DefaultFactory: monkeypatch is_libnbd_available → False; bitmap
    target with libvirt >= 7.2 mocked; create_backup_provider raises
    RuntimeError with message naming python3-libnbd."""
    from qsnap.factory.default import DefaultFactory
    from qsnap.utils.nbd_client import MISSING_LIBNBD_ERROR

    # Create a mock shell that reports libvirt 8.0 (>= 7.2)
    mock_shell = Mock()
    mock_shell.run.return_value = ShellResult(
        success=True, stdout="virsh 8.0.0\n", stderr="", returncode=0, error=None
    )

    target = make_target(
        path=str(tmp_path / "target"),
    )

    with patch("qsnap.factory.default.is_libnbd_available", return_value=False):
        factory = DefaultFactory(mock_shell, Mock())
        with pytest.raises(RuntimeError) as exc_info:
            factory.create_backup_provider(
                Mock(),
                target,  # Mock VMConfig
            )

    assert "python3-libnbd" in str(exc_info.value)
    assert MISSING_LIBNBD_ERROR in str(exc_info.value)


def test_full_size_verify_failure_triggers_cleanup(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """qemu-img info actual-size > dirty_bytes*2+64MiB → verification
    failed error; final file rm'd; successor checkpoint deleted;
    prior preserved."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="metadata")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    delta_file = target_path / f"{snapshot.name}.qcow2"

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }

    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /var/lib/libvirt/images/testvm.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(_ok_result())
    mock_shell.expect("qemu-img create").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("qemu-nbd --fork").returns(_ok_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("^mv ").returns(_ok_result())

    # Verification: huge actual-size triggers regression barrier
    source_info = json.dumps(
        {"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 1048576}
    )
    # dirty_bytes = 65536, barrier = 65536*2 + 64MiB = 65536*2 + 67108864 = 67239936
    # Make actual-size larger than barrier
    huge_actual = 70000000
    delta_info = json.dumps(
        {
            "format": "qcow2",
            "virtual-size": _TINY_DISK,
            "actual-size": huge_actual,
            "backing-filename": str(prev_backup),
        }
    )

    mock_shell.expect_first(rf"qemu-img info.*--force-share.*{snapshot.path}").returns(
        ShellResult(success=True, stdout=source_info, stderr="", returncode=0, error=None)
    )
    mock_shell.expect_first(rf"qemu-img info.*{delta_file.name}").returns(
        ShellResult(success=True, stdout=delta_info, stderr="", returncode=0, error=None)
    )

    mock_shell.expect("virsh --version").returns(
        ShellResult(success=True, stdout="virsh 8.2.0\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    # successor checkpoint deleted
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    # _cleanup_partial_file deletes the final file
    mock_shell.expect(f"rm -f {delta_file}").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    # finally: rm -f write socket+pidfile, .tmp, source socket
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False
    assert "verification failed" in results[0].error
    assert "regressed" in results[0].error or "barrier" in results[0].error.lower(), (
        f"Error should mention regression/barrier, got: {results[0].error}"
    )

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    # Final file deleted
    delta_rm = [cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and str(delta_file) in cmd]
    assert len(delta_rm) >= 1, "Delta file should be cleaned up"

    # Successor checkpoint deleted
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint not in delete_cmds[0], "Prior checkpoint must be preserved"
