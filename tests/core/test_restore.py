"""Tests for Core.restore() — VM disk replacement from snapshot or backup.

Covers the per-disk restore implementation: resolve snapshot via
``_resolve_snapshot()``, verify VM is stopped, pre-verify chain integrity,
create standalone qcow2 via ``qemu-img convert --force-share -O qcow2``
(``convert_with_retry``), verify it via ``verify_standalone_image`` BEFORE
``os.replace``, atomically replace the VM's base image, strip
``<backingStore>`` from that disk's domain XML element, reset only the
restored disk's state (``reset_vm_disk_state`` / ``reset_target_disk_state``),
and best-effort cleanup of that disk's checkpoints.

See OpenSpec change: fix-per-disk-isolation (restore-command/spec.md)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.config import DiskConfig
from qsnap.models.results import (
    ChainScanResult,
    RestoreResult,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import MockConfigFacade

_SIMPLE_DOMAIN_XML = (
    "<domain type='kvm'>\n"
    "  <name>testvm</name>\n"
    "  <uuid>12345678-1234-1234-1234-123456789abc</uuid>\n"
    "  <devices>\n"
    "    <disk type='file' device='disk'>\n"
    "      <source file='/var/lib/libvirt/images/testvm.qcow2'/>\n"
    "      <target dev='vda'/>\n"
    "    </disk>\n"
    "  </devices>\n"
    "</domain>"
)

# Pre-built JSON payloads for verify_standalone_image M1 / M2.
_QEMU_IMG_INFO_JSON = json.dumps({"virtual-size": 1073741824, "format": "qcow2"})
_QEMU_IMG_CHECK_JSON = json.dumps({"corruptions": 0, "errors": 0, "leaks": 0})


def _verify_expectations(mock_shell) -> None:
    """Register MockShell expectations that make verify_standalone_image pass."""
    mock_shell.expect("qemu-img info --force-share --output=json").returns(
        ShellResult(
            success=True,
            stdout=_QEMU_IMG_INFO_JSON,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("qemu-img check --output=json").returns(
        ShellResult(
            success=True,
            stdout=_QEMU_IMG_CHECK_JSON,
            stderr="",
            returncode=0,
            error=None,
        )
    )


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
        disk="vda",
    )


def _make_snapshot_with_path(
    name: str, vm_config, allocation: int = 1048576, disk: str = "vda"
) -> SnapshotInfo:
    """Create a snapshot whose path is inside the VM's snapshot_dir."""
    snapshot_dir = vm_config.snapshot_dir
    if isinstance(snapshot_dir, Path):
        return SnapshotInfo(
            name=name,
            path=snapshot_dir / f"{name}.qcow2",
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=allocation,
            disk=disk,
        )
    # Fallback for old-style non-Path snapshot_dir.
    return SnapshotInfo(
        name=name,
        path=Path(snapshot_dir) / f"{name}.qcow2",
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=allocation,
        disk=disk,
    )


# ── existing tests (updated for convert_with_retry + verify) ───────────────


@pytest.mark.unit
def test_restore_from_snapshot_identifies_disk(
    make_vm_config, mock_factory, mock_state, mock_shell
):
    """restore() resolves snapshot disk, converts, verifies, replaces."""
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
    _verify_expectations(mock_shell)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True,
            stdout="Domain testvm defined",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    with patch("os.replace"), patch("os.path.exists", return_value=True):
        result = core.restore("snap1")
    assert isinstance(result, RestoreResult)
    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.restored_path == vm.disks[0].base_image
    assert len(result.chain_files) == 1
    assert vm.disks[0].base_image in result.chain_files
    assert result.error is None
    assert result.disk == "vda"
    convert_calls = [c for c in mock_shell.call_history if "qemu-img convert" in c]
    assert len(convert_calls) >= 1, "qemu-img convert should have been called"
    convert_cmd = convert_calls[0]
    assert "--force-share" in convert_cmd


