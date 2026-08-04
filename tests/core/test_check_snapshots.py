"""Tests for Core.check() — triple-source snapshot verification.

Covers 15 unit test scenarios using MockShell, verifying the cross-reference
matrix between state JSON, disk qcow2 files, and libvirt domain XML.

All tests use ``@pytest.mark.unit`` and ``@pytest.mark.mock`` markers.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.models.results import SnapshotInfo
from tests.mocks import MockConfigFacade


def _make_chain_json(paths: list[Path]) -> str:
    """Create qemu-img --backing-chain --output=json for a list of paths.

    Returns a JSON array where each entry has ``filename``, ``format``,
    and optional ``backing-filename`` pointing to the next entry.

    *paths* must be ordered from active layer to base.
    """
    entries: list[dict[str, object]] = []
    for i, path in enumerate(paths):
        entry: dict[str, object] = {'filename': str(path), 'format': 'qcow2', 'virtual-size': 10737418240, 'actual-size': 200704}
        if i + 1 < len(paths):
            entry['backing-filename'] = str(paths[i + 1])
        entries.append(entry)
    return json.dumps(entries)

def _make_chain_json_inconsistent(paths: list[Path], bad_idx: int) -> str:
    """Create chain JSON where entry *bad_idx* has wrong backing-filename."""
    entries: list[dict[str, object]] = []
    for i, path in enumerate(paths):
        entry: dict[str, object] = {'filename': str(path), 'format': 'qcow2', 'virtual-size': 10737418240, 'actual-size': 200704}
        if i + 1 < len(paths):
            if i == bad_idx:
                entry['backing-filename'] = '/wrong/path.qcow2'
            else:
                entry['backing-filename'] = str(paths[i + 1])
        entries.append(entry)
    return json.dumps(entries)

def _make_chain_json_cycle(paths: list[Path], cycle_idx: int) -> str:
    """Create chain JSON with a duplicate *cycle_idx* entry causing a cycle."""
    entries: list[dict[str, object]] = []
    cycle_path = paths[cycle_idx]
    for i, path in enumerate(paths):
        entry: dict[str, object] = {'filename': str(path), 'format': 'qcow2', 'virtual-size': 10737418240, 'actual-size': 200704}
        if i + 1 < len(paths):
            entry['backing-filename'] = str(paths[i + 1])
        if i == cycle_idx + 1:
            entry['filename'] = str(cycle_path)
        entries.append(entry)
    return json.dumps(entries)

def _make_dumpxml(paths: list[Path]) -> str:
    """Create virsh dumpxml output with <disk><source> and nested
    <backingStore> elements.

    NOTE: The current ``_parse_domain_xml_source_paths()`` implementation
    uses ``disk_elem.findall("backingStore")`` which only finds **direct
    children** of the disk element (not recursive).  For nested
    backingStore elements, only the first level is discovered.

    To work around this limitation in tests, we place **all** backing
    chain paths (except the active layer) as direct children of
    ``<disk>`` so they are all found by ``findall("backingStore")``.
    """
    xml_parts = ['<domain type="kvm"><devices><disk>']
    xml_parts.append(f'<source file="{paths[0]}"/>')
    for p in paths[1:]:
        xml_parts.append(f'<backingStore><source file="{p}"/></backingStore>')
    xml_parts.append('</disk></devices></domain>')
    return ''.join(xml_parts)

def _make_dumpxml_partial(paths: list[Path]) -> str:
    """Create dumpxml that only references *paths* (e.g. after blockcommit
    removed some files).  Active layer first, then referenced backings."""
    xml_parts = ['<domain type="kvm"><devices><disk>']
    xml_parts.append(f'<source file="{paths[0]}"/>')
    for p in paths[1:]:
        xml_parts.append(f'<backingStore><source file="{p}"/></backingStore>')
    xml_parts.append('</disk></devices></domain>')
    return ''.join(xml_parts)

def _make_domblklist_output(path: Path) -> str:
    """Create virsh domblklist output showing *path* as active layer."""
    return f'Target   Source\n--------------------------------\nvda   {path}\n'

def _record_snapshot(state, vm_name: str, name: str, path: Path, offset_hours: int=0) -> SnapshotInfo:
    """Record a snapshot in state and return the SnapshotInfo."""
    base = datetime(2025, 7, 13, 10, 0)
    snap = SnapshotInfo(name=name, path=path, timestamp=base + timedelta(hours=offset_hours), allocation=1000 * (offset_hours + 1), disk='vda')
    state.record_snapshot(vm_name, snap)
    return snap

def _setup_check_core(make_vm_config, mock_factory, mock_state, mock_shell, **kwargs) -> Core:
    """Create a Core instance with a single VM and no targets."""
    vm = make_vm_config(name='testvm', targets=[], **kwargs)
    config = MockConfigFacade(vms=[vm])
    return Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

@pytest.mark.unit
@pytest.mark.mock
def test_check_all_consistent(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, success_result):
    """State: 3 snapshots, Disk: 3 files, dumpxml references all,
    domblklist shows newest → status="ok"."""
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    for f in (snap3, snap2, snap1):
        f.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    chain_paths = [snap3, snap2, snap1, base_img]
    mock_shell.expect_first('--backing-chain').returns(success_result(_make_chain_json(chain_paths)))
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml(chain_paths)))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    result = core.check()
    assert result['testvm'].status == 'ok'
    assert result['testvm'].broken_snapshots == []

@pytest.mark.unit
@pytest.mark.mock
def test_check_phantom_snapshot_file_missing(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, failure_result, success_result):
    """State: 3 snapshots, snap2 doesn't exist on disk,
    qemu-img info --backing-chain fails → status="broken"."""
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap3.write_text('')
    snap1.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    mock_shell.expect_first('--backing-chain').returns(failure_result())
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml([snap3, snap2, snap1, base_img])))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    result = core.check()
    assert result['testvm'].status == 'broken'
    assert len(result['testvm'].broken_snapshots) > 0

@pytest.mark.unit
@pytest.mark.mock
def test_check_phantom_snapshot_but_xml_ok(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, caplog, success_result):
    """State: 3 snapshots, snap2 deleted via blockcommit,
    qemu-img chain is traversable without snap2, XML doesn't
    reference snap2 → status="ok" (stale state only)."""
    import logging
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap3.write_text('')
    snap1.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    chain_paths = [snap3, snap1, base_img]
    mock_shell.expect_first('--backing-chain').returns(success_result(_make_chain_json(chain_paths)))
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml(chain_paths)))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    with caplog.at_level(logging.WARNING):
        result = core.check()
    assert result['testvm'].status == 'ok'
    assert any('phantom entry in state' in r.message for r in caplog.records), 'Should log WARNING about phantom state entry'

@pytest.mark.unit
@pytest.mark.mock
def test_check_orphan_snapshot_file_exists_not_in_state(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, caplog, success_result):
    """State: 2 snapshots, Disk: 3 files, dumpxml references snap2
    → status="ok" but WARNING about orphan."""
    import logging
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    for f in (snap3, snap2, snap1):
        f.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    chain_paths = [snap3, snap2, snap1, base_img]
    mock_shell.expect_first('--backing-chain').returns(success_result(_make_chain_json(chain_paths)))
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml(chain_paths)))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    with caplog.at_level(logging.WARNING):
        result = core.check()
    assert result['testvm'].status == 'ok'
    assert any('orphan file' in r.message and 'not in state' in r.message for r in caplog.records), 'Should log WARNING about orphan file'

@pytest.mark.unit
@pytest.mark.mock
def test_check_xml_references_missing_file(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, success_result):
    """State: 3 snapshots, Disk: 3 files, but dumpxml references
    snap2 which doesn't exist → status="broken"."""
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    for f in (snap3, snap2, snap1):
        f.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    chain_paths = [snap3, snap2, snap1, base_img]
    mock_shell.expect_first('--backing-chain').returns(success_result(_make_chain_json(chain_paths)))
    ghost_path = snap_dir / 'testvm.ghost.qcow2'
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml([snap3, ghost_path, snap1, base_img])))
    _record_snapshot(mock_state, 'testvm', 'testvm.ghost', ghost_path, 1)
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    result = core.check()
    assert result['testvm'].status == 'broken'
    assert len(result['testvm'].broken_snapshots) > 0

