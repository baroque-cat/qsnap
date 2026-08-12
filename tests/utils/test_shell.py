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
import sys
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


def test_probing_call_with_check_true_logs_debug(caplog) -> None:
    """Probing call (check=True) logs at DEBUG; default (check=False) at ERROR.

    When ``check=True``, a failing command is an expected, non-error
    condition (e.g. probing whether a compress driver is available).
    The failure must be logged at DEBUG level — not ERROR — so it does
    not pollute the error stream.

    Conversely, when ``check`` is omitted (defaults to ``False``), a
    failure is unexpected and must be logged at ERROR level.
    """
    shell = SubprocessShell()

    # ── check=True: probing call, failure must log at DEBUG ────────────
    with caplog.at_level(logging.DEBUG, logger=SHELL_LOGGER):
        result_check_true = shell.run(["false"], timeout=30, check=True)

    assert isinstance(result_check_true, ShellResult)
    assert result_check_true.success is False

    true_records = [r for r in caplog.records if r.name == SHELL_LOGGER]
    true_error = [r for r in true_records if r.levelno == logging.ERROR]
    true_debug = [r for r in true_records if r.levelno == logging.DEBUG]

    assert len(true_error) == 0, "check=True probing failure must NOT log at ERROR level"
    assert len(true_debug) >= 1, (
        "check=True probing failure must log at DEBUG level for traceability"
    )

    caplog.clear()

    # ── default (check=False): failure must log at ERROR ───────────────
    with caplog.at_level(logging.DEBUG, logger=SHELL_LOGGER):
        result_default = shell.run(["false"], timeout=30)

    assert isinstance(result_default, ShellResult)
    assert result_default.success is False

    default_records = [r for r in caplog.records if r.name == SHELL_LOGGER]
    default_error = [r for r in default_records if r.levelno == logging.ERROR]
    _default_debug = [r for r in default_records if r.levelno == logging.DEBUG]

    assert len(default_error) >= 1, "default-mode (check=False) failure must log at ERROR level"
    msg = default_error[0].getMessage()
    assert "false" in msg
    assert "returncode=" in msg
    # Verify the ERROR message carries diagnostic fields.
    assert "error=" in msg or "timeout=" in msg


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


# ── run_with_heartbeat ──────────────────────────────────────────────────


def test_run_with_heartbeat_normal_completion() -> None:
    """A command finishing before the first heartbeat returns success.

    Mirrors ``test_run_with_stall_detection_completes_normally``: the
    child exits 0 immediately, so ``on_heartbeat`` must NEVER be called
    (no poll expiry while the process is still running).
    """
    shell = SubprocessShell()
    heartbeats: list[int] = []

    result = shell.run_with_heartbeat(
        ["echo", "hi"],
        timeout=60,
        heartbeat_seconds=10,
        on_heartbeat=heartbeats.append,
    )

    assert isinstance(result, ShellResult)
    assert result.success is True
    assert result.returncode == 0
    assert "hi" in result.stdout
    assert result.stderr == ""
    assert result.error is None
    # The child exited before the first heartbeat slice elapsed.
    assert heartbeats == []


def test_run_with_heartbeat_heartbeat_fires() -> None:
    """A long-running child triggers on_heartbeat with increasing elapsed.

    The child sleeps ~2s with a 1s heartbeat slice: the poll loop must
    expire at least once while the process is still running and report
    strictly increasing elapsed values.
    """
    shell = SubprocessShell()
    heartbeats: list[int] = []

    result = shell.run_with_heartbeat(
        ["sleep", "2"],
        timeout=30,
        heartbeat_seconds=1,
        on_heartbeat=heartbeats.append,
    )

    assert isinstance(result, ShellResult)
    assert result.success is True
    assert result.returncode == 0
    assert result.error is None

    # At least one heartbeat fired while the child was running.
    assert len(heartbeats) >= 1
    # Elapsed values are strictly increasing (no duplicates, no going back).
    assert all(b > a for a, b in zip(heartbeats, heartbeats[1:], strict=False))
    # Heartbeats are reported only while the process runs — the final
    # value must predate the generous 30s timeout by a wide margin
    # (the child itself only sleeps ~2s).
    assert heartbeats[-1] < 10


