"""Unit tests for SubprocessShell — concrete IShell implementation.

Tests verify successful execution, timeout handling, command-not-found
behaviour, structured DEBUG logging, the ``check`` parameter semantics,
and the ``run_with_stall_detection`` method (stall detection via output-file
growth monitoring).

No source code is modified.
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from pathlib import Path

from qsnap.interfaces.shell import IShell
from qsnap.models.results import ShellResult
from qsnap.shell import subprocess_shell
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


# ── run_with_stall_detection ────────────────────────────────────────────


def test_run_with_stall_detection_completes_normally() -> None:
    """Command completes before the first poll; returns ShellResult(success=True)."""
    shell = SubprocessShell()

    result = shell.run_with_stall_detection(["echo", "hello"])

    assert isinstance(result, ShellResult)
    assert result.success is True
    assert result.returncode == 0
    assert "hello" in result.stdout
    assert result.error is None


def test_run_with_stall_detection_kills_stalled_process(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Output file never grows — process killed after stall_timeout."""
    monkeypatch.setattr(subprocess_shell, "_POLL_INTERVAL", 0.5)  # type: ignore[arg-type]

    shell = SubprocessShell()
    output_file = tmp_path / "output.qcow2"
    output_file.touch()  # create empty file that never grows

    result = shell.run_with_stall_detection(
        ["sleep", "3600"],
        output_file=output_file,
        stall_timeout=3,
    )

    assert result.success is False
    assert "Stall detected" in result.error  # type: ignore[operator]
    assert "no progress" in result.error  # type: ignore[operator]


def test_run_with_stall_detection_slow_but_progressing(tmp_path: Path, monkeypatch: object) -> None:
    """Output file grows slowly (1KB per cycle) — stall not triggered."""
    monkeypatch.setattr(subprocess_shell, "_POLL_INTERVAL", 0.5)  # type: ignore[arg-type]

    shell = SubprocessShell()
    output_file = tmp_path / "output.qcow2"
    output_file.touch()

    stop_event = threading.Event()

    def background_writer() -> None:
        """Write 1KB to output_file every 0.25s until stopped."""
        while not stop_event.is_set():
            with open(output_file, "ab") as f:
                f.write(b"x" * 1024)
            time.sleep(0.25)

    writer_thread = threading.Thread(target=background_writer, daemon=True)
    writer_thread.start()

    try:
        result = shell.run_with_stall_detection(
            ["sleep", "8"],
            output_file=output_file,
            stall_timeout=3,
        )
    finally:
        stop_event.set()
        writer_thread.join(timeout=2)

    assert result.success is True
    assert result.returncode == 0
    assert result.error is None


def test_run_with_stall_detection_no_output_file() -> None:
    """output_file=None behaves like run() with infinite timeout."""
    shell = SubprocessShell()

    result = shell.run_with_stall_detection(["sleep", "1"], output_file=None)

    assert isinstance(result, ShellResult)
    assert result.success is True
    assert result.returncode == 0
    assert result.error is None


def test_run_with_stall_detection_nonzero_exit() -> None:
    """Process exits with returncode=1 — returns ShellResult(success=False)."""
    shell = SubprocessShell()

    result = shell.run_with_stall_detection(["false"])

    assert isinstance(result, ShellResult)
    assert result.success is False
    assert result.returncode == 1
    assert result.error is not None


def test_run_with_stall_detection_check_mode_suppresses_error(
    caplog: object,
) -> None:
    """When check=True, a failing command logs at DEBUG, not ERROR."""
    shell = SubprocessShell()

    with caplog.at_level(logging.DEBUG, logger=SHELL_LOGGER):  # type: ignore[union-attr]
        result = shell.run_with_stall_detection(["false"], check=True)

    assert result.success is False

    shell_records = [r for r in caplog.records if r.name == SHELL_LOGGER]  # type: ignore[union-attr]
    error_records = [r for r in shell_records if r.levelno == logging.ERROR]
    debug_records = [r for r in shell_records if r.levelno == logging.DEBUG]

    assert len(error_records) == 0
    assert len(debug_records) >= 1


