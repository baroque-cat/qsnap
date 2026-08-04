"""Tests for Core.schedule_summary() — count-based retention preview.

``schedule_summary()`` produces a human-readable string containing:
- ``chain_length`` and ``keep_generations`` per context (snapshots, targets)
- current snapshot and chain counts from ``IStateManager``
- real base image actual-size from ``qemu-img info``
- average incremental snapshot size from state history

No synthetic timestamps, retention windows, or size projections.
"""
from __future__ import annotations

import json
import logging
from argparse import Namespace
from datetime import datetime, timedelta
from pathlib import Path

from qsnap.core import Core
from qsnap.models.results import ShellResult, SnapshotInfo
from tests.mocks import MockConfigFacade


def test_empty_state_summary(make_vm_config, mock_factory, mock_state, mock_shell):
    """No VMs configured → schedule_summary returns an empty string."""
    config = MockConfigFacade(vms=[])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    summary = core.schedule_summary()
    assert summary == ''

def test_summary_includes_all_vms_no_filter(make_vm_config, make_target, mock_factory, mock_state, mock_shell):
    """When vm_filter=None, summary includes all VMs in config."""
    vm1 = make_vm_config(name='vm1', targets=[make_target(path='/mnt/backup/vm1')])
    vm2 = make_vm_config(name='vm2', targets=[make_target(path='/mnt/backup/vm2')])
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    summary = core.schedule_summary()
    assert '=== vm1 ===' in summary
    assert '=== vm2 ===' in summary

def test_summary_filters_by_vm_name(make_vm_config, make_target, mock_factory, mock_state, mock_shell):
    """When vm_filter is set, only that VM is shown."""
    vm1 = make_vm_config(name='vm1', targets=[make_target(path='/mnt/backup/vm1')])
    vm2 = make_vm_config(name='vm2', targets=[make_target(path='/mnt/backup/vm2')])
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    summary = core.schedule_summary(vm_filter='vm2')
    assert '=== vm2 ===' in summary
    assert '=== vm1 ===' not in summary

def test_summary_logs_info_on_timer(make_vm_config, make_target, mock_factory, mock_state, mock_shell, caplog):
    """When --timer flag is set, schedule_summary output is logged at INFO."""
    vm = make_vm_config(name='testvm', targets=[make_target(path='/mnt/backup/testvm')])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    args = Namespace(timer=True, print_schedule=False, vm=[])
    caplog.set_level(logging.INFO)
    from qsnap.cli.commands import _handle_schedule_and_timer
    _handle_schedule_and_timer(core, args)
    assert 'Schedule summary' in caplog.text

def test_summary_shows_snapshot_and_backup_counts(make_vm_config, make_target, mock_factory, mock_state, mock_shell):
    """Summary includes current snapshot count and current chain count from state."""
    snap_base = datetime(2025, 6, 1, 12, 0)
    for i in range(4):
        mock_state.record_snapshot('testvm', SnapshotInfo(name=f'snap.{i}', path=Path(f'/var/lib/libvirt/snapshots/testvm/snap.{i}.qcow2'), timestamp=snap_base + timedelta(hours=i), allocation=1048576, disk='vda'))
    target_path = '/mnt/backup/testvm'
    for i in range(2):
        mock_state.record_full_backup(target_path, name=f'testvm.FULL.{i}', timestamp=snap_base + timedelta(days=i), disk='vda')
    vm = make_vm_config(name='testvm', targets=[make_target(path=target_path)])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    summary = core.schedule_summary()
    assert 'Snapshots:' in summary
    assert 'Current chain: 4 snapshots' in summary
    assert f'Backups [{target_path}]:' in summary
    assert 'Current chains: 2' in summary

def test_summary_includes_base_image_size(make_vm_config, make_target, mock_factory, mock_state, mock_shell):
    """Summary includes real base image actual-size from qemu-img info."""
    base_image = '/var/lib/libvirt/images/testvm.qcow2'
    vm = make_vm_config(name='testvm', base_image=base_image, targets=[make_target(path='/mnt/backup/testvm')])
    config = MockConfigFacade(vms=[vm])
    info_json = json.dumps({'actual-size': 1073741824})
    mock_shell.expect('qemu-img info.*--output=json').returns(ShellResult(success=True, stdout=info_json, stderr='', returncode=0, error=None))
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    summary = core.schedule_summary()
    assert 'Current allocated: ~1.0 GB' in summary

def test_summary_includes_avg_incremental_size(make_vm_config, make_target, mock_factory, mock_state, mock_shell):
    """Summary includes average incremental size computed from state snapshot history."""
    snap_base = datetime(2025, 6, 1, 12, 0)
    allocations = [524288, 1048576, 1572864, 2097152, 2621440]
    for i, alloc in enumerate(allocations):
        mock_state.record_snapshot('testvm', SnapshotInfo(name=f'snap.{i}', path=Path(f'/var/lib/libvirt/snapshots/testvm/snap.{i}.qcow2'), timestamp=snap_base + timedelta(hours=i), allocation=alloc, disk='vda'))
    vm = make_vm_config(name='testvm', targets=[make_target(path='/mnt/backup/testvm')])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    summary = core.schedule_summary()
    assert 'Avg incremental:' in summary
    assert 'last 5 snapshots' in summary
    assert '0.0 GB' in summary
