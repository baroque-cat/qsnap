"""MockShell — mock IShell for unit tests.

Supports ``.expect("pattern").returns(ShellResult(...))`` and
``.expect("pattern").raises(exception)`` for preconfigured command
responses.  Also supports ``.call_history`` for inspecting all calls made.
"""

from __future__ import annotations

import re
from pathlib import Path

from qsnap.interfaces.shell import IShell
from qsnap.models.results import ShellResult


class _Expectation:
    """A single mock expectation: pattern → action (returns or raises)."""

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self._result: ShellResult | None = None
        self._exception: Exception | None = None

    def returns(self, result: ShellResult) -> _Expectation:
        self._result = result
        return self

    def raises(self, exception: Exception) -> _Expectation:
        self._exception = exception
        return self

    def execute(self) -> ShellResult:
        if self._exception is not None:
            raise self._exception
        if self._result is not None:
            return self._result
        return ShellResult(
            success=False,
            stdout="",
            stderr="",
            returncode=-1,
            error="Mock expectation not configured",
        )


class MockShell(IShell):
    """Mock shell that returns preconfigured ``ShellResult`` objects.

    Tracks every command in ``call_history`` for post-run assertions
    (e.g. "qemu-img rebase was NOT called").
    """

    def __init__(self) -> None:
        self._expectations: list[_Expectation] = []
        self._call_history: list[str] = []

    @property
    def call_history(self) -> list[str]:
        """All commands executed so far, in order (for post-run assertions)."""
        return list(self._call_history)

    def expect(self, pattern: str) -> _Expectation:
        """Register an expectation for commands matching *pattern* (regex)."""
        exp = _Expectation(pattern)
        self._expectations.append(exp)
        return exp

    def expect_first(self, pattern: str) -> _Expectation:
        """Register a high-priority expectation at the front of the list.

        Useful for overriding global fixture expectations that match the
        same pattern.  The first matching expectation wins.
        """
        exp = _Expectation(pattern)
        self._expectations.insert(0, exp)
        return exp

    def run(self, cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        cmd_str = " ".join(cmd)
        self._call_history.append(cmd_str)
        for exp in self._expectations:
            if re.search(exp.pattern, cmd_str):
                return exp.execute()
        return ShellResult(
            success=False,
            stdout="",
            stderr="",
            returncode=-1,
            error=f"No mock configured for: {cmd_str}",
        )

    def run_with_stall_detection(
        self,
        cmd: list[str],
        output_file: Path | None = None,
        stall_timeout: int = 1800,
        check: bool = False,
    ) -> ShellResult:
        """Mock stall-detection execution — matches expectations like ``run``.

        The mock does not perform real polling or stall detection.  It
        matches the command against registered expectations (same as
        :meth:`run`) and returns the preconfigured :class:`ShellResult`.
        Tests that need to assert stall-detection parameters (e.g.
        ``output_file``, ``stall_timeout``) should inspect the command
        string via the expectation pattern.
        """
        cmd_str = " ".join(cmd)
        self._call_history.append(cmd_str)
        for exp in self._expectations:
            if re.search(exp.pattern, cmd_str):
                return exp.execute()
        return ShellResult(
            success=False,
            stdout="",
            stderr="",
            returncode=-1,
            error=f"No mock configured for: {cmd_str}",
        )
