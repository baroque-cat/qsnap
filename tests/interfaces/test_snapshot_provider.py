"""Contract tests: ISnapshotProvider ABC and concrete implementations.

Verifies that every implementation of ISnapshotProvider obeys the interface
contract: correct return types, ABC enforcement, and no Core inheritance (D1).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import VMConfig
from qsnap.models.results import ShellResult, SnapshotInfo, SnapshotResult
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from tests.mocks.mock_modules import MockSnapshotProvider
from tests.mocks.mock_shell import MockShell


def test_isnapshot_provider_is_abstract():
    """ISnapshotProvider is an ABC with non-empty abstract methods.

    It cannot be instantiated directly.
    """
    assert hasattr(ISnapshotProvider, "__abstractmethods__")
    assert len(ISnapshotProvider.__abstractmethods__) > 0
    with pytest.raises(TypeError):
        ISnapshotProvider()  # type: ignore[abstract]


def test_external_snapshot_provider_is_isnapshot_provider():
    """ExternalSnapshotProvider is a subclass of ISnapshotProvider."""
    assert issubclass(ExternalSnapshotProvider, ISnapshotProvider)


def test_external_snapshot_provider_no_core_inheritance():
    """ExternalSnapshotProvider does NOT inherit from Core (design D1)."""
    assert not issubclass(ExternalSnapshotProvider, Core)


def test_external_snapshot_provider_cannot_instantiate_without_shell():
    """ExternalSnapshotProvider requires an IShell constructor argument."""
    with pytest.raises(TypeError):
        ExternalSnapshotProvider()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (ExternalSnapshotProvider, {"shell": MockShell()}),
        (MockSnapshotProvider, {}),
    ],
    ids=["external", "mock"],
)
def test_snapshot_provider_create_returns_result(cls, init_kwargs):
    """create() returns a SnapshotResult with a boolean success field."""
    provider = cls(**init_kwargs)
    vm_config = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    result = provider.create(vm_config, "test-snap", "vda", Path("/tmp/snap.qcow2"), quiesce=False)
    assert isinstance(result, SnapshotResult)
    assert isinstance(result.success, bool)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (ExternalSnapshotProvider, {"shell": MockShell()}),
        (MockSnapshotProvider, {}),
    ],
    ids=["external", "mock"],
)
def test_snapshot_provider_list_returns_list_of_snapshotinfo(cls, init_kwargs):
    """list() returns a list whose elements are all SnapshotInfo."""
    provider = cls(**init_kwargs)
    vm_config = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    result = provider.list(vm_config)
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, SnapshotInfo)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (ExternalSnapshotProvider, {"shell": MockShell()}),
        (MockSnapshotProvider, {}),
    ],
    ids=["external", "mock"],
)
def test_snapshot_provider_delete_returns_shellresult(cls, init_kwargs):
    """delete() returns a ShellResult."""
    provider = cls(**init_kwargs)
    snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    result = provider.delete(snapshot)
    assert isinstance(result, ShellResult)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (ExternalSnapshotProvider, {"shell": MockShell()}),
        (MockSnapshotProvider, {}),
    ],
    ids=["external", "mock"],
)
def test_snapshot_provider_create_returns_no_content_hash(cls, init_kwargs):
    """create() returns a SnapshotResult — content_hash field has been removed.

    ``SnapshotResult`` no longer carries ``content_hash``.  Accessing
    ``result.content_hash`` raises ``AttributeError``.
    """
    provider = cls(**init_kwargs)
    vm_config = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    result = provider.create(vm_config, "test-snap", "vda", Path("/tmp/snap.qcow2"), quiesce=False)
    assert isinstance(result, SnapshotResult)
    # content_hash field was removed — accessing it must raise AttributeError.
    with pytest.raises(AttributeError):
        _ = result.content_hash  # type: ignore[reportAttributeAccessIssue]
