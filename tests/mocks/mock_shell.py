"""MockShell — mock IShell for unit tests.

Supports ``.expect("pattern").returns(ShellResult(...))`` and
``.expect("pattern").raises(exception)`` for preconfigured command
responses.
"""

from __future__ import annotations

import re

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
    """Mock shell that returns preconfigured ``ShellResult`` objects."""

    def __init__(self) -> None:
        self._expectations: list[_Expectation] = []

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
