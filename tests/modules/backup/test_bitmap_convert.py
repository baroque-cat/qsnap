"""Unit tests for qemu-img convert path in BitmapBackupProvider (fast-compressed-full-backup).

Tests cover the ``qemu-img convert`` FULL backup path under the orthogonal
``run_backup(vm_config, target, disk, *, opts)`` entry point.  When no qsnap
checkpoint exists for the VM+target+disk, ``run_backup`` decides FULL and:

- Running VMs use ``nbd:unix:<socket>:exportname=<disk>``.
- Stopped VMs use the direct source qcow2 file.
- The output is freeze-timestamp named ``{vm}.FULL.{freeze_ts}_{disk}_{hex6}.qcow2``
  (standalone — no backing file) and is created atomically via
  ``mv <name>.qcow2.tmp <name>.qcow2``.

All shell calls through ``MockShell``; zero real I/O.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from unittest.mock import patch

import pytest

from qsnap.models.results import ShellResult
from qsnap.modules.backup.bitmap import BitmapBackupProvider


# Module-level result factory for helper functions (not pytest fixtures).
def _ok() -> ShellResult:
    return ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


def _expect_no_blockjob(mock_shell) -> None:
    """Register the blockjob probe expectation: no active job on the disk."""
    mock_shell.expect("virsh blockjob").returns(
        ShellResult(
            success=True,
            stdout="No current block job\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )


# ── Helpers ────────────────────────────────────────────────────────────────


def _setup_running_full_expectations(
    mock_shell, target, *, compress: bool = False, vm_name: str = "testvm"
):
    """Register expectations for a running-VM FULL ``run_backup``.

    MockShell already has ``virsh dominfo`` (→ State: running) from conftest,
    so ``is_vm_running`` returns True by default.

    Covers: checkpoint-list (empty → FULL), blockjob probe (no job),
    stale-socket rm -f, virsh backup-begin, qemu-img convert via
    run_with_stall_detection, mv .tmp → final, and finally cleanups
    (rm -f catch-all + domjobabort).
    """
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    _expect_no_blockjob(mock_shell)
    mock_shell.expect_first("virsh backup-begin").returns(_ok())
    # qemu-img convert — always via run_with_stall_detection
    mock_shell.expect_first("qemu-img convert").returns(_ok())
    # Generic rm -f catch-all (stale socket, .tmp, socket cleanup)
    mock_shell.expect("rm -f").returns(_ok())
    # mv .tmp → final file (on success)
    mock_shell.expect("^mv ").returns(_ok())
    # virsh domjobabort in _full_pull_lifecycle finally (always called, idempotent)
    mock_shell.expect("virsh domjobabort").returns(_ok())


def _setup_stopped_full_expectations(mock_shell):
    """Register expectations for a stopped-VM offline FULL ``run_backup``.

    Overrides ``virsh dominfo`` → shut off, then: checkpoint-list (empty →
    FULL), qemu-img convert from the source file, mv .tmp → final, and
    finally cleanups (rm -f catch-all + domjobabort).  No backup-begin,
    no blockjob probe, no socket.
    """
    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(success=True, stdout="State: shut off\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect_first("qemu-img convert").returns(_ok())
    mock_shell.expect("rm -f").returns(_ok())
    mock_shell.expect("^mv ").returns(_ok())
    mock_shell.expect("virsh domjobabort").returns(_ok())


# ── Tests: 1a–1d — qemu-img convert command construction ────────────────────


@pytest.mark.unit
def test_run_backup_running_vm_compressed(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """1a: Running VM FULL with zstd compression — verify exact convert command."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=True, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()

    _setup_running_full_expectations(mock_shell, target, compress=True)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"

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
    assert f"nbd:unix:/tmp/qsnap-backup-{pid}-vda.sock:exportname=vda" in cmd, (
        f"Expected NBD socket source, got: {cmd}"
    )
    assert cmd.endswith(".qcow2.tmp"), f"Expected output to .tmp file, got: {cmd}"


