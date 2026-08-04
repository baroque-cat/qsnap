"""TimeBasedRetention — count-based retention engine.

Pure function: no I/O, no Core inheritance, no side effects.

The count-based engine sorts items by timestamp ascending, keeps the
newest N, and marks the rest for removal.  For snapshots, N =
``policy.chain_length``; for targets (per-chain), N =
``policy.keep_generations``.  When ``chain_length`` is ``0`` (unset for
snapshots), the engine falls back to ``keep_generations`` as the keep
count; when both are ``0``, all items are marked for removal.
"""

from __future__ import annotations

from datetime import datetime

from qsnap.interfaces.retention import IRetentionEngine
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import RetentionItem, RetentionResult
from qsnap.utils.time import (  # noqa: F401 — re-export for backward compatibility
    parse_duration,
    parse_stall_timeout,
)


def _keep_count(policy: RetentionPolicy) -> int:
    """Return the number of items to keep for the given policy.

    When ``chain_length`` is positive, it is the keep count (snapshot
    context).  Otherwise ``keep_generations`` is the keep count (target
    context).  When both are zero, all items are removed.
    """
    if policy.chain_length > 0:
        return policy.chain_length
    return policy.keep_generations


class TimeBasedRetention(IRetentionEngine):
    """Count-based retention engine.

    The engine is a pure function: given the same inputs, it always
    returns the same output.  No I/O, no random, no external state.

    The keep count N is determined by :func:`_keep_count`: ``chain_length``
    when positive, otherwise ``keep_generations``.  Items are sorted by
    timestamp ascending; the newest N are kept and the rest removed.
    """

    def __init__(self, policy: RetentionPolicy) -> None:
        self._policy = policy

    def evaluate(
        self,
        items: list[RetentionItem],
        policy: RetentionPolicy,
        now: datetime,
    ) -> RetentionResult:
        if not items:
            return RetentionResult(keep=[], remove=[])

        # Sort by timestamp ascending (oldest first).
        sorted_items = sorted(items, key=lambda it: it.timestamp)

        n = _keep_count(policy)

        # Keep the newest N items (last N in ascending order).
        if n <= 0:
            keep: list[str] = []
            remove = [it.name for it in sorted_items]
        else:
            keep = [it.name for it in sorted_items[-n:]]
            remove = [it.name for it in sorted_items[:-n]] if n < len(sorted_items) else []

        return RetentionResult(keep=keep, remove=remove)

    def explain(
        self,
        items: list[RetentionItem],
        policy: RetentionPolicy,
        now: datetime,
    ) -> dict[str, int]:
        """Return a count-based summary of the retention policy.

        Returns a dict with ``keep_count`` (number of items kept) and
        ``remove_count`` (number of items removed).
        """
        total = len(items)
        n = _keep_count(policy)
        keep_count = min(n, total) if n > 0 else 0
        remove_count = total - keep_count
        return {"keep_count": keep_count, "remove_count": remove_count}