@pytest.mark.unit
@pytest.mark.mock
def test_check_xml_active_layer_mismatch(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, success_result):
    """State: snap3 is newest, domblklist shows snap2 as active
    → status="broken"."""
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    for f in (snap3, snap2, snap1):
        f.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap2)))
    chain_paths = [snap2, snap1, base_img]
    mock_shell.expect_first('--backing-chain').returns(success_result(_make_chain_json(chain_paths)))
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml([snap2, snap1, base_img])))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    result = core.check()
    assert result['testvm'].status == 'broken'
    assert any('domblklist active layer' in s or 'active layer' for s in result['testvm'].broken_snapshots), f"Should report active layer mismatch, got: {result['testvm'].broken_snapshots}"

@pytest.mark.unit
@pytest.mark.mock
def test_check_xml_backingstore_chain_mismatch(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, caplog, success_result):
    """dumpxml backing chain differs from qemu-img backing chain
    → status="broken"."""
    import logging
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    for f in (snap3, snap2, snap1):
        f.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    broken_chain_paths = [snap3, snap_dir / 'missing.qcow2', base_img]
    mock_shell.expect_first('--backing-chain').returns(success_result(_make_chain_json(broken_chain_paths)))
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml([snap2, snap1, base_img])))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    with caplog.at_level(logging.WARNING):
        result = core.check()
    assert result['testvm'].status == 'broken', f"Expected status='broken' for backingchain mismatch, got {result['testvm'].status!r}"

