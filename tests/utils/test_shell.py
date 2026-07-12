"""Unit tests for SubprocessShell — concrete IShell implementation.

Tests verify successful execution, timeout handling, command-not-found
behaviour, and structured DEBUG logging.  No source code is modified.
"""

from __future__ import annotations

import logging

from qsnap.models.results import ShellResult
from qsnap.shell.subprocess_shell import SubprocessShell


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
