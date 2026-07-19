"""SubprocessShell — concrete IShell implementation using subprocess.run()."""

from __future__ import annotations

import logging
import subprocess
import time
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
