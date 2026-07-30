"""Unit tests for duration/stall-timeout parsing utilities in qsnap.utils.time.

Tests verify parse_duration and parse_stall_timeout behaviour.
Pure functions — no I/O, no side effects.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from qsnap.utils.time import parse_duration, parse_stall_timeout


# ---------------------------------------------------------------------------
# parse_duration
# ---------------------------------------------------------------------------


def test_parse_duration_hours() -> None:
    """parse_duration('6h') returns 6 hours."""
    assert parse_duration("6h") == timedelta(hours=6)


def test_parse_duration_days() -> None:
    """parse_duration('2d') returns 2 days."""
    assert parse_duration("2d") == timedelta(days=2)


def test_parse_duration_weeks() -> None:
    """parse_duration('1w') returns 1 week."""
    assert parse_duration("1w") == timedelta(weeks=1)


def test_parse_duration_months() -> None:
    """parse_duration('1m') returns ~30 days."""
    assert parse_duration("1m") == timedelta(days=30)


def test_parse_duration_years() -> None:
    """parse_duration('1y') returns ~365 days."""
    assert parse_duration("1y") == timedelta(days=365)


def test_parse_duration_all_returns_max() -> None:
    """parse_duration('all') returns timedelta.max (infinite)."""
    assert parse_duration("all") == timedelta.max


def test_parse_duration_latest_returns_zero() -> None:
    """parse_duration('latest') returns timedelta(0)."""
    assert parse_duration("latest") == timedelta(0)


def test_parse_duration_invalid_raises() -> None:
    """parse_duration with invalid string raises ValueError."""
    with pytest.raises(ValueError):
        parse_duration("bogus")


# ---------------------------------------------------------------------------
# parse_stall_timeout
# ---------------------------------------------------------------------------


def test_parse_stall_timeout_seconds() -> None:
    """parse_stall_timeout('30s') returns 30."""
    assert parse_stall_timeout("30s") == 30


def test_parse_stall_timeout_minutes() -> None:
    """parse_stall_timeout('30m') returns 1800."""
    assert parse_stall_timeout("30m") == 1800


def test_parse_stall_timeout_hours() -> None:
    """parse_stall_timeout('1h') returns 3600."""
    assert parse_stall_timeout("1h") == 3600


def test_parse_stall_timeout_days() -> None:
    """parse_stall_timeout('2d') returns 172800."""
    assert parse_stall_timeout("2d") == 172800


def test_parse_stall_timeout_zero() -> None:
    """parse_stall_timeout('0s') returns 0."""
    assert parse_stall_timeout("0s") == 0


def test_parse_stall_timeout_invalid_raises() -> None:
    """parse_stall_timeout with invalid string raises ValueError."""
    with pytest.raises(ValueError):
        parse_stall_timeout("bogus")
