"""Integration tests for Core.fork() — full pipeline through Core with MockShell.

Tests the fork pipeline: snapshot resolution → chain size estimation →
qemu-img convert (with retry) → verification (M1 virtual-size equality,
M2 qemu-img check) → RestoreResult.  Uses MockShell (no real virsh/qemu-img
calls) but exercises the full Core.fork() method end-to-end.

See OpenSpec change: fix-per-disk-isolation (fork-mode/spec.md).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.results import RestoreResult, ShellResult, SnapshotInfo
from tests.mocks import MockConfigFacade


def _make_snapshot(
    name: str = "snap1",
    path: str = "/snapshots/snap1.qcow2",
    timestamp: datetime | None = None,
    allocation: int = 1048576,
) -> SnapshotInfo:
    """Create a SnapshotInfo fixture for tests."""
    if timestamp is None:
        timestamp = datetime(2025, 7, 13, 10, 0)
    return SnapshotInfo(
        name=name,
        path=Path(path),
        timestamp=timestamp,
        allocation=allocation,
        disk="vda",
    )


def _setup_fork_shell(mock_shell, chain_size_json=None):
    """Configure MockShell expectations for Core.fork() happy path.

    Registers responses for:
      - qemu-img info --backing-chain --output=json (chain size estimate)
      - qemu-img convert --force-share -O qcow2 (standalone image)
      - qemu-img info --force-share --output=json (M1: virtual-size probe,
        called for both source and output by verify_standalone_image)
      - qemu-img check --output=json (M2: structural integrity)
    """
    if chain_size_json is None:
        chain_size_json = json.dumps(
            [
                {"filename": "/snapshots/snap1.qcow2", "actual-size": 1048576},
                {"filename": "/var/lib/libvirt/images/testvm.qcow2", "actual-size": 2097152},
            ]
        )
    # Chain size info (JSON list with actual-size per image)
    mock_shell.expect("backing-chain.*output=json").returns(
        ShellResult(success=True, stdout=chain_size_json, stderr="", returncode=0, error=None)
    )
    # Convert: standalone qcow2
    mock_shell.expect("convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # M1: virtual-size equality probes (source + output).  The pattern
    # must NOT match the backing-chain command (which has --backing-chain
    # and is already handled by the expectation above).  The \\b word
    # boundary after "info" ensures "qemu-img check" is not matched.
    mock_shell.expect(r"qemu-img info\b.*--force-share.*--output=json").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"virtual-size": 1048576}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # M2: structural integrity via qemu-img check
    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"errors": 0, "corruptions": 0}),
            stderr="",
            returncode=0,
            error=None,
        )
    )


def _add_rm_expectation(mock_shell):
    """Register a catch-all expectation for ``rm -f`` cleanup commands."""
    mock_shell.expect(r"rm\s+-f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )


# ── test_fork_full_pipeline_creates_standalone_qcow2 ────────────────────────


@pytest.mark.integration
def test_fork_full_pipeline_creates_standalone_qcow2(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """End-to-end: fork resolves snapshot, estimates chain size, converts to
    standalone qcow2, verifies it (M1+M2), and returns success."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))
    _setup_fork_shell(mock_shell)

    output_path = tmp_path / "output.qcow2"
    result = core.fork("snap1", output_path)

    assert isinstance(result, RestoreResult)
    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.restored_path == output_path
    assert output_path in result.chain_files
    assert result.error is None

    # Verify qemu-img convert was called
    convert_calls = [c for c in mock_shell.call_history if "convert" in c]
    assert len(convert_calls) >= 1, "qemu-img convert should have been called"


# ── test_fork_pipeline_with_vm_filter ───────────────────────────────────────


