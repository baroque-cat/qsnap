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
from qsnap.modules.backup.file_copy import FileCopyBackupProvider
from tests.mocks.mock_modules import MockBackupProvider, MockBitmapBackupProvider
from tests.mocks.mock_shell import MockShell
from tests.mocks.mock_state import InMemoryStateManager


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
        (BitmapBackupProvider, {"shell": MockShell()}),
        (BitmapBackupProvider, {"shell": MockShell(), "state": InMemoryStateManager()}),
        (MockBackupProvider, {}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["file_copy", "bitmap", "bitmap_with_state", "mock", "mock_bitmap"],
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
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBackupProvider, {}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["file_copy", "bitmap", "mock", "mock_bitmap"],
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
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBackupProvider, {}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["file_copy", "bitmap", "mock", "mock_bitmap"],
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
    assert callable(IBackupProvider.create_full_backup)

    # It is NOT abstract — subclasses are not forced to override it.
    assert "create_full_backup" not in IBackupProvider.__abstractmethods__

    # A bare subclass (implements only the abstract methods) inherits the
    # default implementation that raises NotImplementedError.
    class _BareBackupProvider(IBackupProvider):
        def transfer_missing(
            self, vm_config, target, snapshots, *, full_verify_before_rebase="metadata"
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
    assert callable(FileCopyBackupProvider.create_full_backup)
    assert callable(MockBackupProvider.create_full_backup)


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (FileCopyBackupProvider, {"shell": MockShell()}),
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBackupProvider, {}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["file_copy", "bitmap", "mock", "mock_bitmap"],
)
def test_backup_provider_create_full_backup_returns_backup_result(cls, init_kwargs):
    """create_full_backup() returns a BackupResult instance.

    For ``FileCopyBackupProvider`` the bare ``MockShell`` causes the
    ``qemu-img convert`` step to fail, but the provider still returns a
    ``BackupResult`` (with ``success=False``).  ``BitmapBackupProvider``
    similarly fails on the NBD export step but returns a
    ``BackupResult(success=False)``.  ``MockBackupProvider`` returns a
    successful ``BackupResult``.
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


def test_ibackup_provider_create_full_backup_bucket_level_parameter():
    """create_full_backup signature includes bucket_level parameter.

    The ``bucket_level`` parameter must be present in the signature of
    ``IBackupProvider.create_full_backup``, defaulting to ``"monthly"``.
    """
    sig = inspect.signature(IBackupProvider.create_full_backup)
    assert "bucket_level" in sig.parameters, (
        f"bucket_level missing from create_full_backup signature: {sig.parameters}"
    )

    param = sig.parameters["bucket_level"]
    assert param.default == "monthly", (
        f"bucket_level default should be 'monthly', got {param.default!r}"
    )

    assert "vm_name" in sig.parameters, (
        f"vm_name missing from create_full_backup signature: {sig.parameters}"
    )
    # Verify vm_name is the first parameter (after self)
    params = list(sig.parameters.keys())
    # params[0] is 'self', params[1] should be 'vm_name'
    assert params[1] == "vm_name", (
        f"vm_name should be the first positional parameter, got {params[1]}"
    )


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (FileCopyBackupProvider, {"shell": MockShell()}),
        (BitmapBackupProvider, {"shell": MockShell()}),
        (MockBackupProvider, {}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["file_copy", "bitmap", "mock", "mock_bitmap"],
)
def test_backup_provider_create_full_backup_bucket_level_in_concrete_signatures(
    cls,
    init_kwargs,
):
    """Every concrete provider that overrides create_full_backup() has bucket_level parameter.

    This ensures that adding new backup provider implementations does not
    accidentally omit the ``bucket_level`` parameter from the signature.
    """
    sig = inspect.signature(cls.create_full_backup)
    assert "bucket_level" in sig.parameters, (
        f"bucket_level missing from {cls.__name__}.create_full_backup signature"
    )
    assert "vm_name" in sig.parameters, (
        f"vm_name missing from {cls.__name__}.create_full_backup signature"
    )


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (MockBackupProvider, {}),
        (MockBitmapBackupProvider, {}),
    ],
    ids=["mock", "mock_bitmap"],
)
def test_backup_provider_create_full_backup_bucket_level_custom_value(
    cls,
    init_kwargs,
):
    """create_full_backup() correctly uses a non-default bucket_level value.

    When ``bucket_level="yearly"`` is passed, the resulting ``BackupResult``
    should reflect that value in the ``target_path``.
    """
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
        bucket_level="yearly",
    )
    assert isinstance(result, BackupResult)
    assert result.success
    assert ".FULL.yearly.qcow2" in str(result.target_path), (
        f"bucket_level 'yearly' not reflected in target_path: {result.target_path}"
    )
