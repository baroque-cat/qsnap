"""Unit tests for DefaultFactory."""

from __future__ import annotations

import pytest

from qsnap.factory.default import DefaultFactory
from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import RetentionPolicy


def test_default_factory_stores_shell_and_state(mock_shell, mock_state):
    """DefaultFactory stores the shell and state references passed at construction."""
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    assert factory._shell is mock_shell
    assert factory._state is mock_state


@pytest.mark.parametrize(
    ("method_name", "expected_interface"),
    [
        ("create_snapshot_provider", ISnapshotProvider),
        ("create_backup_provider", IBackupProvider),
        ("create_change_detector", IChangeDetector),
        ("create_lifecycle_manager", ILifecycleManager),
    ],
)
def test_default_factory_returns_correct_interface_types(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
    method_name,
    expected_interface,
):
    """Each create_* method (except create_retention_engine) returns an instance
    that implements the correct ABC interface.

    ``create_retention_engine`` was already implemented earlier and is
    verified separately in ``test_default_factory_all_five_methods_return_instances``.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    method = getattr(factory, method_name)

    # Build appropriate arguments per method signature.
    if method_name == "create_snapshot_provider":
        args = (make_vm_config(),)
    elif method_name == "create_backup_provider":
        args = (make_vm_config(), make_target())
    elif method_name == "create_change_detector":
        args = ("always",)
    else:  # create_lifecycle_manager
        args = ()

    result = method(*args)
    assert isinstance(result, expected_interface)


def test_default_factory_all_five_methods_return_instances(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """All five create_* methods return concrete instances of their ABC.

    This includes ``create_retention_engine``, which was implemented before
    the other four.  Together with the parametrized test above, this
    guarantees that no factory method returns ``None`` or raises.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    assert isinstance(factory.create_snapshot_provider(make_vm_config()), ISnapshotProvider)
    assert isinstance(factory.create_backup_provider(make_vm_config(), make_target()), IBackupProvider)
    assert isinstance(factory.create_retention_engine(RetentionPolicy()), IRetentionEngine)
    assert isinstance(factory.create_change_detector("always"), IChangeDetector)
    assert isinstance(factory.create_lifecycle_manager(), ILifecycleManager)