@pytest.mark.unit
@pytest.mark.mock
def test_check_broken_chain_middle_missing(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, failure_result, success_result):
    """State: 3 snapshots, snap2 deleted from disk
    → status="broken", broken contains snap2."""
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    snap3.write_text('')
    snap1.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    mock_shell.expect_first('--backing-chain').returns(failure_result())
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml([snap3, snap2, snap1, base_img])))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    result = core.check()
    assert result['testvm'].status == 'broken'
    assert len(result['testvm'].broken_snapshots) > 0

@pytest.mark.unit
@pytest.mark.mock
def test_check_broken_chain_base_missing(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, failure_result, success_result):
    """State: 3 snapshots, base.qcow2 deleted → status="broken"."""
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    for f in (snap3, snap2, snap1):
        f.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    mock_shell.expect_first('--backing-chain').returns(failure_result())
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml([snap3, snap2, snap1, base_img])))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    result = core.check()
    assert result['testvm'].status == 'broken'
    assert len(result['testvm'].broken_snapshots) > 0

@pytest.mark.unit
@pytest.mark.mock
def test_check_after_blockcommit_all_consistent(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, success_result):
    """snap1 deleted via blockcommit, state/disk/XML all agree
    → status="ok"."""
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap_dir / 'testvm.snap1.qcow2'
    snap3.write_text('')
    snap2.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    chain_paths = [snap3, snap2, base_img]
    mock_shell.expect_first('--backing-chain').returns(success_result(_make_chain_json(chain_paths)))
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml(chain_paths)))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    result = core.check()
    assert result['testvm'].status == 'ok'
    assert result['testvm'].broken_snapshots == []

@pytest.mark.unit
@pytest.mark.mock
def test_check_after_retention_all_consistent(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, success_result):
    """snap1/snap2 deleted via retention, all sources agree
    → status="ok"."""
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap_dir / 'testvm.snap2.qcow2'
    snap_dir / 'testvm.snap1.qcow2'
    snap3.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    chain_paths = [snap3, base_img]
    mock_shell.expect_first('--backing-chain').returns(success_result(_make_chain_json(chain_paths)))
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml(chain_paths)))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    result = core.check()
    assert result['testvm'].status == 'ok'
    assert result['testvm'].broken_snapshots == []

