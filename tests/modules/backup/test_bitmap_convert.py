"""Unit tests for qemu-img convert path in BitmapBackupProvider (fast-compressed-full-backup).

Tests cover the NEW ``qemu-img convert`` FULL backup path that replaces the
former ``_start_write_server()`` + ``_transfer()`` ``pread``/``pwrite`` loop
for FULL backups.  Running VMs use ``nbd:unix:<socket>``; stopped VMs use
direct source qcow2 files.

All shell calls through ``MockShell``; zero real I/O.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.models.results import ShellResult, SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider

# Module-level result factory for helper functions (not pytest fixtures).
_ok = lambda: ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_snapshot() -> SnapshotInfo:
    """A standard SnapshotInfo for FULL backup tests."""
    return SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )


def _setup_convert_expectations(mock_shell, target, running: bool = True, *, compress: bool = False):
    """Register core expectations for create_full_backup via qemu-img convert.

    MockShell already has ``virsh dominfo`` (→ State: running) from conftest,
    so ``is_vm_running`` returns True by default.

    For *running* VMs: adds virsh backup-begin expectation.
    For *stopped* VMs: caller must patch ``is_vm_running`` and ``get_first_disk_path``.

    Generic ``rm -f`` catches stale socket, .tmp removal, and socket cleanup.
    """
    # qemu-img convert — always via run_with_stall_detection
    mock_shell.expect_first("qemu-img convert").returns(_ok())
    # Generic rm -f catch-all (stale socket, .tmp, socket cleanup)
    mock_shell.expect("rm -f").returns(_ok())
    # mv .tmp → final file (on success)
    mock_shell.expect("^mv ").returns(_ok())
    # virsh domjobabort in _full_pull_lifecycle finally (always called, idempotent)
    mock_shell.expect("virsh domjobabort").returns(_ok())

    # Post-creation FULL validation (design D5): no backing file — FULL must
    # be standalone (no backing-filename in qemu-img info output).
    mock_shell.expect(r"qemu-img info.*--force-share.*--output=json").returns(
        ShellResult(
            success=True,
            stdout='{"format": "qcow2", "virtual-size": 1073741824, "actual-size": 1048576}',
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Post-creation FULL validation (design D5): checkpoint existence for
    # running VMs — a qsnap- checkpoint must exist as baseline for future
    # incrementals.  Stopped VMs don't create checkpoints.
    if running:
        target_hash = hashlib.md5(str(target.path).encode()).hexdigest()[:8]
        # A future timestamp (9999) ensures _delete_superseded_checkpoints
        # sees it as NOT superseded (ts >= successor_ts → skip).
        checkpoint_name = f"qsnap-{target_hash}-99991231T000000-deadbe"
        mock_shell.expect("checkpoint-list").returns(
            ShellResult(
                success=True,
                stdout=f"{checkpoint_name}\n",
                stderr="",
                returncode=0,
                error=None,
            )
        )


# ── Tests: 1a–1d — qemu-img convert command construction ────────────────────


@pytest.mark.unit
def test_convert_cmd_running_vm_compressed(mock_shell, make_target, tmp_path, success_result):
    """1a: Running VM FULL with zstd compression — verify exact convert command."""
    target = make_target(path=str(tmp_path / "backups"), compress=True)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()
    pid = os.getpid()

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=True)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=True
        )

    assert result.success is True

    # Verify the convert command was passed to run_with_stall_detection
    convert_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_calls) == 1, "Expected exactly one qemu-img convert via stall detection"
    cmd = " ".join(convert_calls[0].args[0])
    assert "-c" in cmd, "Compressed convert should include -c flag"
    assert "-O qcow2" in cmd
    assert "-o compression_type=zstd" in cmd
    assert "-m 4" in cmd
    assert "-W" in cmd
    assert "-p" in cmd
    assert f"nbd:unix:/tmp/qsnap-backup-{pid}.sock" in cmd, (
        f"Expected NBD socket source, got: {cmd}"
    )
    assert cmd.endswith(".qcow2.tmp"), f"Expected output to .tmp file, got: {cmd}"


@pytest.mark.unit
def test_convert_cmd_running_vm_uncompressed(mock_shell, make_target, tmp_path, success_result):
    """1b: Running VM FULL without compression — no -c flag, no -o."""
    target = make_target(path=str(tmp_path / "backups"), compress=False)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()
    pid = os.getpid()

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=False)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False
        )

    assert result.success is True

    convert_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_calls) == 1
    cmd = " ".join(convert_calls[0].args[0])
    assert "-c" not in cmd, "Uncompressed convert should NOT include -c flag"
    assert "-o compression_type=" not in cmd, "Uncompressed convert should NOT include -o flag"
    assert "-O qcow2" in cmd
    assert "-m 4" in cmd
    assert "-W" in cmd
    assert "-p" in cmd
    assert f"nbd:unix:/tmp/qsnap-backup-{pid}.sock" in cmd
    assert cmd.endswith(".qcow2.tmp")


@pytest.mark.unit
def test_convert_cmd_stopped_vm_compressed(mock_shell, make_target, tmp_path):
    """1c: Stopped VM FULL with compression — direct source path, no NBD URI."""
    target = make_target(path=str(tmp_path / "backups"), compress=True)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()
    source_path = "/var/lib/libvirt/images/testvm.qcow2"

    _setup_convert_expectations(mock_shell, target, running=False, compress=True)

    provider = BitmapBackupProvider(mock_shell)

    with (
        patch("qsnap.modules.backup.bitmap.is_vm_running", return_value=False),
        patch("qsnap.modules.backup.bitmap.get_first_disk_path", return_value=source_path),
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=True
        )

    assert result.success is True

    convert_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_calls) == 1
    cmd = " ".join(convert_calls[0].args[0])
    assert "-c" in cmd
    assert "-O qcow2" in cmd
    assert "-o compression_type=zstd" in cmd
    assert "-m 4" in cmd
    assert "-W" in cmd
    assert "-p" in cmd
    # Source should be the direct file path, NOT NBD
    assert source_path in cmd, f"Expected source {source_path}, got: {cmd}"
    assert "nbd:unix:" not in cmd, "Stopped VM should NOT use NBD source"
    assert cmd.endswith(".qcow2.tmp")


@pytest.mark.unit
def test_convert_cmd_stopped_vm_uncompressed(mock_shell, make_target, tmp_path):
    """1d: Stopped VM FULL without compression — direct source, no -c, no NBD."""
    target = make_target(path=str(tmp_path / "backups"), compress=False)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()
    source_path = "/var/lib/libvirt/images/testvm.qcow2"

    _setup_convert_expectations(mock_shell, target, running=False, compress=False)

    provider = BitmapBackupProvider(mock_shell)

    with (
        patch("qsnap.modules.backup.bitmap.is_vm_running", return_value=False),
        patch("qsnap.modules.backup.bitmap.get_first_disk_path", return_value=source_path),
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False
        )

    assert result.success is True

    convert_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_calls) == 1
    cmd = " ".join(convert_calls[0].args[0])
    assert "-c" not in cmd
    assert "-o compression_type=" not in cmd, "Uncompressed should NOT include -o flag"
    assert "-O qcow2" in cmd
    assert "-m 4" in cmd
    assert "-W" in cmd
    assert "-p" in cmd
    assert source_path in cmd
    assert "nbd:unix:" not in cmd
    assert cmd.endswith(".qcow2.tmp")


# ── Tests: 1e–1f — Failure / success lifecycle ──────────────────────────────


@pytest.mark.unit
def test_convert_failure_removes_tmp(mock_shell, make_target, tmp_path, success_result):
    """1e: qemu-img convert failure → .tmp deleted, no final file, BackupResult.success=False."""
    target = make_target(path=str(tmp_path / "backups"), compress=False)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    convert_error = "qemu-img convert failed: I/O error"
    mock_shell.expect_first("qemu-img convert").returns(
        ShellResult(
            success=False, stdout="", stderr=convert_error, returncode=1, error=convert_error
        )
    )
    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    # rm -f for stale socket, .tmp removal in finally, socket cleanup in finally
    mock_shell.expect("rm -f").returns(success_result())
    # domjobabort in _full_pull_lifecycle finally
    mock_shell.expect("virsh domjobabort").returns(success_result())
    # mv should NOT be called (transfer failed) — no expectation set

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False
        )

    assert result.success is False
    assert convert_error in (result.error or "")
    assert result.bytes_transferred == 0

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    # .tmp removed in finally block
    tmp_rm = [cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and ".tmp" in cmd]
    assert len(tmp_rm) >= 1, f"Expected rm -f of .tmp file in finally, got: {all_run_cmds}"

    # mv never called (transfer failed)
    mv_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 0, "mv should not be called on failure"


@pytest.mark.unit
def test_convert_success_renames_tmp_to_final(mock_shell, make_target, tmp_path, success_result):
    """1f: qemu-img convert success → mv .tmp → .qcow2, BackupResult.success=True."""
    target = make_target(path=str(tmp_path / "backups"), compress=False)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=False)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False
        )

    assert result.success is True
    assert result.target_path.suffix == ".qcow2"
    assert ".tmp" not in result.target_path.name

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    mv_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 1, f"Expected exactly one mv command, got: {all_run_cmds}"
    assert ".tmp" in mv_cmds[0]
    assert ".qcow2" in mv_cmds[0]


# ── Tests: 1g–1h — NBD vs direct convert path selection ─────────────────────


@pytest.mark.unit
def test_running_vm_uses_nbd_convert(mock_shell, make_target, tmp_path, success_result):
    """1g: Running VM → virsh backup-begin called, qemu-img convert reads from nbd:unix:<socket>."""
    target = make_target(path=str(tmp_path / "backups"), compress=False)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=False)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False
        )

    assert result.success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    # virsh backup-begin should be called
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1, f"Expected virsh backup-begin, got: {all_run_cmds}"
    assert "testvm" in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        "backup-begin should receive checkpoint XML 3rd arg"
    )


@pytest.mark.unit
def test_stopped_vm_uses_direct_convert(mock_shell, make_target, tmp_path):
    """1h: Stopped VM → no virsh backup-begin, qemu-img convert reads from source qcow2."""
    target = make_target(path=str(tmp_path / "backups"), compress=False)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()
    source_path = "/var/lib/libvirt/images/testvm.qcow2"

    _setup_convert_expectations(mock_shell, target, running=False, compress=False)

    provider = BitmapBackupProvider(mock_shell)

    with (
        patch("qsnap.modules.backup.bitmap.is_vm_running", return_value=False),
        patch("qsnap.modules.backup.bitmap.get_first_disk_path", return_value=source_path),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False
        )

    assert result.success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    # No virsh backup-begin for stopped VM
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 0, "Stopped VM should NOT call virsh backup-begin"

    # domjobabort still called in _full_pull_lifecycle finally (idempotent)
    abort_cmds = [cmd for cmd in all_run_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1

    # qemu-img convert reads from source path (not NBD) — verified via stall detection spy


# ── Test: 1k — No _start_write_server or _transfer for FULLs ─────────────────


@pytest.mark.unit
def test_full_backup_does_not_use_write_server_or_transfer(mock_shell, make_target, tmp_path, success_result):
    """1k: Spy on _start_write_server and _transfer; assert neither called during create_full_backup()."""
    target = make_target(path=str(tmp_path / "backups"), compress=False)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=False)

    provider = BitmapBackupProvider(mock_shell)

    with (
        patch.object(BitmapBackupProvider, "_start_write_server") as mock_wserver,
        patch.object(BitmapBackupProvider, "_transfer") as mock_transfer,
    ):
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False
        )

    assert result.success is True
    mock_wserver.assert_not_called()
    mock_transfer.assert_not_called()


# ── Tests: 1l, 5c–5e — Stall detection ──────────────────────────────────────


@pytest.mark.unit
def test_convert_uses_stall_detection(mock_shell, make_target, tmp_path, success_result):
    """1l/5c: run_with_stall_detection used for qemu-img convert FULL backup."""
    target = make_target(path=str(tmp_path / "backups"), compress=False, backup_stall_timeout="30m")
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=False)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            stall_timeout=1800,
        )

    assert result.success is True

    # Spied run_with_stall_detection should have been called
    convert_stall_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_stall_calls) >= 1, "qemu-img convert should use run_with_stall_detection"

    # Verify output_file is the .tmp file
    convert_call = convert_stall_calls[0]
    output_file = convert_call.kwargs.get("output_file")
    assert output_file is not None, "run_with_stall_detection should receive output_file"
    assert str(output_file).endswith(".tmp"), f"output_file should be .tmp, got: {output_file}"


@pytest.mark.unit
def test_stall_detection_output_file_is_tmp(mock_shell, make_target, tmp_path, success_result):
    """5d: run_with_stall_detection output_file ends in .tmp."""
    target = make_target(path=str(tmp_path / "backups"), compress=False)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=False)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            stall_timeout=1800,
        )

    assert result.success is True

    convert_stall_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_stall_calls) >= 1

    output_file = convert_stall_calls[0].kwargs.get("output_file")
    assert output_file is not None
    assert ".tmp" in str(output_file), f"output_file should be a .tmp file, got: {output_file}"
    assert str(output_file).endswith(".tmp"), f"output_file should end in .tmp, got: {output_file}"


@pytest.mark.unit
def test_stall_detection_timeout_from_target_config(mock_shell, make_target, tmp_path, success_result):
    """5e: TargetConfig.backup_stall_timeout='5m' → stall_timeout=300 passed to run_with_stall_detection."""
    target = make_target(path=str(tmp_path / "backups"), compress=False, backup_stall_timeout="5m")
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=False)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=False,
            stall_timeout=300,  # 5m → 300s
        )

    assert result.success is True

    convert_stall_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_stall_calls) >= 1

    stall_timeout_passed = convert_stall_calls[0].kwargs.get("stall_timeout")
    assert stall_timeout_passed == 300, (
        f"Expected stall_timeout=300 (from backup_stall_timeout='5m'), got: {stall_timeout_passed}"
    )


# ── Test: 3a — First backup FULL via convert with atomic checkpoint ──────────


@pytest.mark.unit
def test_first_backup_full_via_convert_with_checkpoint(mock_shell, make_target, tmp_path, success_result):
    """3a: No prior checkpoint; virsh backup-begin called with checkpoint XML;
    qemu-img convert executed (not pread/pwrite); no _start_write_server."""
    target = make_target(path=str(tmp_path / "backups"), compress=False)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()
    pid = os.getpid()

    # Register expectations from most-specific to least-specific
    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=False)

    provider = BitmapBackupProvider(mock_shell)

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(BitmapBackupProvider, "_start_write_server") as mock_wserver,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False
        )

    assert result.success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    # checkpoint-create-as must NOT be called
    cp_create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(cp_create_cmds) == 0

    # virsh backup-begin called with checkpoint XML as 3rd arg
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        "backup-begin should receive checkpoint XML as 3rd positional arg"
    )

    # qemu-img convert was executed via run_with_stall_detection (not pread/pwrite)
    convert_stall_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_stall_calls) >= 1, "qemu-img convert should be used for FULL backup"
    convert_cmd = " ".join(convert_stall_calls[0].args[0])
    assert f"nbd:unix:/tmp/qsnap-backup-{pid}.sock" in convert_cmd

    # _start_write_server must NOT be called
    mock_wserver.assert_not_called()


# ── Tests: 3d, 3e — _full_pull_lifecycle shared helper ─────────────────────


@pytest.mark.unit
def test_full_pull_lifecycle_uses_convert(mock_shell, make_vm_config, make_target, tmp_path, success_result):
    """3d: _full_pull_lifecycle used by both create_full_backup() and transfer_missing() full-pull;
    qemu-img convert used inside the helper."""
    snapshot = _make_snapshot()

    # ── Part 1: create_full_backup calls _full_pull_lifecycle ──
    target_cfb = make_target(path=str(tmp_path / "backups_cfb"))
    target_cfb.path.mkdir(parents=True, exist_ok=True)

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target_cfb, running=True, compress=False)

    with patch.object(
        BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 65536)
    ) as mock_fpl:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target_cfb, compress=False
        )

    assert result.success is True
    assert mock_fpl.call_count == 1, (
        f"create_full_backup() should call _full_pull_lifecycle once, got {mock_fpl.call_count}"
    )

    # ── Part 2: transfer_missing() full-pull path calls _full_pull_lifecycle ──
    from tests.mocks.mock_shell import MockShell

    shell2 = MockShell()
    vm_config = make_vm_config()
    target_tm = make_target(path=str(tmp_path / "target_tm"), verify="off")

    # Setup: no prior checkpoint → full export
    shell2.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Snapshot exists on disk
    shell2.expect("test -f").returns(success_result())
    shell2.expect("rm -f").returns(success_result())  # stale socket
    shell2.expect("backup-begin").returns(success_result())
    shell2.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /var/lib/libvirt/images/testvm.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # _full_pull_lifecycle finally: domjobabort + rm -f (write_socket, .tmp, source socket)
    shell2.expect("domjobabort").returns(success_result())
    shell2.expect("rm -f").returns(success_result())
    shell2.expect("rm -f").returns(success_result())
    shell2.expect("rm -f").returns(success_result())

    with patch.object(
        BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 0)
    ) as mock_fpl2:
        provider2 = BitmapBackupProvider(shell2)
        results = provider2.transfer_missing(vm_config, target_tm, [snapshot])

    assert len(results) == 1
    assert results[0].success is True
    assert mock_fpl2.call_count == 1, (
        f"transfer_missing() full-pull should call _full_pull_lifecycle once, got {mock_fpl2.call_count}"
    )


@pytest.mark.unit
def test_full_pull_lifecycle_no_write_server(mock_shell, make_target, tmp_path, success_result):
    """3e: _start_write_server and _transfer NOT called inside _full_pull_lifecycle for FULL."""
    target = make_target(path=str(tmp_path / "backups"), compress=False)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=False)

    provider = BitmapBackupProvider(mock_shell)

    # Spy on _start_write_server and _transfer but let _full_pull_lifecycle
    # execute normally (it should call _qemu_img_convert_transfer, not those)
    with (
        patch.object(BitmapBackupProvider, "_start_write_server") as mock_wserver,
        patch.object(BitmapBackupProvider, "_transfer") as mock_transfer,
    ):
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False
        )

    assert result.success is True
    mock_wserver.assert_not_called()
    mock_transfer.assert_not_called()


# ── Test: 4g — Timestamp matches snapshot ───────────────────────────────────


@pytest.mark.unit
def test_full_timestamp_matches_snapshot(mock_shell, make_target, tmp_path, success_result):
    """4g: BackupResult filename embeds SnapshotInfo.timestamp date, not wall clock."""
    target = make_target(path=str(tmp_path / "backups"), compress=False)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()  # timestamp: 2025-01-01

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=False)

    provider = BitmapBackupProvider(mock_shell)
    result = provider.create_full_backup(
        "testvm", snapshot, target, compress=False
    )

    assert result.success is True

    # The filename is constructed as: vm.FULL.YYYYMMDDTHHMMSS_{6hex}.qcow2
    # where the timestamp comes from source_snapshot.timestamp.strftime("%Y%m%dT%H%M%S")
    expected_date = snapshot.timestamp.strftime("%Y%m%dT%H%M%S")

    assert result.target_path.name.startswith(f"testvm.FULL.{expected_date}_"), (
        f"Expected filename starting with testvm.FULL.{expected_date}_ (from snapshot timestamp {snapshot.timestamp}), "
        f"got {result.target_path.name}"
    )
    assert result.target_path.name.endswith(".qcow2")
    assert expected_date == "20250101T000000", "Snapshot timestamp should be 2025-01-01T000000"


# ── Test: 2e — Global compress=false affects convert command ────────────────


@pytest.mark.unit
def test_global_section_compress_false_affects_convert_cmd(mock_shell, make_target, tmp_path, success_result):
    """2e: compress=False from [global] propagates → qemu-img convert has no -c flag."""
    # compress=False at global level, target inherits it
    target = make_target(path=str(tmp_path / "backups"), compress=False)
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()
    pid = os.getpid()

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=False)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=target.compress,  # Respects target config
        )

    assert result.success is True

    convert_stall_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_stall_calls) >= 1

    convert_cmd = " ".join(convert_stall_calls[0].args[0])
    assert "-c" not in convert_cmd, (
        f"compress=False should omit -c flag from qemu-img convert, got: {convert_cmd}"
    )
    assert "-o compression_type=" not in convert_cmd, (
        f"compress=False should omit -o flag from qemu-img convert, got: {convert_cmd}"
    )
    assert "-O qcow2" in convert_cmd
    assert f"nbd:unix:/tmp/qsnap-backup-{pid}.sock" in convert_cmd


@pytest.mark.unit
def test_create_full_custom_convert_parallel(mock_shell, make_target, tmp_path, success_result):
    """create_full_backup with convert_parallel=2 uses -m 2 in qemu-img convert."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    _setup_convert_expectations(mock_shell, target, running=True, compress=False)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False, convert_parallel=2
        )

    assert result.success is True

    convert_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_calls) == 1
    cmd = " ".join(convert_calls[0].args[0])
    assert "-m 2" in cmd, f"convert_parallel=2 should set -m 2, got: {cmd}"