@pytest.mark.unit
def test_restore_from_backup_identifies_disk(
    tmp_path, make_vm_config, make_target, mock_factory, mock_state, mock_shell
):
    """restore() finds backup via provider, resolves disk, replaces."""
    target = make_target(path=str(tmp_path / "backups"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    backup_info = SnapshotInfo(
        name="backup1",
        path=tmp_path / "backups" / "backup1.qcow2",
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1048576,
        disk="vda",
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
    _verify_expectations(mock_shell)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True,
            stdout="Domain testvm defined",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    with (
        patch.object(mock_factory._backup_provider, "list", return_value=[backup_info]),
        patch("os.replace"),
        patch("os.path.exists", return_value=True),
    ):
        result = core.restore("backup1")
    assert result.success is True
    assert result.snapshot_name == "backup1"


@pytest.mark.unit
def test_restore_vm_running_fails(make_vm_config, mock_factory, mock_state, mock_shell):
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
    assert result.restored_path == vm.disks[0].base_image


@pytest.mark.unit
def test_restore_aborts_on_broken_chain(make_vm_config, mock_factory, mock_state, mock_shell):
    """restore() returns failure when scan_backing_chain reports broken chain."""
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


@pytest.mark.unit
def test_restore_dry_run_shows_planned_actions(
    make_vm_config, mock_factory, mock_state, mock_shell, caplog
):
    """restore() in dry-run mode logs planned actions but does not execute."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    core.dry_run = True
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
    caplog.set_level(logging.INFO)
    with patch("os.path.exists", return_value=True):
        result = core.restore("snap1")
    assert result.success is True
    assert result.snapshot_name == "snap1"
    log_text = caplog.text.lower()
    assert "dry-run" in log_text, f"Expected dry-run log messages, got: {caplog.text}"
    convert_calls = [c for c in mock_shell.call_history if "qemu-img convert" in c]
    assert len(convert_calls) == 0, "convert should not be called in dry-run"


@pytest.mark.unit
def test_restore_nonexistent_snapshot_returns_failure(
    make_vm_config, mock_factory, mock_state, mock_shell
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


@pytest.mark.unit
def test_restore_strips_backingStore_from_xml(make_vm_config, mock_factory, mock_state, mock_shell):
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
    _verify_expectations(mock_shell)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    xml_with_backing = (
        "<domain type='kvm'>\n"
        "  <name>testvm</name>\n"
        "  <devices>\n"
        "    <disk type='file' device='disk'>\n"
        "      <source file='/var/lib/libvirt/images/testvm.qcow2'/>\n"
        "      <target dev='vda'/>\n"
        "      <backingStore type='file'>\n"
        "        <source file='/snapshots/snap1.qcow2'/>\n"
        "      </backingStore>\n"
        "    </disk>\n"
        "  </devices>\n"
        "</domain>"
    )
    mock_shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=xml_with_backing, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True,
            stdout="Domain testvm defined",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    with patch("os.replace"), patch("os.path.exists", return_value=True):
        result = core.restore("snap1")
    assert result.success is True
    dumpxml_calls = [c for c in mock_shell.call_history if "dumpxml" in c]
    assert len(dumpxml_calls) >= 1, "virsh dumpxml should have been called"
    define_calls = [c for c in mock_shell.call_history if "virsh define" in c]
    assert len(define_calls) >= 1, "virsh define should have been called"


@pytest.mark.unit
def test_restore_deletes_old_snapshot_overlays(
    make_vm_config, mock_factory, mock_state, mock_shell
):
    """restore() deletes old snapshot overlay files from snapshot_dir."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm, allocation=1048576))
    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap2", vm, allocation=2097152))
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
    _verify_expectations(mock_shell)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True,
            stdout="Domain testvm defined",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    with patch("os.replace"), patch("os.path.exists", return_value=True):
        result = core.restore("snap1")
    assert result.success is True
    rm_calls = [c for c in mock_shell.call_history if "rm -f" in c]
    assert len(rm_calls) >= 1, "rm -f should be called to delete old overlays"


@pytest.mark.unit
def test_restore_convert_failure_returns_error(
    make_vm_config, mock_factory, mock_state, mock_shell
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
        ShellResult(success=False, stdout="", stderr="disk full", returncode=1, error="disk full")
    )
    # scan_backing_chain needs os.path.exists to pass
    with patch("os.path.exists", return_value=True):
        result = core.restore("snap1")
    assert result.success is False
    assert "image conversion failed" in result.error


@pytest.mark.unit
def test_core_restore_from_snapshot_replaces_disk(
    make_vm_config, mock_factory, mock_state, mock_shell
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
    _verify_expectations(mock_shell)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True,
            stdout="Domain testvm defined",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    with patch("os.replace"), patch("os.path.exists", return_value=True):
        result = core.restore("snap1")
    assert result.success is True
    assert result.snapshot_name == "snap1"


@pytest.mark.unit
def test_core_restore_fails_on_running_vm(make_vm_config, mock_factory, mock_state, mock_shell):
    """Core.restore() returns failure when is_vm_running() returns True."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))
    result = core.restore("snap1")
    assert result.success is False
    assert "VM must be stopped" in result.error


@pytest.mark.unit
def test_core_restore_from_backup_replaces_disk(
    tmp_path, make_vm_config, make_target, mock_factory, mock_state, mock_shell
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
    assert mock_state.get_snapshots("testvm") == []
    backup_info = SnapshotInfo(
        name="backup_snap1",
        path=tmp_path / "backups" / "backup_snap1.qcow2",
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1048576,
        disk="vda",
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
    _verify_expectations(mock_shell)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True,
            stdout="Domain testvm defined",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    with (
        patch.object(mock_factory._backup_provider, "list", return_value=[backup_info]),
        patch("os.replace"),
        patch("os.path.exists", return_value=True),
    ):
        result = core.restore("backup_snap1")
    assert result.success is True
    assert result.snapshot_name == "backup_snap1"
    assert result.restored_path == vm.disks[0].base_image
    assert vm.disks[0].base_image in result.chain_files
    convert_calls = [c for c in mock_shell.call_history if "qemu-img convert" in c]
    assert len(convert_calls) >= 1
    assert "backup_snap1" in convert_calls[0]


@pytest.mark.unit
def test_restore_atomic_replace_preserves_original_on_crash(
    make_vm_config, mock_factory, mock_state, mock_shell
):
    """When os.replace fails during restore, exception propagates; convert was attempted.

    Design D2: writes to .tmp path, then os.replace to base_image.
    If os.replace fails, the original base_image is unchanged because
    the replace is atomic (rename).  The test verifies that the
    exception propagates after convert and verify succeed.
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
    _verify_expectations(mock_shell)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True,
            stdout="Domain testvm defined",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    with (
        patch("qsnap.core.os.replace", side_effect=OSError("File exists")),
        patch("os.path.exists", return_value=True),
        pytest.raises(OSError, match="File exists"),
    ):
        core.restore("snap1")
    convert_calls = [c for c in mock_shell.call_history if "qemu-img convert" in c]
    assert len(convert_calls) >= 1, "qemu-img convert should have been called"


# ── new tests (design D4: per-disk isolation) ──────────────────────────────


@pytest.mark.unit
def test_restore_resets_only_restored_disk_state(
    make_vm_config, make_target, mock_factory, mock_state, mock_shell
):
    """restore() calls per-disk reset methods; reset_vm_state/reset_target_state NOT called.

    Design D4: step 8 resets only the restored disk's state via
    ``reset_vm_disk_state(vm_name, disk)`` and
    ``reset_target_disk_state(target_path, vm_name, disk)``.
    The old ``reset_vm_state`` / ``reset_target_state`` (full reset)
    are NOT called.  Other disks' state survives.
    """
    target = make_target()
    vm = make_vm_config(
        name="testvm",
        targets=[target],
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm_vda.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm_vdb.qcow2")),
        ],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Record snapshots for both disks.
    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm, disk="vda"))
    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap_vdb", vm, disk="vdb"))
    # Record some target state for both disks.
    mock_state.record_full_backup(str(target.path), "testvm.FULL.vda", datetime(2025, 1, 1), "vda")
    mock_state.record_full_backup(str(target.path), "testvm.FULL.vdb", datetime(2025, 1, 1), "vdb")

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
    _verify_expectations(mock_shell)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True,
            stdout="Domain testvm defined",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        patch("os.replace"),
        patch("os.path.exists", return_value=True),
        patch.object(
            mock_state, "reset_vm_disk_state", wraps=mock_state.reset_vm_disk_state
        ) as vdisk_spy,
        patch.object(
            mock_state, "reset_target_disk_state", wraps=mock_state.reset_target_disk_state
        ) as tdisk_spy,
        patch.object(mock_state, "reset_vm_state", wraps=mock_state.reset_vm_state) as vm_spy,
        patch.object(
            mock_state, "reset_target_state", wraps=mock_state.reset_target_state
        ) as target_spy,
    ):
        result = core.restore("snap1")

    assert result.success is True
    assert result.disk == "vda"

    # Per-disk reset was called with the restored disk.
    vdisk_spy.assert_called_once_with("testvm", "vda")
    tdisk_spy.assert_called_once_with(str(target.path), "testvm", "vda")

    # The old full-reset methods were NOT called.
    vm_spy.assert_not_called()
    target_spy.assert_not_called()

    # Other disk's snapshot state survives.
    remaining = mock_state.get_snapshots("testvm")
    vdb_snaps = [s for s in remaining if s.disk == "vdb"]
    assert len(vdb_snaps) == 1, "vdb snapshots should survive per-disk state reset"
    assert vdb_snaps[0].name == "snap_vdb"

    # Other disk's FULL records survive.
    fulls = mock_state.get_full_backups(str(target.path))
    vdb_fulls = [f for f in fulls if f.disk == "vdb"]
    assert len(vdb_fulls) == 1, "vdb FULL records should survive per-disk state reset"


@pytest.mark.unit
def test_restore_cleans_only_restored_disk_checkpoints(
    make_vm_config, make_target, mock_factory, mock_state, mock_shell
):
    """restore() deletes only the restored disk's 5-segment checkpoints.

    Checkpoint names follow: qsnap-{hash}-{disk}-{timestamp}-{hex}.
    Only checkpoints whose third dash-separated segment equals the
    restored disk are deleted via ``virsh checkpoint-delete --metadata``.
    Other disks' checkpoints are left untouched.
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    checkpoints = [
        "qsnap-abc12345-vda-20260701T120000-a1b2c3",
        "qsnap-abc12345-vdb-20260701T120000-d4e5f6",
    ]

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
    _verify_expectations(mock_shell)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True,
            stdout="Domain testvm defined",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Checkpoint-delete success for any checkpoint (we assert which ones
    # are called based on call_history, not the result).
    mock_shell.expect("checkpoint-delete").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with (
        patch.object(mock_factory._backup_provider, "list_checkpoints", return_value=checkpoints),
        patch("os.replace"),
        patch("os.path.exists", return_value=True),
    ):
        result = core.restore("snap1")

    assert result.success is True
    checkpoint_calls = [c for c in mock_shell.call_history if "checkpoint-delete" in c]
    assert len(checkpoint_calls) == 1, (
        f"Expected 1 checkpoint-delete call (vda only), got {len(checkpoint_calls)}: "
        f"{checkpoint_calls}"
    )
    assert "vda" in checkpoint_calls[0]
    assert "vdb" not in checkpoint_calls[0]


@pytest.mark.unit
def test_restore_skips_legacy_checkpoints_with_warning(
    make_vm_config, make_target, mock_factory, mock_state, mock_shell, caplog
):
    """restore() skips legacy checkpoint names (no disk segment) with WARNING.

    Legacy checkpoint names like qsnap-{hash}-{timestamp}-{hex}
    (3 dash-separated segments, no disk at index 2) are NOT deleted.
    A WARNING is logged naming the skipped checkpoint.
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("testvm", _make_snapshot_with_path("snap1", vm))

    # Legacy format: 3 segments, no disk. The code identifies this by
    # len(parts)!=5 or parts[3] not matching \d{8}T\d{6}.
    legacy = "qsnap-abc12345-20260701T120000-a1b2c3"

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
    _verify_expectations(mock_shell)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True,
            stdout="Domain testvm defined",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("checkpoint-delete").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.WARNING)
    with (
        patch.object(mock_factory._backup_provider, "list_checkpoints", return_value=[legacy]),
        patch("os.replace"),
        patch("os.path.exists", return_value=True),
    ):
        result = core.restore("snap1")

    assert result.success is True

    # Legacy checkpoint should NOT be deleted.
    checkpoint_calls = [c for c in mock_shell.call_history if "checkpoint-delete" in c]
    assert len(checkpoint_calls) == 0, (
        f"Legacy checkpoint should NOT be deleted; got calls: {checkpoint_calls}"
    )

    # WARNING log should mention the legacy checkpoint.
    warn_text = caplog.text.lower()
    assert "legacy checkpoint" in warn_text or legacy.lower() in warn_text, (
        f"Expected WARNING about legacy checkpoint; got: {caplog.text}"
    )


