"""Contract tests: ISnapshotProvider ABC and concrete implementations.

Verifies that every implementation of ISnapshotProvider obeys the interface
contract: correct return types, ABC enforcement, and no Core inheritance (D1).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import DiskConfig, VMConfig
from qsnap.models.results import ShellResult, SnapshotInfo, SnapshotResult, SnapshotSpec
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from tests.mocks.mock_modules import MockSnapshotProvider
from tests.mocks.mock_shell import MockShell


def _make_vm_config() -> VMConfig:
    """A minimal two-disk VMConfig for multi-disk snapshot contract tests."""
    return VMConfig(
        name="testvm",
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm-vdb.qcow2")),
        ],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )


def _make_specs() -> list[SnapshotSpec]:
    """Two SnapshotSpecs (vda, vdb) exercising the multi-disk batch path."""
    return [
        SnapshotSpec(disk="vda", name="test-snap-vda", path=Path("/tmp/testvm_vda.qcow2")),
        SnapshotSpec(disk="vdb", name="test-snap-vdb", path=Path("/tmp/testvm_vdb.qcow2")),
    ]


def _success_batch_shell(specs: list[SnapshotSpec]) -> MockShell:
    """A MockShell pre-configured for a fully successful create_multi batch.

    Covers the complete command sequence of
    ``ExternalSnapshotProvider.create_multi``: pre/post ``virsh domblklist``,
    one ``virsh snapshot-create-as`` batch, per-file ``chmod``/``qemu-img
    info``/``test -f``, and the pivot check.
    """
    shell = MockShell()
    domblklist_out = "Target   Source\n" + "".join(f"{s.disk}   {s.path}\n" for s in specs)
    # Pre and pivot domblklist both show the snapshot files as active —
    # this makes the backing-filename and pivot checks pass with one
    # expectation for the identical command string.
    shell.expect("virsh domblklist").returns(
        ShellResult(success=True, stdout=domblklist_out, stderr="", returncode=0, error=None)
    )
    shell.expect("virsh snapshot-create-as").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    shell.expect("chmod").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    shell.expect("test -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    for spec in specs:
        info = json.dumps(
            {
                "format": "qcow2",
                "actual-size": 65536,
                "virtual-size": 1073741824,
                "backing-filename": str(spec.path),
                "incompatible-features": [],
            }
        )
        shell.expect(re.escape(str(spec.path))).returns(
            ShellResult(success=True, stdout=info, stderr="", returncode=0, error=None)
        )
    return shell


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
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
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
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
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
        disk="vda",
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
def test_snapshot_provider_create_multi_returns_result_list(cls, init_kwargs):
    """create_multi() returns a list with exactly one SnapshotResult per spec.

    The list length must equal the spec count and every element must be a
    SnapshotResult whose ``success`` field is a bool.  This holds on the
    failure path (bare MockShell, unconfigured commands) for both
    implementations.
    """
    provider = cls(**init_kwargs)
    vm_config = _make_vm_config()
    specs = _make_specs()
    results = provider.create_multi(vm_config, specs, quiesce=False)
    assert isinstance(results, list)
    assert len(results) == len(specs)
    for result in results:
        assert isinstance(result, SnapshotResult)
        assert isinstance(result.success, bool)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (ExternalSnapshotProvider, {"shell": _success_batch_shell(_make_specs())}),
        (MockSnapshotProvider, {}),
    ],
    ids=["external", "mock"],
)
def test_snapshot_provider_create_multi_success_path(cls, init_kwargs):
    """create_multi() returns one successful SnapshotResult per spec in order.

    Exercises the happy path: every result succeeds, is a SnapshotResult,
    and is attributed to the correct spec (name/path) in spec order
    (snapshot-provider spec: "one SnapshotResult per spec, in spec order").
    """
    provider = cls(**init_kwargs)
    vm_config = _make_vm_config()
    specs = _make_specs()
    results = provider.create_multi(vm_config, specs, quiesce=True)
    assert isinstance(results, list)
    assert len(results) == len(specs)
    for result, spec in zip(results, specs, strict=False):
        assert isinstance(result, SnapshotResult)
        assert isinstance(result.success, bool)
        assert result.success is True
        assert result.name == spec.name
        assert result.path == spec.path
