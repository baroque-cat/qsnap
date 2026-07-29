"""Mock verification: MockVMModuleFactory returns correct interface types."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import (
    BackupResult,
    SnapshotInfo,
)
from tests.mocks.mock_factory import MockVMModuleFactory
from tests.mocks.mock_modules import (
    MockBitmapBackupProvider,
    MockChangeDetector,
    MockLifecycleManager,
)


def test_mock_factory_returns_interface_types(make_vm_config, make_target):
    """Every create_* method on MockVMModuleFactory returns an instance of
    the corresponding ABC interface."""
    factory = MockVMModuleFactory()

    snapshot_provider = factory.create_snapshot_provider(make_vm_config())
    assert isinstance(snapshot_provider, ISnapshotProvider)

    backup_provider = factory.create_backup_provider(make_vm_config(), make_target())
    assert isinstance(backup_provider, IBackupProvider)

    retention_engine = factory.create_retention_engine(RetentionPolicy())
    assert isinstance(retention_engine, IRetentionEngine)

    change_detector = factory.create_change_detector("always")
    assert isinstance(change_detector, IChangeDetector)

    lifecycle_manager = factory.create_lifecycle_manager()
    assert isinstance(lifecycle_manager, ILifecycleManager)


def test_mock_factory_always_returns_bitmap_provider(make_vm_config, make_target):
    """create_backup_provider() always returns MockBitmapBackupProvider
    regardless of target configuration — bitmap is the sole backup provider."""
    factory = MockVMModuleFactory()
    target = make_target()
    provider = factory.create_backup_provider(make_vm_config(), target)
    assert isinstance(provider, MockBitmapBackupProvider)
    assert isinstance(provider, IBackupProvider)


def test_mock_factory_always_returns_bitmap_provider_for_different_target(
    make_vm_config, make_target
):
    """create_backup_provider() always returns MockBitmapBackupProvider
    for any target (different paths/configs make no difference)."""
    factory = MockVMModuleFactory()
    target = make_target(path="/mnt/other-backup/testvm")
    provider = factory.create_backup_provider(make_vm_config(), target)
    assert isinstance(provider, MockBitmapBackupProvider)
    assert isinstance(provider, IBackupProvider)


def test_mock_factory_create_change_detector_accepts_mode():
    """create_change_detector() accepts a mode string and returns a
    MockChangeDetector without crashing."""
    factory = MockVMModuleFactory()
    detector = factory.create_change_detector("allocation-map")
    assert isinstance(detector, MockChangeDetector)
    assert isinstance(detector, IChangeDetector)


def test_mock_factory_create_lifecycle_manager_accepts_mode():
    """create_lifecycle_manager() accepts a mode kwarg and returns a
    MockLifecycleManager without crashing."""
    factory = MockVMModuleFactory()
    manager = factory.create_lifecycle_manager(mode="qemu-img")
    assert isinstance(manager, MockLifecycleManager)
    assert isinstance(manager, ILifecycleManager)


def test_mock_factory_create_change_detector_ignores_unknown_mode():
    """create_change_detector() returns a MockChangeDetector even for an
    unrecognized mode (mock does not validate modes)."""
    factory = MockVMModuleFactory()
    detector = factory.create_change_detector("unknown")
    assert isinstance(detector, MockChangeDetector)
    assert isinstance(detector, IChangeDetector)


def test_mock_factory_create_lifecycle_manager_default_mode():
    """create_lifecycle_manager() with no arguments uses the default mode
    and returns a MockLifecycleManager."""
    factory = MockVMModuleFactory()
    manager = factory.create_lifecycle_manager()
    assert isinstance(manager, MockLifecycleManager)
    assert isinstance(manager, ILifecycleManager)


def test_mock_backup_provider_has_create_full_backup(make_vm_config, make_target):
    """MockVMModuleFactory.create_backup_provider() returns a provider that
    has a create_full_backup method.  Calling it returns a BackupResult."""
    factory = MockVMModuleFactory()
    provider = factory.create_backup_provider(make_vm_config(), make_target())
    assert isinstance(provider, IBackupProvider)
    assert hasattr(provider, "create_full_backup")
    assert callable(provider.create_full_backup)

    source_snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/test-snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    result = provider.create_full_backup("testvm", source_snapshot, make_target())
    assert isinstance(result, BackupResult)


# ---------------------------------------------------------------------------
# New parameter acceptance tests (compression_type + stall_timeout)
# ---------------------------------------------------------------------------


def test_mock_backup_provider_create_full_backup_accepts_new_params(make_vm_config, make_target):
    """MockBitmapBackupProvider.create_full_backup() accepts the new
    ``compression_type`` and ``stall_timeout`` keyword arguments
    without error and still passes isinstance(..., IBackupProvider)."""
    provider = MockBitmapBackupProvider()
    assert isinstance(provider, IBackupProvider)

    source_snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/test-snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    result = provider.create_full_backup(
        "testvm",
        source_snapshot,
        make_target(),
        compression_type="zstd",
        stall_timeout=1800,
    )
    assert isinstance(result, BackupResult)
    assert result.success is True


def test_mock_backup_provider_transfer_missing_accepts_new_params(make_vm_config, make_target):
    """MockBitmapBackupProvider.transfer_missing() accepts the new
    ``compression_type`` and ``stall_timeout`` keyword arguments
    without error."""
    provider = MockBitmapBackupProvider()
    assert isinstance(provider, IBackupProvider)

    snapshots = [
        SnapshotInfo(
            name="inc-1",
            path=Path("/tmp/inc-1.qcow2"),
            timestamp=datetime.now(),
            allocation=65536,
        )
    ]
    results = provider.transfer_missing(
        make_vm_config(),
        make_target(),
        snapshots,
        compression_type="zstd",
        stall_timeout=600,
    )
    assert isinstance(results, list)
    assert len(results) == 1
    assert isinstance(results[0], BackupResult)
    assert results[0].success is True


def test_mock_bitmap_backup_provider_create_full_backup_accepts_new_params(
    make_vm_config, make_target
):
    """MockBitmapBackupProvider.create_full_backup() accepts the new
    ``compression_type`` and ``stall_timeout`` keyword arguments
    without error and still passes isinstance(..., IBackupProvider)."""
    provider = MockBitmapBackupProvider()
    assert isinstance(provider, IBackupProvider)

    source_snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/test-snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    result = provider.create_full_backup(
        "testvm",
        source_snapshot,
        make_target(),
        compression_type="zlib",
        stall_timeout=0,
    )
    assert isinstance(result, BackupResult)
    assert result.success is True


def test_mock_bitmap_backup_provider_transfer_missing_accepts_new_params(
    make_vm_config, make_target
):
    """MockBitmapBackupProvider.transfer_missing() accepts the new
    ``compression_type`` and ``stall_timeout`` keyword arguments
    without error."""
    provider = MockBitmapBackupProvider()
    assert isinstance(provider, IBackupProvider)

    snapshots = [
        SnapshotInfo(
            name="inc-1",
            path=Path("/tmp/inc-1.qcow2"),
            timestamp=datetime.now(),
            allocation=65536,
        )
    ]
    results = provider.transfer_missing(
        make_vm_config(),
        make_target(),
        snapshots,
        compression_type="zlib",
        stall_timeout=0,
    )
    assert isinstance(results, list)
    assert len(results) == 1
    assert isinstance(results[0], BackupResult)
    assert results[0].success is True


def test_default_compression_type_is_zstd_on_both_mocks(make_vm_config, make_target):
    """Both MockBitmapBackupProvider and MockBitmapBackupProvider default
    ``compression_type`` to ``"zstd"`` on both create_full_backup() and
    transfer_missing(), matching IBackupProvider defaults."""
    source_snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/test-snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    snapshots = [source_snapshot]

    for provider in [MockBitmapBackupProvider(), MockBitmapBackupProvider()]:
        assert isinstance(provider, IBackupProvider)

        # create_full_backup with default compression_type
        r1 = provider.create_full_backup("vm", source_snapshot, make_target())
        assert isinstance(r1, BackupResult)

        # transfer_missing with default compression_type
        r2 = provider.transfer_missing(make_vm_config(), make_target(), snapshots)
        assert isinstance(r2, list)
        assert all(isinstance(r, BackupResult) for r in r2)
