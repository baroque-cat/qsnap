"""Contract test: IShell ABC and SubprocessShell implementation."""

from __future__ import annotations

import inspect
import typing

import pytest

from qsnap.interfaces.shell import IShell
from qsnap.shell.subprocess_shell import SubprocessShell


def test_ishell_is_abstract():
    """IShell is an ABC with abstract methods; cannot be instantiated directly.

    SubprocessShell is a concrete subclass whose instances pass
    ``isinstance`` against ``IShell``.
    """
    # IShell is an ABC with non-empty abstract methods.
    assert hasattr(IShell, "__abstractmethods__")
    assert len(IShell.__abstractmethods__) > 0

    # Cannot instantiate the ABC directly.
    with pytest.raises(TypeError):
        IShell()  # type: ignore[abstract]

    # SubprocessShell is a subclass of IShell.
    assert issubclass(SubprocessShell, IShell)

    # An instance of SubprocessShell is an IShell.
    assert isinstance(SubprocessShell(), IShell)


def test_ishell_run_accepts_check_parameter():
    """IShell.run signature includes ``check: bool = False``.

    The *check* parameter controls logging severity for expected command
    failures (pre-flight checks).  It must default to ``False`` so that
    callers who omit it get ERROR-level logging on failure.
    """
    sig = inspect.signature(IShell.run)
    assert "check" in sig.parameters
    check_param = sig.parameters["check"]
    assert check_param.default is False
    # ``from __future__ import annotations`` stores annotations as strings,
    # so resolve them via ``typing.get_type_hints`` for a real type check.
    hints = typing.get_type_hints(IShell.run)
    assert hints.get("check") is bool
