"""IShell — abstract shell interface for subprocess execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from qsnap.models.results import ShellResult


class IShell(ABC):
    """Abstract shell interface wrapping subprocess execution.

    All ``virsh``, ``qemu-img``, and filesystem calls go through this
    interface.  This enables timeout enforcement, structured logging, and
    full mockability in tests.

    Two execution methods are provided:

    - :meth:`run` — fixed-timeout execution for short commands (``virsh``,
      ``qemu-img info``).  Kills the process after *timeout* seconds.
    - :meth:`run_with_stall_detection` — output-growth monitoring for
      long-running data-transfer commands (``qemu-img convert``).
      Polls the *output_file* size every 60 seconds; kills the process
      only when no growth is observed for *stall_timeout* seconds.  No
      maximum timeout — if data flows, the process runs to completion.
    """

    @abstractmethod
    def run(self, cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        """Execute *cmd* with a *timeout* in seconds.

        When *check* is ``True``, command failures are logged at DEBUG
        level (not ERROR) — useful for pre-flight checks where failure
        is expected and not an error condition.

        Returns a :class:`ShellResult` — never raises for expected
        failures (non-zero exit, timeout, command not found).
        """
        ...

    @abstractmethod
    def run_with_stall_detection(
        self,
        cmd: list[str],
        output_file: Path | None = None,
        stall_timeout: int = 1800,
        check: bool = False,
    ) -> ShellResult:
        """Execute *cmd* with stall detection via output-file growth.

        Used for long-running data-transfer commands (``qemu-img
        convert``) where a fixed timeout would kill a
        correctly-progressing but slow transfer.  Instead, the process
        is killed only when *output_file* shows no size growth for
        *stall_timeout* seconds.

        When *output_file* is ``None``, behaves like :meth:`run` with an
        effectively infinite timeout (no stall detection, no maximum
        timeout).  This is the fallback for commands that do not write
        to a single file.

        Polls the process every 60 seconds.  On each poll, if the
        process has finished, returns its :class:`ShellResult`.  If the
        process is still running, checks *output_file* size; if it has
        not grown since the last poll that observed a change, and the
        elapsed time since the last growth exceeds *stall_timeout*,
        kills the process and returns
        ``ShellResult(success=False, error="Stall detected: no progress
        for {N}s")``.

        No speed or progress is logged — only DEBUG-level logs for
        command start, stall, and error events.
        """
        ...