def test_subprocess_shell_stall_kills(tmp_path: Path, monkeypatch: object) -> None:
    """SubprocessShell kills ``sleep 3600`` after stall_timeout with no growth."""
    monkeypatch.setattr(subprocess_shell, "_POLL_INTERVAL", 0.5)  # type: ignore[arg-type]

    shell = SubprocessShell()
    output_file = tmp_path / "output.qcow2"
    output_file.touch()

    start = time.monotonic()
    result = shell.run_with_stall_detection(
        ["sleep", "3600"],
        output_file=output_file,
        stall_timeout=5,
    )
    elapsed = time.monotonic() - start

    assert result.success is False
    assert "Stall detected" in result.error  # type: ignore[operator]
    assert "no progress" in result.error  # type: ignore[operator]
    # Should have been killed within a reasonable margin (stall_timeout + 2 poll cycles).
    assert elapsed < 15


def test_subprocess_shell_stall_allows_growth(tmp_path: Path, monkeypatch: object) -> None:
    """SubprocessShell allows a slowly-growing file to complete without stall."""
    monkeypatch.setattr(subprocess_shell, "_POLL_INTERVAL", 0.5)  # type: ignore[arg-type]

    shell = SubprocessShell()
    output_file = tmp_path / "output.qcow2"
    output_file.touch()

    stop_event = threading.Event()

    def background_writer() -> None:
        """Write data to output_file every 0.25s to simulate slow transfer."""
        while not stop_event.is_set():
            with open(output_file, "ab") as f:
                f.write(b"x" * 1024)
            time.sleep(0.25)

    writer_thread = threading.Thread(target=background_writer, daemon=True)
    writer_thread.start()

    try:
        start = time.monotonic()
        result = shell.run_with_stall_detection(
            ["sleep", "8"],
            output_file=output_file,
            stall_timeout=3,
        )
        elapsed = time.monotonic() - start
    finally:
        stop_event.set()
        writer_thread.join(timeout=2)

    assert result.success is True
    assert result.returncode == 0
    # The process completed naturally (not killed by stall detection).
    assert elapsed < 12


def test_stall_detection_logs_no_speed(caplog: object, tmp_path: Path, monkeypatch: object) -> None:
    """No speed/progress/rate/MB-s logged during stall detection polling."""
    monkeypatch.setattr(subprocess_shell, "_POLL_INTERVAL", 0.5)  # type: ignore[arg-type]

    shell = SubprocessShell()
    output_file = tmp_path / "output.qcow2"
    output_file.touch()

    with caplog.at_level(logging.DEBUG, logger=SHELL_LOGGER):  # type: ignore[union-attr]
        result = shell.run_with_stall_detection(
            ["sleep", "3600"],
            output_file=output_file,
            stall_timeout=3,
        )

    # Verify stall detection fired (process was killed).
    assert result.success is False
    assert "Stall detected" in result.error  # type: ignore[operator]

    # Assert no log record contains speed/progress *metrics*.
    # The error message "Stall detected: no progress for Ns" is the
    # legitimate stall-detection output, not a speed/progress metric.
    # We check for "MB/s", "speed" (as a standalone metric), and "rate"
    # as indicators of speed/progress logging.  We do NOT check for the
    # word "progress" alone — it is unavoidably part of the stall error.
    all_messages = [r.getMessage() for r in caplog.records]  # type: ignore[union-attr]
    for msg in all_messages:
        msg_lower = msg.lower()
        assert "speed" not in msg_lower, f"Unexpected 'speed' in log: {msg}"
        assert "MB/s" not in msg, f"Unexpected 'MB/s' in log: {msg}"
        assert "rate" not in msg_lower, f"Unexpected 'rate' in log: {msg}"