@pytest.mark.unit
def test_run_backup_running_vm_uncompressed(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """1b: Running VM FULL without compression — no -c flag, no -o."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()

    _setup_running_full_expectations(mock_shell, target, compress=False)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"

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
    assert f"nbd:unix:/tmp/qsnap-backup-{pid}-vda.sock:exportname=vda" in cmd
    assert cmd.endswith(".qcow2.tmp")


@pytest.mark.unit
def test_run_backup_stopped_vm_compressed(mock_shell, make_vm_config, make_target, tmp_path):
    """1c: Stopped VM FULL with compression — direct source path, no NBD URI."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=True, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)
    source_path = "/var/lib/libvirt/images/testvm.qcow2"

    _setup_stopped_full_expectations(mock_shell)

    provider = BitmapBackupProvider(mock_shell)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"

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
def test_run_backup_stopped_vm_uncompressed(mock_shell, make_vm_config, make_target, tmp_path):
    """1d: Stopped VM FULL without compression — direct source, no -c, no NBD."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)
    source_path = "/var/lib/libvirt/images/testvm.qcow2"

    _setup_stopped_full_expectations(mock_shell)

    provider = BitmapBackupProvider(mock_shell)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"

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
def test_run_backup_convert_failure_removes_tmp(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """1e: qemu-img convert failure → .tmp deleted, no final file, BackupResult.success=False."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    convert_error = "qemu-img convert failed: I/O error"
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    _expect_no_blockjob(mock_shell)
    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    # rm -f for stale socket, .tmp removal in finally, socket cleanup in finally
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect_first("qemu-img convert").returns(
        ShellResult(
            success=False, stdout="", stderr=convert_error, returncode=1, error=convert_error
        )
    )
    # domjobabort in _full_pull_lifecycle finally
    mock_shell.expect("virsh domjobabort").returns(success_result())
    # successor checkpoint best-effort delete
    mock_shell.expect("checkpoint-delete").returns(success_result())
    # mv should NOT be called (transfer failed) — no expectation set

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert convert_error in (result.error or "")
    assert result.bytes_transferred == 0
    assert result.disk == "vda"

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    # .tmp removed in finally block
    tmp_rm = [cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and ".tmp" in cmd]
    assert len(tmp_rm) >= 1, f"Expected rm -f of .tmp file in finally, got: {all_run_cmds}"

    # mv never called (transfer failed)
    mv_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 0, "mv should not be called on failure"


@pytest.mark.unit
def test_run_backup_convert_success_renames_tmp_to_final(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """1f: qemu-img convert success → mv .tmp → .qcow2, BackupResult.success=True."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_running_full_expectations(mock_shell, target, compress=False)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"
    assert result.target_path.suffix == ".qcow2"
    assert ".tmp" not in result.target_path.name

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    mv_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 1, f"Expected exactly one mv command, got: {all_run_cmds}"
    assert ".tmp" in mv_cmds[0]
    assert ".qcow2" in mv_cmds[0]


# ── Tests: 1g–1h — NBD vs direct convert path selection ─────────────────────


@pytest.mark.unit
def test_run_backup_running_vm_uses_nbd_convert(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """1g: Running VM → virsh backup-begin called, qemu-img convert reads from nbd:unix:<socket>."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_running_full_expectations(mock_shell, target, compress=False)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    # virsh backup-begin should be called
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1, f"Expected virsh backup-begin, got: {all_run_cmds}"
    assert "testvm" in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        "backup-begin should receive checkpoint XML 3rd arg"
    )


@pytest.mark.unit
def test_run_backup_stopped_vm_uses_direct_convert(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """1h: Stopped VM → no virsh backup-begin, qemu-img convert reads from source qcow2."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)
    source_path = "/var/lib/libvirt/images/testvm.qcow2"

    _setup_stopped_full_expectations(mock_shell)

    provider = BitmapBackupProvider(mock_shell)

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    # No virsh backup-begin for stopped VM
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 0, "Stopped VM should NOT call virsh backup-begin"

    # domjobabort still called in _full_pull_lifecycle finally (idempotent)
    abort_cmds = [cmd for cmd in all_run_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1

    # qemu-img convert reads from source path (not NBD)
    convert_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_calls) == 1
    cmd = " ".join(convert_calls[0].args[0])
    assert source_path in cmd
    assert "nbd:unix:" not in cmd


# ── Test: 1k — No _start_write_server or _transfer for FULLs ─────────────────


@pytest.mark.unit
def test_run_backup_full_does_not_use_write_server_or_transfer(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """1k: Spy on _start_write_server and _transfer; assert neither called during run_backup FULL."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_running_full_expectations(mock_shell, target, compress=False)

    provider = BitmapBackupProvider(mock_shell)

    with (
        patch.object(BitmapBackupProvider, "_start_write_server") as mock_wserver,
        patch.object(BitmapBackupProvider, "_transfer") as mock_transfer,
    ):
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"
    mock_wserver.assert_not_called()
    mock_transfer.assert_not_called()


# ── Tests: 1l, 5c–5e — Stall detection ──────────────────────────────────────


@pytest.mark.unit
def test_run_backup_convert_uses_stall_detection(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """1l/5c: run_with_stall_detection used for qemu-img convert FULL backup."""
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"), compress=False, verify="off", backup_stall_timeout="30m"
    )
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_running_full_expectations(mock_shell, target, compress=False)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0], stall_timeout=1800)

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"

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
def test_run_backup_stall_detection_output_file_is_tmp(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """5d: run_with_stall_detection output_file ends in .tmp."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_running_full_expectations(mock_shell, target, compress=False)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0], stall_timeout=1800)

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"

    convert_stall_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_stall_calls) >= 1

    output_file = convert_stall_calls[0].kwargs.get("output_file")
    assert output_file is not None
    assert ".tmp" in str(output_file), f"output_file should be a .tmp file, got: {output_file}"
    assert str(output_file).endswith(".tmp"), f"output_file should end in .tmp, got: {output_file}"


@pytest.mark.unit
def test_run_backup_stall_detection_timeout_from_target_config(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """5e: TargetConfig.backup_stall_timeout='5m' → stall_timeout=300 passed to run_with_stall_detection."""
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "backups"), compress=False, verify="off", backup_stall_timeout="5m"
    )
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_running_full_expectations(mock_shell, target, compress=False)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(
            vm_config,
            target,
            vm_config.disks[0],
            stall_timeout=300,  # 5m → 300s
        )

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"

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
def test_run_backup_first_full_via_qemu_img_convert(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """3a: No prior checkpoint; virsh backup-begin called with checkpoint XML;
    qemu-img convert executed (not pread/pwrite); no _start_write_server."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()

    # Register expectations from most-specific to least-specific
    _setup_running_full_expectations(mock_shell, target, compress=False)

    provider = BitmapBackupProvider(mock_shell)

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(BitmapBackupProvider, "_start_write_server") as mock_wserver,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"

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
    assert f"nbd:unix:/tmp/qsnap-backup-{pid}-vda.sock:exportname=vda" in convert_cmd

    # _start_write_server must NOT be called
    mock_wserver.assert_not_called()

    # FULL backups are freeze-ts named.
    assert re.fullmatch(
        r"testvm\.FULL\.\d{8}T\d{6}_vda_[0-9a-f]{6}\.qcow2",
        result.target_path.name,
    ), f"FULL filename must be freeze-ts named, got {result.target_path.name}"


# ── Tests: 3d, 3e — _full_pull_lifecycle shared helper ─────────────────────


@pytest.mark.unit
def test_run_backup_full_pull_lifecycle_uses_convert(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """3d: run_backup FULL delegates to _full_pull_lifecycle; qemu-img convert
    used inside the helper."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups_cfb"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_running_full_expectations(mock_shell, target, compress=False)

    with patch.object(
        BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 65536)
    ) as mock_fpl:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"
    assert mock_fpl.call_count == 1, (
        f"run_backup() FULL should call _full_pull_lifecycle once, got {mock_fpl.call_count}"
    )


@pytest.mark.unit
def test_run_backup_full_pull_lifecycle_no_write_server(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """3e: _start_write_server and _transfer NOT called inside _full_pull_lifecycle for FULL."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_running_full_expectations(mock_shell, target, compress=False)

    provider = BitmapBackupProvider(mock_shell)

    with (
        patch.object(BitmapBackupProvider, "_start_write_server") as mock_wserver,
        patch.object(BitmapBackupProvider, "_transfer") as mock_transfer,
    ):
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"
    mock_wserver.assert_not_called()
    mock_transfer.assert_not_called()


# ── Test: 2e — Global compress=false affects convert command ────────────────


@pytest.mark.unit
def test_run_backup_global_section_compress_false_affects_convert_cmd(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """2e: compress=False at global level propagates → qemu-img convert has no -c flag."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()

    _setup_running_full_expectations(mock_shell, target, compress=False)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"

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
    assert f"nbd:unix:/tmp/qsnap-backup-{pid}-vda.sock:exportname=vda" in convert_cmd


@pytest.mark.unit
def test_run_backup_custom_convert_parallel(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """run_backup with convert_parallel=2 uses -m 2 in qemu-img convert."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_running_full_expectations(mock_shell, target, compress=False)

    with patch.object(
        mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
    ) as stall_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0], convert_parallel=2)

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"

    convert_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_calls) == 1
    cmd = " ".join(convert_calls[0].args[0])
    assert "-m 2" in cmd, f"convert_parallel=2 should set -m 2, got: {cmd}"


