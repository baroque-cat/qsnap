"""Contract test: IStateManager ABC and concrete implementations."""

from __future__ import annotations

import pytest

from qsnap.interfaces.state import IStateManager
from qsnap.state.json_manager import JsonStateManager
from tests.mocks.mock_state import InMemoryStateManager


def test_istate_manager_is_abstract():
    """IStateManager is an ABC; cannot be instantiated directly.

    JsonStateManager is a subclass, and InMemoryStateManager instances pass
    ``isinstance`` against ``IStateManager``.
    """
    # IStateManager is an ABC with non-empty abstract methods.
    assert hasattr(IStateManager, "__abstractmethods__")
    assert len(IStateManager.__abstractmethods__) > 0

    # Cannot instantiate the ABC directly.
    with pytest.raises(TypeError):
        IStateManager()  # type: ignore[abstract]

    # JsonStateManager is a subclass of IStateManager.
    assert issubclass(JsonStateManager, IStateManager)

    # An InMemoryStateManager instance is an IStateManager.
    assert isinstance(InMemoryStateManager(), IStateManager)


def test_istate_manager_deferred_operations_methods_exist():
    """IStateManager declares deferred blockcommit operations as abstract.

    The methods ``get_deferred_operations``, ``add_deferred_blockcommit``,
    and ``clear_deferred_operations`` must all be in
    ``IStateManager.__abstractmethods__`` so that every concrete
    implementation is forced to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_deferred_operations" in abstract_methods
    assert "add_deferred_blockcommit" in abstract_methods
    assert "clear_deferred_operations" in abstract_methods


def test_istate_manager_full_backup_methods_abstract():
    """IStateManager declares full-backup tracking methods as abstract.

    The methods ``get_last_full_backup`` and ``set_last_full_backup`` must
    be in ``IStateManager.__abstractmethods__`` so that every concrete
    implementation (e.g. ``JsonStateManager``, ``InMemoryStateManager``)
    is forced to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_last_full_backup" in abstract_methods
    assert "set_last_full_backup" in abstract_methods


def test_istate_manager_update_deferred_warning_abstract(tmp_path):
    """IStateManager declares update_deferred_warning as abstract.

    The method ``update_deferred_warning`` must be in
    ``IStateManager.__abstractmethods__`` so that every concrete
    implementation is forced to provide it.  Both ``JsonStateManager``
    and ``InMemoryStateManager`` must implement it.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "update_deferred_warning" in abstract_methods

    # Both concrete implementations implement the method.
    json_mgr = JsonStateManager(state_dir=tmp_path)
    inmemory_mgr = InMemoryStateManager()
    assert callable(json_mgr.update_deferred_warning)
    assert callable(inmemory_mgr.update_deferred_warning)


def test_json_state_manager_passes_isinstance_after_corruption_recovery(
    tmp_path,
) -> None:
    """JsonStateManager remains an IStateManager after corruption recovery.

    Create a ``JsonStateManager``, verify ``isinstance(manager,
    IStateManager)`` is True.  Then trigger corruption recovery (write
    a corrupt file, load), and verify ``isinstance`` is still True.
    """
    import json as _json

    manager = JsonStateManager(state_dir=tmp_path)

    # Before corruption — passes isinstance.
    assert isinstance(manager, IStateManager), (
        "JsonStateManager should be an IStateManager before corruption"
    )

    # Write corrupt file and trigger recovery.
    state_file = tmp_path / "testvm_corrupt.json"
    state_file.write_text("{ this is not valid json", encoding="utf-8")

    # Loading triggers corruption recovery (rename + empty state).
    last_alloc = manager.get_last_allocation("testvm_corrupt")
    assert last_alloc is None, (
        "Corrupt state should return None after recovery"
    )

    # After corruption recovery — still passes isinstance.
    assert isinstance(manager, IStateManager), (
        "JsonStateManager should still be an IStateManager "
        "after corruption recovery"
    )
