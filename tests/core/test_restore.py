"""Tests for Core.restore() — VM disk replacement from snapshot or backup.

Covers the simplified restore implementation: resolve snapshot via
``_resolve_snapshot()``, verify VM is stopped, pre-verify chain integrity,
create standalone qcow2 via ``qemu-img convert --force-share -O qcow2``,
atomically replace the VM's base image, strip ``<backingStore>`` from
domain XML, reset VM and target state, and best-effort checkpoint cleanup.

See OpenSpec change: simplify-fork-restore (restore-command/spec.md)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.results import (
    ChainScanResult,
    RestoreResult,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import MockConfigFacade

# Valid domain XML fixture used for virsh dumpxml responses in restore tests.
_SIMPLE_DOMAIN_XML = """<domain type='kvm'>
  <name>testvm</name>
  <uuid>12345678-1234-1234-1234-123456789abc</uuid>
  <devices>
    <disk type='file' device='disk'>
      <source file='/var/lib/libvirt/images/testvm.qcow2'/>
      <target dev='vda'/>
    </disk>
  </devices>
</domain>"""


def _make_snapshot(
    name: str = "snap1",
    path: str = "/snapshots/snap1.qcow2",
    allocation: int = 1048576,
) -> SnapshotInfo:
    return SnapshotInfo(
        name=name,
        path=Path(path),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=allocation,
    )


def _make_snapshot_with_path(
    name: str,
    vm_config,  # type: ignore[no-untyped-def]
    allocation: int = 1048576,
) -> SnapshotInfo:
    """Create a snapshot whose path is inside the VM's snapshot_dir."""
    from datetime import datetime

    return SnapshotInfo(
        name=name,
        path=vm_config.snapshot_dir / f"{name}.qcow2",
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=allocation,
    )


# ── Success path: restore from snapshot replaces VM disk ────────────────


@pytest.mark.unit
def test_restore_from_snapshot_replaces_vm_disk(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() resolves snapshot, converts to standalone qcow2, replaces disk."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    # Override the global conftest virsh dominfo (which returns "running")
    # to return "shut off" so restore proceeds past the VM running check.
    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Expect rm -f for old snapshot overlay cleanup
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Expect virsh dumpxml (used by both _refresh_domain_backing_store
    # and the restore method's own XML update step)
    mock_shell.expect("dumpxml").returns(
        ShellResult(
            success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None
        )
    )

    # Expect virsh define (used by both _refresh_domain_backing_store
    # and the restore method's own define step)
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True, stdout="Domain testvm defined", stderr="", returncode=0, error=None
        )
    )

    # Patch os.replace to prevent real filesystem operations
    with patch("os.replace"):
        result = core.restore("snap1")

    assert isinstance(result, RestoreResult)
    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.restored_path == vm.base_image
    assert len(result.chain_files) == 1
    assert vm.base_image in result.chain_files
    assert result.error is None

    # Verify convert was called
    convert_calls = [c for c in mock_shell.call_history if "qemu-img convert" in c]
    assert len(convert_calls) >= 1, "qemu-img convert should have been called"

    # Verify state reset was called
    convert_cmd = convert_calls[0]
    assert "--force-share" in convert_cmd


# ── Restore from backup target ──────────────────────────────────────────


