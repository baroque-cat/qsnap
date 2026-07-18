"""Mocks package — mock implementations of every ABC."""

from __future__ import annotations

from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_factory import MockVMModuleFactory
from tests.mocks.mock_modules import (
    MockBackupProvider,
    MockBitmapBackupProvider,
    MockBucketFullStrategy,
    MockChangeDetector,
    MockLifecycleManager,
    MockRetentionEngine,
    MockSnapshotProvider,
)
from tests.mocks.mock_shell import MockShell
from tests.mocks.mock_state import InMemoryStateManager

__all__ = [
    "InMemoryStateManager",
    "MockBackupProvider",
    "MockBitmapBackupProvider",
    "MockBucketFullStrategy",
    "MockChangeDetector",
    "MockConfigFacade",
    "MockLifecycleManager",
    "MockRetentionEngine",
    "MockShell",
    "MockSnapshotProvider",
    "MockVMModuleFactory",
]
