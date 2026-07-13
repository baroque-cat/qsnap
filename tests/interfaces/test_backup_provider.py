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
from qsnap.modules.backup.file_copy import FileCopyBackupProvider
from tests.mocks.mock_modules import MockBackupProvider
from tests.mocks.mock_shell import MockShell


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


@pytest.mark.parametrize(
    "cls,init_kwargs",
    [
        (FileCopyBackupProvider, {"shell": MockShell()}),
        (MockBackupProvider, {}),
    ],
    ids=["file_copy", "mock"],
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
        (MockBackupProvider, {}),
    ],
    ids=["file_copy", "mock"],
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
        (MockBackupProvider, {}),
    ],
    ids=["file_copy", "mock"],
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
