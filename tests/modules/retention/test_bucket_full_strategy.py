"""Unit tests for ``BucketFullStrategy`` — bucket-driven FULL backup decisions.

Extracted from ``Core._should_create_bucket_full`` and its helpers
``_period_key``, ``_active_buckets``, ``_f_anchor_buckets``.  All tests
now call ``BucketFullStrategy().should_create_full()`` directly — no Core
instantiation needed.  Pure strategy unit tests.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.models.config import RetentionPolicy
from qsnap.models.results import FullBackupInfo
from qsnap.modules.backup.bucket_strategy import BucketFullStrategy

# ── Period key and helper tests ──────────────────────────────────────────


def test_period_key_yearly():
    assert BucketFullStrategy._period_key(datetime(2025, 7, 13), "yearly") == "2025"


def test_period_key_monthly():
    assert BucketFullStrategy._period_key(datetime(2025, 7, 13), "monthly") == "202507"


def test_period_key_weekly():
    # 2025-07-14 is a Monday (ISO W29)
    key = BucketFullStrategy._period_key(datetime(2025, 7, 14), "weekly")
    assert key == "2025-W29"


def test_period_key_daily():
    assert BucketFullStrategy._period_key(datetime(2025, 7, 13), "daily") == "20250713"


def test_period_key_hourly():
    assert BucketFullStrategy._period_key(datetime(2025, 7, 13, 10), "hourly") == "2025071310"


def test_period_key_unknown_bucket():
    assert BucketFullStrategy._period_key(datetime(2025, 7, 13), "unknown") == ""


def test_active_buckets_descending_order():
    policy = RetentionPolicy(yearly=1, monthly=12, weekly=4, daily=7, hourly=24)
    assert BucketFullStrategy._active_buckets(policy) == [
        "yearly",
        "monthly",
        "weekly",
        "daily",
        "hourly",
    ]


def test_active_buckets_single():
    policy = RetentionPolicy(daily=7)
    assert BucketFullStrategy._active_buckets(policy) == ["daily"]


def test_active_buckets_none():
    policy = RetentionPolicy()  # all zeros
    assert BucketFullStrategy._active_buckets(policy) == []


def test_f_anchor_buckets_descending():
    policy = RetentionPolicy(
        anchor_monthly=True,
        anchor_weekly=True,
        weekly=4,
        monthly=12,
    )
    assert BucketFullStrategy._f_anchor_buckets(policy) == ["monthly", "weekly"]


def test_f_anchor_buckets_none():
    policy = RetentionPolicy()
    assert BucketFullStrategy._f_anchor_buckets(policy) == []


# ── should_create_full — basic scenarios ────────────────────────────────


def test_should_create_full_first_monthly_returns_true(make_target):
    """First snapshot at new monthly period returns (True, "monthly")."""
    policy = RetentionPolicy(monthly=6)
    target = make_target()
    snapshot_ts = datetime(2025, 7, 13, 10, 0)
    now = datetime(2025, 7, 13, 10, 0)

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, [], snapshot_ts, now)

    assert should is True
    assert level == "monthly"


def test_should_create_full_same_period_returns_false(make_target):
    """Same monthly period as existing FULL returns (False, "")."""
    policy = RetentionPolicy(monthly=6)
    target = make_target()
    snapshot_ts = datetime(2025, 7, 13, 10, 0)
    now = datetime(2025, 7, 13, 10, 0)

    all_fulls = [
        FullBackupInfo(
            name="full1.FULL.monthly.qcow2",
            path=Path("/mnt/backup/full1.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 1, 0, 0),
            bucket_level="monthly",
        ),
    ]
    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is False
    assert level == ""


def test_should_create_full_multi_level_anchors(make_target):
    """Multi-level F-anchor configuration: only F-marked buckets checked."""
    policy = RetentionPolicy(
        weekly=4,
        anchor_weekly=True,
        daily=7,
        anchor_daily=False,
    )
    target = make_target()
    snapshot_ts = datetime(2025, 6, 17, 10, 0)  # Wednesday W25
    now = datetime(2025, 6, 17, 10, 0)

    # Weekly FULL in same week (W25 Monday), no daily FULL
    all_fulls = [
        FullBackupInfo(
            name="full_weekly.qcow2",
            path=Path("/mnt/backup/full_weekly.qcow2"),
            timestamp=datetime(2025, 6, 16),  # W25 Monday
            bucket_level="weekly",
        ),
    ]
    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    # F-anchor mode: only weekly checked, daily ignored. Same week → skip.
    assert should is False
    assert level == ""


# ── Highest bucket tests ────────────────────────────────────────────────


def test_highest_bucket_yearly(make_target):
    """Highest active bucket is yearly when yearly > 0."""
    policy = RetentionPolicy(yearly=3, monthly=6)
    target = make_target()
    snapshot_ts = datetime(2025, 7, 13, 10, 0)
    now = datetime(2025, 7, 13, 10, 0)

    strategy = BucketFullStrategy()

    # No prior FULL — should create full with yearly bucket
    should, level = strategy.should_create_full(target, policy, [], snapshot_ts, now)
    assert should is True
    assert level == "yearly"

    # Same year + same month — should NOT create
    should, level = strategy.should_create_full(
        target,
        policy,
        [
            FullBackupInfo(
                name="full1.FULL.yearly.qcow2",
                path=Path("/mnt/backup/full1.FULL.yearly.qcow2"),
                timestamp=datetime(2025, 2, 1, 0, 0),
                bucket_level="yearly",
            ),
            FullBackupInfo(
                name="full2.FULL.monthly.qcow2",
                path=Path("/mnt/backup/full2.FULL.monthly.qcow2"),
                timestamp=datetime(2025, 7, 1, 0, 0),
                bucket_level="monthly",
            ),
        ],
        snapshot_ts,
        now,
    )
    assert should is False

    # New year — should create (yearly is checked first)
    should, level = strategy.should_create_full(
        target,
        policy,
        [
            FullBackupInfo(
                name="full1.FULL.yearly.qcow2",
                path=Path("/mnt/backup/full1.FULL.yearly.qcow2"),
                timestamp=datetime(2024, 12, 31, 23, 59),
                bucket_level="yearly",
            ),
            FullBackupInfo(
                name="full2.FULL.monthly.qcow2",
                path=Path("/mnt/backup/full2.FULL.monthly.qcow2"),
                timestamp=datetime(2025, 6, 1, 0, 0),
                bucket_level="monthly",
            ),
        ],
        snapshot_ts,
        now,
    )
    assert should is True
    assert level == "yearly"


def test_highest_bucket_daily(make_target):
    """Highest active bucket is daily when daily > 0 and no higher bucket."""
    policy = RetentionPolicy(daily=7, hourly=24)
    target = make_target()
    snapshot_ts = datetime(2025, 7, 13, 10, 0)
    now = datetime(2025, 7, 13, 10, 0)

    strategy = BucketFullStrategy()

    # No prior FULL
    should, level = strategy.should_create_full(target, policy, [], snapshot_ts, now)
    assert should is True
    assert level == "daily"

    # Same day + same hour — should NOT create
    should, level = strategy.should_create_full(
        target,
        policy,
        [
            FullBackupInfo(
                name="full1.FULL.daily.qcow2",
                path=Path("/mnt/backup/full1.FULL.daily.qcow2"),
                timestamp=datetime(2025, 7, 13, 2, 0),
                bucket_level="daily",
            ),
            FullBackupInfo(
                name="full2.FULL.hourly.qcow2",
                path=Path("/mnt/backup/full2.FULL.hourly.qcow2"),
                timestamp=datetime(2025, 7, 13, 10, 30),
                bucket_level="hourly",
            ),
        ],
        snapshot_ts,
        now,
    )
    assert should is False

    # Next day — should create (daily is checked before hourly)
    should, level = strategy.should_create_full(
        target,
        policy,
        [
            FullBackupInfo(
                name="full1.FULL.daily.qcow2",
                path=Path("/mnt/backup/full1.FULL.daily.qcow2"),
                timestamp=datetime(2025, 7, 12, 8, 0),
                bucket_level="daily",
            ),
            FullBackupInfo(
                name="full2.FULL.hourly.qcow2",
                path=Path("/mnt/backup/full2.FULL.hourly.qcow2"),
                timestamp=datetime(2025, 7, 12, 10, 0),
                bucket_level="hourly",
            ),
        ],
        snapshot_ts,
        now,
    )
    assert should is True
    assert level == "daily"


def test_no_active_buckets_returns_false(make_target):
    """No active buckets returns (False, "")."""
    policy = RetentionPolicy()  # all zeros
    target = make_target()
    snapshot_ts = datetime(2025, 7, 13, 10, 0)
    now = datetime(2025, 7, 13, 10, 0)

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, [], snapshot_ts, now)

    assert should is False
    assert level == ""


def test_new_weekly_period_triggers_full_all_buckets(make_target):
    """New weekly period triggers FULL when higher buckets are in same period."""
    policy = RetentionPolicy(yearly=1, monthly=12, weekly=4)
    target = make_target()
    snapshot_ts = datetime(2025, 6, 16)  # W25 Monday
    now = datetime(2025, 6, 16)

    all_fulls: list[FullBackupInfo] = [
        FullBackupInfo(
            name="full_yearly.qcow2",
            path=Path("/mnt/backup/full_yearly.qcow2"),
            timestamp=datetime(2025, 1, 15),
            bucket_level="yearly",
        ),
        FullBackupInfo(
            name="full_monthly.qcow2",
            path=Path("/mnt/backup/full_monthly.qcow2"),
            timestamp=datetime(2025, 6, 10),  # W24, same month as snapshot
            bucket_level="monthly",
        ),
    ]

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is True
    assert level == "weekly"


def test_f_anchor_weekly_only_full_on_week_boundary(make_target):
    """F-anchor mode: only F-marked buckets checked, daily is ignored."""
    policy = RetentionPolicy(
        weekly=4,
        anchor_weekly=True,
        daily=7,
        anchor_daily=False,
    )
    target = make_target()

    weekly_full = FullBackupInfo(
        name="full_weekly.qcow2",
        path=Path("/mnt/backup/full_weekly.qcow2"),
        timestamp=datetime(2025, 6, 9),  # W24 Monday
        bucket_level="weekly",
    )

    strategy = BucketFullStrategy()

    # Case A: Same week (W24 Tuesday), day changed → skip (daily ignored)
    should, level = strategy.should_create_full(
        target,
        policy,
        [weekly_full],
        datetime(2025, 6, 10),
        datetime(2025, 6, 10),
    )
    assert should is False
    assert level == ""

    # Case B: New week (W25 Monday) → trigger
    should, level = strategy.should_create_full(
        target,
        policy,
        [weekly_full],
        datetime(2025, 6, 16),
        datetime(2025, 6, 16),
    )
    assert should is True
    assert level == "weekly"


# ── Full anchor scenario tests ──────────────────────────────────────────


def test_all_active_buckets_trigger_on_period_change(make_target):
    """Short-circuit: yearly period change returns (True, "yearly")."""
    policy = RetentionPolicy(
        hourly=24,
        daily=7,
        weekly=4,
        monthly=12,
        yearly=1,
        preserve_min="all",
    )
    target = make_target()
    snapshot_ts = datetime(2025, 1, 1, 5, 0)
    now = datetime(2025, 1, 1, 5, 0)

    all_fulls = [
        FullBackupInfo(
            name="yearly_full.FULL.yearly.qcow2",
            path=Path("/backup/yearly_full.FULL.yearly.qcow2"),
            timestamp=datetime(2024, 6, 1),
            bucket_level="yearly",
        ),
        FullBackupInfo(
            name="monthly_full.FULL.monthly.qcow2",
            path=Path("/backup/monthly_full.FULL.monthly.qcow2"),
            timestamp=datetime(2024, 12, 1),
            bucket_level="monthly",
        ),
        FullBackupInfo(
            name="weekly_full.FULL.weekly.qcow2",
            path=Path("/backup/weekly_full.FULL.weekly.qcow2"),
            timestamp=datetime(2024, 12, 22),
            bucket_level="weekly",
        ),  # ISO W51
        FullBackupInfo(
            name="daily_full.FULL.daily.qcow2",
            path=Path("/backup/daily_full.FULL.daily.qcow2"),
            timestamp=datetime(2024, 12, 31),
            bucket_level="daily",
        ),
        FullBackupInfo(
            name="hourly_full.FULL.hourly.qcow2",
            path=Path("/backup/hourly_full.FULL.hourly.qcow2"),
            timestamp=datetime(2024, 12, 31, 23, 0),
            bucket_level="hourly",
        ),
    ]

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is True
    assert level == "yearly"


def test_first_backup_checks_all_active_buckets(make_target):
    """Empty all_fulls list → first active bucket (descending) triggers."""
    policy = RetentionPolicy(yearly=1, monthly=12, weekly=4, preserve_min="all")
    target = make_target()
    snapshot_ts = datetime(2025, 7, 13, 10, 0)
    now = datetime(2025, 7, 13, 10, 0)

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, [], snapshot_ts, now)

    assert should is True
    assert level == "yearly"


def test_same_period_all_buckets_skips_full(make_target):
    """When all prior FULLs are in the same period as the snapshot, skip."""
    snapshot_ts = datetime(2025, 7, 14, 14, 30)  # W29 Monday
    now = datetime(2025, 7, 14, 14, 30)

    policy = RetentionPolicy(
        hourly=24,
        daily=7,
        weekly=4,
        monthly=12,
        yearly=1,
        preserve_min="all",
    )
    target = make_target()

    all_fulls = [
        FullBackupInfo(
            name="yearly_full.FULL.yearly.qcow2",
            path=Path("/backup/yearly_full.FULL.yearly.qcow2"),
            timestamp=datetime(2025, 1, 1),
            bucket_level="yearly",
        ),
        FullBackupInfo(
            name="monthly_full.FULL.monthly.qcow2",
            path=Path("/backup/monthly_full.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 1),
            bucket_level="monthly",
        ),
        FullBackupInfo(
            name="weekly_full.FULL.weekly.qcow2",
            path=Path("/backup/weekly_full.FULL.weekly.qcow2"),
            timestamp=datetime(2025, 7, 14, 10, 0),
            bucket_level="weekly",
        ),  # W29
        FullBackupInfo(
            name="daily_full.FULL.daily.qcow2",
            path=Path("/backup/daily_full.FULL.daily.qcow2"),
            timestamp=datetime(2025, 7, 14, 8, 0),
            bucket_level="daily",
        ),
        FullBackupInfo(
            name="hourly_full.FULL.hourly.qcow2",
            path=Path("/backup/hourly_full.FULL.hourly.qcow2"),
            timestamp=datetime(2025, 7, 14, 14, 0),
            bucket_level="hourly",
        ),
    ]

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is False
    assert level == ""


def test_single_active_bucket_behaves_like_highest_only(make_target):
    """Only ``monthly`` active — same behaviour as old highest-only logic."""
    policy = RetentionPolicy(monthly=12, preserve_min="all")
    target = make_target()
    snapshot_ts = datetime(2025, 8, 1, 0, 0)
    now = datetime(2025, 8, 1, 0, 0)

    all_fulls = [
        FullBackupInfo(
            name="old_monthly.FULL.monthly.qcow2",
            path=Path("/backup/old_monthly.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 15),
            bucket_level="monthly",
        ),
    ]

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is True
    assert level == "monthly"


def test_full_list_passed_to_bucket_check(make_target):
    """Method receives a list of FULLs at different bucket levels."""
    policy = RetentionPolicy(monthly=12, weekly=4, preserve_min="all")
    target = make_target()
    snapshot_ts = datetime(2025, 8, 4, 10, 0)  # Monday, ISO week 32
    now = datetime(2025, 8, 4, 10, 0)

    all_fulls = [
        FullBackupInfo(
            name="weekly_old.FULL.weekly.qcow2",
            path=Path("/backup/weekly_old.FULL.weekly.qcow2"),
            timestamp=datetime(2025, 7, 28),
            bucket_level="weekly",
        ),
        FullBackupInfo(
            name="monthly_old.FULL.monthly.qcow2",
            path=Path("/backup/monthly_old.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 15),
            bucket_level="monthly",
        ),
    ]

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is True
    assert level == "monthly"


def test_one_full_per_snapshot_despite_multiple_changes(make_target):
    """Short-circuit: yearly wins, no second FULL."""
    policy = RetentionPolicy(yearly=1, monthly=12, weekly=4, preserve_min="all")
    target = make_target()
    snapshot_ts = datetime(2025, 1, 1, 0, 0)
    now = datetime(2025, 1, 1, 0, 0)

    all_fulls = [
        FullBackupInfo(
            name="old_yearly.FULL.yearly.qcow2",
            path=Path("/backup/old_yearly.FULL.yearly.qcow2"),
            timestamp=datetime(2024, 6, 1),
            bucket_level="yearly",
        ),
        FullBackupInfo(
            name="old_monthly.FULL.monthly.qcow2",
            path=Path("/backup/old_monthly.FULL.monthly.qcow2"),
            timestamp=datetime(2024, 12, 1),
            bucket_level="monthly",
        ),
        FullBackupInfo(
            name="old_weekly.FULL.weekly.qcow2",
            path=Path("/backup/old_weekly.FULL.weekly.qcow2"),
            timestamp=datetime(2024, 12, 22),
            bucket_level="weekly",
        ),
    ]

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is True
    assert level == "yearly"


def test_f_anchor_disables_non_f_buckets(make_target):
    """F-anchor mode: only F-marked buckets checked, non-F ignored."""
    policy = RetentionPolicy(
        daily=7,
        weekly=4,
        preserve_min="all",
        anchor_weekly=True,
        anchor_daily=False,
    )
    target = make_target()
    snapshot_ts = datetime(2025, 7, 15, 10, 0)  # Tuesday
    now = datetime(2025, 7, 15, 10, 0)

    all_fulls = [
        FullBackupInfo(
            name="prior_weekly.FULL.weekly.qcow2",
            path=Path("/backup/prior_weekly.FULL.weekly.qcow2"),
            timestamp=datetime(2025, 7, 14, 10, 0),  # Monday W29
            bucket_level="weekly",
        ),
    ]

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is False
    assert level == ""


def test_multiple_f_anchors_highest_first(make_target):
    """Multiple F-anchored buckets: monthly checked before weekly."""
    policy = RetentionPolicy(
        monthly=12,
        weekly=4,
        preserve_min="all",
        anchor_monthly=True,
        anchor_weekly=True,
    )
    target = make_target()
    snapshot_ts = datetime(2025, 8, 11, 10, 0)  # Monday, W33
    now = datetime(2025, 8, 11, 10, 0)

    all_fulls = [
        FullBackupInfo(
            name="prior_monthly.FULL.monthly.qcow2",
            path=Path("/backup/prior_monthly.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 20),
            bucket_level="monthly",
        ),
        FullBackupInfo(
            name="prior_weekly.FULL.weekly.qcow2",
            path=Path("/backup/prior_weekly.FULL.weekly.qcow2"),
            timestamp=datetime(2025, 8, 11, 8, 0),
            bucket_level="weekly",
        ),
    ]

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is True
    assert level == "monthly"


def test_descending_order_all_buckets(make_target):
    """Descending order: yearly → monthly → weekly → daily → hourly."""
    policy = RetentionPolicy(
        yearly=1,
        monthly=12,
        weekly=4,
        daily=7,
        hourly=24,
        preserve_min="all",
    )
    target = make_target()
    snapshot_ts = datetime(2025, 7, 14, 14, 30)  # Monday, W29
    now = datetime(2025, 7, 14, 14, 30)

    all_fulls = [
        FullBackupInfo(
            name="old_yearly.FULL.yearly.qcow2",
            path=Path("/backup/old_yearly.FULL.yearly.qcow2"),
            timestamp=datetime(2024, 9, 1),
            bucket_level="yearly",
        ),
        FullBackupInfo(
            name="curr_monthly.FULL.monthly.qcow2",
            path=Path("/backup/curr_monthly.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 1),
            bucket_level="monthly",
        ),
        FullBackupInfo(
            name="curr_weekly.FULL.weekly.qcow2",
            path=Path("/backup/curr_weekly.FULL.weekly.qcow2"),
            timestamp=datetime(2025, 7, 14, 10, 0),
            bucket_level="weekly",
        ),  # W29
        FullBackupInfo(
            name="curr_daily.FULL.daily.qcow2",
            path=Path("/backup/curr_daily.FULL.daily.qcow2"),
            timestamp=datetime(2025, 7, 14, 8, 0),
            bucket_level="daily",
        ),
        FullBackupInfo(
            name="curr_hourly.FULL.hourly.qcow2",
            path=Path("/backup/curr_hourly.FULL.hourly.qcow2"),
            timestamp=datetime(2025, 7, 14, 14, 0),
            bucket_level="hourly",
        ),
    ]

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is True
    assert level == "yearly"


def test_f_anchor_daily_only_ignores_others(make_target):
    """Only daily is F-anchored — monthly/weekly ignored even if active."""
    policy = RetentionPolicy(
        daily=7,
        monthly=12,
        weekly=4,
        preserve_min="all",
        anchor_daily=True,
    )
    target = make_target()
    snapshot_ts = datetime(2025, 8, 15, 10, 0)
    now = datetime(2025, 8, 15, 10, 0)

    all_fulls = [
        FullBackupInfo(
            name="old_monthly.FULL.monthly.qcow2",
            path=Path("/backup/old_monthly.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 20),
            bucket_level="monthly",
        ),
        FullBackupInfo(
            name="same_daily.FULL.daily.qcow2",
            path=Path("/backup/same_daily.FULL.daily.qcow2"),
            timestamp=datetime(2025, 8, 15, 8, 0),
            bucket_level="daily",
        ),
    ]

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is False
    assert level == ""


def test_no_active_buckets_no_f_anchors_returns_false(make_target):
    """Policy with all counts zero and no F-anchors returns (False, "")."""
    policy = RetentionPolicy()
    target = make_target()
    snapshot_ts = datetime(2025, 7, 13, 10, 0)
    now = datetime(2025, 7, 13, 10, 0)

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, None, snapshot_ts, now)

    assert should is False
    assert level == ""


def test_first_backup_empty_fulls_list_creates_full(make_target):
    """Empty list → first active bucket in descending order triggers."""
    policy = RetentionPolicy(yearly=1, monthly=12, preserve_min="all")
    target = make_target()
    snapshot_ts = datetime(2025, 7, 13, 10, 0)
    now = datetime(2025, 7, 13, 10, 0)

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, [], snapshot_ts, now)

    assert should is True
    assert level == "yearly"


def test_accepts_list_single_and_none(make_target):
    """Parameter accepts list[FullBackupInfo], single FullBackupInfo, and None."""
    policy = RetentionPolicy(monthly=12, preserve_min="all")
    target = make_target()
    snapshot_ts = datetime(2025, 8, 1, 0, 0)
    now = datetime(2025, 8, 1, 0, 0)

    single_full = FullBackupInfo(
        name="single.FULL.monthly.qcow2",
        path=Path("/backup/single.FULL.monthly.qcow2"),
        timestamp=datetime(2025, 7, 15),
        bucket_level="monthly",
    )

    strategy = BucketFullStrategy()

    # (a) List[FullBackupInfo]
    should, level = strategy.should_create_full(target, policy, [single_full], snapshot_ts, now)
    assert should is True
    assert level == "monthly"

    # (b) Single FullBackupInfo (backward compat)
    should2, level2 = strategy.should_create_full(target, policy, single_full, snapshot_ts, now)
    assert should2 is True
    assert level2 == "monthly"

    # (c) None (backward compat)
    should3, level3 = strategy.should_create_full(target, policy, None, snapshot_ts, now)
    assert should3 is True
    assert level3 == "monthly"


def test_descending_yearly_skipped_monthly_triggers(make_target):
    """Descending order: yearly skipped (same period), monthly triggers."""
    policy = RetentionPolicy(
        yearly=1,
        monthly=12,
        weekly=4,
        daily=7,
        hourly=24,
        preserve_min="all",
    )
    target = make_target()
    snapshot_ts = datetime(2025, 8, 4, 14, 30)  # Monday, W32
    now = datetime(2025, 8, 4, 14, 30)

    all_fulls = [
        FullBackupInfo(
            name="same_yearly.FULL.yearly.qcow2",
            path=Path("/backup/same_yearly.FULL.yearly.qcow2"),
            timestamp=datetime(2025, 1, 1),
            bucket_level="yearly",
        ),
        FullBackupInfo(
            name="old_monthly.FULL.monthly.qcow2",
            path=Path("/backup/old_monthly.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 15),
            bucket_level="monthly",
        ),
        FullBackupInfo(
            name="old_weekly.FULL.weekly.qcow2",
            path=Path("/backup/old_weekly.FULL.weekly.qcow2"),
            timestamp=datetime(2025, 7, 28),
            bucket_level="weekly",
        ),  # W31
    ]

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is True
    assert level == "monthly"


def test_f_anchor_bucket_no_prior_full_creates_full(make_target):
    """F-anchor bucket has no prior FULL → triggers creation."""
    policy = RetentionPolicy(
        weekly=4,
        monthly=12,
        preserve_min="all",
        anchor_weekly=True,
        anchor_monthly=False,
    )
    target = make_target()
    snapshot_ts = datetime(2025, 7, 14, 10, 0)  # Monday, W29
    now = datetime(2025, 7, 14, 10, 0)

    all_fulls = [
        FullBackupInfo(
            name="only_monthly.FULL.monthly.qcow2",
            path=Path("/backup/only_monthly.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 1),
            bucket_level="monthly",
        ),
    ]

    strategy = BucketFullStrategy()
    should, level = strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)

    assert should is True
    assert level == "weekly"
