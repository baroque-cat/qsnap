"""Tests verifying that mocks correctly implement their ABC interfaces."""

from __future__ import annotations

from pathlib import Path

from qsnap.models.config import DiskConfig, VMConfig
from qsnap.models.results import SnapshotResult, SnapshotSpec
from tests.mocks.mock_modules import MockSnapshotProvider


def test_mock_shell_implements_full_interface():
    """MockShell implements the full IShell ABC, including both ``run``
    and ``run_with_stall_detection``."""
    from qsnap.interfaces.shell import IShell
    from tests.mocks.mock_shell import MockShell

    mock = MockShell()
    assert isinstance(mock, IShell), "MockShell must be an IShell instance"

    # Both abstract methods must exist
    assert hasattr(mock, "run"), "MockShell must define run()"
    assert hasattr(mock, "run_with_stall_detection"), (
        "MockShell must define run_with_stall_detection()"
    )

    # run_with_stall_detection must be callable and accept the full
    # IShell ABC signature.
    assert callable(mock.run_with_stall_detection), "run_with_stall_detection must be callable"


def _make_vm_config() -> VMConfig:
    """A minimal two-disk VMConfig for mock validity checks."""
    return VMConfig(
        name="testvm",
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm-vdb.qcow2")),
        ],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )


def test_mock_create_multi_validity():
    """MockSnapshotProvider.create_multi never returns None and returns
    exactly one SnapshotResult per spec, in spec order (TESTING.md §2)."""
    provider = MockSnapshotProvider()
    specs = [
        SnapshotSpec(disk="vda", name="test-snap-vda", path=Path("/tmp/testvm_vda.qcow2")),
        SnapshotSpec(disk="vdb", name="test-snap-vdb", path=Path("/tmp/testvm_vdb.qcow2")),
    ]
    results = provider.create_multi(_make_vm_config(), specs, quiesce=True)

    # Never returns None.
    assert results is not None
    # One result per spec.
    assert isinstance(results, list)
    assert len(results) == len(specs)
    for result, spec in zip(results, specs):
        assert isinstance(result, SnapshotResult)
        assert result.success is True
        assert result.disk == spec.disk
        assert result.name == spec.name
        assert result.path == spec.path


def test_mock_create_multi_validity_empty_specs():
    """MockSnapshotProvider.create_multi([]) returns [] — never None."""
    provider = MockSnapshotProvider()
    results = provider.create_multi(_make_vm_config(), [], quiesce=False)
    assert results is not None
    assert results == []