@pytest.mark.unit
def test_stopped_vm_no_checkpoint_offline_full(mock_shell, make_vm_config, make_target, tmp_path):
    """Stopped VM with no checkpoint → offline FULL via direct convert; no
    checkpoint created; result named with the .FULL. freeze-ts infix."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)
    source_path = "/var/lib/libvirt/images/testvm.qcow2"

    _setup_stopped_full_expectations(mock_shell)

    provider = BitmapBackupProvider(mock_shell)

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "full", f"FULL convert path must report kind='full', got {result.kind!r}"
    assert result.deferred is False
    assert result.disk == "vda"
    assert result.checkpoint is None, "Offline FULL creates no checkpoint"

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 0, "Stopped VM should NOT call virsh backup-begin"

    convert_calls = [
        call for call in stall_spy.call_args_list if "qemu-img" in " ".join(call.args[0])
    ]
    assert len(convert_calls) == 1
    cmd = " ".join(convert_calls[0].args[0])
    assert source_path in cmd
    assert "nbd:unix:" not in cmd

    # Freeze-ts FULL naming.
    assert re.fullmatch(
        r"testvm\.FULL\.\d{8}T\d{6}_vda_[0-9a-f]{6}\.qcow2",
        result.target_path.name,
    ), f"Offline FULL filename must be freeze-ts named, got {result.target_path.name}"


@pytest.mark.unit
def test_convert_failure_deletes_partial_file_before_result(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Bitmap NBD convert failure does not leave a partial file — the failed
    FULL file is deleted immediately (rm -f, timeout=10) before the
    BackupResult is returned."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    convert_error = "qemu-img convert failed: I/O error"
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale socket + cleanups
    mock_shell.expect_first("virsh backup-begin").returns(success_result())
    mock_shell.expect_first("qemu-img convert").returns(
        ShellResult(
            success=False, stdout="", stderr=convert_error, returncode=1, error=convert_error
        )
    )
    mock_shell.expect("virsh domjobabort").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # successor best-effort

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.datetime") as mock_dt,
        patch(
            "qsnap.modules.backup.bitmap.secrets.token_hex",
            return_value="a1b2c3",
        ),
    ):
        mock_dt.now.return_value = datetime(2026, 8, 8, 3, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_dt.strptime = datetime.strptime
        mock_dt.min = datetime.min

        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert convert_error in (result.error or "")

    target_file = target.path / "testvm.FULL.20260808T030000_vda_a1b2c3.qcow2"
    partial_rm = [
        c for c in run_spy.call_args_list if " ".join(c.args[0]).endswith(" " + str(target_file))
    ]
    assert len(partial_rm) == 1, "Failed FULL file must be deleted immediately"
    assert partial_rm[0].kwargs.get("timeout") == 10, (
        "Partial-file cleanup must use the short fixed timeout (10s)"
    )
    # No final file may remain.
    assert not target_file.exists()
