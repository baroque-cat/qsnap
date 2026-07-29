"""Contract tests: IBackupProvider ABC and concrete implementations.

Verifies that every implementation of IBackupProvider obeys the interface
contract: correct return types, ABC enforcement, and no Core inheritance (D1).
"""

from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.interfaces.backup import IBackupProvider
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import BackupResult, ShellResult, SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from tests.mocks.mock_modules import MockBitmapBackupProvider
from tests.mocks.mock_shell import MockShell


def test_ibackup_provider_is_abstract():
    """IBackupProvider is an ABC with non-empty abstract methods.

    It cannot be instantiated directly.
    """
    assert hasattr(IBackupProvider, "__abstractmethods__")
    assert len(IBackupProvider.__abstractmethods__) > 0
    with pytest.raises(TypeError):
        IBackupProvider()  # type: ignore[abstract]


def test_bitmap_backup_provider_is_ibackup_provider():
    """BitmapBackupProvider is a subclass of IBackupProvider."""
    assert issubclass(BitmapBackupProvider, IBackupProvider)


def test_bitmap_backup_provider_no_core_inheritance():
    """BitmapBackupProvider does NOT inherit from Core (design D1)."""
    assert not issubclass(BitmapBackupProvider, Core)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["bitmap", "mock_bitmap"],
)
def test_backup_provider_transfer_missing_returns_list_of_backup_result(cls, init_kwargs):
    """transfer_missing() returns a list whose elements are all BackupResult."""
    provider = cls(**init_kwargs)
    vm_config = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    target = TargetConfig(path=Path("/mnt/backup/testvm"))
    snapshots = [
        SnapshotInfo(
            name="test-snap",
            path=Path("/tmp/snap.qcow2"),
            timestamp=datetime.now(),
            allocation=65536,
        )
    ]
    result = provider.transfer_missing(vm_config, target, snapshots)
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, BackupResult)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["bitmap", "mock_bitmap"],
)
def test_backup_provider_list_returns_list_of_snapshotinfo(cls, init_kwargs):
    """list() returns a list whose elements are all SnapshotInfo."""
    provider = cls(**init_kwargs)
    target = TargetConfig(path=Path("/mnt/backup/testvm"))
    result = provider.list(target)
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, SnapshotInfo)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["bitmap", "mock_bitmap"],
)
def test_backup_provider_delete_returns_shellresult(cls, init_kwargs):
    """delete() returns a ShellResult."""
    provider = cls(**init_kwargs)
    backup = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    result = provider.delete(backup)
    assert isinstance(result, ShellResult)


def test_ibackup_provider_create_full_backup_abstract():
    """create_full_backup is a non-abstract method on IBackupProvider.

    It has a default implementation that raises ``NotImplementedError``, so
    it is NOT in ``__abstractmethods__`` — concrete implementations are not
    forced to override it.  However, a bare subclass that does not override it
    must raise ``NotImplementedError`` when the method is called.
    """
    # The method exists on the ABC and is callable.
    assert hasattr(IBackupProvider, "create_full_backup")
    assert callable(IBackupProvider.create_full_backup)

    # It is NOT abstract — subclasses are not forced to override it.
    assert "create_full_backup" not in IBackupProvider.__abstractmethods__

    # A bare subclass (implements only the abstract methods) inherits the
    # default implementation that raises NotImplementedError.
    class _BareBackupProvider(IBackupProvider):
        def transfer_missing(
            self,
            vm_config,
            target,
            snapshots,
            *,
            compression_type="zstd",
            stall_timeout=1800,
            full_transfer_engine="qemu-img-convert",
            convert_parallel=4,
            convert_out_of_order=True,
        ):
            return []

        def list(self, target):
            return []

        def delete(self, backup):
            return ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)

    bare = _BareBackupProvider()
    with pytest.raises(NotImplementedError):
        bare.create_full_backup(
            vm_name="testvm",
            source_snapshot=SnapshotInfo(
                name="test-snap",
                path=Path("/tmp/snap.qcow2"),
                timestamp=datetime.now(),
                allocation=65536,
            ),
            target=TargetConfig(path=Path("/mnt/backup/testvm")),
            compress=False,
        )

    # Concrete implementations that override it expose the method.
    assert callable(MockBitmapBackupProvider.create_full_backup)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["bitmap", "mock_bitmap"],
)
def test_backup_provider_create_full_backup_returns_backup_result(cls, init_kwargs):
    """create_full_backup() returns a BackupResult instance.

    ``BitmapBackupProvider`` with bare ``MockShell`` fails on the NBD
    export step but returns a ``BackupResult(success=False)``.
    ``MockBitmapBackupProvider`` returns a successful ``BackupResult``.
    """
    provider = cls(**init_kwargs)
    source_snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    target = TargetConfig(path=Path("/mnt/backup/testvm"))
    result = provider.create_full_backup("testvm", source_snapshot, target, compress=False)
    assert isinstance(result, BackupResult)


