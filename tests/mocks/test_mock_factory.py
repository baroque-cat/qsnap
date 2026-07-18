"""Mock verification: MockVMModuleFactory returns correct interface types."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.bucket_strategy import IBucketFullStrategy
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.factory import IVMModuleFactory
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import (
    BackupResult,
    RetentionItem,
    RetentionResult,
    SnapshotInfo,
    SnapshotResult,
)
from tests.mocks.mock_factory import MockVMModuleFactory
from tests.mocks.mock_modules import (
    MockBackupProvider,
    MockBitmapBackupProvider,
    MockBucketFullStrategy,
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


def test_mock_retention_engine_accepts_preserve_day_of_week(make_vm_config):
    """MockRetentionEngine.evaluate accepts preserve_day_of_week kwarg."""
    from datetime import datetime

    factory = MockVMModuleFactory()
    engine = factory.create_retention_engine(RetentionPolicy())
    items = [RetentionItem(name="snap1", timestamp=datetime(2025, 1, 6, 12, 0))]
    result = engine.evaluate(
        items,
        RetentionPolicy(),
        now=datetime(2025, 1, 6, 12, 0),
        preserve_day_of_week="wednesday",
    )
    assert isinstance(result, RetentionResult)


def test_mock_factory_returns_bitmap_provider_for_bitmap_mode(make_vm_config, make_target):
    """create_backup_provider() returns MockBitmapBackupProvider when
    target.incremental_mode == 'bitmap'."""
    factory = MockVMModuleFactory()
    target = make_target(incremental_mode="bitmap")
    provider = factory.create_backup_provider(make_vm_config(), target)
    assert isinstance(provider, MockBitmapBackupProvider)
    assert isinstance(provider, IBackupProvider)


def test_mock_factory_returns_file_copy_provider_for_default_mode(make_vm_config, make_target):
    """create_backup_provider() returns MockBackupProvider for default mode."""
    factory = MockVMModuleFactory()
    target = make_target(incremental_mode="file-copy")
    provider = factory.create_backup_provider(make_vm_config(), target)
    assert isinstance(provider, MockBackupProvider)
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


def test_mock_snapshot_provider_returns_content_hash(make_vm_config):
    """MockVMModuleFactory.create_snapshot_provider() returns a provider
    whose create() returns a SnapshotResult with content_hash being a
    64-char hex string (not None)."""
    factory = MockVMModuleFactory()
    provider = factory.create_snapshot_provider(make_vm_config())
    assert isinstance(provider, ISnapshotProvider)

    result = provider.create(
        make_vm_config(),
        "test-snap",
        "vda",
        Path("/tmp/test-snap.qcow2"),
    )
    assert isinstance(result, SnapshotResult)
    assert result.content_hash is not None
    assert len(result.content_hash) == 64
    # Verify it is a valid hex string (0-9, a-f).
    int(result.content_hash, 16)


def test_mock_factory_create_bucket_full_strategy_returns_mock():
    """create_bucket_full_strategy() returns a MockBucketFullStrategy that
    also satisfies ``isinstance(..., IBucketFullStrategy)``."""
    factory = MockVMModuleFactory()
    strategy = factory.create_bucket_full_strategy()
    assert isinstance(strategy, IBucketFullStrategy)
    assert isinstance(strategy, MockBucketFullStrategy)


def test_mock_factory_satisfies_new_interface():
    """MockVMModuleFactory passes ``isinstance(..., IVMModuleFactory)``
    after the addition of ``create_bucket_full_strategy`` to the ABC."""
    factory = MockVMModuleFactory()
    assert isinstance(factory, IVMModuleFactory) is True