@pytest.mark.unit
def test_restore_verifies_tmp_before_replace(make_vm_config, mock_factory, mock_state, mock_shell):
    """When verify_standalone_image fails, tmp is removed and os.replace NOT called.

    Verification runs BEFORE os.replace.  On failure the temp file is
    deleted via ``rm -f``, the base image is untouched, and the result
    carries ``success=False``.
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
    # rm -f for tmp cleanup after failed verification.
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with (
        patch(
            "qsnap.core.verify_standalone_image",
            return_value="M1 failed: virtual-size mismatch",
        ),
        patch("os.replace") as mock_replace,
        patch("os.path.exists", return_value=True),
    ):
        result = core.restore("snap1")

    assert result.success is False
    assert "verification failed" in result.error
    assert result.disk == "vda"
    # os.replace must NOT be called when verification fails.
    mock_replace.assert_not_called()

    # rm -f should have been called for the tmp file (verify failure cleanup).
    rm_calls = [c for c in mock_shell.call_history if "rm -f" in c]
    assert len(rm_calls) >= 1, "rm -f should be called to clean up failed tmp image"


@pytest.mark.unit
def test_restore_convert_retries_on_retryable_error(
    make_vm_config, mock_factory, mock_state, mock_shell
):
    """convert_with_retry retries on retryable errors, then succeeds.

    The first ``qemu-img convert`` fails with "eof" (retryable),
    the second succeeds.  ``time.sleep`` is called between retries.
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
    # convert_with_retry calls convert_to_standalone repeatedly.
    # We patch convert_to_standalone (not convert_to_standalone in core
    # but in convert_with_retry's module) to simulate retry.
    # Patch verify_standalone_image also to keep the test simple.
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True,
            stdout="Domain testvm defined",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        patch("qsnap.utils.convert.convert_to_standalone") as mock_convert,
        patch("qsnap.utils.convert.time.sleep") as mock_sleep,
        patch("os.path.exists", return_value=True),
        patch("qsnap.core.verify_standalone_image", return_value=None),
        patch("os.replace"),
    ):
        mock_convert.side_effect = [
            ShellResult(
                success=False,
                stdout="",
                stderr="",
                returncode=1,
                error="eof",
            ),
            ShellResult(
                success=True,
                stdout="",
                stderr="",
                returncode=0,
                error=None,
            ),
        ]
        result = core.restore("snap1")

    assert result.success is True
    assert mock_convert.call_count == 2, (
        f"Expected 2 convert attempts, got {mock_convert.call_count}"
    )
    assert mock_sleep.called, "time.sleep should be called between retries"


