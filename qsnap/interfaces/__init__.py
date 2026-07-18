"""Interfaces package — ABC interfaces for all domain modules."""

from __future__ import annotations

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.bucket_strategy import IBucketFullStrategy
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.config import IConfigFacade
from qsnap.interfaces.factory import IVMModuleFactory
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.interfaces.state import IStateManager

__all__ = [
    "IBackupProvider",
    "IBucketFullStrategy",
    "IChangeDetector",
    "IConfigFacade",
    "IRetentionEngine",
    "IShell",
    "ISnapshotProvider",
    "IStateManager",
    "IVMModuleFactory",
    "ILifecycleManager",
]
