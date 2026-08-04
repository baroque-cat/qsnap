"""Integration tests for Core.restore() — full pipeline through Core with MockShell.

Tests the restore pipeline: snapshot resolution → VM-stopped check →
chain integrity pre-verify → qemu-img convert → old overlay cleanup →
atomic base-image replace → XML refresh → state reset → checkpoint cleanup.

Uses MockShell (no real virsh/qemu-img calls) but exercises the full
Core.restore() method end-to-end.
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


def _setup_restore_shell(mock_shell, snapshot_path="/snapshots/snap1.qcow2"):
    """Configure MockShell expectations for Core.restore() happy path.

    Registers responses for:
      - virsh dominfo (VM-stopped check) — overrides conftest default
      - test -f (file existence checks in scan_backing_chain — conftest default suffices)
      - qemu-img info --backing-chain (chain integrity pre-verify)
      - qemu-img convert (standalone image to tmp)
      - rm -f (old overlay cleanup)
      - virsh dumpxml (domain XML refresh, called twice)
      - virsh define (re-define the domain, called twice)
    """
    # Override conftest default: VM is shut off (restore requires stopped VM)
    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Chain integrity pre-verify: valid non-broken chain
    chain_json = json.dumps([
        {"filename": snapshot_path, "format": "qcow2", "virtual-size": 10737418240},
        {"filename": "/var/lib/libvirt/images/testvm.qcow2", "format": "qcow2", "virtual-size": 21474836480},
    ])
    mock_shell.expect_first("qemu-img info --force-share --backing-chain").returns(
        ShellResult(success=True, stdout=chain_json, stderr="", returncode=0, error=None)
    )
    # qemu-img convert to tmp
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # rm -f old overlays
    mock_shell.expect(r"rm.*-f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # virsh dumpxml — used by _refresh_domain_backing_store AND restore source update
    # Return XML without backingStore so _refresh_domain_backing_store returns early
    dumpxml = (
        '<domain type="kvm">'
        "<name>testvm</name>"
        "<devices>"
        '<disk type="file" device="disk">'
        '<source file="/var/lib/libvirt/images/testvm.qcow2"/>'
        '<target dev="vda"/>'
        "</disk>"
        "</devices>"
        "</domain>"
    )
    mock_shell.expect("virsh dumpxml").returns(
        ShellResult(success=True, stdout=dumpxml, stderr="", returncode=0, error=None)
    )
    # virsh define — called by restore for source file update
    mock_shell.expect("virsh define").returns(
        ShellResult(success=True, stdout="Domain testvm defined", stderr="", returncode=0, error=None)
    )


# ── test_restore_full_pipeline_replaces_disk ────────────────────────────────


@pytest.mark.integration
def test_restore_full_pipeline_replaces_disk(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """End-to-end: restore resolves snapshot, converts to standalone, replaces base image."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))
    _setup_restore_shell(mock_shell)

    # scan_backing_chain uses shell.run(["test", "-f", ...]) for file existence
    # (already mocked by conftest).  os.replace needs patching at the core module.
    with patch("qsnap.core.os.replace"):
        result = core.restore("snap1")

    assert isinstance(result, RestoreResult)
    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.restored_path == vm.disks[0].base_image
    assert vm.disks[0].base_image in result.chain_files
    assert result.error is None

    # Verify qemu-img convert was called
    convert_calls = [c for c in mock_shell.call_history if "convert" in c]
    assert len(convert_calls) >= 1, "qemu-img convert should have been called"

    # Verify virsh dumpxml and define were called (XML refresh + source file update)
    dumpxml_calls = [c for c in mock_shell.call_history if "dumpxml" in c]
    assert len(dumpxml_calls) >= 1, "virsh dumpxml should have been called"

    define_calls = [c for c in mock_shell.call_history if "virsh define" in c]
    assert len(define_calls) >= 1, "virsh define should have been called"


