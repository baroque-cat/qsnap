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
