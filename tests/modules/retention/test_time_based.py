"""Tests for the TimeBasedRetention engine — count-based retention.

The count-based engine is a pure function: it sorts items by timestamp
ascending, keeps the newest N, and removes the rest.  N is determined by
``policy.chain_length`` when positive, otherwise ``policy.keep_generations``.
No I/O, no Core inheritance, no side effects.

``evaluate()`` returns a ``RetentionResult`` with ``keep`` and ``remove``
name lists.  ``explain()`` returns ``dict[str, int]`` with ``keep_count``
and ``remove_count``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from qsnap.models.config import RetentionPolicy
from qsnap.models.results import RetentionItem, RetentionResult
from qsnap.retention.time_based import TimeBasedRetention

# ── helpers ──────────────────────────────────────────────────────────────


def _mk_items(n: int, *, base_hour: int = 0) -> list[RetentionItem]:
    """Create *n* ``RetentionItem`` objects, one per hour starting at *base_hour*."""
    base = datetime(2025, 1, 1)
    return [
        RetentionItem(name=f"snap.{i:03d}", timestamp=base + timedelta(hours=base_hour + i))
        for i in range(n)
    ]


# ──────────────────────────────────────────────────────────────────────────
# 1. chain_length triggers removal
# ──────────────────────────────────────────────────────────────────────────


def test_snapshot_chain_length_triggers_removal():
    """Policy with chain_length=5, 10 items → keeps newest 5, removes 5."""
    items = _mk_items(10)
    policy = RetentionPolicy(chain_length=5, keep_generations=1)
    engine = TimeBasedRetention(policy)
    now = items[-1].timestamp

    result = engine.evaluate(items, policy, now=now)

    assert isinstance(result, RetentionResult)
    assert len(result.keep) == 5
    assert len(result.remove) == 5

    # Kept items are the 5 newest: snap.005 .. snap.009
    assert set(result.keep) == {f"snap.{i:03d}" for i in range(5, 10)}
    assert set(result.remove) == {f"snap.{i:03d}" for i in range(5)}

    # keep/remove are ordered oldest-first (ascending sort).
    assert result.keep == [f"snap.{i:03d}" for i in range(5, 10)]
    assert result.remove == [f"snap.{i:03d}" for i in range(5)]

    # No item appears in both lists.
    assert not (set(result.keep) & set(result.remove))


# ──────────────────────────────────────────────────────────────────────────
# 2. item count within chain_length keeps all
# ──────────────────────────────────────────────────────────────────────────


def test_snapshot_count_within_chain_length_keeps_all():
    """Policy with chain_length=10, 5 items → keeps all 5, removes 0."""
    items = _mk_items(5)
    policy = RetentionPolicy(chain_length=10, keep_generations=1)
    engine = TimeBasedRetention(policy)
    now = items[-1].timestamp

    result = engine.evaluate(items, policy, now=now)

    assert len(result.keep) == 5
    assert result.remove == []
    assert set(result.keep) == {f"snap.{i:03d}" for i in range(5)}

    # All items kept, ordered oldest-first.
    assert result.keep == [f"snap.{i:03d}" for i in range(5)]


# ──────────────────────────────────────────────────────────────────────────
# 3. keep_generations limits chains (chain_length=0 fallback)
# ──────────────────────────────────────────────────────────────────────────


def test_keep_generations_limits_chains():
    """Policy with chain_length=0, keep_generations=2, 5 items → keeps newest 2."""
    items = _mk_items(5)
    policy = RetentionPolicy(chain_length=0, keep_generations=2)
    engine = TimeBasedRetention(policy)
    now = items[-1].timestamp

    result = engine.evaluate(items, policy, now=now)

    assert len(result.keep) == 2
    assert len(result.remove) == 3
    assert set(result.keep) == {"snap.003", "snap.004"}
    assert set(result.remove) == {"snap.000", "snap.001", "snap.002"}


# ──────────────────────────────────────────────────────────────────────────
# 4. general keep-newest-N
# ──────────────────────────────────────────────────────────────────────────


def test_keep_newest_n_items():
    """N items, chain_length=N/2 → verify newest N/2 are kept."""
    n = 12
    half = n // 2  # 6
    items = _mk_items(n)
    policy = RetentionPolicy(chain_length=half, keep_generations=1)
    engine = TimeBasedRetention(policy)
    now = items[-1].timestamp

    result = engine.evaluate(items, policy, now=now)

    assert len(result.keep) == half
    assert len(result.remove) == half

    # Kept: last half of items (ascending sort → newest 6).
    expected_keep = [f"snap.{i:03d}" for i in range(half, n)]
    assert result.keep == expected_keep
    expected_remove = [f"snap.{i:03d}" for i in range(half)]
    assert result.remove == expected_remove


# ──────────────────────────────────────────────────────────────────────────
# 5. all items within chain_length
# ──────────────────────────────────────────────────────────────────────────


def test_all_items_within_chain_length():
    """chain_length > len(items) → all items kept."""
    items = _mk_items(3)
    policy = RetentionPolicy(chain_length=100, keep_generations=1)
    engine = TimeBasedRetention(policy)
    now = items[-1].timestamp

    result = engine.evaluate(items, policy, now=now)

    assert len(result.keep) == 3
    assert result.remove == []
    assert set(result.keep) == {"snap.000", "snap.001", "snap.002"}


# ──────────────────────────────────────────────────────────────────────────
# 6. empty item list
# ──────────────────────────────────────────────────────────────────────────


def test_empty_item_list_returns_empty():
    """Empty list → both keep and remove are empty."""
    policy = RetentionPolicy(chain_length=5, keep_generations=1)
    engine = TimeBasedRetention(policy)

    result = engine.evaluate([], policy, now=datetime(2025, 1, 1))

    assert isinstance(result, RetentionResult)
    assert result.keep == []
    assert result.remove == []


# ──────────────────────────────────────────────────────────────────────────
# 7. chain_length=0 and keep_generations=0 removes all
# ──────────────────────────────────────────────────────────────────────────


def test_chain_length_zero_removes_all():
    """chain_length=0, keep_generations=0 → removes all items, keep empty."""
    items = _mk_items(5)
    policy = RetentionPolicy(chain_length=0, keep_generations=0)
    engine = TimeBasedRetention(policy)
    now = items[-1].timestamp

    result = engine.evaluate(items, policy, now=now)

    assert result.keep == []
    assert len(result.remove) == 5
    assert set(result.remove) == {f"snap.{i:03d}" for i in range(5)}

    # Remove list ordered oldest-first.
    assert result.remove == [f"snap.{i:03d}" for i in range(5)]


# ──────────────────────────────────────────────────────────────────────────
# 8. explain() returns keep_count / remove_count dict
# ──────────────────────────────────────────────────────────────────────────


def test_explain_returns_counts():
    """explain() returns a dict with keep_count and remove_count keys."""
    items = _mk_items(10)
    policy = RetentionPolicy(chain_length=3, keep_generations=1)
    engine = TimeBasedRetention(policy)
    now = items[-1].timestamp

    explanation = engine.explain(items, policy, now=now)

    assert isinstance(explanation, dict)
    assert "keep_count" in explanation
    assert "remove_count" in explanation
    assert isinstance(explanation["keep_count"], int)
    assert isinstance(explanation["remove_count"], int)


# ──────────────────────────────────────────────────────────────────────────
# 9. explain returns correct keep/remove counts
# ──────────────────────────────────────────────────────────────────────────


def test_explain_returns_keep_remove_counts():
    """explain() returns counts that match evaluate() results."""
    items = _mk_items(8)
    policy = RetentionPolicy(chain_length=3, keep_generations=1)
    engine = TimeBasedRetention(policy)
    now = items[-1].timestamp

    # evaluate for reference
    eval_result = engine.evaluate(items, policy, now=now)
    explanation = engine.explain(items, policy, now=now)

    assert explanation["keep_count"] == len(eval_result.keep)
    assert explanation["remove_count"] == len(eval_result.remove)
    assert explanation["keep_count"] == 3
    assert explanation["remove_count"] == 5

    # keep_count + remove_count equals total items.
    assert explanation["keep_count"] + explanation["remove_count"] == len(items)


# ──────────────────────────────────────────────────────────────────────────
# 10. explain() is a pure function
# ──────────────────────────────────────────────────────────────────────────


def test_explain_is_pure_function():
    """Calling explain() twice with identical inputs returns the same result."""
    items = _mk_items(10)
    policy = RetentionPolicy(chain_length=4, keep_generations=1)
    engine = TimeBasedRetention(policy)
    now = items[-1].timestamp

    result1 = engine.explain(items, policy, now=now)
    result2 = engine.explain(items, policy, now=now)

    assert result1 == result2
    assert result1["keep_count"] == result2["keep_count"]
    assert result1["remove_count"] == result2["remove_count"]


# ──────────────────────────────────────────────────────────────────────────
# 11. keep_generations used when chain_length is zero
# ──────────────────────────────────────────────────────────────────────────


def test_keep_generations_used_when_chain_length_zero():
    """chain_length=0, keep_generations=3 → keeps newest 3 items."""
    items = _mk_items(7)
    policy = RetentionPolicy(chain_length=0, keep_generations=3)
    engine = TimeBasedRetention(policy)
    now = items[-1].timestamp

    result = engine.evaluate(items, policy, now=now)

    assert len(result.keep) == 3
    assert len(result.remove) == 4
    assert set(result.keep) == {"snap.004", "snap.005", "snap.006"}
    assert set(result.remove) == {"snap.000", "snap.001", "snap.002", "snap.003"}

    # Verify explain() also uses keep_generations.
    explanation = engine.explain(items, policy, now=now)
    assert explanation["keep_count"] == 3
    assert explanation["remove_count"] == 4


# ──────────────────────────────────────────────────────────────────────────
# 12. evaluate() is deterministic
# ──────────────────────────────────────────────────────────────────────────


def test_evaluate_deterministic():
    """Same inputs always produce identical outputs — pure function."""
    items = _mk_items(10)
    policy = RetentionPolicy(chain_length=3, keep_generations=1)
    engine = TimeBasedRetention(policy)
    now = items[-1].timestamp

    result1 = engine.evaluate(items, policy, now=now)
    result2 = engine.evaluate(items, policy, now=now)
    result3 = engine.evaluate(items, policy, now=now)

    # Both keep and remove are identical across all calls.
    assert result1.keep == result2.keep == result3.keep
    assert result1.remove == result2.remove == result3.remove

    # RetentionResult supports value equality.
    assert result1 == result2 == result3

    # Calling with a different now (which the count-based engine ignores)
    # still produces the same result — it's purely timestamp-sorted.
    different_now = datetime(2026, 12, 31, 23, 59, 59)
    result4 = engine.evaluate(items, policy, now=different_now)
    assert result1 == result4
