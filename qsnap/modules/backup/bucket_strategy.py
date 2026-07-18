"""BucketFullStrategy — decides when to create a bucket-driven FULL backup.

Extracted from ``Core``'s private ``@staticmethod`` bucket logic so that
``Core`` no longer holds business logic.  The strategy is a stateless
worker created through ``IVMModuleFactory.create_bucket_full_strategy()``.
"""

from __future__ import annotations

from datetime import datetime

from qsnap.interfaces.bucket_strategy import IBucketFullStrategy
from qsnap.models.config import RetentionPolicy, TargetConfig
from qsnap.models.results import FullBackupInfo


class BucketFullStrategy(IBucketFullStrategy):
    """Stateless bucket FULL backup strategy.

    Determines which buckets to check:
    1. If any ``anchor_*`` field is True, check only F-marked buckets
       in descending order (yearly → monthly → weekly → daily → hourly).
    2. Otherwise, check ALL buckets where ``policy.{bucket} > 0``
       in descending order.

    For each checked bucket, finds the most recent FULL from *all_fulls*
    with matching ``bucket_level``.  Returns ``(True, bucket_level)``
    when: (a) no previous FULL exists for that bucket, or (b) the
    snapshot's timestamp falls in a new period of that bucket compared
    to the matching FULL's timestamp.  Short-circuits on the first match
    — at most one FULL is created per snapshot.
    """

    @staticmethod
    def _period_key(ts: datetime, bucket: str) -> str:
        """Compute the period key for *ts* under *bucket*.

        Buckets: yearly, monthly, weekly, daily, hourly.
        """
        if bucket == "yearly":
            return ts.strftime("%Y")
        elif bucket == "monthly":
            return ts.strftime("%Y%m")
        elif bucket == "weekly":
            cal = ts.isocalendar()
            return f"{cal.year}-W{cal.week:02d}"
        elif bucket == "daily":
            return ts.strftime("%Y%m%d")
        elif bucket == "hourly":
            return ts.strftime("%Y%m%d%H")
        return ""

    @staticmethod
    def _active_buckets(policy: RetentionPolicy) -> list[str]:
        """Return buckets where ``policy.{bucket} > 0`` in descending order.

        Order: yearly, monthly, weekly, daily, hourly.
        """
        result: list[str] = []
        for bucket, count in (
            ("yearly", policy.yearly),
            ("monthly", policy.monthly),
            ("weekly", policy.weekly),
            ("daily", policy.daily),
            ("hourly", policy.hourly),
        ):
            if count > 0:
                result.append(bucket)
        return result

    @staticmethod
    def _f_anchor_buckets(policy: RetentionPolicy) -> list[str]:
        """Return buckets where ``policy.anchor_{bucket}`` is True, descending.

        Order: yearly, monthly, weekly, daily, hourly.
        """
        result: list[str] = []
        for bucket, anchor in (
            ("yearly", policy.anchor_yearly),
            ("monthly", policy.anchor_monthly),
            ("weekly", policy.anchor_weekly),
            ("daily", policy.anchor_daily),
            ("hourly", policy.anchor_hourly),
        ):
            if anchor:
                result.append(bucket)
        return result

    def should_create_full(
        self,
        target: TargetConfig,
        policy: RetentionPolicy,
        all_fulls: list[FullBackupInfo],
        snapshot_ts: datetime,
        now: datetime,
    ) -> tuple[bool, str]:
        """Return ``(True, bucket_level)`` when a new FULL should be created.

        Returns ``(False, "")`` if no checked bucket triggers a new FULL.
        The *now* parameter is accepted for interface consistency and
        future deferred-threshold logic; current bucket logic keys on
        *snapshot_ts*.
        """
        # Handle backward compatibility: old callers may pass None or a
        # single FullBackupInfo instead of a list.
        if all_fulls is None:
            all_fulls = []
        elif isinstance(all_fulls, FullBackupInfo):
            all_fulls = [all_fulls]

        # Determine which buckets to check.
        f_buckets = self._f_anchor_buckets(policy)
        if f_buckets:
            buckets_to_check = f_buckets
        else:
            buckets_to_check = self._active_buckets(policy)

        if not buckets_to_check:
            return False, ""

        for bucket in buckets_to_check:
            # Find the most recent FULL with matching bucket_level.
            matching_fulls = [f for f in all_fulls if f.bucket_level == bucket]
            if not matching_fulls:
                # No previous FULL for this bucket — create one.
                return True, bucket

            most_recent = max(matching_fulls, key=lambda f: f.timestamp)
            snapshot_key = self._period_key(snapshot_ts, bucket)
            last_key = self._period_key(most_recent.timestamp, bucket)

            if snapshot_key != last_key:
                return True, bucket

        return False, ""