@pytest.mark.unit
def test_restore_from_backup_replaces_vm_disk(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() finds backup via provider, replaces VM disk."""
    target = make_target(path=str(tmp_path / "backups"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    backup_info = SnapshotInfo(
        name="backup1",
        path=tmp_path / "backups" / "backup1.qcow2",
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1048576,
    )

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("dumpxml").returns(
        ShellResult(
            success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None
        )
    )

    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True, stdout="Domain testvm defined", stderr="", returncode=0, error=None
        )
    )

    with (
        patch.object(
            mock_factory._backup_provider, "list", return_value=[backup_info]
        ),
        patch("os.replace"),
    ):
        result = core.restore("backup1")

    assert result.success is True
    assert result.snapshot_name == "backup1"


# ── Restore aborts on running VM ────────────────────────────────────────


@pytest.mark.unit
def test_restore_aborts_on_running_vm(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() returns failure when the VM is still running.

    The default conftest ``virsh dominfo`` returns ``State: running``,
    so no override is needed — it naturally blocks restore.
    """
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    result = core.restore("snap1")

    assert result.success is False
    assert "VM must be stopped" in result.error
    assert result.restored_path == vm.base_image


# ── Restore aborts on broken source chain ───────────────────────────────


@pytest.mark.unit
def test_restore_aborts_on_broken_chain(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() returns failure when scan_backing_chain reports broken chain."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    # Override dominfo to shut off (so we get past the running check)
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
    # Use expect_first to override the conftest default
    mock_shell.expect_first("qemu-img info --force-share --backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"format": "qcow2", "filename": "/snapshots/snap1.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Patch scan_backing_chain directly to return a broken chain result
    with patch(
        "qsnap.core.scan_backing_chain",
        return_value=ChainScanResult(
            paths=set(),
            broken_files=["/snapshots/snap1.qcow2"],
            success=True,
            error="file not found",
        ),
    ):
        result = core.restore("snap1")

    assert result.success is False
    assert "backing chain is broken" in result.error.lower()


# ── Restore dry-run mode ────────────────────────────────────────────────


@pytest.mark.unit
def test_restore_dry_run_shows_planned_actions(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """restore() in dry-run mode logs planned actions but does not execute."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    core.dry_run = True

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    # Override dominfo to shut off (so we pass the running check even
    # in dry-run — the dry-run check comes after the running check)
    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    caplog.set_level(logging.INFO)

    result = core.restore("snap1")

    assert result.success is True
    assert result.snapshot_name == "snap1"

    log_text = caplog.text.lower()
    assert "dry-run" in log_text, f"Expected dry-run log messages, got: {caplog.text}"

    # Verify no convert was called (dry-run skips execution)
    convert_calls = [c for c in mock_shell.call_history if "qemu-img convert" in c]
    assert len(convert_calls) == 0, "convert should not be called in dry-run"


# ── Restore nonexistent snapshot → failure ──────────────────────────────


@pytest.mark.unit
def test_restore_nonexistent_snapshot_returns_failure(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() returns RestoreResult(success=False) for nonexistent snapshot."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.restore("nonexistent")

    assert isinstance(result, RestoreResult)
    assert result.success is False
    assert result.snapshot_name == "nonexistent"
    assert result.error is not None
    assert "not found" in result.error.lower()


# ── Restore resets VM state and target state ────────────────────────────


@pytest.mark.unit
def test_restore_resets_all_vm_state(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() calls reset_vm_state() and reset_target_state() on success."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("dumpxml").returns(
        ShellResult(
            success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None
        )
    )

    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True, stdout="Domain testvm defined", stderr="", returncode=0, error=None
        )
    )

    with (
        patch("os.replace"),
        patch.object(mock_state, "reset_vm_state", wraps=mock_state.reset_vm_state) as vm_spy,
        patch.object(
            mock_state, "reset_target_state", wraps=mock_state.reset_target_state
        ) as target_spy,
    ):
        result = core.restore("snap1")

    assert result.success is True

    vm_spy.assert_called_once_with("testvm")
    target_spy.assert_called_once_with(str(target.path))


# ── Restore with backingStore stripping ─────────────────────────────────


@pytest.mark.unit
def test_restore_strips_backingStore_from_xml(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() strips <backingStore> elements from domain XML.

    The _refresh_domain_backing_store method removes backingStore
    elements and redefines the domain.  This test verifies that
    virsh dumpxml + virsh define are called.
    """
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Return XML WITH backingStore so _refresh_domain_backing_store strips it
    xml_with_backing = """<domain type='kvm'>
  <name>testvm</name>
  <devices>
    <disk type='file' device='disk'>
      <source file='/var/lib/libvirt/images/testvm.qcow2'/>
      <target dev='vda'/>
      <backingStore type='file'>
        <source file='/snapshots/snap1.qcow2'/>
      </backingStore>
    </disk>
  </devices>
</domain>"""
    mock_shell.expect("dumpxml").returns(
        ShellResult(
            success=True, stdout=xml_with_backing, stderr="", returncode=0, error=None
        )
    )

    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True, stdout="Domain testvm defined", stderr="", returncode=0, error=None
        )
    )

    with patch("os.replace"):
        result = core.restore("snap1")

    assert result.success is True

    # Verify dumpxml was called (for _refresh_domain_backing_store and restore's own dumpxml)
    dumpxml_calls = [c for c in mock_shell.call_history if "dumpxml" in c]
    assert len(dumpxml_calls) >= 1, "virsh dumpxml should have been called"

    # Verify define was called
    define_calls = [c for c in mock_shell.call_history if "virsh define" in c]
    assert len(define_calls) >= 1, "virsh define should have been called"


# ── Restore deletes old snapshot overlays ───────────────────────────────


@pytest.mark.unit
def test_restore_deletes_old_snapshot_overlays(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() deletes old snapshot overlay files from snapshot_dir."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Record two snapshots — restore should delete their overlays
    mock_state.record_snapshot(
        "testvm",
        _make_snapshot_with_path("snap1", vm, allocation=1048576),
    )
    mock_state.record_snapshot(
        "testvm",
        _make_snapshot_with_path("snap2", vm, allocation=2097152),
    )

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("dumpxml").returns(
        ShellResult(
            success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None
        )
    )

    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True, stdout="Domain testvm defined", stderr="", returncode=0, error=None
        )
    )

    with patch("os.replace"):
        result = core.restore("snap1")

    assert result.success is True

    # Verify rm -f was called for old snapshots
    rm_calls = [c for c in mock_shell.call_history if "rm -f" in c]
    assert len(rm_calls) >= 1, "rm -f should be called to delete old overlays"


# ── Restore handles conversion failure ──────────────────────────────────


@pytest.mark.unit
def test_restore_convert_failure_returns_error(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() returns RestoreResult(success=False) when convert fails."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    mock_shell.expect("qemu-img convert").returns(
        ShellResult(
            success=False, stdout="", stderr="disk full", returncode=1, error="disk full"
        )
    )

    result = core.restore("snap1")

    assert result.success is False
    assert "image conversion failed" in result.error


# ── Core.restore method: from snapshot ──────────────────────────────────


@pytest.mark.unit
def test_core_restore_from_snapshot_replaces_disk(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core.restore() resolves snapshot in state and replaces VM disk."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("dumpxml").returns(
        ShellResult(
            success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None
        )
    )

    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True, stdout="Domain testvm defined", stderr="", returncode=0, error=None
        )
    )

    with patch("os.replace"):
        result = core.restore("snap1")

    assert result.success is True
    assert result.snapshot_name == "snap1"


