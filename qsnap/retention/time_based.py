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

_WEEKDAY_MAP: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _parse_duration(text: str) -> timedelta:
    """Parse a duration string like ``"6h"``, ``"2d"`` into a ``timedelta``.

    ``"all"`` returns ``timedelta.max`` (effectively infinite).
    ``"latest"`` returns ``timedelta(0)`` (zero-width window).
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


def _bucket_key(
    ts: datetime,
    bucket: str,
    preserve_day_of_week: str = "monday",
) -> tuple[int, ...]:
    """Return the grouping key for *ts* under the given *bucket*.

    For the ``weekly`` bucket, the week boundary is determined by
    *preserve_day_of_week* (case-insensitive, ``"monday"`` … ``"sunday"``).
    """
    if bucket == "hourly":
        return (ts.year, ts.month, ts.day, ts.hour)
    if bucket == "daily":
        return (ts.year, ts.month, ts.day)
    if bucket == "weekly":
        target_dow = _WEEKDAY_MAP[preserve_day_of_week.lower()]
        delta = (ts.weekday() - target_dow) % 7
        week_start = ts.date() - timedelta(days=delta)
        return (week_start.year, week_start.month, week_start.day)
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
        preserve_day_of_week: str = "monday",
    ) -> RetentionResult:
        if not items:
            return RetentionResult(keep=[], remove=[])

        # Sort by timestamp ascending (oldest first).
        sorted_items = sorted(items, key=lambda it: it.timestamp)

        keep_names: set[str] = set()

        # 1. preserve_min — keep all items within the minimum window.
        if policy.preserve_min == "all":
            keep_names.update(it.name for it in sorted_items)
        elif policy.preserve_min == "latest":
            # Keep only the single most recent item.
            if sorted_items:
                keep_names.add(sorted_items[-1].name)
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
            kept = self._select_by_bucket(sorted_items, bucket_name, count, preserve_day_of_week)
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
        preserve_day_of_week: str = "monday",
    ) -> list[RetentionItem]:
        """Select the earliest item per bucket, keeping the *count* most recent buckets."""
        # Group items by bucket key; keep the first (earliest) item per group.
        groups: dict[tuple[int, ...], RetentionItem] = {}
        for item in items:
            key = _bucket_key(item.timestamp, bucket, preserve_day_of_week)
            if key not in groups:
                groups[key] = item  # items are sorted ascending → first is earliest

        # Sort group keys descending (most recent bucket first).
        sorted_keys = sorted(groups.keys(), reverse=True)

        # Take the first *count* buckets.
        selected_keys = sorted_keys[:count]
        return [groups[key] for key in selected_keys]
