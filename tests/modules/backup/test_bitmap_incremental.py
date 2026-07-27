"""Unit tests for BitmapBackupProvider incremental dirty-block copy loop.

Tests cover the unified NBD engine (design D1/D2/D4) that replaces
the former bare ``qemu-img convert`` pull on ALL paths (FULL and incremental).
All shell calls go through ``MockShell``; all NBD operations through
``MockNbdClient`` — zero real I/O.
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
    mock_shell.expect_first("qemu-img info.*" + str(prev_path.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_path}").returns(_ok_result())
    mock_shell.expect("qemu-img create").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("qemu-nbd --fork").returns(_ok_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
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

    if verify_mode == "compare":
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

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")
    nbd.block_status_payload = {
        "base:allocation": [
            NbdExtent(offset=0, length=65536, data=True),
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
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    pread_calls = [c for c in nbd.calls if c[0] == "pread"]
    assert len(pread_calls) > 0, "Expected at least one pread call"
    total_read = sum(c[2] for c in pread_calls)
    assert total_read == 65536, f"Expected 65536 bytes read (dirty total), got {total_read}"
    assert nbd.bytes_read == 65536

    # No convert commands — unified engine for ALL paths
    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, "No qemu-img convert — unified NBD engine for ALL paths"


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

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

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
    # Backing-chain validation (new in fix-broken-backing-chain): the
    # walk validates non-FULL backups via qemu-img info --backing-chain.
    mock_shell.expect_first(r"qemu-img info.*--backing-chain").returns(_ok_result())

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
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    # Custom run wrapper: the first test -f on the PREVIOUS BACKUP
    # (during the backwards walk) succeeds; the second test -f on the
    # same file (the (1b) re-check) fails — this simulates the previous
    # backup vanishing between the walk and qemu-img create.
    # Other test -f calls (e.g., source snapshot existence check in
    # transfer_missing) are delegated to the mock shell.
    prev_test_f_count = 0
    original_run = mock_shell.run

    def _run_with_vanish(cmd, timeout, check=False):
        nonlocal prev_test_f_count
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("test -f") and str(prev_backup) in cmd_str:
            prev_test_f_count += 1
            if prev_test_f_count == 1:
                return _ok_result()
            return ShellResult(
                success=False, stdout="", stderr="", returncode=1, error="test -f failed"
            )
        return original_run(cmd, timeout, check)

    with (
        patch.object(mock_shell, "run", side_effect=_run_with_vanish) as run_spy,
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

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint not in delete_cmds[0]
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
    nbd.fail_pread = "pread I/O error"

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
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
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

    tmp_rm_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and tmp_suffix in cmd]
    assert len(tmp_rm_cmds) >= 1, "Expected rm -f of .tmp file, got none"

    write_socket_rm = [cmd for cmd in all_run_cmds if write_socket in cmd]
    assert len(write_socket_rm) >= 1, "Write socket not cleaned up"

    kill_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("kill")]
    assert len(kill_cmds) >= 1, "qemu-nbd kill not issued"

    abort_cmds = [cmd for cmd in all_run_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) >= 1, "domjobabort not issued"

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

    mv_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 1, "mv tmp→final must be issued"
    assert tmp_suffix in mv_cmds[0], f"mv should involve the .tmp file, got: {mv_cmds[0]}"
    assert ".qcow2" in mv_cmds[0]

    tmp_rm_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and tmp_suffix in cmd]
    assert len(tmp_rm_cmds) >= 1, "rm -f <tmp> must appear in finally cleanup"

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


def test_incremental_uses_unified_engine_no_convert(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """No 'qemu-img convert' in shell history when prior checkpoint exists;
    unified NBD engine (pread/pwrite) used instead.  mock connect called
    with both contexts."""
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

    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, (
        "No qemu-img convert — unified NBD engine (pread/pwrite) for ALL paths"
    )

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

    # No -c compression flag anywhere in the incremental path
    for cmd in all_run_cmds:
        tokens = cmd.split()
        assert "-c" not in tokens, f"Command '{cmd}' should not contain -c flag"
        assert "-o" not in tokens or "compression_type" not in cmd, (
            f"Command '{cmd}' should not contain -o compression_type"
        )

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
                target,
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

    source_info = json.dumps(
        {"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 1048576}
    )
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
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    mock_shell.expect(f"rm -f {delta_file}").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
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

    delta_rm = [cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and str(delta_file) in cmd]
    assert len(delta_rm) >= 1, "Delta file should be cleaned up"

    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint not in delete_cmds[0], "Prior checkpoint must be preserved"


# ══════════════════════════════════════════════════════════════════════════
# CONFIGURABLE FULL BACKUP ENGINE TESTS (configurable-full-backup-engine)
# ══════════════════════════════════════════════════════════════════════════


def test_incremental_unaffected_by_full_transfer_engine(
    mock_shell, make_vm_config, make_target, tmp_path
) -> None:
    """Incremental transfers ignore full_transfer_engine — always use
    pread/pwrite unified NBD engine regardless of engine setting."""
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
        # Pass full_transfer_engine="libnbd" — incrementals should ignore this
        results = provider.transfer_missing(
            vm_config, target, [snapshot], full_transfer_engine="libnbd"
        )

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    # No qemu-img convert — incrementals always use pread/pwrite unified engine
    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, (
        "Incremental should NEVER use qemu-img convert, regardless of full_transfer_engine"
    )

    # pread/pwrite was called (unified NBD engine)
    pread_calls = [c for c in nbd.calls if c[0] == "pread"]
    assert len(pread_calls) > 0, "pread should be called for incremental transfer"


def test_incremental_zero_skip_false(mock_shell, make_vm_config, make_target, tmp_path) -> None:
    """Incremental _transfer is called with zero_skip=False — copies only
    dirty∩allocated extents, never performs zero-skip optimization."""
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
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
        patch.object(BitmapBackupProvider, "_transfer") as transfer_mock,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"
        transfer_mock.return_value = (None, 65536)
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    # Verify _transfer was called with zero_skip=False
    assert transfer_mock.call_count == 1
    call_kwargs = transfer_mock.call_args.kwargs
    assert call_kwargs.get("zero_skip") is False, (
        f"Incremental _transfer should have zero_skip=False, got: {call_kwargs}"
    )
