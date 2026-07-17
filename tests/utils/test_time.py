"""Unit tests for timestamp formatting utilities in qsnap.utils.time.

Tests verify format resolution, strftime output for each format name,
and fallback behaviour for unknown format values.  Pure functions —
no I/O, no side effects.
"""

from __future__ import annotations

from datetime import datetime

from qsnap.utils.time import format_snapshot_timestamp, resolve_format


def test_short_format_produces_yyyymmdd() -> None:
    """The 'short' format produces a YYYYMMDD string with no time component."""
    result = format_snapshot_timestamp(datetime(2025, 7, 13, 15, 31), "short")

    assert result == "20250713"


def test_long_format_produces_yyyymmdd_thhmm() -> None:
    """The 'long' format produces a YYYYMMDDTHHMM string."""
    result = format_snapshot_timestamp(datetime(2025, 7, 13, 15, 31), "long")

    assert result == "20250713T1531"


def test_long_iso_format_produces_yyyymmdd_thhmmss_offset() -> None:
    """The 'long-iso' format produces YYYYMMDDTHHMMSS followed by a tz offset."""
    result = format_snapshot_timestamp(datetime(2025, 7, 13, 15, 31, 23), "long-iso")

    assert result.startswith("20250713T153123")
    assert len(result) > len("20250713T153123")
    assert result[len("20250713T153123")] in ("+", "-")


def test_unknown_format_defaults_to_long() -> None:
    """An unknown format name falls back to the 'long' format."""
    result = format_snapshot_timestamp(datetime(2025, 7, 13, 15, 31), "bogus")

    assert result == "20250713T1531"


def test_resolve_format_short_returns_pctY_pctm_pctd() -> None:
    """resolve_format('short') returns the %Y%m%d strftime string."""
    assert resolve_format("short") == "%Y%m%d"


def test_resolve_format_long_returns_pctY_pctm_pctdT_pctH_pctM() -> None:
    """resolve_format('long') returns the %Y%m%dT%H%M strftime string."""
    assert resolve_format("long") == "%Y%m%dT%H%M"


def test_resolve_format_long_iso_returns_full_iso_format() -> None:
    """resolve_format('long-iso') returns the full ISO strftime string."""
    assert resolve_format("long-iso") == "%Y%m%dT%H%M%S%z"


def test_resolve_format_unknown_defaults_to_long() -> None:
    """resolve_format with an unknown name returns the same as 'long'."""
    assert resolve_format("bogus") == resolve_format("long")
