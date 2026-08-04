"""Tests for Core.reconcile() — orphan detection, broken chain detection (B6),
and dry-run mode.

Covers the new reconcile features:
- Broken backing-chain detection before orphan classification (B6)
- Dependency record cleanup on orphan deletion (B4)
- Dry-run reporting of broken chains
- Intact chains produce empty broken_chains
- Non-qsnap file skip (integration with broken-chain check)
- Orphan snapshot file removal
- Non-fatal error handling during orphan detection
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
    return json.dumps({'filename': str(orphan_path), 'format': 'qcow2', 'virtual-size': 10737418240, 'actual-size': 200704, 'backing-filename': str(full_path)})

@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_skips_non_qsnap_files_on_target(make_vm_config, make_target, mock_factory, mock_state, mock_shell, caplog, success_result):
    """A ``.qcow2`` file on target not matching ``{vm_name}.*`` pattern
    is NOT deleted and a WARNING is logged.

    The new broken-chain detection (B6) also runs on this file, so
    we mock it to succeed (intact chain).
    """
    target = make_target()
    vm = make_vm_config(name='testvm', targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    non_qsnap = SnapshotInfo(name='my-backup', path=target.path / 'my-backup.qcow2', timestamp=datetime.now(), allocation=0, disk='vda')
    mock_shell.expect_first('--backing-chain').returns(success_result())
    with patch.object(mock_factory._bitmap_backup_provider, 'list', return_value=[non_qsnap]), patch.object(mock_factory._bitmap_backup_provider, 'delete', wraps=mock_factory._bitmap_backup_provider.delete) as delete_spy, caplog.at_level(logging.WARNING):
        result = core.reconcile()
    assert not delete_spy.called, 'Non-qsnap file should NOT be deleted'
    assert result['testvm'].orphan_files_removed == 0
    assert any(('not qsnap pattern' in r.message for r in caplog.records)), 'Should log WARNING about non-qsnap file'
    assert result['testvm'].broken_chains == []

@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_removes_orphan_snapshot_files(make_vm_config, make_target, mock_factory, mock_state, mock_shell, success_result):
    """A ``.qcow2`` file on target not tracked in state, matching the
    qsnap pattern ``{vm_name}.*``, is deleted and counted in
    ``orphan_files_removed``.

    The broken-chain check runs first on non-FULL files.  We mock it
    as intact so the file proceeds to orphan classification.  When the
    chain resolves to no tracked FULL anchor, the file is truly orphan
    and gets deleted.

    NOTE: In the new reconcile behavior, when ``virsh dumpxml`` does
    NOT reference the orphan file (the file is NOT in domain XML), the
    file is deleted.  When the XML DOES reference the file, it would
    be state-supplemented instead.  This test covers the deletion path
    for target-backup files where XML is irrelevant (backups are not
    in domain XML).
    """
    target = make_target()
    vm = make_vm_config(name='testvm', targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    orphan_backup = SnapshotInfo(name='testvm.20250726T1531_vda', path=target.path / 'testvm.20250726T1531_vda.qcow2', timestamp=datetime.now(), allocation=0, disk='vda')
    mock_shell.expect_first('--backing-chain').returns(success_result('[{"format": "qcow2", "filename": "' + str(orphan_backup.path) + '"}]'))
    mock_shell.expect('qemu-img info --output=json').returns(success_result('{}'))
    with patch.object(mock_factory._bitmap_backup_provider, 'list', return_value=[orphan_backup]), patch.object(mock_factory._bitmap_backup_provider, 'delete', wraps=mock_factory._bitmap_backup_provider.delete) as delete_spy:
        result = core.reconcile()
    assert delete_spy.called, 'provider.delete() should be called for orphan file'
    assert result['testvm'].orphan_files_removed == 1
    assert result['testvm'].broken_chains == []
    rec = result['testvm']
    assert rec.state_supplemented == 0, 'no files should be supplemented in this scenario'
    assert rec.xml_refreshed is False, 'XML should not be refreshed in this scenario'
    assert rec.allocation_fixed is False, 'no allocation fix in this scenario'

@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_orphan_file_cleanup_non_fatal(make_vm_config, make_target, mock_factory, mock_state, mock_shell, caplog):
    """Error during orphan detection (e.g. target dir not accessible)
    is logged as WARNING, recorded in ``errors``, and reconcile continues.
    """
    target = make_target()
    vm = make_vm_config(name='testvm', targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    with patch.object(core, '_detect_broken_chains', return_value=[]), patch.object(mock_factory._bitmap_backup_provider, 'list', side_effect=OSError('target directory not accessible')), caplog.at_level(logging.WARNING):
        result = core.reconcile()
    assert len(result['testvm'].errors) > 0
    assert any(('orphan files' in e for e in result['testvm'].errors)), 'Should record error about orphan files'
    assert result['testvm'].orphan_files_removed == 0
    assert any(('error checking orphan files' in r.message for r in caplog.records)), 'Should log WARNING about orphan files error'

@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_cleans_dependency_records_on_orphan_deletion(make_vm_config, make_target, mock_factory, mock_state, mock_shell, success_result):
    """Orphan file on target with intact chain to a tracked FULL →
    state is supplemented (record_incremental_dependency called),
    file is NOT deleted.

    Under the new reconcile behavior (D2/D3), when a file on target has
    an intact chain to a tracked FULL, reconcile supplements state
    instead of deleting.  The file stays on disk.
    """
    target = make_target()
    vm = make_vm_config(name='testvm', targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    orphan_name = 'testvm.20250726T1531_vda'
    orphan_path = target.path / f'{orphan_name}.qcow2'
    full_name = 'testvm.FULL.20250725'
    full_path = target.path / f'{full_name}.qcow2'
    orphan_backup = SnapshotInfo(name=orphan_name, path=orphan_path, timestamp=datetime.now(), allocation=0, disk='vda')
    mock_state.record_full_backup('vda', str(target.path), full_name, datetime.now())
    mock_shell.expect_first('--backing-chain').returns(success_result('[{"format": "qcow2", "filename": "' + str(orphan_path) + '"}]'))
    mock_shell.expect('qemu-img info --output=json').returns(success_result(json.dumps({'filename': str(orphan_path), 'format': 'qcow2', 'virtual-size': 10737418240, 'actual-size': 200704, 'backing-filename': str(full_path)})))
    with patch.object(mock_factory._bitmap_backup_provider, 'list', return_value=[orphan_backup]), patch.object(mock_factory._bitmap_backup_provider, 'delete', wraps=mock_factory._bitmap_backup_provider.delete) as delete_spy:
        result = core.reconcile()
    assert not delete_spy.called, 'Orphan file with intact chain to tracked FULL should NOT be deleted'
    assert result['testvm'].orphan_files_removed == 0
    assert result['testvm'].state_supplemented == 1, f"state_supplemented should be 1, got {result['testvm'].state_supplemented}"
    deps = mock_state.get_incremental_dependencies(str(target.path), full_name)
    assert orphan_name in deps, f'Dependency {orphan_name} → {full_name} should have been supplemented into state (D2)'
    assert result['testvm'].broken_chains == []

@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_detects_broken_chain_before_orphan(make_vm_config, make_target, mock_factory, mock_state, mock_shell, caplog, failure_result):
    """Non-FULL backup has broken backing chain →
    CRITICAL logged, backup name in ``broken_chains``,
    and file is NOT deleted (left for operator review).

    This tests the B6 fix and design D3: broken chains are detected,
    logged at CRITICAL level, added to broken_chains, but the file
    stays on disk — no deletion, no auto-rebase.
    """
    target = make_target()
    vm = make_vm_config(name='testvm', targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    orphan_name = 'testvm.20250726T1531_vda'
    orphan_path = target.path / f'{orphan_name}.qcow2'
    orphan_backup = SnapshotInfo(name=orphan_name, path=orphan_path, timestamp=datetime.now(), allocation=0, disk='vda')
    mock_shell.expect_first('--backing-chain').returns(failure_result())
    with patch.object(mock_factory._bitmap_backup_provider, 'list', return_value=[orphan_backup]), patch.object(mock_factory._bitmap_backup_provider, 'delete', wraps=mock_factory._bitmap_backup_provider.delete) as delete_spy, caplog.at_level(logging.CRITICAL):
        result = core.reconcile()
    assert not delete_spy.called, 'Broken chain files must NOT be deleted'
    assert result['testvm'].orphan_files_removed == 0
    assert orphan_name in result['testvm'].broken_chains, f'Broken chain for {orphan_name} should be in broken_chains'
    critical_logs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical_logs, 'Broken chain must emit CRITICAL log'
    assert any(('broken chain' in r.message for r in critical_logs)), 'CRITICAL log should mention broken chain'
    rec = result['testvm']
    assert rec.state_supplemented == 0, 'no files should be supplemented'
    assert rec.xml_refreshed is False
    assert rec.allocation_fixed is False

@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_intact_chains_no_broken_chains(make_vm_config, make_target, mock_factory, mock_state, mock_shell, tmp_path, success_result):
    """All non-FULL backups have intact backing chains →
    ``broken_chains`` is an empty list.

    The backup file is tracked in state so it is NOT treated as an orphan.
    Files must actually exist on disk so the phantom-full detection
    in steps 1-4 does not prematurely remove them from state.
    """
    target_dir = tmp_path / 'target'
    target_dir.mkdir()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name='testvm', targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    full_name = 'testvm.FULL.20250725.qcow2'
    full_stem = 'testvm.FULL.20250725'
    full_path = target_dir / full_name
    full_path.write_text('')
    full_info = FullBackupInfo(name=full_name, path=full_path, timestamp=datetime.now(), disk='vda')
    mock_state._full_backups[str(target.path)] = [full_info]
    inc_name = 'testvm.20250726T1531_vda'
    inc_path = target_dir / f'{inc_name}.qcow2'
    inc_path.write_text('')
    mock_state.record_incremental_dependency(str(target.path), inc_name, full_name)
    incremental_info = SnapshotInfo(name=inc_name, path=inc_path, timestamp=datetime.now(), allocation=0, disk='vda')
    full_snap_info = SnapshotInfo(name=full_stem, path=full_path, timestamp=datetime.now(), allocation=0, disk='vda')
    mock_shell.expect_first('--backing-chain').returns(success_result('[{"format": "qcow2", "filename": "' + str(inc_path) + '"}]'))
    with patch.object(mock_factory._bitmap_backup_provider, 'list', return_value=[incremental_info, full_snap_info]), patch.object(mock_factory._bitmap_backup_provider, 'delete', wraps=mock_factory._bitmap_backup_provider.delete) as delete_spy:
        result = core.reconcile()
    assert not delete_spy.called, 'Tracked files should NOT be deleted'
    assert result['testvm'].orphan_files_removed == 0
    assert result['testvm'].broken_chains == [], 'broken_chains should be empty when all chains are intact'
    rec = result['testvm']
    assert rec.state_supplemented == 0, 'no state supplementation needed when all files are tracked'
    assert rec.xml_refreshed is False, 'XML refresh not triggered when no stale backingStore references'
    assert rec.allocation_fixed is False, 'allocation not fixed in this scenario'

@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_dry_run_reports_broken_chains_no_deletion(make_vm_config, make_target, mock_factory, mock_state, mock_shell, caplog, failure_result):
    """``--dry-run`` mode: broken chain detected and reported in
    ``broken_chains``, but the file is NOT deleted.

    The broken-chain detection runs before orphan classification and
    populates ``broken_chains``.  In dry-run mode the orphan file is
    counted but not deleted.
    """
    target = make_target()
    vm = make_vm_config(name='testvm', targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    core.dry_run = True
    orphan_name = 'testvm.20250726T1531_vda'
    orphan_path = target.path / f'{orphan_name}.qcow2'
    orphan_backup = SnapshotInfo(name=orphan_name, path=orphan_path, timestamp=datetime.now(), allocation=0, disk='vda')
    mock_shell.expect_first('--backing-chain').returns(failure_result())
    with patch.object(mock_factory._bitmap_backup_provider, 'list', return_value=[orphan_backup]), patch.object(mock_factory._bitmap_backup_provider, 'delete', wraps=mock_factory._bitmap_backup_provider.delete) as delete_spy, caplog.at_level(logging.WARNING):
        result = core.reconcile()
    assert not delete_spy.called, 'provider.delete() should NOT be called in dry-run mode'
    assert result['testvm'].orphan_files_removed == 0, 'broken chain files should NOT be counted as orphan_files_removed'
    assert orphan_name in result['testvm'].broken_chains, f'Broken chain for {orphan_name} should be in broken_chains even in dry-run mode'
    critical_logs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical_logs, 'Broken chain must emit CRITICAL log in dry-run mode'
    assert any(('broken chain' in r.message for r in critical_logs)), 'CRITICAL log should mention broken chain in dry-run mode'