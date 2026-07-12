"""DefaultFactory — concrete IVMModuleFactory.

Constructor receives ``IShell`` and ``IStateManager``, storing them for
injection into module constructors.  Methods for modules not yet
implemented raise ``NotImplementedError``.  The retention engine factory
method works immediately (``TimeBasedRetention`` is pure, no Core
dependency).
"""

from __future__ import annotations

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.factory import IVMModuleFactory
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import RetentionPolicy, TargetConfig, VMConfig
from qsnap.retention.time_based import TimeBasedRetention


class DefaultFactory(IVMModuleFactory):
    """Concrete factory that creates module instances per VM + target.

    Adding a new snapshot strategy means: (a) implement
    ``ISnapshotProvider``, (b) add a branch here.  Nothing else changes.
    """

    def __init__(self, shell: IShell, state: IStateManager) -> None:
        self._shell = shell
        self._state = state

    def create_snapshot_provider(self, vm_config: VMConfig) -> ISnapshotProvider:
        raise NotImplementedError("SnapshotProvider not yet implemented")

    def create_backup_provider(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
    ) -> IBackupProvider:
        raise NotImplementedError("BackupProvider not yet implemented")

    def create_retention_engine(self, policy: RetentionPolicy) -> IRetentionEngine:
        return TimeBasedRetention(policy)

    def create_change_detector(self, mode: str) -> IChangeDetector:
        raise NotImplementedError("ChangeDetector not yet implemented")

    def create_lifecycle_manager(self) -> ILifecycleManager:
        raise NotImplementedError("LifecycleManager not yet implemented")