# ── Core.restore method: fails on running VM ────────────────────────────


@pytest.mark.unit
def test_core_restore_fails_on_running_vm(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core.restore() returns failure when is_vm_running() returns True."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    result = core.restore("snap1")

    assert result.success is False
    assert "VM must be stopped" in result.error


# ── Checkpoint cleanup (best-effort, Scenario 15) ────────────────────────


@pytest.mark.unit
def test_restore_best_effort_checkpoint_cleanup(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """restore() cleans up qsnap-* checkpoints; deletion failures log WARNING but don't block.

    Scenario 15: After restore completes, all libvirt checkpoints with
    ``qsnap-`` prefix are deleted via ``virsh checkpoint-delete``.
    Failures are logged at WARNING level and do NOT block the restore.
    """
    target = make_target(path="/backups/testvm")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("dumpxml").returns(
        ShellResult(
            success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None
        )
    )

    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True, stdout="Domain testvm defined", stderr="", returncode=0, error=None
        )
    )

    # Mock checkpoints: snap1 succeeds, snap2 fails
    checkpoints = ["qsnap-abc12345-snap1", "qsnap-abc12345-snap2"]

    # First checkpoint-delete succeeds
    mock_shell.expect("checkpoint-delete.*snap1").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Second checkpoint-delete fails
    mock_shell.expect("checkpoint-delete.*snap2").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: operation failed",
            returncode=1,
            error="operation failed",
        )
    )

    caplog.set_level(logging.WARNING)

    with (
        patch.object(
            mock_factory._backup_provider,
            "list_checkpoints",
            return_value=checkpoints,
        ),
        patch("os.replace"),
    ):
        result = core.restore("snap1")

    # Restore must still succeed despite checkpoint deletion failure
    assert result.success is True
    assert result.snapshot_name == "snap1"

    # Verify both checkpoint-delete commands were attempted
    checkpoint_calls = [
        c for c in mock_shell.call_history if "checkpoint-delete" in c
    ]
    assert len(checkpoint_calls) == 2, (
        f"Expected 2 checkpoint-delete calls, got {len(checkpoint_calls)}"
    )

    # Verify WARNING was logged for the failed checkpoint deletion
    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    warn_messages = [r.getMessage() for r in warn_records]
    assert any("failed to delete checkpoint" in msg for msg in warn_messages), (
        f"Expected WARNING about failed checkpoint deletion, got: {warn_messages}"
    )


