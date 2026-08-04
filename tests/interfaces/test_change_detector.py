"""Contract tests: IChangeDetector ABC and concrete implementations.

Verifies that every implementation of IChangeDetector obeys the interface
contract: correct return types, ABC enforcement, and no Core inheritance (D1).

The IChangeDetector interface is unchanged by the
``independent-target-onchange`` spec — the backup gate change detection uses
``IStateManager.get_last_backup_allocation()`` / ``set_last_backup_allocation()``
to track per-target baselines, not IChangeDetector protocol changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.interfaces.change import IChangeDetector
from qsnap.models.config import DiskConfig, VMConfig
from qsnap.models.results import ChangeResult
from qsnap.modules.change.allocation_detector import AllocationSizeDetector
from qsnap.modules.change.map_detector import MapChangeDetector
from tests.mocks.mock_modules import MockChangeDetector
from tests.mocks.mock_shell import MockShell
from tests.mocks.mock_state import InMemoryStateManager


def test_ichange_detector_is_abstract():
    """IChangeDetector is an ABC with non-empty abstract methods.

    It cannot be instantiated directly.
    """
    assert hasattr(IChangeDetector, "__abstractmethods__")
    assert len(IChangeDetector.__abstractmethods__) > 0
    with pytest.raises(TypeError):
        IChangeDetector()  # type: ignore[abstract]


def test_allocation_size_detector_is_ichange_detector():
    """AllocationSizeDetector is a subclass of IChangeDetector."""
    assert issubclass(AllocationSizeDetector, IChangeDetector)


def test_allocation_size_detector_no_core_inheritance():
    """AllocationSizeDetector does NOT inherit from Core (design D1)."""
    assert not issubclass(AllocationSizeDetector, Core)


def test_map_change_detector_is_ichange_detector():
    """MapChangeDetector is a subclass of IChangeDetector."""
    assert issubclass(MapChangeDetector, IChangeDetector)


def test_map_change_detector_no_core_inheritance():
    """MapChangeDetector does NOT inherit from Core (design D1)."""
    assert not issubclass(MapChangeDetector, Core)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (AllocationSizeDetector, {"shell": MockShell(), "state": InMemoryStateManager()}),
        (MapChangeDetector, {"shell": MockShell(), "state": InMemoryStateManager()}),
        (MockChangeDetector, {}),
    ],
    ids=["allocation", "map", "mock"],
)
def test_change_detector_has_changed_returns_change_result(cls, init_kwargs):
    """has_changed() returns a ChangeResult."""
    detector = cls(**init_kwargs)
    vm_config = VMConfig(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    result = detector.has_changed(vm_config, disk="vda")
    assert isinstance(result, ChangeResult)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (AllocationSizeDetector, {"shell": MockShell(), "state": InMemoryStateManager()}),
        (MapChangeDetector, {"shell": MockShell(), "state": InMemoryStateManager()}),
        (MockChangeDetector, {}),
    ],
    ids=["allocation", "map", "mock"],
)
def test_change_detector_has_changed_accepts_disk_parameter(cls, init_kwargs):
    """has_changed() accepts optional disk: str parameter for all implementations."""
    detector = cls(**init_kwargs)
    vm_config = VMConfig(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    result = detector.has_changed(vm_config, disk="vda")
    assert isinstance(result, ChangeResult)


def test_allocation_size_detector_requires_shell_and_state():
    """AllocationSizeDetector requires both IShell and IStateManager constructor arguments."""
    with pytest.raises(TypeError):
        AllocationSizeDetector()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        AllocationSizeDetector(shell=MockShell())  # type: ignore[call-arg]


def test_map_change_detector_requires_shell_and_state():
    """MapChangeDetector requires both IShell and IStateManager constructor arguments."""
    with pytest.raises(TypeError):
        MapChangeDetector()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        MapChangeDetector(shell=MockShell())  # type: ignore[call-arg]
