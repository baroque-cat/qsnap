"""Mock validity tests for configurable-full-backup-engine change.

Verifies that MockBackupProvider and MockBitmapBackupProvider accept the
new ``full_transfer_engine``, ``convert_parallel``, and
``convert_out_of_order`` keyword arguments on both ``create_full_backup()``
and ``transfer_missing()``, and that ``MockVMModuleFactory.create_backup_provider()``
returns correct ``IBackupProvider`` instances.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.interfaces.backup import IBackupProvider
from qsnap.models.results import BackupResult, SnapshotInfo
from tests.mocks.mock_factory import MockVMModuleFactory
from tests.mocks.mock_modules import (
    MockBackupProvider,
    MockBitmapBackupProvider,
)


def test_mock_backup_provider_accepts_new_kwargs(make_vm_config, make_target):
    """MockBackupProvider.create_full_backup() and transfer_missing() accept
    the new ``full_transfer_engine``, ``convert_parallel``, and
    ``convert_out_of_order`` keyword arguments without error."""
    provider = MockBackupProvider()
    assert isinstance(provider, IBackupProvider)

    source_snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/test-snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    snapshots = [source_snapshot]
    target = make_target()

    # --- create_full_backup with all 3 new kwargs ---
    result = provider.create_full_backup(
        "testvm",
        source_snapshot,
        target,
        compress=True,
        compression_type="zstd",
        stall_timeout=1800,
        full_transfer_engine="libnbd",
        convert_parallel=8,
        convert_out_of_order=False,
    )
    assert isinstance(result, BackupResult)
    assert result.success is True

    # --- transfer_missing with all 3 new kwargs ---
    results = provider.transfer_missing(
        make_vm_config(),
        target,
        snapshots,
        compression_type="zstd",
        stall_timeout=1800,
        full_transfer_engine="libnbd",
        convert_parallel=2,
        convert_out_of_order=True,
    )
    assert isinstance(results, list)
    assert len(results) == 1
    assert isinstance(results[0], BackupResult)
    assert results[0].success is True


def test_mock_bitmap_backup_provider_accepts_new_kwargs(make_vm_config, make_target):
    """MockBitmapBackupProvider.create_full_backup() and transfer_missing()
    accept the new ``full_transfer_engine``, ``convert_parallel``, and
    ``convert_out_of_order`` keyword arguments without error."""
    provider = MockBitmapBackupProvider()
    assert isinstance(provider, IBackupProvider)

    source_snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/test-snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    snapshots = [source_snapshot]
    target = make_target()

    # --- create_full_backup with all 3 new kwargs ---
    result = provider.create_full_backup(
        "testvm",
        source_snapshot,
        target,
        compress=True,
        compression_type="zstd",
        stall_timeout=1800,
        full_transfer_engine="libnbd",
        convert_parallel=8,
        convert_out_of_order=False,
    )
    assert isinstance(result, BackupResult)
    assert result.success is True

    # --- transfer_missing with all 3 new kwargs ---
    results = provider.transfer_missing(
        make_vm_config(),
        target,
        snapshots,
        compression_type="zstd",
        stall_timeout=1800,
        full_transfer_engine="libnbd",
        convert_parallel=2,
        convert_out_of_order=True,
    )
    assert isinstance(results, list)
    assert len(results) == 1
    assert isinstance(results[0], BackupResult)
    assert results[0].success is True


def test_mock_factory_backup_provider_returns_correct_interface(make_vm_config, make_target):
    """MockVMModuleFactory.create_backup_provider() returns an
    ``IBackupProvider`` instance that also accepts the 3 new kwargs
    on ``create_full_backup()`` and ``transfer_missing()``."""
    factory = MockVMModuleFactory()
    vm_config = make_vm_config()
    target = make_target()

    provider = factory.create_backup_provider(vm_config, target)

    # Must satisfy the IBackupProvider interface
    assert isinstance(provider, IBackupProvider)

    # And must accept the 3 new kwargs (verify via call)
    source_snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/test-snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )

    result = provider.create_full_backup(
        "testvm",
        source_snapshot,
        target,
        full_transfer_engine="qemu-img-convert",
        convert_parallel=4,
        convert_out_of_order=True,
    )
    assert isinstance(result, BackupResult)
    assert result.success is True
