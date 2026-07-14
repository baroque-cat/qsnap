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
