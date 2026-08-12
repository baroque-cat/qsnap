"""SubprocessShell — concrete IShell implementation using subprocess.run()."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from qsnap.interfaces.shell import IShell
from qsnap.models.results import ShellResult

logger = logging.getLogger(__name__)

# Polling interval for stall detection (seconds).
_POLL_INTERVAL = 60


class SubprocessShell(IShell):
    """Concrete shell implementation wrapping ``subprocess.run()``.

    Logs every command at DEBUG level (command, timeout, returncode,
    duration).  Returns ``ShellResult`` — never raises for expected
    failures (non-zero exit, timeout, command not found).
    """

    def run(self, cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            duration = time.monotonic() - start
            stdout = proc.stdout.decode() if proc.stdout else ""
            stderr = proc.stderr.decode() if proc.stderr else ""
            success = proc.returncode == 0
            error: str | None = None
            if not success:
                error = stderr.strip() or f"Command failed with return code {proc.returncode}"
            result = ShellResult(
                success=success,
                stdout=stdout,
                stderr=stderr,
                returncode=proc.returncode,
                error=error,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            result = ShellResult(
                success=False,
                stdout="",
                stderr="",
                returncode=-1,
                error=f"Command timed out after {timeout}s",
            )
        except FileNotFoundError as exc:
            duration = time.monotonic() - start
            result = ShellResult(
                success=False,
                stdout="",
                stderr="",
                returncode=-1,
                error=f"Command not found: {exc}",
            )

        if not result.success and not check:
            logger.error(
                "cmd=%s timeout=%d returncode=%d duration=%.3fs error=%s",
                cmd,
                timeout,
                result.returncode,
                duration,
                result.error,
            )
        else:
            logger.debug(
                "cmd=%s timeout=%d returncode=%d duration=%.3fs",
                cmd,
                timeout,
                result.returncode,
                duration,
            )
        return result

    def run_with_stall_detection(
        self,
        cmd: list[str],
        output_file: Path | None = None,
        stall_timeout: int = 1800,
        check: bool = False,
    ) -> ShellResult:
        """Execute *cmd* with stall detection via output-file growth.

        Uses :func:`subprocess.Popen` and polls every ``_POLL_INTERVAL``
        seconds.  When *output_file* is provided and its size does not
        change for *stall_timeout* seconds, the process is killed and a
        stall-error :class:`ShellResult` is returned.

        When *output_file* is ``None``, no stall detection is performed
        — the process runs to completion (effectively infinite timeout).
        """
        start = time.monotonic()
        logger.debug("cmd=%s stall_timeout=%d output_file=%s", cmd, stall_timeout, output_file)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            duration = time.monotonic() - start
            result = ShellResult(
                success=False,
                stdout="",
                stderr="",
                returncode=-1,
                error=f"Command not found: {exc}",
            )
            logger.debug(
                "cmd=%s returncode=%d duration=%.3fs error=%s",
                cmd,
                result.returncode,
                duration,
                result.error,
            )
            return result

        last_size = 0
        last_growth_time = time.monotonic()

        try:
            while True:
                try:
                    proc.wait(timeout=_POLL_INTERVAL)
                except subprocess.TimeoutExpired:
                    # Process still running — check output file growth.
                    if output_file is not None:
                        try:
                            current_size = output_file.stat().st_size
                        except OSError:
                            current_size = last_size
                        now = time.monotonic()
                        if current_size > last_size:
                            last_size = current_size
                            last_growth_time = now
                        elif (now - last_growth_time) >= stall_timeout:
                            # Stall detected — kill the process.
                            proc.kill()
                            proc.wait()
                            duration = time.monotonic() - start
                            result = ShellResult(
                                success=False,
                                stdout="",
                                stderr="",
                                returncode=-1,
                                error=f"Stall detected: no progress for {stall_timeout}s",
                            )
                            logger.debug(
                                "cmd=%s stall_timeout=%d returncode=%d duration=%.3fs error=%s",
                                cmd,
                                stall_timeout,
                                result.returncode,
                                duration,
                                result.error,
                            )
                            return result
                    continue

                # Process finished — collect output.
                duration = time.monotonic() - start
                stdout = proc.stdout.read().decode() if proc.stdout else ""
                stderr = proc.stderr.read().decode() if proc.stderr else ""
                success = proc.returncode == 0
                error: str | None = None
                if not success:
                    error = stderr.strip() or f"Command failed with return code {proc.returncode}"
                result = ShellResult(
                    success=success,
                    stdout=stdout,
                    stderr=stderr,
                    returncode=proc.returncode,
                    error=error,
                )
                if not result.success and not check:
                    logger.error(
                        "cmd=%s stall_timeout=%d returncode=%d duration=%.3fs error=%s",
                        cmd,
                        stall_timeout,
                        result.returncode,
                        duration,
                        result.error,
                    )
                else:
                    logger.debug(
                        "cmd=%s stall_timeout=%d returncode=%d duration=%.3fs",
                        cmd,
                        stall_timeout,
                        result.returncode,
                        duration,
                    )
                return result
        except Exception:
            # Unexpected error — ensure process is reaped.
            proc.kill()
            proc.wait()
            raise

    def run_with_heartbeat(
        self,
        cmd: list[str],
        timeout: int,
        heartbeat_seconds: int,
        on_heartbeat: Callable[[int], None],
        check: bool = False,
    ) -> ShellResult:
        """Execute *cmd* with a hard *timeout* and periodic heartbeat callback.

        Runs the command via ``Popen`` with stdout/stderr pipes drained
        continuously by daemon reader threads.  Polls the process every
        *heartbeat_seconds*; on each poll expiry calls
        ``on_heartbeat(elapsed)``.  When the total elapsed time reaches
        *timeout*, the process is killed and a timeout
        :class:`ShellResult` is returned.
        """
        start = time.monotonic()
        logger.debug(
            "cmd=%s timeout=%d heartbeat=%d",
            cmd,
            timeout,
            heartbeat_seconds,
        )

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            duration = time.monotonic() - start
            result = ShellResult(
                success=False,
                stdout="",
                stderr="",
                returncode=-1,
                error=f"Command not found: {exc}",
            )
            logger.debug(
                "cmd=%s returncode=%d duration=%.3fs error=%s",
                cmd,
                result.returncode,
                duration,
                result.error,
            )
            return result

        # Daemon reader threads so a chatty child never blocks on a full
        # pipe buffer.  Captured output is collected into mutable
        # containers and joined after process exit with a bounded wait.
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        def _drain(pipe: subprocess.PIPE | None, sink: list[bytes]) -> None:  # type: ignore[valid-type]
            if pipe is not None:
                while True:
                    chunk = pipe.read(65536)
                    if not chunk:
                        break
                    sink.append(chunk)

        stdout_thread = threading.Thread(
            target=_drain, args=(proc.stdout, stdout_chunks), daemon=True
        )
        stderr_thread = threading.Thread(
            target=_drain, args=(proc.stderr, stderr_chunks), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()

        try:
            while True:
                elapsed = time.monotonic() - start
                if elapsed >= timeout:
                    # Hard timeout — kill the process.
                    proc.kill()
                    proc.wait()
                    duration = time.monotonic() - start
                    result = ShellResult(
                        success=False,
                        stdout="",
                        stderr="",
                        returncode=-1,
                        error=f"Command timed out after {timeout}s",
                    )
                    logger.error(
                        "cmd=%s timeout=%d returncode=%d duration=%.3fs error=%s",
                        cmd,
                        timeout,
                        result.returncode,
                        duration,
                        result.error,
                    )
                    # Drain remaining output from pipes before joining threads.
                    stdout_thread.join(timeout=5)
                    stderr_thread.join(timeout=5)
                    return result

                try:
                    proc.wait(timeout=heartbeat_seconds)
                except subprocess.TimeoutExpired:
                    # Process still running — invoke heartbeat callback.
                    # Elapsed is measured AT CALLBACK TIME (after the
                    # slice wait expired), so the reported value matches
                    # wall-clock reality (~60s, ~120s, ...) rather than
                    # lagging one slice behind (observability spec).
                    on_heartbeat(int(time.monotonic() - start))
                    continue

                # Process finished normally.
                duration = time.monotonic() - start
                # Drain remaining output.
                stdout_thread.join(timeout=5)
                stderr_thread.join(timeout=5)
                stdout = b"".join(stdout_chunks).decode()
                stderr = b"".join(stderr_chunks).decode()
                success = proc.returncode == 0
                error: str | None = None
                if not success:
                    error = stderr.strip() or f"Command failed with return code {proc.returncode}"
                result = ShellResult(
                    success=success,
                    stdout=stdout,
                    stderr=stderr,
                    returncode=proc.returncode,
                    error=error,
                )
                if not result.success and not check:
                    logger.error(
                        "cmd=%s timeout=%d heartbeat=%d returncode=%d duration=%.3fs error=%s",
                        cmd,
                        timeout,
                        heartbeat_seconds,
                        result.returncode,
                        duration,
                        result.error,
                    )
                else:
                    logger.debug(
                        "cmd=%s timeout=%d heartbeat=%d returncode=%d duration=%.3fs",
                        cmd,
                        timeout,
                        heartbeat_seconds,
                        result.returncode,
                        duration,
                    )
                return result
        except Exception:
            # Unexpected error — ensure process is reaped.
            proc.kill()
            proc.wait()
            raise
