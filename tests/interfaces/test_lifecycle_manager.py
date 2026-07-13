"""Contract tests: ILifecycleManager ABC and concrete implementations.

Verifies that every implementation of ILifecycleManager obeys the interface
contract: correct return types, ABC enforcement, and no Core inheritance (D1).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.models.config import VMConfig
from qsnap.models.results import CommitResult, SnapshotInfo
from qsnap.modules.lifecycle.blockcommit_manager import BlockCommitManager
from tests.mocks.mock_modules import MockLifecycleManager
from tests.mocks.mock_shell import MockShell


def test_ilifecycle_manager_is_abstract():
    """ILifecycleManager is an ABC with non-empty abstract methods.

    It cannot be instantiated directly.
    """
    assert hasattr(ILifecycleManager, "__abstractmethods__")
    assert len(ILifecycleManager.__abstractmethods__) > 0
    with pytest.raises(TypeError):
        ILifecycleManager()  # type: ignore[abstract]


def test_blockcommit_manager_is_ilifecycle_manager():
    """BlockCommitManager is a subclass of ILifecycleManager."""
    assert issubclass(BlockCommitManager, ILifecycleManager)


def test_blockcommit_manager_no_core_inheritance():
    """BlockCommitManager does NOT inherit from Core (design D1)."""
    assert not issubclass(BlockCommitManager, Core)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (BlockCommitManager, {"shell": MockShell()}),
        (MockLifecycleManager, {}),
    ],
    ids=["blockcommit", "mock"],
)
def test_lifecycle_manager_blockcommit_returns_commit_result(cls, init_kwargs):
    """blockcommit() returns a CommitResult."""
    manager = cls(**init_kwargs)
    vm_config = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    snapshots = [
        SnapshotInfo(
            name="test-snap",
            path=Path("/tmp/snap.qcow2"),
            timestamp=datetime.now(),
            allocation=65536,
        )
    ]
    result = manager.blockcommit(vm_config, snapshots)
    assert isinstance(result, CommitResult)


def test_blockcommit_manager_requires_shell():
    """BlockCommitManager requires an IShell constructor argument."""
    with pytest.raises(TypeError):
        BlockCommitManager()  # type: ignore[call-arg]
