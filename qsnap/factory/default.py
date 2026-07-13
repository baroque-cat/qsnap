"""DefaultFactory — concrete IVMModuleFactory.

Constructor receives ``IShell`` and ``IStateManager``, storing them for
injection into module constructors.  All five ``create_*`` methods return
concrete module instances.
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
from qsnap.modules.backup.file_copy import FileCopyBackupProvider
from qsnap.modules.change.allocation_detector import AllocationSizeDetector
from qsnap.modules.lifecycle.blockcommit_manager import BlockCommitManager
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
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
        return ExternalSnapshotProvider(self._shell)

    def create_backup_provider(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
    ) -> IBackupProvider:
        return FileCopyBackupProvider(self._shell)

    def create_retention_engine(self, policy: RetentionPolicy) -> IRetentionEngine:
        return TimeBasedRetention(policy)

    def create_change_detector(self, mode: str) -> IChangeDetector:
        return AllocationSizeDetector(self._shell, self._state)

    def create_lifecycle_manager(self) -> ILifecycleManager:
        return BlockCommitManager(self._shell)
