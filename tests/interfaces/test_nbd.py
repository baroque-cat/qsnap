"""Contract test: INbdClient ABC, MockNbdClient, and LibnbdClient.

Verifies that every concrete ``INbdClient`` implementation obeys the
interface contract: methods exist, return correct types, and are safe
to call without a prior ``connect()`` (TESTING.md paradigm: contract test
parametrized over all implementations).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from qsnap.interfaces.nbd import INbdClient
from qsnap.utils.nbd_client import LibnbdClient
from tests.mocks.mock_nbd import MockNbdClient


@pytest.mark.unit
@pytest.mark.parametrize("cls", [MockNbdClient, LibnbdClient])
def test_nbd_client_contract_parametrized(cls: Callable[[], INbdClient]) -> None:
    """Every concrete INbdClient implementation satisfies the interface.

    Verifies isinstance check, method existence, and correct return types
    for the three methods that are callable without a prior ``connect()``:
    ``get_size()``, ``get_max_request_size()``, and ``disconnect()``.
    """
    instance = cls()

    # isinstance check against the ABC.
    assert isinstance(instance, INbdClient), (
        f"{cls.__name__} must pass isinstance(INbdClient)"
    )

    # All seven interface methods must exist.
    for method_name in (
        "connect",
        "get_size",
        "get_max_request_size",
        "block_status",
        "pread",
        "pwrite",
        "disconnect",
    ):
        assert hasattr(instance, method_name), (
            f"{cls.__name__} is missing method {method_name!r}"
        )
        assert callable(getattr(instance, method_name)), (
            f"{cls.__name__}.{method_name} must be callable"
        )

    # get_size() returns int.
    size = instance.get_size()
    assert isinstance(size, int), (
        f"{cls.__name__}.get_size() must return int, got {type(size)}"
    )

    # get_max_request_size() returns int.
    max_sz = instance.get_max_request_size()
    assert isinstance(max_sz, int), (
        f"{cls.__name__}.get_max_request_size() must return int, got {type(max_sz)}"
    )

    # disconnect() is safe on a freshly-constructed / unconnected client.
    instance.disconnect()  # must not raise


@pytest.mark.unit
def test_mock_nbd_client_is_inbdclient() -> None:
    """MockNbdClient passes ``isinstance`` against ``INbdClient``."""
    mock = MockNbdClient()
    assert isinstance(mock, INbdClient), (
        "MockNbdClient must be an instance of INbdClient"
    )
