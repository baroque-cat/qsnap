"""Unit tests for shared parsing utilities in qsnap.utils.parsing.

Tests cover ``parse_domblklist_path_map``, ``parse_domblklist_path_for_disk``,
``parse_disk_from_snapshot_name``, ``parse_domblklist_disks``, and
``parse_timestamp``.  All functions are pure -- no I/O except ``parse_timestamp``
which reads file metadata (``stat().st_mtime``) as a fallback.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from qsnap.utils.parsing import (
    parse_disk_from_snapshot_name,
    parse_domblklist_disks,
    parse_domblklist_path_for_disk,
    parse_domblklist_path_map,
    parse_timestamp,
)

# ── parse_domblklist_path_map ────────────────────────────────────────────────


def test_parse_domblklist_path_map_one_disk():
    """Standard domblklist output with vda returns a dict mapping target to source path."""
    stdout = (
        " Target   Source\n"
        "------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
    )
    result = parse_domblklist_path_map(stdout)
    assert result == {"vda": "/var/lib/libvirt/images/testvm.qcow2"}


def test_parse_domblklist_path_map_multiple_disks():
    """Multiple data lines -- returns a dict mapping all target -> source pairs."""
    stdout = (
        " Target   Source\n"
        "------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
        " vdb      /var/lib/libvirt/images/testvm-disk2.qcow2\n"
    )
    result = parse_domblklist_path_map(stdout)
    assert result == {
        "vda": "/var/lib/libvirt/images/testvm.qcow2",
        "vdb": "/var/lib/libvirt/images/testvm-disk2.qcow2",
    }


def test_parse_domblklist_path_map_empty():
    """Empty output returns an empty dict."""
    result = parse_domblklist_path_map("")
    assert result == {}


# ── parse_domblklist_path_for_disk ───────────────────────────────────────────


def test_parse_domblklist_path_for_disk_finds_target():
    """Finds the source path for a specific disk target."""
    stdout = (
        " Target   Source\n"
        "------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
        " vdb      /var/lib/libvirt/images/testvm-disk2.qcow2\n"
    )
    result = parse_domblklist_path_for_disk(stdout, "vdb")
    assert result == "/var/lib/libvirt/images/testvm-disk2.qcow2"


def test_parse_domblklist_path_for_disk_missing_raises_value_error():
    """Raises ValueError when the requested disk target is not in the output."""
    stdout = (
        " Target   Source\n"
        "------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
    )
    with pytest.raises(ValueError, match="no row for disk 'vdb'"):
        parse_domblklist_path_for_disk(stdout, "vdb")


# ── parse_disk_from_snapshot_name ────────────────────────────────────────────


def test_parse_disk_from_snapshot_name_vda():
    """Extracts vda from a single-disk snapshot name."""
    result = parse_disk_from_snapshot_name("testvm.20250713T153123_vda_a1b2c3.qcow2")
    assert result == "vda"


def test_parse_disk_from_snapshot_name_vdb():
    """Extracts vdb from a multi-disk snapshot name."""
    result = parse_disk_from_snapshot_name("testvm.20250713T153123_vdb_123abc.qcow2")
    assert result == "vdb"


def test_parse_disk_from_snapshot_name_no_disk_returns_none():
    """Returns None when the name has no recognizable disk segment."""
    # VM name with dots but no _{disk}_ pattern
    result = parse_disk_from_snapshot_name("3.Projects_opencode.20250713T153123_a1b2c3.qcow2")
    assert result is None


def test_parse_disk_from_snapshot_name_full_backup():
    """Returns None for a FULL backup name (has hex but no disk segment)."""
    result = parse_disk_from_snapshot_name("testvm.FULL.20250713T153123_a1b2c3.qcow2")
    assert result is None


def test_parse_disk_from_snapshot_name_no_timestamp():
    """Returns None when there is no timestamp pattern at all."""
    result = parse_disk_from_snapshot_name("some_random_name.qcow2")
    assert result is None


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


def test_parse_timestamp_unified_format_from_filename_with_disk_suffix():
    """Parse ``vm.20250101T120000_vda`` -> datetime(2025, 1, 1, 12, 0, 0)."""
    result = parse_timestamp(
        "vm.20250101T120000_vda", Path("/fake/path/qsnap_vm.20250101T120000_vda.qcow2")
    )
    assert result == datetime(2025, 1, 1, 12, 0, 0)


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
        "3.Projects_opencode.20250713T153123_vda",
        Path("/path/3.Projects_opencode.20250713T153123_vda.qcow2"),
    )
    assert result == datetime(2025, 7, 13, 15, 31, 23)


def test_parse_timestamp_full_backup_name():
    """Parse timestamp from a FULL backup filename with hex suffix."""
    result = parse_timestamp(
        "vm.FULL.20250101T120000_abc123",
        Path("/path/vm.FULL.20250101T120000_abc123.qcow2"),
    )
    assert result == datetime(2025, 1, 1, 12, 0, 0)


def test_parse_timestamp_collision_suffix():
    """Parse timestamp when a collision suffix (``_1``) is present."""
    result = parse_timestamp(
        "vm.20250101T120000_vda_abc123_1",
        Path("/path/vm.20250101T120000_vda_abc123_1.qcow2"),
    )
    assert result == datetime(2025, 1, 1, 12, 0, 0)
