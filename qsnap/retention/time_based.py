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

import re
from datetime import datetime, timedelta

from qsnap.interfaces.retention import IRetentionEngine
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import RetentionItem, RetentionResult

_UNIT_TO_DELTA: dict[str, timedelta] = {
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
    "m": timedelta(days=30),
    "y": timedelta(days=365),
}


# Multipliers for stall-timeout parsing (seconds per unit).
_STALL_UNIT_TO_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


def parse_duration(text: str) -> timedelta:
    """Parse a duration string like ``"6h"``, ``"2d"`` into a ``timedelta``.

    ``"all"`` returns ``timedelta.max`` (effectively infinite).
    ``"latest"`` returns ``timedelta(0)`` (zero-width window).

    Units: ``h`` (hour), ``d`` (day), ``w`` (week), ``m`` (month ≈ 30 d),
    ``y`` (year ≈ 365 d).  This function is intended for *retention*
    durations where ``m`` means months.  For short timeout values where
    ``m`` means minutes, use :func:`parse_stall_timeout` instead.
    """
    if text == "all":
        return timedelta.max
    if text == "latest":
        return timedelta(0)
    match = re.match(r"^(\d+)([hdwmy])$", text)
    if match is None:
        raise ValueError(f"Invalid duration string: {text!r}")
    count = int(match.group(1))
    unit = match.group(2)
    return count * _UNIT_TO_DELTA[unit]


def parse_stall_timeout(text: str) -> int:
    """Parse a stall-timeout duration string into whole seconds.

    Supports: ``"30s"`` (30 seconds), ``"30m"`` (30 minutes),
    ``"1h"`` (1 hour), ``"2d"`` (2 days).  ``"0s"`` returns ``0``
    (disables stall detection — callers fall back to fixed-timeout
    ``shell.run()``).

    Unlike :func:`parse_duration`, ``m`` here means *minutes*, not months,
    because stall timeouts are short-lived durations, not retention windows.
    """
    match = re.match(r"^(\d+)([smhd])$", text)
    if match is None:
        raise ValueError(f"Invalid stall timeout string: {text!r}")
    count = int(match.group(1))
    unit = match.group(2)
    return count * _STALL_UNIT_TO_SECONDS[unit]


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
