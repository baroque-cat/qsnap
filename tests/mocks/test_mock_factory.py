"""Mock verification: MockVMModuleFactory returns correct interface types."""

from __future__ import annotations

import inspect
from pathlib import Path

from qsnap.core import Core
from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import BackupResult, SnapshotResult, SnapshotSpec
from tests.mocks.mock_factory import MockVMModuleFactory
from tests.mocks.mock_modules import (
    MockBitmapBackupProvider,
    MockChangeDetector,
    MockLifecycleManager,
    MockSnapshotProvider,
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


def test_mock_backup_provider_has_run_backup(make_vm_config, make_target):
    """MockVMModuleFactory.create_backup_provider() returns a provider that
    has a run_backup method.  Calling it returns a BackupResult carrying
    the source disk."""
    factory = MockVMModuleFactory()
    provider = factory.create_backup_provider(make_vm_config(), make_target())
    assert isinstance(provider, IBackupProvider)
    assert hasattr(provider, "run_backup")
    assert callable(provider.run_backup)
    vm_config = make_vm_config()
    result = provider.run_backup(vm_config, make_target(), vm_config.disks[0])
    assert isinstance(result, BackupResult)
    assert result.success is True
    assert result.disk == "vda"


def test_mock_backup_provider_deferred_simulation(make_vm_config, make_target):
    """MockBitmapBackupProvider constructed with ``deferred=True`` returns
    a BackupResult with ``deferred=True`` from run_backup(), simulating the
    stopped-VM-with-checkpoint case."""
    provider = MockBitmapBackupProvider(deferred=True)
    assert isinstance(provider, IBackupProvider)
    vm_config = make_vm_config()
    result = provider.run_backup(vm_config, make_target(), vm_config.disks[0])
    assert isinstance(result, BackupResult)
    assert result.success is True
    assert result.deferred is True


def test_mock_change_detector_accepts_current_allocation_constructor():
    """MockChangeDetector constructor accepts ``current_allocation`` parameter
    and stores it for later retrieval via the property."""
    detector = MockChangeDetector(current_allocation=5000000)
    assert detector.current_allocation == 5000000


def test_mock_change_detector_current_allocation_default_value():
    """MockChangeDetector.current_allocation defaults to 2000000 when not
    specified in constructor."""
    detector = MockChangeDetector()
    assert detector.current_allocation == 2000000


def test_mock_change_detector_current_allocation_setter():
    """MockChangeDetector.current_allocation is a writable property (setter)
    so tests can reconfigure the detector after construction."""
    detector = MockChangeDetector()
    detector.current_allocation = 999000
    assert detector.current_allocation == 999000


def test_mock_change_detector_has_changed_returns_configured_allocation(make_vm_config):
    """MockChangeDetector.has_changed() returns a ChangeResult whose
    ``current_allocation`` matches the value configured on the detector."""
    detector = MockChangeDetector(current_allocation=3000000)
    result = detector.has_changed(make_vm_config(), disk="vda")
    assert result.current_allocation == 3000000
    assert result.changed is True
    assert result.last_allocation == 1000000


def test_mock_change_detector_has_changed_after_setter(make_vm_config):
    """After mutating ``current_allocation`` via the setter,
    ``has_changed()`` returns the updated value."""
    detector = MockChangeDetector()
    detector.current_allocation = 888000
    result = detector.has_changed(make_vm_config(), disk="vda")
    assert result.current_allocation == 888000


def test_mock_factory_change_detector_property():
    """MockVMModuleFactory.change_detector property returns the
    MockChangeDetector instance that the factory holds internally."""
    factory = MockVMModuleFactory()
    cd = factory.change_detector
    assert isinstance(cd, MockChangeDetector)
    assert isinstance(cd, IChangeDetector)


def test_mock_factory_create_change_detector_returns_same_instance():
    """MockVMModuleFactory.create_change_detector() returns the same
    MockChangeDetector instance as the ``change_detector`` property,
    so tests can configure one and it affects all calls."""
    factory = MockVMModuleFactory()
    cd_prop = factory.change_detector
    cd_create = factory.create_change_detector("allocation-size")
    assert cd_prop is cd_create


def test_mock_factory_change_detector_configuration_flows_through_create():
    """Configuring ``current_allocation`` on ``factory.change_detector``
    is visible through ``factory.create_change_detector()`` — both
    reference the same object."""
    factory = MockVMModuleFactory()
    factory.change_detector.current_allocation = 777000
    detector = factory.create_change_detector("allocation-map")
    assert detector.current_allocation == 777000


def test_default_compression_type_is_zstd_on_mock(make_vm_config, make_target):
    """MockBitmapBackupProvider.run_backup() defaults ``compression_type``
    to ``"zstd"`` and ``stall_timeout`` to ``1800``, matching
    IBackupProvider defaults, and returns a successful BackupResult."""
    provider = MockBitmapBackupProvider()
    assert isinstance(provider, IBackupProvider)
    sig = inspect.signature(provider.run_backup)
    assert sig.parameters["compression_type"].default == "zstd"
    assert sig.parameters["stall_timeout"].default == 1800
    vm_config = make_vm_config()
    result = provider.run_backup(vm_config, make_target(), vm_config.disks[0])
    assert isinstance(result, BackupResult)
    assert result.success is True


def test_mock_snapshot_provider_has_create_multi(make_vm_config):
    """MockSnapshotProvider.create_multi returns list[SnapshotResult].

    The mock satisfies ISnapshotProvider, does not inherit from Core
    (design D1), and implements ``create_multi`` returning one
    ``SnapshotResult`` per spec, in spec order.
    """
    provider = MockSnapshotProvider()
    assert isinstance(provider, ISnapshotProvider)
    assert not isinstance(provider, Core), (
        "MockSnapshotProvider must not inherit from Core (design D1)"
    )
    assert hasattr(provider, "create_multi"), "MockSnapshotProvider must define create_multi()"
    assert callable(provider.create_multi)

    specs = [
        SnapshotSpec(disk="vda", name="test-snap-vda", path=Path("/tmp/testvm_vda.qcow2")),
        SnapshotSpec(disk="vdb", name="test-snap-vdb", path=Path("/tmp/testvm_vdb.qcow2")),
    ]
    results = provider.create_multi(make_vm_config(), specs, quiesce=True)
    assert isinstance(results, list)
    assert len(results) == len(specs)
    assert all(isinstance(r, SnapshotResult) for r in results)


def test_mock_factory_snapshot_provider_create_multi(make_vm_config):
    """MockVMModuleFactory.create_snapshot_provider() returns a provider
    whose create_multi() returns one SnapshotResult per spec."""
    factory = MockVMModuleFactory()
    provider = factory.create_snapshot_provider(make_vm_config())
    assert isinstance(provider, ISnapshotProvider)
    assert isinstance(provider, MockSnapshotProvider)

    specs = [
        SnapshotSpec(disk="vda", name="batch-vda", path=Path("/tmp/batch_vda.qcow2")),
        SnapshotSpec(disk="vdb", name="batch-vdb", path=Path("/tmp/batch_vdb.qcow2")),
    ]
    results = provider.create_multi(make_vm_config(), specs, quiesce=False)
    assert isinstance(results, list)
    assert len(results) == len(specs)
    for result, spec in zip(results, specs, strict=False):
        assert isinstance(result, SnapshotResult)
        assert result.success is True
        assert result.disk == spec.disk
        assert result.name == spec.name
        assert result.path == spec.path
