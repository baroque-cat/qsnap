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

        Args:
            items: Snapshots or backups with timestamps.
            policy: Retention policy (hourly/daily/weekly/monthly/yearly
                    counts plus ``preserve_min``).
            now: Current datetime for ``preserve_min`` evaluation.

        Returns:
            ``RetentionResult`` with ``keep`` and ``remove`` name lists.
        """
        ...