# ── test_restore_pipeline_vm_running_fails ──────────────────────────────────


@pytest.mark.integration
def test_restore_pipeline_vm_running_fails(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() returns RestoreResult(success=False) when VM is running."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))

    # Conftest default for virsh dominfo is "running" — that's what we want here.
    result = core.restore("snap1")

    assert isinstance(result, RestoreResult)
    assert result.success is False
    assert result.error is not None
    assert "must be stopped" in result.error.lower()


# ── test_restore_pipeline_dry_run ───────────────────────────────────────────


@pytest.mark.integration
def test_restore_pipeline_dry_run(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """restore() in dry-run mode shows planned actions without making changes."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    core.dry_run = True

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))

    # VM must be shut off for dry-run to proceed past the VM check.
    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Provide valid chain for pre-verify
    chain_json = json.dumps([
        {"filename": "/snapshots/snap1.qcow2", "format": "qcow2"},
        {"filename": "/var/lib/libvirt/images/testvm.qcow2", "format": "qcow2"},
    ])
    mock_shell.expect_first("qemu-img info --force-share --backing-chain").returns(
        ShellResult(success=True, stdout=chain_json, stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.INFO)

    result = core.restore("snap1")

    assert result.success is True
    assert result.snapshot_name == "snap1"

    # Verify dry-run log messages
    assert "[dry-run]" in caplog.text, "Expected dry-run log messages"

    # Verify no convert was actually executed
    convert_calls = [c for c in mock_shell.call_history if "convert" in c]
    assert len(convert_calls) == 0, (
        "qemu-img convert should NOT be called in dry-run mode"
    )


# ── test_restore_pipeline_resets_state ──────────────────────────────────────


@pytest.mark.integration
def test_restore_pipeline_resets_state(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """restore() calls reset_vm_state() and reset_target_state() after disk replacement."""
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))

    # Pre-populate state: full backups, deps, allocation baselines
    target_path = str(target.path)
    mock_state.record_full_backup(target_path, "testvm.FULL.20250713.qcow2", datetime(2025, 7, 13, 8, 0), disk="vda")
    mock_state.record_incremental_dependency(target_path, "snap1_inc.qcow2", "testvm.FULL.20250713.qcow2")
    mock_state.set_last_backup_allocation(target_path, "vda", 1048576)
    mock_state.set_last_allocation("testvm", "vda", 2097152)

    # Verify state is populated before restore
    assert len(mock_state.get_full_backups(target_path)) == 1
    assert len(mock_state.get_incremental_dependencies(target_path, "testvm.FULL.20250713.qcow2")) == 1
    assert mock_state.get_last_backup_allocation(target_path, "vda") == 1048576
    assert mock_state.get_last_allocation("testvm", "vda") == 2097152
    assert len(mock_state.get_snapshots("testvm")) == 1

    _setup_restore_shell(mock_shell)

    with patch("qsnap.core.os.replace"):
        result = core.restore("snap1")

    assert result.success is True

    # Verify VM state was reset
    snapshots_after = mock_state.get_snapshots("testvm")
    assert snapshots_after == [], f"Expected empty snapshots, got {snapshots_after}"

    # Verify target state was reset
    fulls_after = mock_state.get_full_backups(target_path)
    assert fulls_after == [], f"Expected empty full backups, got {fulls_after}"

    deps_after = mock_state.get_incremental_dependencies(target_path, "testvm.FULL.20250713.qcow2")
    assert deps_after == [], f"Expected empty deps, got {deps_after}"

    # Verify last_allocation was reset
    assert mock_state.get_last_allocation("testvm", "vda") is None


# ── test_restore_pipeline_strips_backing_store ──────────────────────────────


@pytest.mark.integration
def test_restore_pipeline_strips_backing_store(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() updates domain XML with correct source file and calls virsh define."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))
    _setup_restore_shell(mock_shell)

    with patch("qsnap.core.os.replace"):
        result = core.restore("snap1")

    assert result.success is True

    # Verify virsh define was called (for the source file update)
    define_calls = [c for c in mock_shell.call_history if "virsh define" in c]
    assert len(define_calls) >= 1, "virsh define should be called for domain XML update"

    # Verify the restored_path points to the base image
    assert result.restored_path == vm.disks[0].base_image


# ── test_restore_cleanup_libvirt_checkpoints ────────────────────────────────


@pytest.mark.integration
def test_restore_cleanup_libvirt_checkpoints(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() cleans up qsnap-* libvirt checkpoints after disk replacement.

    Scenario 57: Verify qsnap-* checkpoints deleted after restore.
    checkpoint-delete is called for each qsnap-* checkpoint, and
    restore still succeeds even when a deletion fails.
    """
    target = make_target(path="/backups/testvm")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))
    _setup_restore_shell(mock_shell)

    # Mock list_checkpoints to return qsnap-* checkpoints
    checkpoints = ["qsnap-abc12345-snap1", "qsnap-abc12345-snap2"]

    # Expect checkpoint-delete calls — first one succeeds, second fails
    # (use expect_first so each matches its specific checkpoint name)
    mock_shell.expect("checkpoint-delete.*snap1").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("checkpoint-delete.*snap2").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: operation failed",
            returncode=1,
            error="operation failed",
        )
    )

    with (
        patch.object(
            mock_factory._backup_provider,
            "list_checkpoints",
            return_value=checkpoints,
        ),
        patch("qsnap.core.os.replace"),
    ):
        result = core.restore("snap1")

    # Restore must still succeed despite checkpoint deletion failure
    assert result.success is True
    assert result.snapshot_name == "snap1"

    # Verify checkpoint-delete was called for each checkpoint
    checkpoint_calls = [
        c for c in mock_shell.call_history if "checkpoint-delete" in c
    ]
    assert len(checkpoint_calls) == 2, (
        f"Expected 2 checkpoint-delete calls, got {len(checkpoint_calls)}"
    )
    assert any("snap1" in c for c in checkpoint_calls), (
        "checkpoint-delete must target snap1"
    )
    assert any("snap2" in c for c in checkpoint_calls), (
        "checkpoint-delete must target snap2"
    )


