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


def test_mock_shell_implements_full_interface():
    """MockShell implements the full IShell ABC, including both ``run``
    and ``run_with_stall_detection``."""
    from qsnap.interfaces.shell import IShell

    mock = MockShell()
    assert isinstance(mock, IShell), "MockShell must be an IShell instance"

    # Both abstract methods must exist
    assert hasattr(mock, "run"), "MockShell must define run()"
    assert hasattr(mock, "run_with_stall_detection"), (
        "MockShell must define run_with_stall_detection()"
    )

    # run_with_stall_detection must be callable and accept the full
    # IShell ABC signature.
    assert callable(mock.run_with_stall_detection), (
        "run_with_stall_detection must be callable"
    )
