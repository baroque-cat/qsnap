"""TimeBasedRetention — btrbk-style time-based retention engine.

Pure function: no I/O, no Core inheritance, no side effects.
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


def _parse_duration(text: str) -> timedelta:
    """Parse a duration string like ``"6h"``, ``"2d"`` into a ``timedelta``.

    ``"all"`` returns ``timedelta.max`` (effectively infinite).
    """
    if text == "all":
        return timedelta.max
    match = re.match(r"^(\d+)([hdwmy])$", text)
    if match is None:
        raise ValueError(f"Invalid duration string: {text!r}")
    count = int(match.group(1))
    unit = match.group(2)
    return count * _UNIT_TO_DELTA[unit]


def _bucket_key(ts: datetime, bucket: str) -> tuple[int, ...]:
    """Return the grouping key for *ts* under the given *bucket*."""
    if bucket == "hourly":
        return (ts.year, ts.month, ts.day, ts.hour)
    if bucket == "daily":
        return (ts.year, ts.month, ts.day)
    if bucket == "weekly":
        iso = ts.isocalendar()
        return (iso.year, iso.week)
    if bucket == "monthly":
        return (ts.year, ts.month)
    if bucket == "yearly":
        return (ts.year,)
    raise ValueError(f"Unknown bucket: {bucket!r}")


class TimeBasedRetention(IRetentionEngine):
    """Time-based retention engine implementing the btrbk algorithm.

    The engine is a pure function: given the same inputs, it always
    returns the same output.  No I/O, no random, no external state.
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

        keep_names: set[str] = set()

        # 1. preserve_min — keep all items within the minimum window.
        if policy.preserve_min == "all":
            keep_names.update(it.name for it in sorted_items)
        else:
            min_delta = _parse_duration(policy.preserve_min)
            threshold = now - min_delta
            keep_names.update(it.name for it in sorted_items if it.timestamp >= threshold)

        # 2. Time-bucket retention.
        buckets = [
            ("hourly", policy.hourly),
            ("daily", policy.daily),
            ("weekly", policy.weekly),
            ("monthly", policy.monthly),
            ("yearly", policy.yearly),
        ]
        for bucket_name, count in buckets:
            if count <= 0:
                continue
            kept = self._select_by_bucket(sorted_items, bucket_name, count)
            keep_names.update(it.name for it in kept)

        # Build ordered keep / remove lists (oldest first).
        keep = [it.name for it in sorted_items if it.name in keep_names]
        remove = [it.name for it in sorted_items if it.name not in keep_names]
        return RetentionResult(keep=keep, remove=remove)

    @staticmethod
    def _select_by_bucket(
        items: list[RetentionItem],
        bucket: str,
        count: int,
    ) -> list[RetentionItem]:
        """Select the earliest item per bucket, keeping the *count* most recent buckets."""
        # Group items by bucket key; keep the first (earliest) item per group.
        groups: dict[tuple[int, ...], RetentionItem] = {}
        for item in items:
            key = _bucket_key(item.timestamp, bucket)
            if key not in groups:
                groups[key] = item  # items are sorted ascending → first is earliest

        # Sort group keys descending (most recent bucket first).
        sorted_keys = sorted(groups.keys(), reverse=True)

        # Take the first *count* buckets.
        selected_keys = sorted_keys[:count]
        return [groups[key] for key in selected_keys]