@pytest.mark.integration
def test_fork_pipeline_with_vm_filter(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """fork() with vm_filter resolves the correct VM's snapshot."""
    vm1 = make_vm_config(name="vm1")
    vm2 = make_vm_config(name="vm2")
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    snap1 = _make_snapshot("snap1", "/snapshots/vm1_snap1.qcow2")
    snap2 = _make_snapshot("snap1", "/snapshots/vm2_snap1.qcow2")
    mock_state.record_snapshot("vm1", snap1)
    mock_state.record_snapshot("vm2", snap2)

    _setup_fork_shell(mock_shell)

    output_path = tmp_path / "output.qcow2"
    result = core.fork("snap1", output_path, vm_filter="vm1")

    assert result.success is True
    assert result.snapshot_name == "snap1"

    # Verify the source path used for convert is vm1's snapshot
    convert_calls = [c for c in mock_shell.call_history if "convert" in c]
    assert len(convert_calls) >= 1
    convert_cmd = convert_calls[0]
    assert "vm1_snap1.qcow2" in convert_cmd, (
        f"Expected vm1's snapshot in convert command, got: {convert_cmd}"
    )


# ── test_fork_pipeline_nonexistent_snapshot ─────────────────────────────────


@pytest.mark.integration
def test_fork_pipeline_nonexistent_snapshot(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """fork() returns RestoreResult(success=False) when snapshot is not found."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # No snapshots in state, no backups in provider
    output_path = tmp_path / "output.qcow2"
    result = core.fork("nonexistent", output_path)

    assert isinstance(result, RestoreResult)
    assert result.success is False
    assert result.snapshot_name == "nonexistent"
    assert result.error is not None
    assert "not found" in result.error.lower()


# ── test_fork_pipeline_logs_chain_size ──────────────────────────────────────


@pytest.mark.integration
def test_fork_pipeline_logs_chain_size(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    caplog,
):
    """fork() runs qemu-img info --backing-chain and logs chain size before
    converting."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))

    chain_json = json.dumps(
        [
            {"filename": "/snapshots/snap1.qcow2", "actual-size": 1048576},
            {"filename": "/var/lib/libvirt/images/testvm.qcow2", "actual-size": 2097152},
        ]
    )
    _setup_fork_shell(mock_shell, chain_size_json=chain_json)

    caplog.set_level(logging.INFO)
    output_path = tmp_path / "output.qcow2"
    result = core.fork("snap1", output_path)

    assert result.success is True

    # Verify qemu-img info --backing-chain was called with --force-share
    backing_calls = [c for c in mock_shell.call_history if "backing-chain" in c]
    assert len(backing_calls) >= 1, "qemu-img info --backing-chain should have been called"
    backing_cmd = backing_calls[0]
    assert "--force-share" in backing_cmd, (
        "qemu-img info --backing-chain must include --force-share"
    )

    # Verify chain size log message
    assert "chain size" in caplog.text.lower() or "Converting snapshot" in caplog.text, (
        "Expected chain size log message, got: " + caplog.text
    )

    # Verify ordering: backing-chain call before convert call
    backing_idx = None
    convert_idx = None
    for i, cmd in enumerate(mock_shell.call_history):
        if "backing-chain" in cmd:
            backing_idx = i
        if "convert" in cmd and "backing-chain" not in cmd:
            convert_idx = i
    if backing_idx is not None and convert_idx is not None:
        assert backing_idx < convert_idx, (
            "qemu-img info --backing-chain must run before qemu-img convert"
        )


# ── test_fork_from_incremental_flattens_chain ───────────────────────────────


@pytest.mark.integration
def test_fork_from_incremental_flattens_chain(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """fork() from an incremental backup converts to standalone qcow2
    (no backing file).

    Covers both "from backup target" and "flattens chain" scenarios:
    Create FULL + 2 incrementals, fork the incremental, verify standalone
    result (no backing file, --force-share -O qcow2).
    """
    target = make_target(path=str(tmp_path / "backups"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    full_name = "testvm.FULL.20250713T080000"
    inc1_name = "testvm.20250713T090000"
    inc2_name = "testvm.20250713T100000"
    target_path = str(target.path)

    # Record FULL + 2 incrementals in state
    mock_state.record_full_backup(
        target_path,
        full_name,
        datetime(2025, 7, 13, 8, 0),
        disk="vda",
    )
    mock_state.record_incremental_dependency(target_path, inc1_name, full_name)
    mock_state.record_incremental_dependency(target_path, inc2_name, full_name)

    # The backup provider returns the latest incremental
    inc2_info = SnapshotInfo(
        name=inc2_name,
        path=target.path / f"{inc2_name}.qcow2",
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=524288,
        disk="vda",
    )

    _setup_fork_shell(mock_shell)

    output_path = tmp_path / "standalone.qcow2"

    with patch.object(mock_factory._backup_provider, "list", return_value=[inc2_info]):
        result = core.fork(inc2_name, output_path)

    assert result.success is True
    assert result.snapshot_name == inc2_name
    assert result.restored_path == output_path
    assert output_path in result.chain_files
    assert result.error is None

    # Verify convert flattens the chain (uses --force-share -O qcow2)
    convert_calls = [c for c in mock_shell.call_history if "convert" in c]
    assert len(convert_calls) >= 1, "qemu-img convert should have been called"
    convert_cmd = convert_calls[0]
    assert "--force-share" in convert_cmd, "convert must use --force-share"
    assert "-O" in convert_cmd and "qcow2" in convert_cmd, "convert must output qcow2 format"
    assert str(inc2_info.path) in convert_cmd, "convert must use the incremental backup as source"

    # The result is standalone (single chain file, no backing dependencies)
    assert len(result.chain_files) == 1
    assert output_path in result.chain_files


# ── test_fork_verifies_output_after_convert ─────────────────────────────────


@pytest.mark.integration
def test_fork_verifies_output_after_convert(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """fork() runs verify_standalone_image (M1 virtual-size + M2 qemu-img check)
    after a successful conversion and returns success when both pass."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))
    _setup_fork_shell(mock_shell)

    output_path = tmp_path / "output.qcow2"
    result = core.fork("snap1", output_path)

    assert result.success is True

    # Verify that M1 probes were called (qemu-img info without --backing-chain)
    info_calls = [
        c for c in mock_shell.call_history if "qemu-img info" in c and "backing-chain" not in c
    ]
    assert len(info_calls) >= 2, (
        f"verify_standalone_image M1 should probe source + output virtual-size, "
        f"got {len(info_calls)} info calls: {info_calls}"
    )

    # Verify that M2 qemu-img check was called
    check_calls = [c for c in mock_shell.call_history if "qemu-img check" in c]
    assert len(check_calls) >= 1, "verify_standalone_image M2 should run qemu-img check"

    # Ordering: info calls must come before check call
    last_info_idx = max(
        i
        for i, cmd in enumerate(mock_shell.call_history)
        if "qemu-img info" in cmd and "backing-chain" not in cmd
    )
    first_check_idx = min(
        i for i, cmd in enumerate(mock_shell.call_history) if "qemu-img check" in cmd
    )
    assert last_info_idx < first_check_idx, "M1 (info) must run before M2 (check)"


# ── test_fork_removes_output_on_verify_failure ──────────────────────────────


@pytest.mark.integration
def test_fork_removes_output_on_verify_failure(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """fork() removes the output file and returns failure when
    verify_standalone_image reports an error (M2 check failures)."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))

    # Set up normal chain-size + convert + M1 expectations
    _setup_fork_shell(mock_shell)

    # Override M2 check to return errors (use expect_first to prepend)
    _add_rm_expectation(mock_shell)
    mock_shell.expect_first("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"errors": 2, "corruptions": 1}),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    output_path = tmp_path / "output.qcow2"
    # Create a dummy file to simulate a converted file that must be cleaned up
    output_path.write_text("dummy content")

    result = core.fork("snap1", output_path)

    assert result.success is False
    assert result.snapshot_name == "snap1"
    assert result.error is not None
    assert "verification failed" in result.error, (
        f"Expected verification failure, got: {result.error}"
    )

    # Verify rm -f was issued for the output path (MockShell records
    # commands but does not execute them, so physical file existence is
    # irrelevant; what matters is that Core attempted cleanup).
    rm_calls = [c for c in mock_shell.call_history if "rm" in c and "-f" in c]
    assert len(rm_calls) >= 1, f"rm -f should have been called, got: {rm_calls}"
    assert str(output_path) in rm_calls[0], (
        f"rm -f should target the output path, got: {rm_calls[0]}"
    )


# ── test_fork_removes_partial_output_on_convert_failure ─────────────────────


@pytest.mark.integration
def test_fork_removes_partial_output_on_convert_failure(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """fork() removes partial output and returns failure when qemu-img convert
    fails (after retries are exhausted)."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))

    # Set up normal chain-size expectation
    _setup_fork_shell(mock_shell)

    # Override convert to fail (use expect_first to prepend)
    mock_shell.expect_first("convert").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="disk full",
            returncode=1,
            error="disk full",
        )
    )
    _add_rm_expectation(mock_shell)

    output_path = tmp_path / "output.qcow2"
    # Create a dummy file to simulate partial output from a failed convert
    output_path.write_text("partial content")

    result = core.fork("snap1", output_path)

    assert result.success is False
    assert result.error is not None
    assert "image conversion failed" in result.error, (
        f"Expected conversion failure, got: {result.error}"
    )

    # Verify rm -f was issued (MockShell records commands but does not
    # execute them; the test verifies Core attempted cleanup).
    rm_calls = [c for c in mock_shell.call_history if "rm" in c and "-f" in c]
    assert len(rm_calls) >= 1, f"rm -f should have been called, got: {rm_calls}"


# ── test_fork_dry_run_no_file ───────────────────────────────────────────────


@pytest.mark.integration
def test_fork_dry_run_no_file(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    caplog,
):
    """fork() with dry_run=True logs the plan and returns success without
    creating any output file or running qemu-img convert."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))
    _setup_fork_shell(mock_shell)

    core.dry_run = True
    caplog.set_level(logging.INFO)

    output_path = tmp_path / "output.qcow2"
    result = core.fork("snap1", output_path)

    # Must return success (dry-run is a valid no-op)
    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.restored_path == output_path
    # chain_files is empty in dry-run (no file created)
    assert len(result.chain_files) == 0
    assert result.error is None

    # No output file must exist
    assert not output_path.exists(), "Dry-run must not create an output file"

    # No qemu-img convert must be called
    convert_calls = [c for c in mock_shell.call_history if "convert" in c]
    assert len(convert_calls) == 0, f"Dry-run must not run qemu-img convert, got: {convert_calls}"

    # Ensure the dry-run log message was emitted
    assert "[dry-run]" in caplog.text.lower(), f"Expected [dry-run] log message, got: {caplog.text}"
    assert "Would convert" in caplog.text, (
        f"Expected 'Would convert' in dry-run log, got: {caplog.text}"
    )
    # Chain-size estimate should still appear (read-only)
    assert "chain size" in caplog.text.lower() or "Converting snapshot" in caplog.text, (
        "Chain-size estimate should still run in dry-run mode"
    )

    # No verify calls in dry-run mode
    check_calls = [c for c in mock_shell.call_history if "qemu-img check" in c]
    assert len(check_calls) == 0, "verify_standalone_image must not run in dry-run mode"