# ── test_restore_prechecks_chain_integrity ──────────────────────────────────


@pytest.mark.integration
def test_restore_prechecks_chain_integrity(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() aborts before modification when source backing chain is broken.

    Scenario 61: Create broken chain, verify restore aborts before modification.
    No qemu-img convert, no state reset, no file modification.
    """
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))

    # VM is shut off
    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Override chain scan to return a broken chain
    from qsnap.models.results import ChainScanResult

    with patch(
        "qsnap.core.scan_backing_chain",
        return_value=ChainScanResult(
            paths=set(),
            broken_files=["/snapshots/snap1.qcow2"],
            success=False,
            error="file not found in backing chain",
        ),
    ):
        result = core.restore("snap1")

    assert result.success is False
    assert result.error is not None
    assert "backing chain is broken" in result.error.lower(), (
        f"Expected 'backing chain is broken' in error, got: {result.error}"
    )

    # Verify NO qemu-img convert was called
    convert_calls = [
        c for c in mock_shell.call_history if "qemu-img convert" in c
    ]
    assert len(convert_calls) == 0, (
        "qemu-img convert should NOT be called when chain is broken"
    )

    # Verify NO state was reset (snapshots still present)
    snapshots_after = mock_state.get_snapshots("testvm")
    assert len(snapshots_after) == 1, (
        f"State should not be modified, got {len(snapshots_after)} snapshots"
    )

    # Verify NO rm commands were issued
    rm_calls = [c for c in mock_shell.call_history if "rm -f" in c]
    assert len(rm_calls) == 0, (
        "No files should be deleted when chain is broken"
    )