# ═══════════════════════════════════════════════════════════════════════════
# Restore --at (restore-points superset selection)
# ═══════════════════════════════════════════════════════════════════════════


def _setup_restore_at_shell(mock_shell) -> None:
    """Pre-configure the shell so a successful --at restore completes."""
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
    _verify_expectations(mock_shell)
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=_SIMPLE_DOMAIN_XML, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(
            success=True,
            stdout="Domain testvm defined",
            stderr="",
            returncode=0,
            error=None,
        )
    )


def _restore_points(*timestamps: datetime) -> list:
    """Build BackupInfo restore points for the target."""
    from qsnap.models.results import BackupInfo

    points = []
    for i, ts in enumerate(timestamps):
        name = f"testvm.{ts.strftime('%Y%m%dT%H%M%S')}_vda_a1b2c{i:02d}"
        points.append(
            BackupInfo(
                name=name,
                path=Path(f"/mnt/backup/testvm/{name}.qcow2"),
                timestamp=ts,
                disk="vda",
                is_full=(i == 0),
            )
        )
    return points


@pytest.mark.unit
def test_restore_at_selects_first_point_above(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """restore(at=...) selects the FIRST point >= at (superset policy).

    restore-command scenario "First point above the requested timestamp is
    used": with points at 10:00 and 12:00, requesting 11:00 restores the
    12:00 point and logs the actually-used point.
    """
    target = make_target(path=str(tmp_path / "backups"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    points = _restore_points(
        datetime(2025, 7, 13, 10, 0, 0),
        datetime(2025, 7, 13, 12, 0, 0),
    )
    _setup_restore_at_shell(mock_shell)

    caplog.set_level(logging.INFO)
    with (
        patch.object(mock_factory._backup_provider, "list", return_value=points),
        patch("os.replace"),
        patch("os.path.exists", return_value=True),
    ):
        result = core.restore(at=datetime(2025, 7, 13, 11, 0, 0))

    assert result.success is True
    assert result.snapshot_name == points[1].name, (
        f"First point >= at should be selected, got {result.snapshot_name}"
    )
    assert "[restore] --at" in caplog.text, (
        "The actually-used point must be logged"
    )
    assert points[1].name in caplog.text


@pytest.mark.unit
def test_restore_at_exact_match_selected(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore(at=...) selects the exact-matching point when present."""
    target = make_target(path=str(tmp_path / "backups"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    requested = datetime(2025, 7, 13, 12, 0, 0)
    points = _restore_points(
        datetime(2025, 7, 13, 10, 0, 0),
        requested,
        datetime(2025, 7, 13, 14, 0, 0),
    )
    _setup_restore_at_shell(mock_shell)

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=points),
        patch("os.replace"),
        patch("os.path.exists", return_value=True),
    ):
        result = core.restore(at=requested)

    assert result.success is True
    assert result.snapshot_name == points[1].name, (
        "The exact-matching point should be selected"
    )


@pytest.mark.unit
def test_restore_at_no_satisfying_point_lists_available(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore(at=...) with no point >= at fails with an informative error."""
    target = make_target(path=str(tmp_path / "backups"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    points = _restore_points(datetime(2025, 7, 13, 10, 0, 0))

    with patch.object(mock_factory._backup_provider, "list", return_value=points):
        result = core.restore(at=datetime(2025, 7, 13, 11, 0, 0))

    assert result.success is False
    assert "No restore point found at or after" in (result.error or ""), (
        f"Error should name the unsatisfiable request, got {result.error}"
    )


@pytest.mark.unit
def test_restore_at_and_name_conflict(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """restore() rejects specifying both --at and a name."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.restore(name="snap1", at=datetime(2025, 7, 13, 10, 0, 0))

    assert result.success is False
    assert "either" in (result.error or "").lower() and "at" in (result.error or "").lower(), (
        f"Error should explain the --at/name conflict, got {result.error}"
    )


@pytest.mark.unit
def test_restore_at_legacy_name_shim(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Legacy snapshot/backup names still resolve via the name shim.

    restore-command scenario "Restore --at with legacy snapshot name
    shim": a legacy-named backup on the target (no freeze-ts pattern)
    resolves through ``_resolve_snapshot`` → ``provider.list`` and
    restores normally.
    """
    from qsnap.models.results import BackupInfo

    target = make_target(path=str(tmp_path / "backups"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    legacy = BackupInfo(
        name="testvm.FULL.daily.qcow2",
        path=tmp_path / "backups" / "testvm.FULL.daily.qcow2",
        timestamp=datetime(2025, 7, 13, 10, 0, 0),
        disk="vda",
        is_full=True,
    )
    _setup_restore_at_shell(mock_shell)

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=[legacy]),
        patch("os.replace"),
        patch("os.path.exists", return_value=True),
    ):
        result = core.restore("testvm.FULL.daily.qcow2")

    assert result.success is True
    assert result.snapshot_name == "testvm.FULL.daily.qcow2"
