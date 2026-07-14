"""IShell — abstract shell interface for subprocess execution."""

from __future__ import annotations

from abc import ABC, abstractmethod

from qsnap.models.results import ShellResult


class IShell(ABC):
    """Abstract shell interface wrapping subprocess execution.

    All ``virsh``, ``qemu-img``, and filesystem calls go through this
    interface.  This enables timeout enforcement, structured logging, and
    full mockability in tests.
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
