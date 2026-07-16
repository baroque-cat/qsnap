"""Tests for the TimeBasedRetention engine.

These tests exercise the btrbk-style time-based retention algorithm using
fixed timestamp fixtures (no I/O, no side effects).  The engine is a pure
function: given the same inputs it always returns the same output.

Fixture data lives under ``tests/fixtures/timestamps/``:

* ``hourly_set.json`` — 48 items, one per hour, spanning 2025-01-01T00:00:00
  to 2025-01-02T23:00:00.
* ``daily_set.json`` — 28 items, two per day (00:00 and 12:00), spanning 14
  days from 2025-01-01 to 2025-01-14.
* ``mixed_set.json`` — 19 items with irregular intervals over 7 days.

Algorithm summary (see ``qsnap/retention/time_based.py``):

1. ``preserve_min`` keeps *every* item whose timestamp falls within
   ``[now - preserve_min, now]``.  ``preserve_min="all"`` keeps everything.
2. Each time bucket (hourly/daily/weekly/monthly/yearly) groups items by a
   calendar key, keeps the *earliest* item per group, then selects the
   ``count`` most-recent groups.
3. The union of all kept names forms ``keep``; the remainder forms
   ``remove``.  Both lists are ordered oldest-first.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from qsnap.models.config import RetentionPolicy
from qsnap.models.results import RetentionItem, RetentionResult
from qsnap.retention.time_based import TimeBasedRetention

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "timestamps"


def _load_fixture(name: str) -> list[RetentionItem]:
    """Load a timestamp fixture JSON file into a list of RetentionItem.

    The fixture files contain a list of ``{"name": str, "timestamp": str}``
    objects where ``timestamp`` is an ISO-8601 datetime string.
    """
    path = FIXTURES_DIR / name
    with path.open() as fh:
        raw = json.load(fh)
    return [
        RetentionItem(name=item["name"], timestamp=datetime.fromisoformat(item["timestamp"]))
        for item in raw
    ]


# ──────────────────────────────────────────────────────────────────────────
# 1. Hourly retention
# ──────────────────────────────────────────────────────────────────────────


def test_hourly_retention_24h():
    """Hourly retention with hourly=24 keeps exactly the 24 most-recent hour buckets.

    The hourly bucket key is ``(year, month, day, hour)``; because the fixture
    has one item per hour, each item lands in its own bucket.  The engine
    selects the 24 most-recent buckets (each contributing its earliest — and
    only — item), yielding 24 kept and 24 removed.

    ``preserve_min="0h"`` restricts the preserve window to ``[now, now]`` so
    that only the single item at ``now`` is force-kept — it is already
    captured by the hourly bucket, so the totals remain 24/24.
    """
    items = _load_fixture("hourly_set.json")
    assert len(items) == 48

    now = items[-1].timestamp  # 2025-01-02T23:00:00
    policy = RetentionPolicy(
        hourly=24,
        daily=0,
        weekly=0,
        monthly=0,
        yearly=0,
        preserve_min="0h",
    )

    result = TimeBasedRetention(policy).evaluate(items, policy, now=now)

    assert len(result.keep) == 24
    assert len(result.remove) == 24

    # The kept items are the 24 most-recent hours: snap.024 .. snap.047.
    expected_keep = {f"snap.{i:03d}" for i in range(24, 48)}
    expected_remove = {f"snap.{i:03d}" for i in range(0, 24)}
    assert set(result.keep) == expected_keep
    assert set(result.remove) == expected_remove

    # keep/remove are ordered oldest-first.
    assert result.keep[0] == "snap.024"
    assert result.keep[-1] == "snap.047"
    assert result.remove[0] == "snap.000"
    assert result.remove[-1] == "snap.023"

    # No item appears in both lists.
    assert not (set(result.keep) & set(result.remove))


# ──────────────────────────────────────────────────────────────────────────
# 2. preserve_min keeps recent items
# ──────────────────────────────────────────────────────────────────────────


def test_preserve_min_keeps_recent():
    """preserve_min="24h" force-keeps every item within the last 24 hours.

    With all bucket counts at 0, only preserve_min applies.  ``now`` is the
    last item's timestamp (2025-01-02T23:00:00); the 24h window starts at
    2025-01-01T23:00:00.  The boundary item snap.023 (exactly at the
    threshold) is included because the comparison is ``>=``.
    """
    items = _load_fixture("hourly_set.json")
    now = items[-1].timestamp  # 2025-01-02T23:00:00
    policy = RetentionPolicy(preserve_min="24h")

    result = TimeBasedRetention(policy).evaluate(items, policy, now=now)

    threshold = now - timedelta(hours=24)
    expected_recent = {it.name for it in items if it.timestamp >= threshold}
    expected_old = {it.name for it in items if it.timestamp < threshold}

    # Every item inside the 24h window is kept.
    assert expected_recent.issubset(set(result.keep))
    # No item older than the window is kept (no bucket retention to rescue it).
    assert not (expected_old & set(result.keep))
    # Every old item is removed.
    assert expected_old == set(result.remove)

    # Boundary item at exactly the threshold is kept (>= comparison).
    assert "snap.023" in result.keep
    # Item one hour before the threshold is removed.
    assert "snap.022" in result.remove


# ──────────────────────────────────────────────────────────────────────────
# 3. Daily retention — first snapshot per day
# ──────────────────────────────────────────────────────────────────────────


def test_daily_retention_first_per_day():
    """Daily retention keeps the earliest snapshot of each of the 7 most-recent days.

    The daily bucket key is ``(year, month, day)``.  Items are sorted
    ascending, so the first item encountered per day-bucket is the earliest
    (00:00) snapshot.  With ``daily=7`` the engine selects the 7 most-recent
    day-buckets (Jan 8–14), each contributing its 00:00 snapshot.
    ``preserve_min="0h"`` additionally force-keeps the item exactly at
    ``now`` (snap.d13h12), which is on Jan 14 — a day already represented.
    """
    items = _load_fixture("daily_set.json")
    assert len(items) == 28

    now = items[-1].timestamp  # 2025-01-14T12:00:00
    policy = RetentionPolicy(
        hourly=0,
        daily=7,
        weekly=0,
        monthly=0,
        yearly=0,
        preserve_min="0h",
    )

    result = TimeBasedRetention(policy).evaluate(items, policy, now=now)

    kept = set(result.keep)

    # The 7 most-recent days are Jan 8 .. Jan 14.  For each, the earliest
    # snapshot (the 00:00 one) must be in the keep list.
    expected_first_per_day = [
        "snap.d07h00",  # Jan 8
        "snap.d08h00",  # Jan 9
        "snap.d09h00",  # Jan 10
        "snap.d10h00",  # Jan 11
        "snap.d11h00",  # Jan 12
        "snap.d12h00",  # Jan 13
        "snap.d13h00",  # Jan 14
    ]
    for name in expected_first_per_day:
        assert name in kept, f"{name} (earliest of its day) should be kept"

    # The 12:00 snapshots for the kept days are NOT kept by the daily bucket
    # (only the earliest per day is) and are outside the preserve_min window,
    # so they must be removed.
    for name in [
        "snap.d07h12",
        "snap.d08h12",
        "snap.d09h12",
        "snap.d10h12",
        "snap.d11h12",
        "snap.d12h12",
    ]:
        assert name in set(result.remove), f"{name} should be removed"

    # Days outside the 7-day window (Jan 1 .. Jan 7) have their snapshots
    # removed (they are older than the preserve_min window of 0h).
    for name in ["snap.d00h00", "snap.d01h00", "snap.d06h00"]:
        assert name in set(result.remove)

    # The item exactly at `now` (snap.d13h12) is force-kept by preserve_min.
    assert "snap.d13h12" in kept

    # No item is in both keep and remove.
    assert not (set(result.keep) & set(result.remove))

    # keep is ordered oldest-first.
    assert result.keep[0] == "snap.d07h00"
    assert result.keep[-1] == "snap.d13h12"


# ──────────────────────────────────────────────────────────────────────────
# 4. preserve_min="all" keeps everything
# ──────────────────────────────────────────────────────────────────────────


def test_preserve_min_all_keeps_everything():
    """preserve_min="all" keeps every item; remove is empty.

    RetentionPolicy() defaults all bucket counts to 0 and preserve_min to
    "all", so the engine short-circuits to keeping everything.
    """
    items = _load_fixture("hourly_set.json")
    now = items[-1].timestamp

    policy = RetentionPolicy()  # defaults: all counts 0, preserve_min="all"
    assert policy.preserve_min == "all"

    result = TimeBasedRetention(policy).evaluate(items, policy, now=now)

    assert len(result.keep) == len(items)
    assert result.remove == []
    # keep preserves the input order (oldest-first after sorting).
    assert result.keep == [it.name for it in sorted(items, key=lambda i: i.timestamp)]


# ──────────────────────────────────────────────────────────────────────────
# 5. Determinism + boundary conditions
# ──────────────────────────────────────────────────────────────────────────


def test_evaluate_is_deterministic():
    """evaluate() is a pure function: identical inputs yield identical outputs.

    Also covers the boundary conditions from the test plan (line 136):
    * empty item list → empty keep and remove;
    * single item → always kept;
    * items exactly at midnight (00:00) are assigned to the correct day bucket.
    """
    items = _load_fixture("mixed_set.json")
    now = items[-1].timestamp  # 2025-01-08T00:00:00

    policy = RetentionPolicy(
        hourly=6,
        daily=3,
        weekly=0,
        monthly=0,
        yearly=0,
        preserve_min="0h",
    )
    engine = TimeBasedRetention(policy)

    result1 = engine.evaluate(items, policy, now=now)
    result2 = engine.evaluate(items, policy, now=now)

    # Deterministic: two identical calls return identical lists.
    assert result1.keep == result2.keep
    assert result1.remove == result2.remove
    assert result1 == result2

    # ── Boundary: empty item list ───────────────────────────────────────
    empty_result = engine.evaluate([], policy, now=now)
    assert empty_result.keep == []
    assert empty_result.remove == []

    # ── Boundary: single item is always kept ────────────────────────────
    single = [RetentionItem(name="only-snap", timestamp=now)]
    single_result = engine.evaluate(single, policy, now=now)
    assert single_result.keep == ["only-snap"]
    assert single_result.remove == []

    # A single old item (well before `now`) is still kept by the hourly
    # bucket (it is the only/most-recent bucket).
    old_single = [RetentionItem(name="old-snap", timestamp=now - timedelta(days=10))]
    old_result = engine.evaluate(old_single, policy, now=now)
    assert old_result.keep == ["old-snap"]
    assert old_result.remove == []

    # ── Boundary: midnight items land in the correct day bucket ────────
    # Two items at exactly 00:00 on consecutive days, plus one late-night
    # item on the first day.  With daily=2 the two midnight items are kept
    # (each is the earliest of its own day-bucket), proving they were
    # assigned to distinct day buckets.
    midnight_items = [
        RetentionItem(name="midnight-d1", timestamp=datetime(2025, 1, 1, 0, 0, 0)),
        RetentionItem(name="late-d1", timestamp=datetime(2025, 1, 1, 23, 59, 59)),
        RetentionItem(name="midnight-d2", timestamp=datetime(2025, 1, 2, 0, 0, 0)),
    ]
    midnight_policy = RetentionPolicy(
        hourly=0,
        daily=2,
        weekly=0,
        monthly=0,
        yearly=0,
        preserve_min="0h",
    )
    midnight_now = datetime(2025, 1, 2, 0, 0, 0)
    midnight_result = TimeBasedRetention(midnight_policy).evaluate(
        midnight_items, midnight_policy, now=midnight_now
    )
    kept_midnight = set(midnight_result.keep)
    # Both midnight snapshots are kept (distinct day buckets).
    assert "midnight-d1" in kept_midnight
    assert "midnight-d2" in kept_midnight
    # The late-night item shares day-1's bucket but is not the earliest, so
    # it is removed (and is outside the 0h preserve window).
    assert "late-d1" in set(midnight_result.remove)


# ──────────────────────────────────────────────────────────────────────────
# 6. preserve_day_of_week — weekly bucket boundary
# ──────────────────────────────────────────────────────────────────────────


def test_weekly_retention_tuesday_boundary_keeps_four():
    """Tuesday boundary splits Monday items into distinct week buckets.

    Five items, one per week on Mondays at noon.  With
    ``preserve_day_of_week="tuesday"`` each Monday falls into a different
    week bucket (Monday is before Tuesday, so the week-start is pushed back
    6 days).  With ``weekly=4`` the 4 most-recent buckets are kept and the
    oldest is removed.
    """
    items = [
        RetentionItem(name=f"week{i}", timestamp=datetime(2025, 1, 6, 12, 0) + timedelta(weeks=i))
        for i in range(5)
    ]
    policy = RetentionPolicy(weekly=4, preserve_min="0h")
    result = TimeBasedRetention(policy).evaluate(
        items, policy, now=items[-1].timestamp, preserve_day_of_week="tuesday"
    )
    assert len(result.keep) == 4
    assert len(result.remove) == 1
    assert "week0" in result.remove


def test_weekly_retention_default_monday_boundary_keeps_two():
    """Default Monday boundary groups same-ISO-week items together.

    w1_mon and w1_sun are in the same Monday-anchored week bucket, while
    w2_mon and w3_mon each start their own week.  With ``weekly=2`` the two
    most-recent week buckets (week3 and week2) are selected, so only
    w2_mon and w3_mon are kept.
    """
    items = [
        RetentionItem(name="w1_mon", timestamp=datetime(2025, 1, 6, 12, 0)),
        RetentionItem(name="w1_sun", timestamp=datetime(2025, 1, 12, 12, 0)),
        RetentionItem(name="w2_mon", timestamp=datetime(2025, 1, 13, 12, 0)),
        RetentionItem(name="w3_mon", timestamp=datetime(2025, 1, 20, 12, 0)),
    ]
    policy = RetentionPolicy(weekly=2, preserve_min="0h")
    result = TimeBasedRetention(policy).evaluate(
        items, policy, now=items[-1].timestamp
    )
    assert len(result.keep) == 2
    assert "w2_mon" in result.keep
    assert "w3_mon" in result.keep


def test_preserve_day_of_week_sunday_boundary_keeps_two():
    """Sunday boundary groups Sunday and following Monday together.

    With a Sunday week boundary, sun1 (Jan 5) and mon1 (Jan 6) land in the
    same week bucket (Jan 5), while sat1 (Jan 4) falls into the Dec 29
    bucket.  The four week buckets are: Dec 29, Jan 5, Jan 12, Jan 19.
    With ``weekly=2`` the two most-recent (Jan 12, Jan 19) are kept, so
    sun2 and sun3 are kept while sat1, sun1, and mon1 are removed.
    """
    items = [
        RetentionItem(name="sat1", timestamp=datetime(2025, 1, 4, 12, 0)),
        RetentionItem(name="sun1", timestamp=datetime(2025, 1, 5, 12, 0)),
        RetentionItem(name="mon1", timestamp=datetime(2025, 1, 6, 12, 0)),
        RetentionItem(name="sun2", timestamp=datetime(2025, 1, 12, 12, 0)),
        RetentionItem(name="sun3", timestamp=datetime(2025, 1, 19, 12, 0)),
    ]
    policy = RetentionPolicy(weekly=2, preserve_min="0h")
    result = TimeBasedRetention(policy).evaluate(
        items, policy, now=items[-1].timestamp, preserve_day_of_week="sunday"
    )
    assert "sun2" in result.keep
    assert "sun3" in result.keep
    assert "sun1" in result.remove
    assert "mon1" in result.remove


def test_preserve_day_of_week_case_insensitive():
    """Uppercase preserve_day_of_week produces the same result as lowercase."""
    items = [
        RetentionItem(name=f"week{i}", timestamp=datetime(2025, 1, 6, 12, 0) + timedelta(weeks=i))
        for i in range(5)
    ]
    policy = RetentionPolicy(weekly=4, preserve_min="0h")
    now = items[-1].timestamp

    r_lower = TimeBasedRetention(policy).evaluate(
        items, policy, now=now, preserve_day_of_week="tuesday"
    )
    r_upper = TimeBasedRetention(policy).evaluate(
        items, policy, now=now, preserve_day_of_week="TUESDAY"
    )
    assert r_lower.keep == r_upper.keep
    assert r_lower.remove == r_upper.remove


def test_preserve_day_of_week_does_not_affect_other_buckets():
    """When weekly=0, preserve_day_of_week has no effect on results."""
    items = _load_fixture("daily_set.json")
    now = items[-1].timestamp
    policy = RetentionPolicy(hourly=24, daily=7, weekly=0, preserve_min="0h")

    r1 = TimeBasedRetention(policy).evaluate(
        items, policy, now=now, preserve_day_of_week="wednesday"
    )
    r2 = TimeBasedRetention(policy).evaluate(
        items, policy, now=now, preserve_day_of_week="monday"
    )
    assert r1.keep == r2.keep
    assert r1.remove == r2.remove


# ──────────────────────────────────────────────────────────────────────────
# 7. preserve_min="latest" — keep only the most recent item
# ──────────────────────────────────────────────────────────────────────────


def test_preserve_min_latest_keeps_only_most_recent():
    """preserve_min="latest" keeps only the single most-recent item.

    ``_parse_duration("latest")`` returns ``timedelta(0)`` (a zero-width
    window), and the ``preserve_min == "latest"`` branch in ``evaluate()``
    force-keeps only the most-recent item (the last after ascending sort).
    With all bucket counts at 0, no additional items are rescued by the
    time-bucket retention, so exactly 1 item is kept and the remaining 9
    are removed.
    """
    items = [
        RetentionItem(name=f"snap.{i:02d}", timestamp=datetime(2025, 1, 1, 0, 0) + timedelta(hours=i))
        for i in range(10)
    ]
    now = items[-1].timestamp  # 2025-01-01T09:00:00

    policy = RetentionPolicy(
        hourly=0,
        daily=0,
        weekly=0,
        monthly=0,
        yearly=0,
        preserve_min="latest",
    )

    result = TimeBasedRetention(policy).evaluate(items, policy, now=now)

    # Exactly one item is kept: the most recent (snap.09).
    assert len(result.keep) == 1
    assert result.keep == ["snap.09"]

    # The other 9 items are removed.
    assert len(result.remove) == 9
    assert set(result.remove) == {f"snap.{i:02d}" for i in range(9)}

    # No item appears in both lists.
    assert not (set(result.keep) & set(result.remove))


# ──────────────────────────────────────────────────────────────────────────
# 8. explain() — structured per-bucket breakdown
# ──────────────────────────────────────────────────────────────────────────


def test_explain_returns_per_bucket_counts():
    """``explain()`` returns a dict keyed by bucket name, where each value
    is a dict with ``"count"`` (int) and, when count > 0, ``"range"``
    (a 2-tuple of datetimes: earliest and latest kept timestamps).
    """
    items = _load_fixture("daily_set.json")
    assert len(items) == 28

    now = items[-1].timestamp  # 2025-01-14T12:00:00
    policy = RetentionPolicy(
        hourly=0,
        daily=7,
        weekly=0,
        monthly=0,
        yearly=0,
        preserve_min="0h",
    )

    engine = TimeBasedRetention(policy)
    explanation = engine.explain(items, policy, now=now)

    # Returns a dict
    assert isinstance(explanation, dict)

    # All expected bucket names are present as keys
    expected_keys = {"preserve_min", "hourly", "daily", "weekly", "monthly", "yearly"}
    assert expected_keys.issubset(set(explanation.keys()))

    # Every value is a dict containing an integer "count"
    for bucket_name, bucket_info in explanation.items():
        assert isinstance(bucket_info, dict), f"{bucket_name} value is not a dict"
        assert "count" in bucket_info, f"{bucket_name} missing 'count' key"
        assert isinstance(bucket_info["count"], int), (
            f"{bucket_name} count is not an int"
        )

    # daily=7 → 7 kept items with a range tuple
    assert explanation["daily"]["count"] == 7
    assert "range" in explanation["daily"]
    daily_range = explanation["daily"]["range"]
    assert isinstance(daily_range, tuple)
    assert len(daily_range) == 2
    start, end = daily_range
    assert isinstance(start, datetime)
    assert isinstance(end, datetime)
    assert start <= end

    # preserve_min="0h" keeps exactly 1 item (the one at `now`)
    assert explanation["preserve_min"]["count"] == 1
    assert "range" in explanation["preserve_min"]

    # hourly=0 → count 0, no range key
    assert explanation["hourly"]["count"] == 0
    assert "range" not in explanation["hourly"]

    # weekly=0 → count 0, no range key
    assert explanation["weekly"]["count"] == 0
    assert "range" not in explanation["weekly"]


def test_explain_is_pure_function():
    """Calling ``explain()`` twice with identical inputs returns identical
    results — the engine is deterministic (no I/O, no side effects).
    """
    items = _load_fixture("mixed_set.json")
    now = items[-1].timestamp  # 2025-01-08T00:00:00

    policy = RetentionPolicy(
        hourly=6,
        daily=3,
        weekly=0,
        monthly=0,
        yearly=0,
        preserve_min="0h",
    )

    engine = TimeBasedRetention(policy)
    result1 = engine.explain(items, policy, now=now)
    result2 = engine.explain(items, policy, now=now)

    # Top-level dict equality
    assert result1 == result2

    # Deep equality on every bucket: counts and ranges match
    for key in result1:
        assert result1[key]["count"] == result2[key]["count"], (
            f"Bucket '{key}' count differs between calls"
        )
        if "range" in result1[key]:
            assert "range" in result2[key]
            assert result1[key]["range"] == result2[key]["range"], (
                f"Bucket '{key}' range differs between calls"
            )

    # Also verify with preserve_day_of_week for good measure
    result3 = engine.explain(
        items, policy, now=now, preserve_day_of_week="wednesday"
    )
    result4 = engine.explain(
        items, policy, now=now, preserve_day_of_week="wednesday"
    )
    assert result3 == result4


# ──────────────────────────────────────────────────────────────────────────
# 9. Contract test — pure function returning keep/remove
# ──────────────────────────────────────────────────────────────────────────


def test_retention_engine_returns_pure_keep_remove():
    """Contract test: the retention engine returns a ``RetentionResult`` with
    ``keep`` and ``remove`` lists of names, and is a pure function with no
    I/O, no side effects, and no awareness of backup types or dependencies.

    Design D1: dependency-aware cascade deletion is handled by Core after
    post-processing the retention engine's output.  The engine itself is a
    pure function — it evaluates timestamps against a ``RetentionPolicy``
    and returns which items to keep and remove.  It does NOT understand
    FULL vs incremental backups, nor does it track dependencies.
    """
    items = [
        RetentionItem(name="snap-a", timestamp=datetime(2025, 6, 1, 12, 0)),
        RetentionItem(name="snap-b", timestamp=datetime(2025, 6, 1, 13, 0)),
        RetentionItem(name="snap-c", timestamp=datetime(2025, 6, 1, 14, 0)),
    ]
    now = datetime(2025, 6, 1, 14, 0)
    policy = RetentionPolicy(
        hourly=1, daily=0, weekly=0, monthly=0, yearly=0, preserve_min="0h",
    )
    engine = TimeBasedRetention(policy)

    # ── Contract: returns RetentionResult ──────────────────────────────
    result = engine.evaluate(items, policy, now=now)
    assert isinstance(result, RetentionResult)

    # ── Contract: keep and remove are list[str] ─────────────────────────
    assert isinstance(result.keep, list)
    assert isinstance(result.remove, list)
    assert all(isinstance(name, str) for name in result.keep)
    assert all(isinstance(name, str) for name in result.remove)

    # Every item name appears in exactly one of keep or remove.
    all_names = {it.name for it in items}
    assert all_names == (set(result.keep) | set(result.remove))
    assert not (set(result.keep) & set(result.remove))

    # ── Contract: pure function — deterministic, no side effects ───────
    result2 = engine.evaluate(items, policy, now=now)
    assert result.keep == result2.keep
    assert result.remove == result2.remove
    assert result == result2

    # Calling evaluate() repeatedly on the same engine instance produces
    # identical results (no accumulated internal state / no mutation).
    result3 = engine.evaluate(items, policy, now=now)
    assert result == result3

    # ── Contract: unaware of backup types or dependency metadata ───────
    # The engine evaluates items purely by timestamp.  Items named "FULL"
    # or "INCR" receive no special treatment — the engine does not
    # differentiate by name content.
    typed_items = [
        RetentionItem(name="FULL-backup", timestamp=datetime(2025, 6, 1, 13, 0)),
        RetentionItem(name="INCR-backup", timestamp=datetime(2025, 6, 1, 13, 0)),
    ]
    typed_now = datetime(2025, 6, 1, 14, 0)
    # All bucket counts at 0, preserve_min="0h": with both items at 13:00
    # and now at 14:00, the preserve window threshold is 14:00.  Neither
    # item falls within it, and no buckets can rescue them, so both are
    # removed — no special treatment for "FULL" naming.
    typed_policy = RetentionPolicy(preserve_min="0h")
    typed_result = TimeBasedRetention(typed_policy).evaluate(
        typed_items, typed_policy, now=typed_now
    )
    assert len(typed_result.remove) == 2
    assert "FULL-backup" in typed_result.remove
    assert "INCR-backup" in typed_result.remove
    assert typed_result.keep == []

    # When preserve_min="all", both are kept — again, no name-based logic.
    all_policy = RetentionPolicy(preserve_min="all")
    all_result = TimeBasedRetention(all_policy).evaluate(
        typed_items, all_policy, now=typed_now
    )
    assert set(all_result.keep) == {"FULL-backup", "INCR-backup"}
    assert all_result.remove == []
