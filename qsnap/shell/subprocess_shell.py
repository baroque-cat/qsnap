"""SubprocessShell — concrete IShell implementation using subprocess.run()."""

from __future__ import annotations

import logging
import subprocess
import time

from qsnap.interfaces.shell import IShell
from qsnap.models.results import ShellResult

logger = logging.getLogger(__name__)


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
