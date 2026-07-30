"""MockVMModuleFactory — mock IVMModuleFactory for unit tests.

Returns mock instances for all ``create_*`` methods that satisfy
``isinstance(result, ABC)``.
"""

from __future__ import annotations

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.factory import IVMModuleFactory
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import RetentionPolicy, TargetConfig, VMConfig
from tests.mocks.mock_modules import (
    MockBitmapBackupProvider,
    MockChangeDetector,
    MockLifecycleManager,
    MockRetentionEngine,
    MockSnapshotProvider,
)


class MockVMModuleFactory(IVMModuleFactory):
    """Mock factory returning mock module instances for all create_* methods."""

    def __init__(self) -> None:
        self._snapshot_provider = MockSnapshotProvider()
        self._bitmap_backup_provider = MockBitmapBackupProvider()
        # The factory always returns the bitmap backup provider.
        # Alias _backup_provider so existing tests that spy on it
        # continue to work.
        self._backup_provider = self._bitmap_backup_provider
        self._retention_engine = MockRetentionEngine()
        self._change_detector = MockChangeDetector()
        self._lifecycle_manager = MockLifecycleManager()

    @property
    def change_detector(self) -> MockChangeDetector:
        """Expose the mock change detector for test configuration.

        Core tests configure ``current_allocation`` on this instance to
        simulate "changed" vs "unchanged" source disk for the
        ``backup_create="onchange"`` gate.
        """
        return self._change_detector

    def create_snapshot_provider(self, vm_config: VMConfig) -> ISnapshotProvider:
        return self._snapshot_provider

    def create_backup_provider(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
    ) -> IBackupProvider:
        # Bitmap is the only backup strategy now — always return the
        # bitmap backup provider mock (matches DefaultFactory behaviour).
        return self._bitmap_backup_provider

    def create_retention_engine(self, policy: RetentionPolicy) -> IRetentionEngine:
        return self._retention_engine

    def create_change_detector(self, mode: str) -> IChangeDetector:
        return self._change_detector

    def create_lifecycle_manager(self, mode: str = "virsh") -> ILifecycleManager:
        return self._lifecycle_manager