def test_run_with_heartbeat_hard_timeout_kills() -> None:
    """A child exceeding the hard timeout is killed with a timeout error.

    ``sleep 10`` with ``timeout=2`` must be killed around the 2s mark
    and return ``ShellResult(success=False, returncode=-1,
    error="Command timed out after 2s")`` — and no further heartbeat may
    fire after the kill.
    """
    shell = SubprocessShell()
    heartbeats: list[int] = []

    start = time.monotonic()
    result = shell.run_with_heartbeat(
        ["sleep", "10"],
        timeout=2,
        heartbeat_seconds=1,
        on_heartbeat=heartbeats.append,
    )
    elapsed = time.monotonic() - start

    assert isinstance(result, ShellResult)
    assert result.success is False
    assert result.returncode == -1
    assert result.error == "Command timed out after 2s"
    # The child was killed promptly instead of waiting out `sleep 10`.
    assert elapsed < 9

    # No further heartbeats after the kill/return.
    heartbeats_at_return = len(heartbeats)
    time.sleep(0.2)
    assert len(heartbeats) == heartbeats_at_return


def test_run_with_heartbeat_chatty_child_no_deadlock() -> None:
    """A child writing >64KB to both pipes does not deadlock.

    The child writes 200KB to stdout and 200KB to stderr while running —
    well beyond the 64KB pipe buffer.  The daemon reader threads must
    drain both pipes, the process must complete, and the full output must
    be captured in the returned ShellResult.  Reader threads are joined
    after process exit — no thread leak.
    """
    shell = SubprocessShell()
    script = (
        "import sys; "
        "sys.stdout.write('x' * 200000); sys.stdout.flush(); "
        "sys.stderr.write('y' * 200000); sys.stderr.flush()"
    )
    threads_before = threading.active_count()

    result = shell.run_with_heartbeat(
        [sys.executable, "-c", script],
        timeout=30,
        heartbeat_seconds=1,
        on_heartbeat=lambda elapsed: None,
    )

    assert isinstance(result, ShellResult)
    assert result.success is True
    assert result.returncode == 0
    assert result.error is None
    # Full output from both pipes is captured (no deadlock, no truncation).
    assert len(result.stdout) == 200000
    assert result.stdout == "x" * 200000
    assert len(result.stderr) == 200000
    assert result.stderr == "y" * 200000

    # Reader threads are joined after exit — no thread leak.  Allow a
    # brief settle window for the joined threads to fully tear down.
    for _ in range(20):
        if threading.active_count() <= threads_before:
            break
        time.sleep(0.05)
    assert threading.active_count() <= threads_before


def test_run_with_heartbeat_check_mode_suppresses_error(caplog: object) -> None:
    """run_with_heartbeat with check=True logs a failing command at DEBUG,
    not ERROR — identical semantics to run(check=True) and
    run_with_stall_detection(check=True) (shell-abstraction spec)."""
    shell = SubprocessShell()

    with caplog.at_level(logging.DEBUG, logger=SHELL_LOGGER):  # type: ignore[union-attr]
        result = shell.run_with_heartbeat(
            ["false"],
            timeout=30,
            heartbeat_seconds=10,
            on_heartbeat=lambda elapsed: None,
            check=True,
        )

    assert result.success is False

    shell_records = [r for r in caplog.records if r.name == SHELL_LOGGER]  # type: ignore[union-attr]
    error_records = [r for r in shell_records if r.levelno == logging.ERROR]
    debug_records = [r for r in shell_records if r.levelno == logging.DEBUG]

    assert len(error_records) == 0
    assert len(debug_records) >= 1


def test_run_with_heartbeat_check_false_logs_error(caplog: object) -> None:
    """run_with_heartbeat with the default check=False logs failures at ERROR."""
    shell = SubprocessShell()

    with caplog.at_level(logging.DEBUG, logger=SHELL_LOGGER):  # type: ignore[union-attr]
        result = shell.run_with_heartbeat(
            ["false"],
            timeout=30,
            heartbeat_seconds=10,
            on_heartbeat=lambda elapsed: None,
        )

    assert result.success is False

    shell_records = [r for r in caplog.records if r.name == SHELL_LOGGER]  # type: ignore[union-attr]
    error_records = [r for r in shell_records if r.levelno == logging.ERROR]
    assert len(error_records) == 1