# ── New contract tests: full_transfer_engine, convert_parallel, convert_out_of_order ──


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["bitmap", "mock_bitmap"],
)
def test_backup_provider_create_full_backup_accepts_full_transfer_engine(
    cls,
    init_kwargs,
):
    """create_full_backup() accepts full_transfer_engine with default 'qemu-img-convert'."""
    sig = inspect.signature(cls.create_full_backup)
    assert "full_transfer_engine" in sig.parameters, (
        f"full_transfer_engine missing from {cls.__name__}.create_full_backup"
    )
    param = sig.parameters["full_transfer_engine"]
    assert param.default == "qemu-img-convert", (
        f"default should be 'qemu-img-convert', got {param.default!r}"
    )

    provider = cls(**init_kwargs)
    source_snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    target = TargetConfig(path=Path("/mnt/backup/testvm"))
    result = provider.create_full_backup(
        "testvm",
        source_snapshot,
        target,
        compress=False,
        full_transfer_engine="libnbd",
    )
    assert isinstance(result, BackupResult)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["bitmap", "mock_bitmap"],
)
def test_backup_provider_create_full_backup_accepts_convert_parallel(
    cls,
    init_kwargs,
):
    """create_full_backup() accepts convert_parallel with default 4."""
    sig = inspect.signature(cls.create_full_backup)
    assert "convert_parallel" in sig.parameters, (
        f"convert_parallel missing from {cls.__name__}.create_full_backup"
    )
    param = sig.parameters["convert_parallel"]
    assert param.default == 4, f"default should be 4, got {param.default!r}"

    provider = cls(**init_kwargs)
    source_snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    target = TargetConfig(path=Path("/mnt/backup/testvm"))
    result = provider.create_full_backup(
        "testvm",
        source_snapshot,
        target,
        compress=False,
        convert_parallel=8,
    )
    assert isinstance(result, BackupResult)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["bitmap", "mock_bitmap"],
)
def test_backup_provider_create_full_backup_accepts_convert_out_of_order(
    cls,
    init_kwargs,
):
    """create_full_backup() accepts convert_out_of_order with default True."""
    sig = inspect.signature(cls.create_full_backup)
    assert "convert_out_of_order" in sig.parameters, (
        f"convert_out_of_order missing from {cls.__name__}.create_full_backup"
    )
    param = sig.parameters["convert_out_of_order"]
    assert param.default is True, f"default should be True, got {param.default!r}"

    provider = cls(**init_kwargs)
    source_snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    target = TargetConfig(path=Path("/mnt/backup/testvm"))
    result = provider.create_full_backup(
        "testvm",
        source_snapshot,
        target,
        compress=False,
        convert_out_of_order=False,
    )
    assert isinstance(result, BackupResult)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["bitmap", "mock_bitmap"],
)
def test_backup_provider_transfer_missing_accepts_full_transfer_engine(
    cls,
    init_kwargs,
):
    """transfer_missing() accepts full_transfer_engine with default 'qemu-img-convert'."""
    sig = inspect.signature(cls.transfer_missing)
    assert "full_transfer_engine" in sig.parameters, (
        f"full_transfer_engine missing from {cls.__name__}.transfer_missing"
    )
    param = sig.parameters["full_transfer_engine"]
    assert param.default == "qemu-img-convert", (
        f"default should be 'qemu-img-convert', got {param.default!r}"
    )

    provider = cls(**init_kwargs)
    vm_config = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    target = TargetConfig(path=Path("/mnt/backup/testvm"))
    snapshots: list[SnapshotInfo] = []
    result = provider.transfer_missing(
        vm_config,
        target,
        snapshots,
        full_transfer_engine="libnbd",
    )
    assert isinstance(result, list)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["bitmap", "mock_bitmap"],
)
def test_backup_provider_transfer_missing_accepts_convert_parallel(
    cls,
    init_kwargs,
):
    """transfer_missing() accepts convert_parallel with default 4."""
    sig = inspect.signature(cls.transfer_missing)
    assert "convert_parallel" in sig.parameters, (
        f"convert_parallel missing from {cls.__name__}.transfer_missing"
    )
    param = sig.parameters["convert_parallel"]
    assert param.default == 4, f"default should be 4, got {param.default!r}"

    provider = cls(**init_kwargs)
    vm_config = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    target = TargetConfig(path=Path("/mnt/backup/testvm"))
    snapshots: list[SnapshotInfo] = []
    result = provider.transfer_missing(
        vm_config,
        target,
        snapshots,
        convert_parallel=8,
    )
    assert isinstance(result, list)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["bitmap", "mock_bitmap"],
)
def test_backup_provider_transfer_missing_accepts_convert_out_of_order(
    cls,
    init_kwargs,
):
    """transfer_missing() accepts convert_out_of_order with default True."""
    sig = inspect.signature(cls.transfer_missing)
    assert "convert_out_of_order" in sig.parameters, (
        f"convert_out_of_order missing from {cls.__name__}.transfer_missing"
    )
    param = sig.parameters["convert_out_of_order"]
    assert param.default is True, f"default should be True, got {param.default!r}"

    provider = cls(**init_kwargs)
    vm_config = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    target = TargetConfig(path=Path("/mnt/backup/testvm"))
    snapshots: list[SnapshotInfo] = []
    result = provider.transfer_missing(
        vm_config,
        target,
        snapshots,
        convert_out_of_order=False,
    )
    assert isinstance(result, list)