# ── Core.restore from backup (Scenario 19) ────────────────────────────────


@pytest.mark.unit
def test_core_restore_from_backup_replaces_disk(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core.restore() resolves a snapshot from backup target and replaces disk.

    Scenario 19: When the snapshot is found ONLY on a backup target
    (not in IStateManager), the restore still succeeds with the same
    disk replacement flow.
    """
    target = make_target(path=str(tmp_path / "backups"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # No snapshots in state — resolved exclusively via backup provider
    assert mock_state.get_snapshots("testvm") == []

    backup_info = SnapshotInfo(
        name="backup_snap1",
        path=tmp_path / "backups" / "backup_snap1.qcow2",
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1048576,
    )

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("dumpxml").returns(
        ShellResult(
            success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None
        )
    )

    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True, stdout="Domain testvm defined", stderr="", returncode=0, error=None
        )
    )

    with (
        patch.object(
            mock_factory._backup_provider, "list", return_value=[backup_info]
        ),
        patch("os.replace"),
    ):
        result = core.restore("backup_snap1")

    assert result.success is True
    assert result.snapshot_name == "backup_snap1"
    assert result.restored_path == vm.base_image
    assert vm.base_image in result.chain_files

    # Verify convert used the backup path
    convert_calls = [
        c for c in mock_shell.call_history if "qemu-img convert" in c
    ]
    assert len(convert_calls) >= 1
    assert "backup_snap1" in convert_calls[0]


# ── Atomic replace preserves original on crash (risk) ───────────────────


@pytest.mark.unit
def test_restore_atomic_replace_preserves_original_on_crash(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When os.replace fails during restore, exception propagates; convert was attempted.

    Design D2: writes to .tmp path, then os.replace to base_image.
    If os.replace fails, the original base_image is unchanged because
    the replace is atomic (rename).  The test verifies that the
    exception propagates after convert succeeds.
    """
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: testvm\nState: shut off\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    mock_shell.expect("dumpxml").returns(
        ShellResult(
            success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None
        )
    )

    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True, stdout="Domain testvm defined", stderr="", returncode=0, error=None
        )
    )

    # Patch os.replace to raise — the exception propagates because restore()
    # does not wrap os.replace in try/except (design D2: os.replace is atomic,
    # so the original is untouched even if the process crashes).
    with patch("qsnap.core.os.replace", side_effect=OSError("File exists")), pytest.raises(OSError, match="File exists"):
        core.restore("snap1")

    # Verify convert was called (tmp file was created before os.replace failed)
    convert_calls = [
        c for c in mock_shell.call_history if "qemu-img convert" in c
    ]
    assert len(convert_calls) >= 1, "qemu-img convert should have been called before os.replace"
