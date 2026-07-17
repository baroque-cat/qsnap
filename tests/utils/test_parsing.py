"""Unit tests for shared parsing utilities in qsnap.utils.parsing.

Tests cover ``parse_domblklist_path``, ``parse_domblklist_target``,
``parse_domblklist_disks``, and ``parse_timestamp``.  All functions are
pure — no I/O except ``parse_timestamp`` which reads file metadata
(``stat().st_mtime``) as a fallback.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from qsnap.utils.parsing import (
    parse_domblklist_disks,
    parse_domblklist_path,
    parse_domblklist_target,
    parse_rate_limit,
    parse_timestamp,
    rate_limit_to_kib,
)

# ── parse_domblklist_path ──────────────────────────────────────────────────


def test_parse_domblklist_path_one_disk():
    """Standard domblklist output with vda returns the source path."""
    stdout = (
        " Target   Source\n"
        "------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
    )
    result = parse_domblklist_path(stdout)
    assert result == "/var/lib/libvirt/images/testvm.qcow2"


def test_parse_domblklist_path_multiple_lines_skips_header():
    """Multiple data lines — header and separator are skipped, returns
    the path of the first data row."""
    stdout = (
        " Target   Source\n"
        "------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
        " vdb      /var/lib/libvirt/images/testvm-disk2.qcow2\n"
    )
    result = parse_domblklist_path(stdout)
    assert result == "/var/lib/libvirt/images/testvm.qcow2"


def test_parse_domblklist_path_empty_raises_value_error():
    """Empty output raises ValueError."""
    with pytest.raises(ValueError, match="no data rows"):
        parse_domblklist_path("")


# ── parse_domblklist_target ────────────────────────────────────────────────


def test_parse_domblklist_target_returns_target_name():
    """Returns the first column (target device name, e.g. 'vda')."""
    stdout = (
        " Target   Source\n"
        "------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
    )
    result = parse_domblklist_target(stdout)
    assert result == "vda"


# ── parse_domblklist_disks ─────────────────────────────────────────────────


def test_parse_domblklist_disks_returns_all_disks():
    """Multiple disks returns a list of (target, path) tuples."""
    stdout = (
        " Target   Source\n"
        "------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
        " vdb      /var/lib/libvirt/images/testvm-disk2.qcow2\n"
    )
    result = parse_domblklist_disks(stdout)
    assert result == [
        ("vda", "/var/lib/libvirt/images/testvm.qcow2"),
        ("vdb", "/var/lib/libvirt/images/testvm-disk2.qcow2"),
    ]


# ── parse_timestamp ────────────────────────────────────────────────────────


def test_parse_timestamp_long_format_from_filename_with_disk_suffix():
    """Parse ``vm.20250101T1200_vda`` → datetime(2025, 1, 1, 12, 0)."""
    result = parse_timestamp(
        "vm.20250101T1200_vda", Path("/fake/path/qsnap_vm.20250101T1200_vda.qcow2")
    )
    assert result == datetime(2025, 1, 1, 12, 0)


def test_parse_timestamp_falls_back_to_mtime(tmp_path):
    """When the filename has no parseable timestamp, falls back to the
    file's mtime.
    """
    filepath = tmp_path / "no-timestamp.qcow2"
    filepath.write_bytes(b"\x00")

    mtime = filepath.stat().st_mtime
    expected = datetime.fromtimestamp(mtime)

    result = parse_timestamp("no-timestamp", filepath)
    assert result == expected


def test_parse_timestamp_dotted_vm_name():
    """Parse timestamp from a VM name containing dots."""
    result = parse_timestamp(
        "3.Projects_opencode.20250713T1531_vda",
        Path("/path/3.Projects_opencode.20250713T1531_vda.qcow2"),
    )
    assert result == datetime(2025, 7, 13, 15, 31)


def test_parse_timestamp_short_format():
    """Parse ``vm.20250101_vda`` → datetime(2025, 1, 1, 0, 0) using short
    format ``%Y%m%d``."""
    result = parse_timestamp(
        "vm.20250101_vda", Path("/path/vm.20250101_vda.qcow2")
    )
    assert result == datetime(2025, 1, 1, 0, 0)


def test_parse_timestamp_long_iso_format():
    """Parse long-iso format ``%Y%m%dT%H%M%S%z`` (with timezone)."""
    result = parse_timestamp(
        "vm.20250101T120000+0200_vda",
        Path("/path/vm.20250101T120000+0200_vda.qcow2"),
    )
    expected = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert result == expected


def test_parse_timestamp_full_backup_name():
    """Parse timestamp from a FULL backup filename."""
    result = parse_timestamp(
        "vm.FULL.20250101", Path("/path/vm.FULL.20250101.qcow2")
    )
    assert result == datetime(2025, 1, 1, 0, 0)


def test_parse_timestamp_collision_suffix():
    """Parse timestamp when a collision suffix (``_1``) is present."""
    result = parse_timestamp(
        "vm.20250101T1200_vda_1", Path("/path/vm.20250101T1200_vda_1.qcow2")
    )
    assert result == datetime(2025, 1, 1, 12, 0)


def test_parse_timestamp_long_iso_priority_over_long():
    """Verify long-iso pattern is matched first, before the shorter long
    pattern. The long pattern would match only ``20250101T1200`` and
    return a timezone-naive datetime, but the correct result is the full
    long-iso datetime with seconds and timezone offset."""
    result = parse_timestamp(
        "vm.20250101T120000+0200_vda",
        Path("/path/vm.20250101T120000+0200_vda.qcow2"),
    )
    expected = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert result == expected
    # Verify it is timezone-aware, not a truncated long-format match
    assert result.tzinfo is not None
    assert result.second == 0
    assert result.minute == 0
    assert result.hour == 12
    # Explicitly check that it is NOT the (wrong) long-format result
    long_only = datetime(2025, 1, 1, 12, 0)
    assert result != long_only


# ── parse_rate_limit ────────────────────────────────────────────────────────


def test_parse_rate_limit_500k():
    """parse_rate_limit('500K') == 512000 (500 × 1024)."""
    assert parse_rate_limit("500K") == 512000


def test_parse_rate_limit_100m():
    """parse_rate_limit('100M') == 104857600 (100 × 1024²)."""
    assert parse_rate_limit("100M") == 104857600


def test_parse_rate_limit_no_is_zero():
    """parse_rate_limit('no') == 0 (unlimited)."""
    assert parse_rate_limit("no") == 0


def test_parse_rate_limit_zero_string_is_zero():
    """parse_rate_limit('0') == 0 (unlimited)."""
    assert parse_rate_limit("0") == 0


def test_parse_rate_limit_empty_string_is_zero():
    """parse_rate_limit('') == 0 (unlimited)."""
    assert parse_rate_limit("") == 0


def test_parse_rate_limit_1g():
    """parse_rate_limit('1G') == 1073741824 (1 × 1024³)."""
    assert parse_rate_limit("1G") == 1073741824


def test_parse_rate_limit_lowercase_suffix():
    """parse_rate_limit is case-insensitive: '100m' == 104857600."""
    assert parse_rate_limit("100m") == 104857600


def test_parse_rate_limit_invalid_string_raises_value_error():
    """parse_rate_limit('abc') raises ValueError."""
    with pytest.raises(ValueError, match="Invalid rate_limit value"):
        parse_rate_limit("abc")


def test_parse_rate_limit_no_suffix_raises_value_error():
    """parse_rate_limit('100') (bare integer, no suffix) raises ValueError."""
    with pytest.raises(ValueError, match="Invalid rate_limit value"):
        parse_rate_limit("100")


def test_parse_rate_limit_invalid_suffix_raises_value_error():
    """parse_rate_limit('100X') (unknown suffix) raises ValueError."""
    with pytest.raises(ValueError, match="Invalid rate_limit value"):
        parse_rate_limit("100X")


# ── rate_limit_to_kib ────────────────────────────────────────────────────────


def test_rate_limit_to_kib_100m():
    """rate_limit_to_kib('100M') == 102400 (104857600 // 1024)."""
    assert rate_limit_to_kib("100M") == 102400


def test_rate_limit_to_kib_no_is_zero():
    """rate_limit_to_kib('no') == 0."""
    assert rate_limit_to_kib("no") == 0


def test_rate_limit_to_kib_500k():
    """rate_limit_to_kib('500K') == 500 (512000 // 1024)."""
    assert rate_limit_to_kib("500K") == 500
