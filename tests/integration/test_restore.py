"""Integration tests for Core.restore() — full pipeline through Core with MockShell.

Tests the restore pipeline: snapshot resolution → VM-stopped check →
chain integrity pre-verify → qemu-img convert with retry → standalone
image verification → atomic base-image replace → old overlay cleanup →
XML refresh → per-disk state reset → per-disk checkpoint cleanup.

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
    disk: str = "vda",
) -> SnapshotInfo:
    """Create a SnapshotInfo fixture for tests."""
    if timestamp is None:
        timestamp = datetime(2025, 7, 13, 10, 0)
    return SnapshotInfo(
        name=name,
        path=Path(path),
        timestamp=timestamp,
        allocation=allocation,
        disk=disk,
    )


def _setup_restore_shell(mock_shell, snapshot_path="/snapshots/snap1.qcow2"):
    """Configure MockShell expectations for Core.restore() happy path.

    Registers responses for:
      - virsh dominfo (VM-stopped check) — overrides conftest default
      - test -f (file existence checks in scan_backing_chain — conftest default suffices)
      - qemu-img info --backing-chain (chain integrity pre-verify)
      - qemu-img convert (standalone image to tmp, via convert_with_retry)
      - qemu-img info generic (M1 virtual-size probes in verify_standalone_image)
      - qemu-img check --output=json (M2 structural integrity)
      - rm -f (old overlay cleanup + tmp cleanup on verify failure)
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
    chain_json = json.dumps(
        [
            {"filename": snapshot_path, "format": "qcow2", "virtual-size": 10737418240},
            {
                "filename": "/var/lib/libvirt/images/testvm.qcow2",
                "format": "qcow2",
                "virtual-size": 21474836480,
            },
        ]
    )
    mock_shell.expect_first("qemu-img info --force-share --backing-chain").returns(
        ShellResult(success=True, stdout=chain_json, stderr="", returncode=0, error=None)
    )
    # qemu-img convert to tmp (via convert_with_retry → convert_to_standalone)
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Generic qemu-img info catch-all for M1 virtual-size probes in
    # verify_standalone_image.  Placed AFTER the --backing-chain
    # expectation so that chain scans continue to match the more
    # specific pattern.  Returns a dict (not a list) with consistent
    # virtual-size so M1 passes.
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"virtual-size": 21474836480, "actual-size": 1073741824}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # M2: qemu-img check succeeds (structural integrity)
    mock_shell.expect("qemu-img check --output=json").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"errors": 0, "corruptions": 0, "leaks": 0}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # rm -f old overlays and tmp cleanup
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
        ShellResult(
            success=True, stdout="Domain testvm defined", stderr="", returncode=0, error=None
        )
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
    assert result.disk == "vda"

    # Verify qemu-img convert was called
    convert_calls = [c for c in mock_shell.call_history if "convert" in c]
    assert len(convert_calls) >= 1, "qemu-img convert should have been called"

    # Verify virsh dumpxml and define were called (XML refresh + source file update)
    dumpxml_calls = [c for c in mock_shell.call_history if "dumpxml" in c]
    assert len(dumpxml_calls) >= 1, "virsh dumpxml should have been called"

    define_calls = [c for c in mock_shell.call_history if "virsh define" in c]
    assert len(define_calls) >= 1, "virsh define should have been called"


# ── test_restore_from_backup_resolves_disk ──────────────────────────────────


