"""Contract test: IShell ABC and SubprocessShell implementation."""

from __future__ import annotations

import inspect
import typing

import pytest

from qsnap.interfaces.shell import IShell
from qsnap.models.results import ShellResult
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


def test_ishell_has_run_with_stall_detection():
    """IShell declares ``run_with_stall_detection`` as an abstract method.

    The method supports stall detection via output-file growth monitoring
    for long-running data-transfer commands (``qemu-img convert``).  It must be present in ``IShell.__abstractmethods__`` so
    that any concrete implementation is required to provide it.
    """
    assert hasattr(IShell, "run_with_stall_detection")
    assert "run_with_stall_detection" in IShell.__abstractmethods__

    # Verify signature and return type.
    sig = inspect.signature(IShell.run_with_stall_detection)
    assert "cmd" in sig.parameters
    assert "output_file" in sig.parameters
    assert "stall_timeout" in sig.parameters
    assert "check" in sig.parameters

    # ``output_file`` defaults to None.
    output_file_param = sig.parameters["output_file"]
    assert output_file_param.default is None

    # ``stall_timeout`` defaults to 1800 (30 minutes).
    stall_timeout_param = sig.parameters["stall_timeout"]
    assert stall_timeout_param.default == 1800

    # ``check`` defaults to False.
    check_param = sig.parameters["check"]
    assert check_param.default is False

    # Return type must resolve to ShellResult.
    hints = typing.get_type_hints(IShell.run_with_stall_detection)
    assert hints.get("return") is ShellResult


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


def test_ishell_has_run_with_heartbeat():
    """IShell declares ``run_with_heartbeat`` as an abstract method.

    The method executes *cmd* with a hard *timeout* and a periodic
    ``on_heartbeat(elapsed)`` callback (harden-blockcommit-races
    shell-abstraction spec, design D2).  It must be present in
    ``IShell.__abstractmethods__`` so that any concrete implementation
    is required to provide it.
    """
    assert hasattr(IShell, "run_with_heartbeat")
    assert "run_with_heartbeat" in IShell.__abstractmethods__

    # Verify signature and return type.
    sig = inspect.signature(IShell.run_with_heartbeat)
    assert "cmd" in sig.parameters
    assert "timeout" in sig.parameters
    assert "heartbeat_seconds" in sig.parameters
    assert "on_heartbeat" in sig.parameters
    assert "check" in sig.parameters

    # ``timeout`` has no default — callers must supply it.
    timeout_param = sig.parameters["timeout"]
    assert timeout_param.default is inspect.Parameter.empty

    # ``heartbeat_seconds`` has no default — callers must supply it.
    heartbeat_param = sig.parameters["heartbeat_seconds"]
    assert heartbeat_param.default is inspect.Parameter.empty

    # ``check`` defaults to False.
    check_param = sig.parameters["check"]
    assert check_param.default is False

    # Return type must resolve to ShellResult.
    hints = typing.get_type_hints(IShell.run_with_heartbeat)
    assert hints.get("return") is ShellResult
