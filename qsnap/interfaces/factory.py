"""IVMModuleFactory — abstract factory for creating domain module instances."""

from __future__ import annotations

from abc import ABC, abstractmethod

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.bucket_strategy import IBucketFullStrategy
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import RetentionPolicy, TargetConfig, VMConfig


class IVMModuleFactory(ABC):
    """Abstract factory that creates all module instances for a given VM.

    One factory creates ALL module instances for a given VM + target
    combination.  ``Core`` holds a reference to the factory interface and
    calls it per-VM during pipeline execution.

    Tests inject ``MockFactory``; production injects ``DefaultFactory``.
    Adding a new snapshot strategy means: (a) implement
    ``ISnapshotProvider``, (b) add a branch in ``DefaultFactory``.  Nothing
    else changes.
    """

    @abstractmethod
    def create_snapshot_provider(self, vm_config: VMConfig) -> ISnapshotProvider:
        """Create a snapshot provider for *vm_config*."""
        ...

    @abstractmethod
    def create_backup_provider(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
    ) -> IBackupProvider:
        """Create a backup provider for *vm_config* and *target*."""
        ...

    @abstractmethod
    def create_retention_engine(self, policy: RetentionPolicy) -> IRetentionEngine:
        """Create a retention engine for *policy*."""
        ...

    @abstractmethod
    def create_change_detector(self, mode: str) -> IChangeDetector:
        """Create a change detector for the given *mode*."""
        ...

    @abstractmethod
    def create_lifecycle_manager(self, mode: str = "virsh") -> ILifecycleManager:
        """Create a lifecycle manager for the given *mode*.

        ``mode`` selects the merge strategy: ``"virsh"`` (blockcommit,
        default) or ``"qemu-img"`` (commit).
        """
        ...

    @abstractmethod
    def create_bucket_full_strategy(self) -> IBucketFullStrategy:
        """Create a bucket FULL backup strategy.

        The strategy decides whether a new FULL backup should be created
        for a given snapshot timestamp based on bucket/anchor retention
        policy.  Used by ``Core._backup_target()`` to delegate the bucket
        decision out of the orchestrator.
        """
        ...