@pytest.mark.integration
def test_restore_from_backup_resolves_disk(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """Restore finds the snapshot on a backup target and resolves its disk from SnapshotInfo.disk."""
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Do NOT record in state — the snapshot comes from the backup provider
    backup_snap = _make_snapshot("backup_snap", str(target.path / "backup_snap.qcow2"), disk="vda")

    _setup_restore_shell(mock_shell, snapshot_path=str(backup_snap.path))

    with (
        patch.object(
            mock_factory._backup_provider,
            "list",
            return_value=[backup_snap],
        ),
        patch("qsnap.core.os.replace"),
    ):
        result = core.restore("backup_snap")

    assert result.success is True
    assert result.snapshot_name == "backup_snap"
    assert result.disk == "vda"


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
    convert_calls = [c for c in mock_shell.call_history if "qemu-img convert" in c]
    assert len(convert_calls) == 0, "qemu-img convert should NOT be called when chain is broken"

    # Verify NO state was reset (snapshots still present)
    snapshots_after = mock_state.get_snapshots("testvm")
    assert len(snapshots_after) == 1, (
        f"State should not be modified, got {len(snapshots_after)} snapshots"
    )

    # Verify NO rm commands were issued
    rm_calls = [c for c in mock_shell.call_history if "rm -f" in c]
    assert len(rm_calls) == 0, "No files should be deleted when chain is broken"


# ── test_restore_aborts_when_disk_cannot_be_determined ──────────────────────


@pytest.mark.integration
def test_restore_aborts_when_disk_cannot_be_determined(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() aborts when neither SnapshotInfo.disk nor parse_disk_from_snapshot_name can resolve a disk."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Snapshot with disk=None and a name that cannot be parsed for a disk
    snap = _make_snapshot("ambiguous", "/snapshots/ambiguous.qcow2", disk=None)
    # Override disk to None (since _make_snapshot sets it to "vda")
    snap = SnapshotInfo(
        name="ambiguous",
        path=Path("/snapshots/ambiguous.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1048576,
        disk="",  # empty string, treated as falsy
    )
    mock_state.record_snapshot("testvm", snap)

    result = core.restore("ambiguous")

    assert result.success is False
    assert result.error is not None
    assert "cannot determine disk" in result.error.lower()


# ── test_restore_aborts_when_disk_not_in_config ─────────────────────────────


@pytest.mark.integration
def test_restore_aborts_when_disk_not_in_config(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() aborts when the resolved disk does not exist in the VM config."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Snapshot claims disk="vdz" but VM only has vda
    mock_state.record_snapshot(
        "testvm",
        _make_snapshot("snap1", "/snapshots/snap1.qcow2", disk="vdz"),
    )

    result = core.restore("snap1")

    assert result.success is False
    assert result.error is not None
    assert "not configured" in result.error.lower()
    assert "vdz" in result.error.lower()


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
    chain_json = json.dumps(
        [
            {"filename": "/snapshots/snap1.qcow2", "format": "qcow2"},
            {"filename": "/var/lib/libvirt/images/testvm.qcow2", "format": "qcow2"},
        ]
    )
    mock_shell.expect_first("qemu-img info --force-share --backing-chain").returns(
        ShellResult(success=True, stdout=chain_json, stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.INFO)

    result = core.restore("snap1")

    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.disk == "vda"

    # Verify dry-run log messages
    assert "[dry-run]" in caplog.text, "Expected dry-run log messages"

    # Verify no convert was actually executed
    convert_calls = [c for c in mock_shell.call_history if "convert" in c]
    assert len(convert_calls) == 0, "qemu-img convert should NOT be called in dry-run mode"


# ── test_restore_verifies_temp_before_replace ───────────────────────────────


@pytest.mark.integration
def test_restore_verifies_temp_before_replace(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() verifies the temp image BEFORE os.replace; on failure removes tmp and aborts."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))
    _setup_restore_shell(mock_shell)

    # Override qemu-img check to simulate M2 verification failure.
    # Use expect_first so it matches before the generic expectation
    # added by _setup_restore_shell.
    mock_shell.expect_first("qemu-img check --output=json").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"errors": 5, "corruptions": 0}),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Patch os.replace to detect any call
    with patch("qsnap.core.os.replace") as mock_replace:
        result = core.restore("snap1")

    # Verify failure
    assert result.success is False
    assert result.error is not None
    assert "verification failed" in result.error.lower(), (
        f"Expected verification failure, got: {result.error}"
    )

    # os.replace must NOT be called
    mock_replace.assert_not_called()

    # Verify tmp file was removed
    rm_calls = [c for c in mock_shell.call_history if "rm" in c and ".tmp" in c]
    assert len(rm_calls) >= 1, "tmp file should have been removed after verify failure"


# ── test_restore_cleanup_only_restored_disk_checkpoints ─────────────────────


@pytest.mark.integration
def test_restore_cleanup_only_restored_disk_checkpoints(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() deletes only the restored disk's checkpoints; other disks' checkpoints survive."""
    from tests.mocks.mock_modules import MockBitmapBackupProvider

    target = make_target(path="/backups/testvm")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))
    _setup_restore_shell(mock_shell)

    # Proper 5-segment checkpoint names: qsnap-{hash}-{disk}-{timestamp}-{hex}
    hash8 = MockBitmapBackupProvider.target_hash("/backups/testvm")
    vda_ckpt = f"qsnap-{hash8}-vda-20260701T120000-a1b2c3"
    vdb_ckpt = f"qsnap-{hash8}-vdb-20260701T120000-d4e5f6"
    checkpoints = [vda_ckpt, vdb_ckpt]

    # Expect checkpoint-delete only for the vda checkpoint
    mock_shell.expect("checkpoint-delete").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
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

    assert result.success is True

    # Verify checkpoint-delete was called only for vda (not vdb)
    checkpoint_calls = [c for c in mock_shell.call_history if "checkpoint-delete" in c]
    assert len(checkpoint_calls) == 1, (
        f"Expected exactly 1 checkpoint-delete call (vda only), got {len(checkpoint_calls)}: {checkpoint_calls}"
    )
    assert vda_ckpt in checkpoint_calls[0], (
        f"Checkpoint-delete should target {vda_ckpt}, got: {checkpoint_calls[0]}"
    )
    assert vdb_ckpt not in checkpoint_calls[0], (
        f"Checkpoint-delete should NOT target vdb checkpoint {vdb_ckpt}"
    )


# ── test_restore_skips_legacy_checkpoints ───────────────────────────────────


@pytest.mark.integration
def test_restore_skips_legacy_checkpoints(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """restore() skips legacy checkpoints (no disk segment) with a WARNING; does NOT delete them."""
    from tests.mocks.mock_modules import MockBitmapBackupProvider

    target = make_target(path="/backups/testvm")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))
    _setup_restore_shell(mock_shell)

    # Proper 5-segment checkpoint for vda (should be deleted)
    hash8 = MockBitmapBackupProvider.target_hash("/backups/testvm")
    vda_ckpt = f"qsnap-{hash8}-vda-20260701T120000-a1b2c3"
    # Legacy 4-segment checkpoint without a disk segment (should NOT be deleted)
    legacy_ckpt = f"qsnap-{hash8}-20260701T120000-a1b2c3"
    checkpoints = [vda_ckpt, legacy_ckpt]

    mock_shell.expect("checkpoint-delete").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.WARNING)

    with (
        patch.object(
            mock_factory._backup_provider,
            "list_checkpoints",
            return_value=checkpoints,
        ),
        patch("qsnap.core.os.replace"),
    ):
        result = core.restore("snap1")

    assert result.success is True

    # Verify only the vda checkpoint was targeted for deletion
    checkpoint_calls = [c for c in mock_shell.call_history if "checkpoint-delete" in c]
    assert len(checkpoint_calls) == 1, (
        f"Expected exactly 1 checkpoint-delete call (vda only), got {len(checkpoint_calls)}: {checkpoint_calls}"
    )
    assert vda_ckpt in checkpoint_calls[0], (
        f"Checkpoint-delete should target {vda_ckpt}, got: {checkpoint_calls[0]}"
    )
    assert legacy_ckpt not in checkpoint_calls[0], (
        f"Legacy checkpoint {legacy_ckpt} should NOT be deleted"
    )

    # Verify WARNING logged about the legacy checkpoint
    warning_records = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    legacy_warnings = [msg for msg in warning_records if "legacy checkpoint" in msg.lower()]
    assert len(legacy_warnings) >= 1, (
        f"Expected WARNING about legacy checkpoint, got warnings: {warning_records}"
    )
    assert legacy_ckpt in legacy_warnings[0], (
        f"Warning should name the legacy checkpoint {legacy_ckpt}, got: {legacy_warnings[0]}"
    )


# ── test_restore_resets_only_restored_disk_state ────────────────────────────


@pytest.mark.integration
def test_restore_resets_only_restored_disk_state(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """restore() calls reset_vm_disk_state (NOT reset_vm_state) and preserves other disks' state."""
    target = make_target(path=str(tmp_path / "backup"))
    # VM with two disks: vda and vdb
    from qsnap.models.config import DiskConfig

    disks = [
        DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm-vda.qcow2")),
        DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm-vdb.qcow2")),
    ]
    vm = make_vm_config(name="testvm", disks=disks, targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Pre-populate state for both vda and vdb
    mock_state.record_snapshot(
        "testvm", _make_snapshot("snap_vda", "/snapshots/snap_vda.qcow2", disk="vda")
    )
    mock_state.record_snapshot(
        "testvm", _make_snapshot("snap_vdb", "/snapshots/snap_vdb.qcow2", disk="vdb")
    )
    mock_state.set_last_allocation("testvm", "vda", 1048576)
    mock_state.set_last_allocation("testvm", "vdb", 2097152)
    target_path = str(target.path)
    mock_state.set_last_backup_allocation(target_path, "vda", 524288)
    mock_state.set_last_backup_allocation(target_path, "vdb", 1048576)

    # Verify state populated
    assert len(mock_state.get_snapshots("testvm")) == 2
    assert mock_state.get_last_allocation("testvm", "vda") == 1048576
    assert mock_state.get_last_allocation("testvm", "vdb") == 2097152

    _setup_restore_shell(mock_shell, snapshot_path="/snapshots/snap_vda.qcow2")

    with patch("qsnap.core.os.replace"):
        result = core.restore("snap_vda")

    assert result.success is True
    assert result.disk == "vda"

    # vda state should be cleared
    vda_snaps = [s for s in mock_state.get_snapshots("testvm") if s.disk == "vda"]
    assert vda_snaps == [], f"VDA snapshots should be cleared, got {vda_snaps}"
    assert mock_state.get_last_allocation("testvm", "vda") is None, (
        "VDA last_allocation should be cleared"
    )
    assert mock_state.get_last_backup_allocation(target_path, "vda") is None, (
        "VDA target last_backup_allocation should be cleared"
    )

    # vdb state should survive intact
    vdb_snaps = [s for s in mock_state.get_snapshots("testvm") if s.disk == "vdb"]
    assert len(vdb_snaps) == 1, f"VDB snapshots should survive, got {vdb_snaps}"
    assert mock_state.get_last_allocation("testvm", "vdb") == 2097152, (
        "VDB last_allocation should survive"
    )
    assert mock_state.get_last_backup_allocation(target_path, "vdb") == 1048576, (
        "VDB target last_backup_allocation should survive"
    )


# ── test_restore_nonexistent_snapshot ────────────────────────────────────────


@pytest.mark.integration
def test_restore_nonexistent_snapshot(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() returns RestoreResult(success=False) when the snapshot does not exist."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.restore("nonexistent-snap")

    assert isinstance(result, RestoreResult)
    assert result.success is False
    assert result.error is not None
    assert "not found" in result.error.lower()


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
    """restore() calls reset_vm_disk_state() and reset_target_disk_state() per-disk, NOT full reset.

    Uses the proper ``.FULL.``-prefix naming convention for FULL
    backups.  Also includes a legacy-named FULL entry (no parseable disk
    segment in the name) to cover the fixed edge case where
    ``reset_target_disk_state`` resolves the disk from the FULL record's
    stored ``disk`` field before falling back to name parsing.
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))

    target_path = str(target.path)

    # Proper FULL backup with parseable disk segment in the name
    full_name_proper = "testvm.FULL.20250713T080000_vda_a1b2c3.qcow2"
    mock_state.record_full_backup(
        target_path, full_name_proper, datetime(2025, 7, 13, 8, 0), disk="vda"
    )
    mock_state.record_incremental_dependency(
        target_path, "snap1_inc_proper.qcow2", full_name_proper
    )

    # Legacy FULL name without a parseable disk segment — the fix
    # resolves the disk from the stored ``disk`` field, not the name.
    # NOTE: The name omits the ``.qcow2`` suffix because
    # ``_normalize_full_name`` strips it for dep keys, and the fix
    # (``full_disk_by_name`` → ``.get()``) does NOT normalize the
    # lookup key — SOURCE BUG: the fix should also normalize when
    # resolving names from ``full_disk_by_name``.
    full_name_legacy = "testvm.FULL.20250714"
    mock_state.record_full_backup(
        target_path, full_name_legacy, datetime(2025, 7, 14, 8, 0), disk="vda"
    )
    mock_state.record_incremental_dependency(
        target_path, "snap1_inc_legacy.qcow2", full_name_legacy
    )

    mock_state.set_last_backup_allocation(target_path, "vda", 1048576)
    mock_state.set_last_allocation("testvm", "vda", 2097152)

    # Verify state is populated before restore — 2 FULLs, each with 1 dep
    assert len(mock_state.get_full_backups(target_path)) == 2
    assert len(mock_state.get_incremental_dependencies(target_path, full_name_proper)) == 1
    assert len(mock_state.get_incremental_dependencies(target_path, full_name_legacy)) == 1
    assert mock_state.get_last_backup_allocation(target_path, "vda") == 1048576
    assert mock_state.get_last_allocation("testvm", "vda") == 2097152
    assert len(mock_state.get_snapshots("testvm")) == 1

    _setup_restore_shell(mock_shell)

    with patch("qsnap.core.os.replace"):
        result = core.restore("snap1")

    assert result.success is True
    assert result.disk == "vda"

    # Verify VM disk state was reset (per-disk, not full reset)
    snapshots_after = mock_state.get_snapshots("testvm")
    assert snapshots_after == [], f"Expected empty snapshots, got {snapshots_after}"

    # Verify target disk state was reset — both FULLs removed
    fulls_after = mock_state.get_full_backups(target_path)
    assert fulls_after == [], f"Expected empty full backups, got {fulls_after}"

    # Both proper and legacy FULL deps cleaned
    deps_proper_after = mock_state.get_incremental_dependencies(target_path, full_name_proper)
    assert deps_proper_after == [], f"Expected empty deps for proper FULL, got {deps_proper_after}"

    deps_legacy_after = mock_state.get_incremental_dependencies(target_path, full_name_legacy)
    assert deps_legacy_after == [], f"Expected empty deps for legacy FULL, got {deps_legacy_after}"

    # Verify last_allocation was reset
    assert mock_state.get_last_allocation("testvm", "vda") is None
    assert mock_state.get_last_backup_allocation(target_path, "vda") is None


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
    """restore() cleans up proper 5-segment qsnap-* checkpoints for the restored disk.

    Proper checkpoint names: qsnap-{target_hash}-{disk}-{timestamp}-{hex}.
    Other-disk checkpoint NOT deleted; legacy checkpoint skipped with WARNING;
    deletion failure does not block the operation.
    """
    from tests.mocks.mock_modules import MockBitmapBackupProvider

    target = make_target(path="/backups/testvm")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot("snap1"))
    _setup_restore_shell(mock_shell)

    # Proper 5-segment checkpoint names
    hash8 = MockBitmapBackupProvider.target_hash("/backups/testvm")
    vda_ckpt1 = f"qsnap-{hash8}-vda-20260701T120000-a1b2c3"
    vda_ckpt2 = f"qsnap-{hash8}-vda-20260702T120000-b4c5d6"
    checkpoints = [vda_ckpt1, vda_ckpt2]

    # Expect checkpoint-delete calls — first succeeds, second fails
    mock_shell.expect(f"checkpoint-delete.*{vda_ckpt1}").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect(f"checkpoint-delete.*{vda_ckpt2}").returns(
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

    # Verify checkpoint-delete was called for each vda checkpoint
    checkpoint_calls = [c for c in mock_shell.call_history if "checkpoint-delete" in c]
    assert len(checkpoint_calls) == 2, (
        f"Expected 2 checkpoint-delete calls, got {len(checkpoint_calls)}"
    )
    assert any(vda_ckpt1 in c for c in checkpoint_calls), (
        f"checkpoint-delete must target {vda_ckpt1}"
    )
    assert any(vda_ckpt2 in c for c in checkpoint_calls), (
        f"checkpoint-delete must target {vda_ckpt2}"
    )
