"""Contract tests: IBackupProvider ABC and concrete implementations.

Verifies that every implementation of IBackupProvider obeys the interface
contract: correct return types, ABC enforcement, and no Core inheritance (D1).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.interfaces.backup import IBackupProvider
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import BackupResult, ShellResult, SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.modules.backup.file_copy import FileCopyBackupProvider
from tests.mocks.mock_modules import MockBackupProvider
from tests.mocks.mock_shell import MockShell


def _make_bitmap_shell() -> MockShell:
    """Create a MockShell pre-configured for BitmapBackupProvider construction.

    BitmapBackupProvider.__init__ calls ``_check_libvirt_version()`` which
    runs ``virsh --version``.  The returned stdout must parse to libvirt >= 6.0.
    """
    shell = MockShell()
    shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    return shell


def test_ibackup_provider_is_abstract():
    """IBackupProvider is an ABC with non-empty abstract methods.

    It cannot be instantiated directly.
    """
    assert hasattr(IBackupProvider, "__abstractmethods__")
    assert len(IBackupProvider.__abstractmethods__) > 0
    with pytest.raises(TypeError):
        IBackupProvider()  # type: ignore[abstract]


def test_file_copy_backup_provider_is_ibackup_provider():
    """FileCopyBackupProvider is a subclass of IBackupProvider."""
    assert issubclass(FileCopyBackupProvider, IBackupProvider)


def test_file_copy_backup_provider_no_core_inheritance():
    """FileCopyBackupProvider does NOT inherit from Core (design D1)."""
    assert not issubclass(FileCopyBackupProvider, Core)


def test_bitmap_backup_provider_is_ibackup_provider():
    """BitmapBackupProvider is a subclass of IBackupProvider."""
    assert issubclass(BitmapBackupProvider, IBackupProvider)


def test_bitmap_backup_provider_no_core_inheritance():
    """BitmapBackupProvider does NOT inherit from Core (design D1)."""
    assert not issubclass(BitmapBackupProvider, Core)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (FileCopyBackupProvider, {"shell": MockShell()}),
        (BitmapBackupProvider, {"shell": _make_bitmap_shell()}),
        (MockBackupProvider, {}),
    ],
    ids=["file_copy", "bitmap", "mock"],
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
        (FileCopyBackupProvider, {"shell": MockShell()}),
        (BitmapBackupProvider, {"shell": _make_bitmap_shell()}),
        (MockBackupProvider, {}),
    ],
    ids=["file_copy", "bitmap", "mock"],
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
        (FileCopyBackupProvider, {"shell": MockShell()}),
        (BitmapBackupProvider, {"shell": _make_bitmap_shell()}),
        (MockBackupProvider, {}),
    ],
    ids=["file_copy", "bitmap", "mock"],
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


def test_file_copy_backup_provider_requires_shell():
    """FileCopyBackupProvider requires an IShell constructor argument."""
    with pytest.raises(TypeError):
        FileCopyBackupProvider()  # type: ignore[call-arg]


def test_ibackup_provider_create_full_backup_abstract():
    """create_full_backup is a non-abstract method on IBackupProvider.

    It has a default implementation that raises ``NotImplementedError``, so
    it is NOT in ``__abstractmethods__`` — concrete implementations are not
    forced to override it.  However, a bare subclass that does not override it
    must raise ``NotImplementedError`` when the method is called.
    """
    # The method exists on the ABC and is callable.
    assert hasattr(IBackupProvider, "create_full_backup")
    assert callable(getattr(IBackupProvider, "create_full_backup"))

    # It is NOT abstract — subclasses are not forced to override it.
    assert "create_full_backup" not in IBackupProvider.__abstractmethods__

    # A bare subclass (implements only the abstract methods) inherits the
    # default implementation that raises NotImplementedError.
    class _BareBackupProvider(IBackupProvider):
        def transfer_missing(self, vm_config, target, snapshots):
            return []

        def list(self, target):
            return []

        def delete(self, backup):
            return ShellResult(
                success=True, stdout="", stderr="", returncode=0, error=None
            )

    bare = _BareBackupProvider()
    with pytest.raises(NotImplementedError):
        bare.create_full_backup(
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
    assert callable(getattr(FileCopyBackupProvider, "create_full_backup"))
    assert callable(getattr(MockBackupProvider, "create_full_backup"))


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (FileCopyBackupProvider, {"shell": MockShell()}),
        (MockBackupProvider, {}),
    ],
    ids=["file_copy", "mock"],
)
def test_backup_provider_create_full_backup_returns_backup_result(cls, init_kwargs):
    """create_full_backup() returns a BackupResult instance.

    For ``FileCopyBackupProvider`` the bare ``MockShell`` causes the
    ``qemu-img convert`` step to fail, but the provider still returns a
    ``BackupResult`` (with ``success=False``).  ``MockBackupProvider``
    returns a successful ``BackupResult``.
    """
    provider = cls(**init_kwargs)
    source_snapshot = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/snap.qcow2"),
        timestamp=datetime.now(),
        allocation=65536,
    )
    target = TargetConfig(path=Path("/mnt/backup/testvm"))
    result = provider.create_full_backup(source_snapshot, target, compress=False)
    assert isinstance(result, BackupResult)
