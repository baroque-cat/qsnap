"""Mock verification: MockVMModuleFactory returns correct interface types."""

from __future__ import annotations

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import RetentionItem, RetentionResult
from tests.mocks.mock_factory import MockVMModuleFactory
from tests.mocks.mock_modules import (
    MockBackupProvider,
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
