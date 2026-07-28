"""IRetentionEngine — abstract retention policy evaluation interface.

NOTE: ``IRetentionEngine`` does NOT inherit from ``Core``.  It is a pure
function: no I/O, no side effects, fully deterministic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from qsnap.models.config import RetentionPolicy
from qsnap.models.results import RetentionItem, RetentionResult


class IRetentionEngine(ABC):
    """Abstract interface for retention policy evaluation.

    Implementations are pure functions — no I/O, no Core inheritance, no
    side effects.  Given the same inputs, they always return the same
    output.
    """

    @abstractmethod
    def evaluate(
        self,
        items: list[RetentionItem],
        policy: RetentionPolicy,
        now: datetime,
    ) -> RetentionResult:
        """Determine which items to keep and which to remove.

        The count-based engine sorts items by timestamp ascending, keeps
        the newest N, and marks the rest for removal.  For snapshots,
        N = ``policy.chain_length``; for targets (per-chain), N =
        ``policy.keep_generations``.

        Args:
            items: Snapshots or backups with timestamps.
            policy: Count-based retention policy (``chain_length``,
                    ``keep_generations``).
            now: Current datetime (kept for interface stability; the
                    count-based engine does not use calendar boundaries).

        Returns:
            ``RetentionResult`` with ``keep`` and ``remove`` name lists.
        """
        ...

    def explain(
        self,
        items: list[RetentionItem],
        policy: RetentionPolicy,
        now: datetime,
    ) -> dict[str, int]:
        """Return a count-based summary of the retention policy.

        Returns a dict with ``keep_count`` (number of items kept) and
        ``remove_count`` (number of items removed).

        Default implementation returns an empty dict.  Concrete
        implementations should override this to provide real counts.
        """
        return {}