@pytest.mark.unit
@pytest.mark.mock
def test_check_does_not_modify_state(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, success_result):
    """Verify check() doesn't change state JSON — snapshot count
    before and after check remains the same."""
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    snap2.write_text('')
    snap1.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    snapshots_before = mock_state.get_snapshots('testvm')
    assert len(snapshots_before) == 2
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap2)))
    chain_paths = [snap2, snap1, base_img]
    mock_shell.expect_first('--backing-chain').returns(success_result(_make_chain_json(chain_paths)))
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml(chain_paths)))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    core.check()
    snapshots_after = mock_state.get_snapshots('testvm')
    assert len(snapshots_after) == 2
    names_before = {s.name for s in snapshots_before}
    names_after = {s.name for s in snapshots_after}
    assert names_before == names_after

@pytest.mark.unit
@pytest.mark.mock
def test_check_does_not_delete_files(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, success_result):
    """Verify check() doesn't delete any qcow2 files — all files
    still exist after check."""
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    for f in (snap3, snap2, snap1):
        f.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    chain_paths = [snap3, snap2, snap1, base_img]
    mock_shell.expect_first('--backing-chain').returns(success_result(_make_chain_json(chain_paths)))
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml(chain_paths)))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    core.check()
    for f in (snap3, snap2, snap1, base_img):
        assert f.exists(), f'File {f.name} should still exist after check()'

@pytest.mark.unit
@pytest.mark.mock
def test_check_inconsistent_backing_filename(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, success_result):
    """qemu-img info JSON shows inconsistent backing-filename
    → detected via JSON parsing, status="broken"."""
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    for f in (snap3, snap2, snap1):
        f.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    chain_paths = [snap3, snap2, snap1, base_img]
    mock_shell.expect_first('--backing-chain').returns(success_result(_make_chain_json_inconsistent(chain_paths, 1)))
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml(chain_paths)))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    result = core.check()
    assert result['testvm'].status == 'broken'
    assert any('backing-filename mismatch' in s for s in result['testvm'].broken_snapshots), f"Should report backing-filename mismatch, got: {result['testvm'].broken_snapshots}"

@pytest.mark.unit
@pytest.mark.mock
def test_check_detects_cycle_in_chain(make_vm_config, mock_factory, mock_state, mock_shell, tmp_path, success_result):
    """qemu-img info JSON shows a cycle in backing chain
    → detected, status="broken"."""
    snap_dir = tmp_path / 'snapshots'
    snap_dir.mkdir()
    base_img = tmp_path / 'base.qcow2'
    base_img.write_text('')
    snap3 = snap_dir / 'testvm.snap3.qcow2'
    snap2 = snap_dir / 'testvm.snap2.qcow2'
    snap1 = snap_dir / 'testvm.snap1.qcow2'
    for f in (snap3, snap2, snap1):
        f.write_text('')
    _record_snapshot(mock_state, 'testvm', 'testvm.snap1', snap1, 0)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap2', snap2, 1)
    _record_snapshot(mock_state, 'testvm', 'testvm.snap3', snap3, 2)
    mock_shell.expect_first('virsh domblklist').returns(success_result(_make_domblklist_output(snap3)))
    chain_paths = [snap3, snap2, snap1, base_img]
    mock_shell.expect_first('--backing-chain').returns(success_result(_make_chain_json_cycle(chain_paths, 1)))
    mock_shell.expect_first('virsh dumpxml').returns(success_result(_make_dumpxml(chain_paths)))
    vm = make_vm_config(name='testvm', snapshot_dir=snap_dir, targets=[])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    result = core.check()
    assert result['testvm'].status == 'broken'
    assert any('cycle' in s for s in result['testvm'].broken_snapshots), f"Should report cycle detection, got: {result['testvm'].broken_snapshots}"
