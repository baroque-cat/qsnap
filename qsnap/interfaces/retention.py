"""IRetentionEngine — abstract retention policy evaluation interface.

NOTE: ``IRetentionEngine`` does NOT inherit from ``Core``.  It is a pure
function: no I/O, no side effects, fully deterministic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

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
        preserve_day_of_week: str = "monday",
    ) -> RetentionResult:
        """Determine which items to keep and which to remove.

        Args:
            items: Snapshots or backups with timestamps.
            policy: Retention policy (hourly/daily/weekly/monthly/yearly
                    counts plus ``preserve_min``).
            now: Current datetime for ``preserve_min`` evaluation.
            preserve_day_of_week: Day on which the weekly bucket boundary
                    falls (``"monday"`` … ``"sunday"``, case-insensitive).

        Returns:
            ``RetentionResult`` with ``keep`` and ``remove`` name lists.
        """
        ...

    def explain(
        self,
        items: list[RetentionItem],
        policy: RetentionPolicy,
        now: datetime,
        preserve_day_of_week: str = "monday",
    ) -> dict[str, dict[str, Any]]:
        """Return a structured per-bucket breakdown of the retention policy.

        Each bucket name maps to a dict with ``"count"`` (number of items
        kept by that bucket) and optionally ``"range"`` (earliest and
        latest timestamps of kept items).

        Default implementation returns an empty dict.  Concrete
        implementations should override this to provide real breakdowns.
        """
        return {}
