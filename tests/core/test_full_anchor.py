"""Tests for multi-level FULL anchor and fork-mode bucket logic.

Covers ``Core._should_create_bucket_full()`` and its helpers
``_active_buckets()``, ``_f_anchor_buckets()``, and ``_period_key()``.
All calls are to static methods — no Core instantiation needed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.core import Core
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import FullBackupInfo


# ── Scenario 1: all five active buckets trigger FULL on period change ───


def test_all_active_buckets_trigger_fulls_on_period_change(make_target):
    """Short-circuit: yearly period change returns (True, "yearly").

    All five buckets are active.  Prior FULLs exist for each bucket in
    old periods; the snapshot falls in a new year, new month, new week,
    new day, and new hour.  Yearly is checked first and triggers.
    """
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

    # Prior FULLs in old periods for every bucket.
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
            timestamp=datetime(2024, 12, 22),  # ISO W51-2024
            bucket_level="weekly",
        ),
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

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        all_fulls,
        snapshot_ts,
    )
    assert should is True, "Yearly period changed — should create FULL"
    assert level == "yearly", "Yearly is highest bucket and changed period"


# ── Scenario 2: first backup checks all active buckets ──────────────────


def test_first_backup_checks_all_active_buckets(make_target):
    """Empty all_fulls list → first active bucket (descending) triggers.

    Policy: yearly=1, monthly=12, weekly=4.  No prior FULLs.
    Highest active bucket ("yearly") triggers.
    """
    policy = RetentionPolicy(yearly=1, monthly=12, weekly=4, preserve_min="all")
    target = make_target()
    snapshot_ts = datetime(2025, 7, 13, 10, 0)

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        [],
        snapshot_ts,
    )
    assert should is True, "No prior FULL — should create first FULL"
    assert level == "yearly", "Yearly is the first (highest) active bucket"


# ── Scenario 3: same period for every bucket → skip FULL ────────────────


def test_same_period_all_buckets_skips_full(make_target):
    """When all prior FULLs are in the same period as the snapshot, skip.

    Policy has all five buckets active.  Prior FULLs are placed so that
    each falls in the same period as the snapshot timestamp.
    """
    # Snapshot: 2025-07-14 14:30 (Monday, ISO week 29)
    snapshot_ts = datetime(2025, 7, 14, 14, 30)

    policy = RetentionPolicy(
        hourly=24,
        daily=7,
        weekly=4,
        monthly=12,
        yearly=1,
        preserve_min="all",
    )
    target = make_target()

    # All FULLs within the same periods as snapshot_ts.
    all_fulls = [
        FullBackupInfo(
            name="yearly_full.FULL.yearly.qcow2",
            path=Path("/backup/yearly_full.FULL.yearly.qcow2"),
            timestamp=datetime(2025, 1, 1),  # same year
            bucket_level="yearly",
        ),
        FullBackupInfo(
            name="monthly_full.FULL.monthly.qcow2",
            path=Path("/backup/monthly_full.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 1),  # same month
            bucket_level="monthly",
        ),
        FullBackupInfo(
            name="weekly_full.FULL.weekly.qcow2",
            path=Path("/backup/weekly_full.FULL.weekly.qcow2"),
            timestamp=datetime(2025, 7, 14, 10, 0),  # same ISO week (W29)
            bucket_level="weekly",
        ),
        FullBackupInfo(
            name="daily_full.FULL.daily.qcow2",
            path=Path("/backup/daily_full.FULL.daily.qcow2"),
            timestamp=datetime(2025, 7, 14, 8, 0),  # same day
            bucket_level="daily",
        ),
        FullBackupInfo(
            name="hourly_full.FULL.hourly.qcow2",
            path=Path("/backup/hourly_full.FULL.hourly.qcow2"),
            timestamp=datetime(2025, 7, 14, 14, 0),  # same hour
            bucket_level="hourly",
        ),
    ]

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        all_fulls,
        snapshot_ts,
    )
    assert should is False, "All buckets in same period — should skip FULL"
    assert level == ""


# ── Scenario 4: single active bucket (monthly) ──────────────────────────


def test_single_active_bucket_behaves_like_highest_only(make_target):
    """Only ``monthly`` active — same behaviour as old highest-only logic."""
    policy = RetentionPolicy(monthly=12, preserve_min="all")
    target = make_target()
    snapshot_ts = datetime(2025, 8, 1, 0, 0)

    # Prior monthly FULL in July (different month).
    all_fulls = [
        FullBackupInfo(
            name="old_monthly.FULL.monthly.qcow2",
            path=Path("/backup/old_monthly.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 15),
            bucket_level="monthly",
        ),
    ]

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        all_fulls,
        snapshot_ts,
    )
    assert should is True, "Monthly period changed — should create FULL"
    assert level == "monthly"


# ── Scenario 5: list of FULLs with multiple bucket levels ───────────────


def test_backup_target_passes_full_list_to_bucket_check(make_target):
    """Method receives a list of FULLs at different bucket levels.

    Verifies that the method correctly finds the most recent matching
    FULL per bucket even when multiple FULLs exist.
    """
    policy = RetentionPolicy(monthly=12, weekly=4, preserve_min="all")
    target = make_target()
    snapshot_ts = datetime(2025, 8, 4, 10, 0)  # Monday, ISO week 32

    # Multiple FULLs at different bucket levels — weekly in old week (W31),
    # monthly in old month (July).
    all_fulls = [
        FullBackupInfo(
            name="weekly_old.FULL.weekly.qcow2",
            path=Path("/backup/weekly_old.FULL.weekly.qcow2"),
            timestamp=datetime(2025, 7, 28),  # W31
            bucket_level="weekly",
        ),
        FullBackupInfo(
            name="monthly_old.FULL.monthly.qcow2",
            path=Path("/backup/monthly_old.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 15),
            bucket_level="monthly",
        ),
    ]

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        all_fulls,
        snapshot_ts,
    )
    assert should is True, "Monthly period changed — should create FULL"
    assert level == "monthly", (
        "Monthly checked first (descending); period changed from July to August"
    )


# ── Scenario 6: only ONE FULL despite multiple period changes ───────────


def test_one_full_per_snapshot_despite_multiple_period_changes(make_target):
    """Short-circuit: yearly wins, no second FULL.

    Snapshot on Jan 1 (new year, new month, new week).  Prior FULLs in
    old periods for all three active buckets.  Yearly triggers first.
    """
    policy = RetentionPolicy(yearly=1, monthly=12, weekly=4, preserve_min="all")
    target = make_target()
    snapshot_ts = datetime(2025, 1, 1, 0, 0)

    # Prior FULLs in old periods: 2024 year, Dec month, W52 week.
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
            timestamp=datetime(2024, 12, 22),  # W51-2024
            bucket_level="weekly",
        ),
    ]

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        all_fulls,
        snapshot_ts,
    )
    assert should is True, "Yearly changed — exactly one FULL needed"
    assert level == "yearly", (
        "Yearly short-circuits; only one FULL returned even though all buckets changed periods"
    )


# ── Scenario 7: F-anchor ignores non-F buckets ─────────────────────────


def test_f_anchor_disables_auto_multi_level_non_f_buckets_ignored(make_target):
    """F-anchor mode: only F-marked buckets are checked.

    Policy: daily=7, anchor_weekly=True, weekly=4, anchor_daily=False.
    Daily period changes but weekly does not.  F-anchor mode ignores
    daily, so no FULL is created.
    """
    policy = RetentionPolicy(
        daily=7,
        weekly=4,
        preserve_min="all",
        anchor_weekly=True,
        anchor_daily=False,
    )
    target = make_target()
    snapshot_ts = datetime(2025, 7, 15, 10, 0)  # Tuesday

    # Weekly FULL is in the same ISO week (W29) as the snapshot.
    # Daily period changed (last daily was 2025-07-13, snapshot is 2025-07-15).
    all_fulls = [
        FullBackupInfo(
            name="prior_weekly.FULL.weekly.qcow2",
            path=Path("/backup/prior_weekly.FULL.weekly.qcow2"),
            timestamp=datetime(2025, 7, 14, 10, 0),  # Monday W29
            bucket_level="weekly",
        ),
    ]

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        all_fulls,
        snapshot_ts,
    )
    assert should is False, "F-anchor mode: daily is ignored; weekly is same period → skip"
    assert level == ""


# ── Scenario 8: multiple F-anchors, highest triggers first ──────────────


def test_multiple_f_anchors_all_checked_highest_first(make_target):
    """Multiple F-anchored buckets: monthly checked before weekly.

    Policy: anchor_monthly=True, anchor_weekly=True, monthly=12, weekly=4.
    Prior monthly FULL in old month, prior weekly FULL in same week.
    Snapshot in new month → monthly triggers.
    """
    policy = RetentionPolicy(
        monthly=12,
        weekly=4,
        preserve_min="all",
        anchor_monthly=True,
        anchor_weekly=True,
    )
    target = make_target()
    snapshot_ts = datetime(2025, 8, 11, 10, 0)  # Monday, ISO week 33

    all_fulls = [
        FullBackupInfo(
            name="prior_monthly.FULL.monthly.qcow2",
            path=Path("/backup/prior_monthly.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 20),  # July
            bucket_level="monthly",
        ),
        FullBackupInfo(
            name="prior_weekly.FULL.weekly.qcow2",
            path=Path("/backup/prior_weekly.FULL.weekly.qcow2"),
            timestamp=datetime(2025, 8, 11, 8, 0),  # Monday W33, same week
            bucket_level="weekly",
        ),
    ]

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        all_fulls,
        snapshot_ts,
    )
    assert should is True, "Monthly (higher F-anchor) period changed"
    assert level == "monthly", "Monthly checked before weekly in descending F-anchor order"


# ── Scenario 9: descending iteration order — yearly triumphs ────────────


def test_all_buckets_checked_yearly_monthly_weekly_daily_hourly(make_target):
    """Descending order: yearly → monthly → weekly → daily → hourly.

    Only yearly period changed; lower buckets all same period.
    Yearly triggers immediately.
    """
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

    # Yearly changed (2024 → 2025). All others same period.
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
            timestamp=datetime(2025, 7, 14, 10, 0),  # W29
            bucket_level="weekly",
        ),
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

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        all_fulls,
        snapshot_ts,
    )
    assert should is True, "Yearly period changed — should trigger"
    assert level == "yearly", "Yearly is highest bucket — triggered first"


# ── Scenario 10: F-anchor daily only ignores monthly/weekly ────────────


def test_f_anchor_daily_only_ignores_other_buckets(make_target):
    """Only daily is F-anchored — monthly/weekly are ignored even if active.

    Policy: anchor_daily=True, daily=7, monthly=12, weekly=4.
    Monthly and weekly are active but not F-marked.
    Monthly period changes but only daily is checked — same daily period → skip.
    """
    policy = RetentionPolicy(
        daily=7,
        monthly=12,
        weekly=4,
        preserve_min="all",
        anchor_daily=True,
    )
    target = make_target()
    snapshot_ts = datetime(2025, 8, 15, 10, 0)

    # Monthly FULL was in July (different month from August snapshot).
    # Daily FULL same day as snapshot.
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

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        all_fulls,
        snapshot_ts,
    )
    assert should is False, (
        "F-anchor mode: only daily checked.  Monthly period changed but is NOT F-marked → ignored."
    )
    assert level == ""


# ── Scenario 11: no active buckets and no F-anchors → False ─────────────


def test_no_active_buckets_no_f_anchors_returns_false(make_target):
    """Policy with all counts zero and no F-anchors returns (False, "")."""
    policy = RetentionPolicy()  # all zeros, no F-anchors
    target = make_target()
    snapshot_ts = datetime(2025, 7, 13, 10, 0)

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        None,
        snapshot_ts,
    )
    assert should is False
    assert level == ""


# ── Scenario 12: first backup with empty list creates first active bucket FULL ─


def test_first_backup_empty_fulls_list_creates_first_active_bucket_full(make_target):
    """Empty list → first active bucket in descending order triggers."""
    policy = RetentionPolicy(yearly=1, monthly=12, preserve_min="all")
    target = make_target()
    snapshot_ts = datetime(2025, 7, 13, 10, 0)

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        [],
        snapshot_ts,
    )
    assert should is True, "Empty list — no prior FULLs, first active bucket triggers"
    assert level == "yearly", "Yearly is the first (highest) active bucket"


# ── Scenario 13: accepts list, None, and single FullBackupInfo ──────────


def test_should_create_bucket_full_accepts_list_not_single(make_target):
    """Parameter accepts list[FullBackupInfo], single FullBackupInfo, and None.

    Backward-compat: None → treated as [], single FullBackupInfo → wrapped in list.
    """
    policy = RetentionPolicy(monthly=12, preserve_min="all")
    target = make_target()
    snapshot_ts = datetime(2025, 8, 1, 0, 0)

    single_full = FullBackupInfo(
        name="single.FULL.monthly.qcow2",
        path=Path("/backup/single.FULL.monthly.qcow2"),
        timestamp=datetime(2025, 7, 15),
        bucket_level="monthly",
    )

    # (a) List[FullBackupInfo] — should work.
    should, level = Core._should_create_bucket_full(
        target,
        policy,
        [single_full],
        snapshot_ts,
    )
    assert should is True, "List param: monthly period changed"
    assert level == "monthly"

    # (b) Single FullBackupInfo (backward compat) — should work.
    should2, level2 = Core._should_create_bucket_full(
        target,
        policy,
        single_full,
        snapshot_ts,
    )
    assert should2 is True, "Single FullBackupInfo param: monthly period changed"
    assert level2 == "monthly"

    # (c) None (backward compat) — should work, treated as empty list.
    should3, level3 = Core._should_create_bucket_full(
        target,
        policy,
        None,
        snapshot_ts,
    )
    assert should3 is True, "None param: treated as [], first active bucket triggers"
    assert level3 == "monthly", "Monthly is the only active bucket"

    # (d) Verify no TypeError raised for any form.
    assert isinstance(should, bool)
    assert isinstance(level, str)
    assert isinstance(should2, bool)
    assert isinstance(level2, str)
    assert isinstance(should3, bool)
    assert isinstance(level3, str)


# ── Scenario 14: descending iteration — yearly skipped, monthly triggers ─


def test_should_create_bucket_full_iterates_descending_yearly_first(make_target):
    """Descending order: yearly skipped (same period), monthly triggers.

    Policy with all 5 buckets active.  Prior FULLs: yearly same year,
    monthly old month (triggers), weekly old week, daily/houly old.
    Yearly does NOT trigger → iteration continues to monthly → triggers.
    """
    policy = RetentionPolicy(
        yearly=1,
        monthly=12,
        weekly=4,
        daily=7,
        hourly=24,
        preserve_min="all",
    )
    target = make_target()
    snapshot_ts = datetime(2025, 8, 4, 14, 30)  # Monday, ISO week 32

    # Yearly same year → skip. Monthly old period → trigger.
    all_fulls = [
        FullBackupInfo(
            name="same_yearly.FULL.yearly.qcow2",
            path=Path("/backup/same_yearly.FULL.yearly.qcow2"),
            timestamp=datetime(2025, 1, 1),  # same year as snapshot
            bucket_level="yearly",
        ),
        FullBackupInfo(
            name="old_monthly.FULL.monthly.qcow2",
            path=Path("/backup/old_monthly.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 15),  # July — different from August
            bucket_level="monthly",
        ),
        FullBackupInfo(
            name="old_weekly.FULL.weekly.qcow2",
            path=Path("/backup/old_weekly.FULL.weekly.qcow2"),
            timestamp=datetime(2025, 7, 28),  # W31 — different from W32
            bucket_level="weekly",
        ),
    ]

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        all_fulls,
        snapshot_ts,
    )
    assert should is True, "Yearly skipped (same period), monthly triggers"
    assert level == "monthly", "Monthly is next in descending order and changed period"


# ── Scenario 15: F-anchor bucket with no prior FULL creates FULL ────────


def test_f_anchor_bucket_no_prior_full_creates_full(make_target):
    """F-anchor bucket has no prior FULL → triggers creation.

    Policy: anchor_weekly=True, weekly=4, monthly=12, anchor_monthly=False.
    Only weekly is F-anchored.  No weekly FULL exists → should create one.
    """
    policy = RetentionPolicy(
        weekly=4,
        monthly=12,
        preserve_min="all",
        anchor_weekly=True,
        anchor_monthly=False,
    )
    target = make_target()
    snapshot_ts = datetime(2025, 7, 14, 10, 0)  # Monday, W29

    # Only monthly FULL exists, no weekly FULL.
    all_fulls = [
        FullBackupInfo(
            name="only_monthly.FULL.monthly.qcow2",
            path=Path("/backup/only_monthly.FULL.monthly.qcow2"),
            timestamp=datetime(2025, 7, 1),
            bucket_level="monthly",
        ),
    ]

    should, level = Core._should_create_bucket_full(
        target,
        policy,
        all_fulls,
        snapshot_ts,
    )
    assert should is True, "F-anchor weekly has no prior FULL — should create one"
    assert level == "weekly", "Weekly is the only F-anchored bucket with no prior FULL"
