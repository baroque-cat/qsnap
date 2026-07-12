"""Contract test: IShell ABC and SubprocessShell implementation."""

from __future__ import annotations

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
