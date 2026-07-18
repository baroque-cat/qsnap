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


def test_istate_manager_multi_full_backup_methods_abstract():
    """IStateManager declares multi-FULL backup tracking methods as abstract.

    The methods ``get_full_backups``, ``record_full_backup``,
    ``record_incremental_dependency``, and ``get_incremental_dependencies``
    must be in ``IStateManager.__abstractmethods__`` so that every concrete
    implementation is forced to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_full_backups" in abstract_methods
    assert "record_full_backup" in abstract_methods
    assert "record_incremental_dependency" in abstract_methods
    assert "get_incremental_dependencies" in abstract_methods


def test_istate_manager_concrete_implementations_have_new_methods(tmp_path):
    """JsonStateManager and InMemoryStateManager implement new abstract methods.

    Both concrete implementations must provide ``get_full_backups``,
    ``record_full_backup``, ``record_incremental_dependency``, and
    ``get_incremental_dependencies`` with correct signatures.
    """
    json_mgr = JsonStateManager(state_dir=tmp_path)
    inmemory_mgr = InMemoryStateManager()

    for mgr, label in [
        (json_mgr, "JsonStateManager"),
        (inmemory_mgr, "InMemoryStateManager"),
    ]:
        assert callable(mgr.get_full_backups), f"{label} missing get_full_backups"
        assert callable(mgr.record_full_backup), f"{label} missing record_full_backup"
        assert callable(mgr.record_incremental_dependency), (
            f"{label} missing record_incremental_dependency"
        )
        assert callable(mgr.get_incremental_dependencies), (
            f"{label} missing get_incremental_dependencies"
        )


def test_istate_manager_new_methods_cause_typeerror_on_instantiation():
    """Instantiating a subclass missing the new abstract methods raises TypeError.

    A bare subclass that implements older abstract methods but not the four
    new ones (``get_full_backups``, ``record_full_backup``,
    ``record_incremental_dependency``, ``get_incremental_dependencies``)
    must raise ``TypeError`` because the ABC enforces all abstract methods.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_full_backups" in abstract_methods

    # A subclass that is missing the new methods must fail to instantiate.
    class _MissingNewMethods(IStateManager):
        def get_last_allocation(self, vm_name): ...
        def set_last_allocation(self, vm_name, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp): ...

    with pytest.raises(TypeError):
        _MissingNewMethods()


def test_json_state_manager_passes_isinstance_after_corruption_recovery(
    tmp_path,
) -> None:
    """JsonStateManager remains an IStateManager after corruption recovery.

    Create a ``JsonStateManager``, verify ``isinstance(manager,
    IStateManager)`` is True.  Then trigger corruption recovery (write
    a corrupt file, load), and verify ``isinstance`` is still True.
    """

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
    assert last_alloc is None, "Corrupt state should return None after recovery"

    # After corruption recovery — still passes isinstance.
    assert isinstance(manager, IStateManager), (
        "JsonStateManager should still be an IStateManager after corruption recovery"
    )
