"""Unit tests for DefaultFactory."""

from __future__ import annotations

import pytest

from qsnap.factory.default import DefaultFactory


def test_default_factory_stores_shell_and_state(mock_shell, mock_state):
    """DefaultFactory stores the shell and state references passed at construction."""
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    assert factory._shell is mock_shell
    assert factory._state is mock_state


@pytest.mark.parametrize(
    ("method_name", "expected_substring"),
    [
        ("create_snapshot_provider", "SnapshotProvider"),
        ("create_backup_provider", "BackupProvider"),
        ("create_change_detector", "ChangeDetector"),
        ("create_lifecycle_manager", "LifecycleManager"),
    ],
)
def test_default_factory_unimplemented_raises_notimplementederror(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
    method_name,
    expected_substring,
):
    """Every unimplemented create_* method raises NotImplementedError with a clear message.

    This is a RISK test (test-plan.md line 135): guards against silent stubs
    returning ``None``.  ``create_retention_engine`` IS implemented and is
    intentionally excluded from this parametrization.
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

    with pytest.raises(NotImplementedError) as exc_info:
        method(*args)

    assert expected_substring in str(exc_info.value)
