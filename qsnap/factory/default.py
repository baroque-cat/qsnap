"""DefaultFactory — concrete IVMModuleFactory.

Constructor receives ``IShell`` and ``IStateManager``, storing them for
injection into module constructors.  All five ``create_*`` methods return
concrete module instances.
"""

from __future__ import annotations

import logging
from pathlib import Path

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.factory import IVMModuleFactory
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import RetentionPolicy, TargetConfig, VMConfig
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.modules.backup.file_copy import FileCopyBackupProvider
from qsnap.modules.change.allocation_detector import AllocationSizeDetector
from qsnap.modules.change.map_detector import MapChangeDetector
from qsnap.modules.lifecycle.blockcommit_manager import BlockCommitManager
from qsnap.modules.lifecycle.qemu_img_commit import QemuImgCommitManager
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.retention.time_based import TimeBasedRetention
from qsnap.state.json_manager import JsonStateManager

logger = logging.getLogger(__name__)


class DefaultFactory(IVMModuleFactory):
    """Concrete factory that creates module instances per VM + target.

    Adding a new snapshot strategy means: (a) implement
    ``ISnapshotProvider``, (b) add a branch here.  Nothing else changes.
    """

    def __init__(self, shell: IShell, state: IStateManager) -> None:
        self._shell = shell
        self._state = state

    @staticmethod
    def create_state_manager(
        state_dir: str | Path,
        state_backup_count: int = 2,
    ) -> IStateManager:
        """Create the default concrete ``IStateManager`` (JSON file-backed)."""
        return JsonStateManager(state_dir, state_backup_count=state_backup_count)

    def create_snapshot_provider(self, vm_config: VMConfig) -> ISnapshotProvider:
        return ExternalSnapshotProvider(self._shell)

    def create_backup_provider(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
    ) -> IBackupProvider:
        if target.incremental_mode == "bitmap":
            try:
                return BitmapBackupProvider(self._shell)
            except RuntimeError as exc:
                logger.warning(
                    "BitmapBackupProvider unavailable (%s); "
                    "falling back to FileCopyBackupProvider",
                    exc,
                )
                return FileCopyBackupProvider(self._shell)
        return FileCopyBackupProvider(self._shell)

    def create_retention_engine(self, policy: RetentionPolicy) -> IRetentionEngine:
        return TimeBasedRetention(policy)

    def create_change_detector(self, mode: str) -> IChangeDetector:
        if mode == "allocation-map":
            return MapChangeDetector(self._shell, self._state)
        return AllocationSizeDetector(self._shell, self._state)

    def create_lifecycle_manager(self, mode: str = "virsh") -> ILifecycleManager:
        if mode == "qemu-img":
            return QemuImgCommitManager(self._shell)
        return BlockCommitManager(self._shell)
