"""Tests for Core.reconcile() target repair — phantom FULLs, stale deps,
orphan files on targets, broken chain detection, state supplementation,
orphan checkpoints, stale baselines, dry-run mode.

Follows patterns from test_reconcile.py — same Core setup, MockShell,
InMemoryStateManager, MockVMModuleFactory.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.results import FullBackupInfo, SnapshotInfo
from tests.mocks import MockConfigFacade


def _anchor_json(orphan_path: Path, full_path: Path) -> str:
    """Return JSON for ``qemu-img info --output=json`` with a FULL backing anchor."""
    return json.dumps(
        {
            "filename": str(orphan_path),
            "format": "qcow2",
            "virtual-size": 10737418240,
            "actual-size": 200704,
            "backing-filename": str(full_path),
        }
    )


def _no_backing_json(file_path: Path) -> str:
    """Return JSON for ``qemu-img info --output=json`` with no backing file."""
    return json.dumps(
        {
            "filename": str(file_path),
            "format": "qcow2",
            "virtual-size": 10737418240,
            "actual-size": 200704,
        }
    )


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_phantom_full_removed(
    make_vm_config, make_target, mock_factory, mock_state, mock_shell, tmp_path
):
    """State has FULL1 and FULL2; disk only has FULL2 → FULL1 removed
    with cascade ``remove_all_incremental_dependencies``."""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    full1_name = "testvm.FULL.20250725.qcow2"
    full1_path = target_dir / full1_name
    full2_name = "testvm.FULL.20250726.qcow2"
    full2_path = target_dir / full2_name
    full2_path.write_text("")
    mock_state._full_backups[str(target.path)] = [
        FullBackupInfo(name=full1_name, path=full1_path, timestamp=datetime.now(), disk="vda"),
        FullBackupInfo(name=full2_name, path=full2_path, timestamp=datetime.now(), disk="vda"),
    ]
    mock_state.record_incremental_dependency(
        str(target.path), "testvm.20250725T1200_vda", full1_name
    )
    deps_before = mock_state.get_incremental_dependencies(str(target.path), full1_name)
    assert len(deps_before) == 1, "Dependency should be recorded before reconcile"
    result = core.reconcile()
    assert result["testvm"].phantom_fulls_removed == 1
    deps_after = mock_state.get_incremental_dependencies(str(target.path), full1_name)
    assert len(deps_after) == 0, "Cascade cleanup should remove all deps for phantom FULL1"
    fulls_after = mock_state.get_full_backups(str(target.path))
    assert len(fulls_after) == 1
    assert fulls_after[0].name == full2_name


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_stale_dep_removed(
    make_vm_config, make_target, mock_factory, mock_state, mock_shell, tmp_path
):
    """State has inc1 → FULL dep; disk does NOT have inc1 file →
    ``remove_incremental_dependency`` called, ``stale_deps_removed=1``."""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    full_name = "testvm.FULL.20250725.qcow2"
    full_path = target_dir / full_name
    full_path.write_text("")
    mock_state._full_backups[str(target.path)] = [
        FullBackupInfo(name=full_name, path=full_path, timestamp=datetime.now(), disk="vda")
    ]
    inc1_name = "testvm.20250726T1531_vda"
    target_dir / f"{inc1_name}.qcow2"
    mock_state.record_incremental_dependency(str(target.path), inc1_name, full_name)
    deps_before = mock_state.get_incremental_dependencies(str(target.path), full_name)
    assert inc1_name in deps_before, "Dependency should be recorded before reconcile"
    result = core.reconcile()
    assert result["testvm"].stale_deps_removed == 1
    deps_after = mock_state.get_incremental_dependencies(str(target.path), full_name)
    assert inc1_name not in deps_after, "Stale dep should be removed from state"


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_orphan_backup_recorded(
    make_vm_config, make_target, mock_factory, mock_state, mock_shell, tmp_path, success_result
):
    """State: FULL + inc1; Disk: FULL + inc1 + inc2 (inc2 not in state);
    inc2 chain intact → FULL → ``record_incremental_dependency``,
    ``state_supplemented >= 1``."""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    full_name = "testvm.FULL.20250725.qcow2"
    full_stem = Path(full_name).stem
    full_path = target_dir / full_name
    full_path.write_text("")
    mock_state._full_backups[str(target.path)] = [
        FullBackupInfo(name=full_name, path=full_path, timestamp=datetime.now(), disk="vda")
    ]
    inc1_name = "testvm.20250726T1531_vda"
    inc1_path = target_dir / f"{inc1_name}.qcow2"
    inc1_path.write_text("")
    mock_state.record_incremental_dependency(str(target.path), inc1_name, full_name)
    inc2_name = "testvm.20250726T1800_vda"
    inc2_path = target_dir / f"{inc2_name}.qcow2"
    inc2_path.write_text("")
    full_snap = SnapshotInfo(
        name=full_stem, path=full_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    inc1_snap = SnapshotInfo(
        name=inc1_name, path=inc1_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    inc2_snap = SnapshotInfo(
        name=inc2_name, path=inc2_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    mock_shell.expect_first("--backing-chain").returns(
        success_result('[{"format": "qcow2", "filename": "' + str(inc2_path) + '"}]')
    )
    mock_shell.expect("qemu-img info --output=json").returns(
        success_result(_anchor_json(inc2_path, full_path))
    )
    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=[full_snap, inc1_snap, inc2_snap]
    ):
        result = core.reconcile()
    assert result["testvm"].state_supplemented >= 1
    deps = mock_state.get_incremental_dependencies(str(target.path), full_name)
    assert inc2_name in deps, (
        f"inc2 ({inc2_name}) should be recorded as dependency of FULL after reconcile"
    )
    assert result["testvm"].orphan_files_removed == 0
    assert result["testvm"].broken_chains == []


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_orphan_broken_chain_critical_not_deleted(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    caplog,
    failure_result,
):
    """State: FULL + inc1; Disk: FULL + inc1 + inc2 (inc2 not in state);
    inc2 backing chain broken → CRITICAL log, ``broken_chains=["inc2"]``,
    inc2 is NOT deleted. Tests that the implementation correctly leaves
    broken-chain orphans for operator intervention (does NOT auto-delete)."""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    full_name = "testvm.FULL.20250725.qcow2"
    full_stem = Path(full_name).stem
    full_path = target_dir / full_name
    full_path.write_text("")
    mock_state._full_backups[str(target.path)] = [
        FullBackupInfo(name=full_name, path=full_path, timestamp=datetime.now(), disk="vda")
    ]
    inc1_name = "testvm.20250726T1531_vda"
    inc1_path = target_dir / f"{inc1_name}.qcow2"
    inc1_path.write_text("")
    mock_state.record_incremental_dependency(str(target.path), inc1_name, full_name)
    inc2_name = "testvm.20250726T1800_vda"
    inc2_path = target_dir / f"{inc2_name}.qcow2"
    inc2_path.write_text("")
    full_snap = SnapshotInfo(
        name=full_stem, path=full_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    inc1_snap = SnapshotInfo(
        name=inc1_name, path=inc1_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    inc2_snap = SnapshotInfo(
        name=inc2_name, path=inc2_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    mock_shell.expect_first("--backing-chain").returns(failure_result())
    with (
        patch.object(
            mock_factory._bitmap_backup_provider,
            "list",
            return_value=[full_snap, inc1_snap, inc2_snap],
        ),
        patch.object(
            mock_factory._bitmap_backup_provider,
            "delete",
            wraps=mock_factory._bitmap_backup_provider.delete,
        ) as delete_spy,
        caplog.at_level(logging.CRITICAL),
    ):
        result = core.reconcile()
    assert not delete_spy.called, "provider.delete() should NOT be called for broken-chain orphan"
    assert result["testvm"].orphan_files_removed == 0
    assert inc2_name in result["testvm"].broken_chains, (
        f"Broken chain for {inc2_name} should be in broken_chains"
    )
    assert any("broken chain" in r.message.lower() for r in caplog.records), (
        "Should log CRITICAL about broken chain"
    )


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_orphan_checkpoint_deleted(
    make_vm_config, make_target, mock_factory, mock_state, mock_shell, caplog, success_result
):
    """Checkpoint-list has orphan checkpoint (wrong target_hash) →
    ``virsh checkpoint-delete --metadata`` called,
    ``orphan_checkpoints_deleted=1``."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    import hashlib

    orphan_hash = hashlib.md5(b"/some/other/path").hexdigest()[:8]
    orphan_checkpoint = f"qsnap-{orphan_hash}-testvm.20250726_vda"
    mock_shell.expect("checkpoint-delete").returns(success_result())
    with (
        patch.object(
            mock_factory._bitmap_backup_provider,
            "list_checkpoints",
            return_value=[orphan_checkpoint],
        ) as list_spy,
        caplog.at_level(logging.INFO),
    ):
        result = core.reconcile()
    assert list_spy.called, "list_checkpoints should be called"
    assert result["testvm"].orphan_checkpoints_deleted >= 1, (
        f"Orphan checkpoint {orphan_checkpoint} should be deleted"
    )
    assert any("deleted orphan checkpoint" in r.message for r in caplog.records), (
        "Should log INFO about deleted orphan checkpoint"
    )


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_stale_baseline_cleared(
    make_vm_config, make_target, mock_factory, mock_state, mock_shell
):
    """State: last_backup_allocation=1000; _full_backups empty →
    ``clear_last_backup_allocation``, ``baselines_cleared=1``."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    mock_state.set_last_backup_allocation(str(target.path), "vda", 1000)
    assert mock_state.get_last_backup_allocation(str(target.path), "vda") == 1000
    result = core.reconcile()
    assert result["testvm"].baselines_cleared == 1
    assert mock_state.get_last_backup_allocation(str(target.path), "vda") is None, (
        "last_backup_allocation should be cleared when no FULLs remain"
    )


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_orphan_no_anchor_deleted(
    make_vm_config, make_target, mock_factory, mock_state, mock_shell, tmp_path, success_result
):
    """State: FULL + inc1; Disk: FULL + inc1 + inc2 (inc2 not in state);
    inc2 chain intact but anchor resolution returns None (no .FULL. in
    backing-filename or no backing at all) → provider.delete(inc2),
    ``orphan_files_removed=1``."""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    full_name = "testvm.FULL.20250725.qcow2"
    full_stem = Path(full_name).stem
    full_path = target_dir / full_name
    full_path.write_text("")
    mock_state._full_backups[str(target.path)] = [
        FullBackupInfo(name=full_name, path=full_path, timestamp=datetime.now(), disk="vda")
    ]
    inc1_name = "testvm.20250726T1531_vda"
    inc1_path = target_dir / f"{inc1_name}.qcow2"
    inc1_path.write_text("")
    mock_state.record_incremental_dependency(str(target.path), inc1_name, full_name)
    inc2_name = "testvm.20250726T1800_vda"
    inc2_path = target_dir / f"{inc2_name}.qcow2"
    inc2_path.write_text("")
    full_snap = SnapshotInfo(
        name=full_stem, path=full_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    inc1_snap = SnapshotInfo(
        name=inc1_name, path=inc1_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    inc2_snap = SnapshotInfo(
        name=inc2_name, path=inc2_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    mock_shell.expect_first("--backing-chain").returns(
        success_result('[{"format": "qcow2", "filename": "' + str(inc2_path) + '"}]')
    )
    mock_shell.expect("qemu-img info --output=json").returns(
        success_result(_no_backing_json(inc2_path))
    )
    with (
        patch.object(
            mock_factory._bitmap_backup_provider,
            "list",
            return_value=[full_snap, inc1_snap, inc2_snap],
        ),
        patch.object(
            mock_factory._bitmap_backup_provider,
            "delete",
            wraps=mock_factory._bitmap_backup_provider.delete,
        ) as delete_spy,
    ):
        result = core.reconcile()
    assert delete_spy.called, "Orphan file with no anchor should be deleted"
    assert result["testvm"].orphan_files_removed == 1
    assert result["testvm"].broken_chains == []
    assert result["testvm"].state_supplemented == 0


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_after_retention_no_action(
    make_vm_config, make_target, mock_factory, mock_state, mock_shell, tmp_path
):
    """State contains only the newest FULL chain; disk matches exactly →
    all counters zero, no action taken."""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    full_name = "testvm.FULL.20250726.qcow2"
    full_stem = Path(full_name).stem
    full_path = target_dir / full_name
    full_path.write_text("")
    mock_state._full_backups[str(target.path)] = [
        FullBackupInfo(name=full_name, path=full_path, timestamp=datetime.now(), disk="vda")
    ]
    inc_name = "testvm.20250726T1531_vda"
    inc_path = target_dir / f"{inc_name}.qcow2"
    inc_path.write_text("")
    mock_state.record_incremental_dependency(str(target.path), inc_name, full_name)
    full_snap = SnapshotInfo(
        name=full_stem, path=full_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    inc_snap = SnapshotInfo(
        name=inc_name, path=inc_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    with (
        patch.object(
            mock_factory._bitmap_backup_provider, "list", return_value=[full_snap, inc_snap]
        ),
        patch.object(
            mock_factory._bitmap_backup_provider,
            "delete",
            wraps=mock_factory._bitmap_backup_provider.delete,
        ) as delete_spy,
    ):
        result = core.reconcile()
    assert not delete_spy.called, "No files should be deleted when state matches disk"
    assert result["testvm"].phantom_fulls_removed == 0
    assert result["testvm"].stale_deps_removed == 0
    assert result["testvm"].orphan_files_removed == 0
    assert result["testvm"].state_supplemented == 0
    assert result["testvm"].broken_chains == []
    assert result["testvm"].baselines_cleared == 0


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_dry_run_targets(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    caplog,
    success_result,
):
    """In dry-run mode, phantom FULL, stale dep, and orphan file are
    all reported but no real state changes or file deletions occur."""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    core.dry_run = True
    full1_name = "testvm.FULL.phantom.qcow2"
    full1_path = target_dir / full1_name
    full2_name = "testvm.FULL.real.qcow2"
    full2_stem = Path(full2_name).stem
    full2_path = target_dir / full2_name
    full2_path.write_text("")
    mock_state._full_backups[str(target.path)] = [
        FullBackupInfo(name=full1_name, path=full1_path, timestamp=datetime.now(), disk="vda"),
        FullBackupInfo(name=full2_name, path=full2_path, timestamp=datetime.now(), disk="vda"),
    ]
    stale_dep_name = "testvm.stale_dep_vda"
    target_dir / f"{stale_dep_name}.qcow2"
    mock_state.record_incremental_dependency(str(target.path), stale_dep_name, full2_name)
    orphan_name = "testvm.orphan_vda"
    orphan_path = target_dir / f"{orphan_name}.qcow2"
    orphan_path.write_text("")
    full2_snap = SnapshotInfo(
        name=full2_stem, path=full2_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    orphan_snap = SnapshotInfo(
        name=orphan_name, path=orphan_path, timestamp=datetime.now(), allocation=0, disk="vda"
    )
    mock_shell.expect_first("--backing-chain").returns(
        success_result('[{"format": "qcow2", "filename": "' + str(orphan_path) + '"}]')
    )
    mock_shell.expect("qemu-img info --output=json").returns(
        success_result(_anchor_json(orphan_path, full2_path))
    )
    fulls_before = len(mock_state.get_full_backups(str(target.path)))
    deps_before = len(mock_state.get_incremental_dependencies(str(target.path), full2_name))
    with (
        patch.object(
            mock_factory._bitmap_backup_provider, "list", return_value=[full2_snap, orphan_snap]
        ),
        patch.object(
            mock_factory._bitmap_backup_provider,
            "delete",
            wraps=mock_factory._bitmap_backup_provider.delete,
        ) as delete_spy,
        caplog.at_level(logging.INFO),
    ):
        result = core.reconcile()
    assert not delete_spy.called, "No files should be deleted in dry-run mode"
    fulls_after = len(mock_state.get_full_backups(str(target.path)))
    assert fulls_after == fulls_before, "FULL backups should NOT be removed in dry-run mode"
    deps_after = len(mock_state.get_incremental_dependencies(str(target.path), full2_name))
    assert deps_after == deps_before, "Dependencies should NOT be removed in dry-run mode"
    assert result["testvm"].phantom_fulls_removed >= 1, (
        "Should report phantom FULL would be removed"
    )
    assert result["testvm"].stale_deps_removed >= 1, "Should report stale dep would be removed"
    assert result["testvm"].state_supplemented >= 1, "Should report orphan would be supplemented"
    assert any("dry-run reconcile" in r.message for r in caplog.records), (
        "Should log dry-run reconcile messages"
    )
