"""Unit tests for SubprocessShell — concrete IShell implementation.

Tests verify successful execution, timeout handling, command-not-found
behaviour, structured DEBUG logging, and the ``check`` parameter semantics.
No source code is modified.
"""

from __future__ import annotations

import inspect
import logging

from qsnap.interfaces.shell import IShell
from qsnap.models.results import ShellResult
from qsnap.shell.subprocess_shell import SubprocessShell

SHELL_LOGGER = "qsnap.shell.subprocess_shell"


def test_subprocess_shell_success() -> None:
    """A successful command returns ShellResult with success=True."""
    shell = SubprocessShell()

    result = shell.run(["echo", "hello"], timeout=30)

    assert isinstance(result, ShellResult)
    assert result.success is True
    assert result.returncode == 0
    assert "hello" in result.stdout
    assert result.stderr == ""
    assert result.error is None


def test_subprocess_shell_timeout() -> None:
    """A command that exceeds its timeout returns success=False quickly."""
    shell = SubprocessShell()

    result = shell.run(["sleep", "10"], timeout=1)

    assert isinstance(result, ShellResult)
    assert result.success is False
    assert result.error is not None
    assert "timed out" in result.error.lower()


def test_subprocess_shell_command_not_found() -> None:
    """A non-existent command returns success=False with an error message."""
    shell = SubprocessShell()

    result = shell.run(["this_command_does_not_exist_xyz"], timeout=30)

    assert isinstance(result, ShellResult)
    assert result.success is False
    assert result.error is not None
    assert "not found" in result.error.lower()


def test_subprocess_shell_logs_command(caplog) -> None:
    """Every command execution is logged at DEBUG with command/result info."""
    shell = SubprocessShell()

    with caplog.at_level(logging.DEBUG, logger="qsnap.shell.subprocess_shell"):
        result = shell.run(["echo", "hello"], timeout=30)

    # At least one DEBUG record must have been emitted.
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debug_records) >= 1

    msg = debug_records[0].getMessage()

    # The log must include the command and key result metadata.
    assert "echo" in msg  # command string is logged
    assert "timeout=" in msg
    assert "returncode=" in msg
    assert "duration=" in msg
    # The logged returncode must match the actual result.
    assert str(result.returncode) in msg


# ── check parameter ─────────────────────────────────────────────────────


def test_check_mode_no_error_log_on_failure(caplog) -> None:
    """When check=True, a failing command logs at DEBUG, not ERROR.

    The ``check`` flag is meant for pre-flight checks where failure is an
    expected, non-error condition.  Such failures must not pollute the
    ERROR stream.
    """
    shell = SubprocessShell()

    with caplog.at_level(logging.DEBUG, logger=SHELL_LOGGER):
        result = shell.run(["false"], timeout=30, check=True)

    # The command genuinely failed.
    assert isinstance(result, ShellResult)
    assert result.success is False

    shell_records = [r for r in caplog.records if r.name == SHELL_LOGGER]
    error_records = [r for r in shell_records if r.levelno == logging.ERROR]
    debug_records = [r for r in shell_records if r.levelno == logging.DEBUG]

    # No ERROR record should be emitted when check=True.
    assert len(error_records) == 0
    # A DEBUG record must still be emitted so the call is traceable.
    assert len(debug_records) >= 1


def test_check_mode_default_false_logs_error_on_failure(caplog) -> None:
    """When check is omitted (defaults to False), failure logs at ERROR.

    This is the normal, non-pre-flight path: an unexpected failure should
    be loud.
    """
    shell = SubprocessShell()

    with caplog.at_level(logging.DEBUG, logger=SHELL_LOGGER):
        result = shell.run(["false"], timeout=30)

    assert isinstance(result, ShellResult)
    assert result.success is False

    shell_records = [r for r in caplog.records if r.name == SHELL_LOGGER]
    error_records = [r for r in shell_records if r.levelno == logging.ERROR]

    # At least one ERROR record must be emitted for a default-mode failure.
    assert len(error_records) >= 1
    msg = error_records[0].getMessage()
    assert "false" in msg
    assert "returncode=" in msg


def test_check_mode_returns_shellresult() -> None:
    """shell.run with check=True still returns a ShellResult (never raises).

    Even in check mode the method must not raise for expected failures;
    it returns a ShellResult with success=False so callers can branch.
    """
    shell = SubprocessShell()

    result = shell.run(["test", "-f", "/nonexistent/path/xyz"], timeout=30, check=True)

    assert isinstance(result, ShellResult)
    assert result.success is False
    assert result.returncode != 0
    assert result.error is not None


def test_ishell_run_accepts_check_parameter() -> None:
    """The IShell.run ABC signature declares check: bool = False.

    This is a contract test: every concrete IShell must honour this
    parameter, so the interface itself must advertise it.
    """
    sig = inspect.signature(IShell.run)

    assert "check" in sig.parameters

    check_param = sig.parameters["check"]
    assert check_param.default is False

    # With ``from __future__ import annotations`` the annotation may be
    # the string "bool" rather than the type object; accept either form.
    annotation = check_param.annotation
    assert annotation is bool or annotation == "bool"
