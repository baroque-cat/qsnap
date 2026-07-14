from datetime import datetime, timedelta

from qsnap.cli.format import (
    format_deferred_raw,
    format_deferred_table,
    format_output,
    format_raw,
    format_table,
)
from qsnap.models.results import DeferredSummary


def test_format_table_produces_aligned_columns_uppercase_headers():
    rows = [
        {"name": "snap1", "path": "/tmp/snap1.qcow2"},
        {"name": "snap2", "path": "/tmp/snap2.qcow2"},
    ]
    columns = ["name", "path"]
    output = format_table(rows, columns)

    lines = output.split("\n")
    header = lines[0]
    assert "NAME" in header
    assert "PATH" in header

    # Each header is padded to the column width (ljust), so the header
    # for "name" (width 5, since "snap1"/"snap2" are 5 chars) must be
    # longer than the bare "NAME" (4 chars).
    name_part = header[: header.index("PATH")]
    assert len(name_part.rstrip()) >= len("NAME")
    assert len(name_part) > len("NAME")

    # Data rows are aligned: the "name" column field has the same width
    # in every line.
    for line in lines[1:]:
        name_field = line[: header.index("PATH")]
        assert len(name_field) == len(name_part)


def test_format_raw_produces_space_separated_key_value_pairs():
    rows = [
        {"name": "snap1", "path": "/tmp/snap1.qcow2"},
        {"name": "snap2", "path": "/tmp/snap2.qcow2"},
    ]
    columns = ["name", "path"]
    output = format_raw(rows, columns)

    lines = output.split("\n")
    assert len(lines) == 2  # one item per line

    assert "name=snap1" in lines[0]
    assert "path=/tmp/snap1.qcow2" in lines[0]
    assert "name=snap2" in lines[1]
    assert "path=/tmp/snap2.qcow2" in lines[1]

    # Pairs are space-separated on the same line
    assert lines[0] == "name=snap1 path=/tmp/snap1.qcow2"


def test_format_long_produces_extended_columns():
    rows = [
        {
            "name": "snap1",
            "path": "/tmp/snap1.qcow2",
            "timestamp": "2024-01-01T00:00:00",
            "allocation": "1024",
        },
        {
            "name": "snap2",
            "path": "/tmp/snap2.qcow2",
            "timestamp": "2024-01-02T00:00:00",
            "allocation": "2048",
        },
    ]
    columns = ["name", "path"]
    output = format_output(rows, columns, "long")

    assert "NAME" in output
    assert "PATH" in output
    assert "TIMESTAMP" in output
    assert "ALLOCATION" in output


def test_format_col_selects_custom_columns():
    rows = [
        {"name": "snap1", "path": "/tmp/snap1.qcow2", "timestamp": "2024-01-01"},
        {"name": "snap2", "path": "/tmp/snap2.qcow2", "timestamp": "2024-01-02"},
    ]
    columns = ["name", "path"]
    output = format_output(rows, columns, "col:name,timestamp")

    header = output.split("\n")[0]
    assert "NAME" in header
    assert "TIMESTAMP" in header
    assert "PATH" not in header


def test_format_empty_list_produces_no_output():
    assert format_table([], ["name", "path"]) == ""
    assert format_raw([], ["name", "path"]) == ""
    assert format_output([], ["name", "path"], "table") == ""
    assert format_output([], ["name", "path"], "long") == ""
    assert format_output([], ["name", "path"], "raw") == ""
    assert format_output([], ["name", "path"], "col:name,path") == ""


def test_format_col_invalid_column_produces_empty_cells():
    rows = [
        {"name": "snap1", "path": "/tmp/snap1.qcow2"},
        {"name": "snap2", "path": "/tmp/snap2.qcow2"},
    ]
    columns = ["name", "path"]
    output = format_output(rows, columns, "col:nonexistent")

    # Does not crash; header is the uppercased (nonexistent) column name
    header = output.split("\n")[0]
    assert "NONEXISTENT" in header

    # Data rows contain empty cells (no actual values)
    for line in output.split("\n")[1:]:
        assert line.strip() == ""


# ── deferred blockcommit format tests ───────────────────────────────────


def test_format_deferred_table_with_summaries():
    """format_deferred_table() produces a table with VM, SNAPSHOTS, REASON, AGE columns."""
    summaries = [
        DeferredSummary(
            vm_name="vm-home",
            snapshot_count=3,
            reason="apparmor",
            age=timedelta(hours=2),
            since=datetime(2025, 7, 14, 10, 0),
        ),
    ]
    output = format_deferred_table(summaries)

    lines = output.split("\n")
    header = lines[0]
    assert "VM" in header
    assert "SNAPSHOTS" in header
    assert "REASON" in header
    assert "AGE" in header

    # Data row contains the VM name, snapshot count, reason, and age
    data_line = lines[1]
    assert "vm-home" in data_line
    assert "3" in data_line
    assert "apparmor" in data_line
    assert "2h" in data_line


def test_format_deferred_table_empty():
    """format_deferred_table() with empty list returns the no-operations message."""
    output = format_deferred_table([])
    assert output == "No deferred blockcommit operations"


def test_format_deferred_table_sorted_by_age_desc():
    """format_deferred_table() sorts summaries by age descending (oldest first)."""
    summaries = [
        DeferredSummary(
            vm_name="vm-young",
            snapshot_count=1,
            reason="apparmor",
            age=timedelta(minutes=5),
            since=datetime(2025, 7, 14, 15, 0),
        ),
        DeferredSummary(
            vm_name="vm-old",
            snapshot_count=2,
            reason="selinux",
            age=timedelta(days=3),
            since=datetime(2025, 7, 11, 10, 0),
        ),
        DeferredSummary(
            vm_name="vm-middle",
            snapshot_count=1,
            reason="apparmor",
            age=timedelta(hours=2),
            since=datetime(2025, 7, 14, 10, 0),
        ),
    ]
    output = format_deferred_table(summaries)

    lines = output.split("\n")
    # Header is line 0; data rows are lines 1, 2, 3
    # Sorted by age descending: vm-old (3d) > vm-middle (2h) > vm-young (5m)
    assert "vm-old" in lines[1]
    assert "vm-middle" in lines[2]
    assert "vm-young" in lines[3]


def test_format_deferred_raw_with_summaries():
    """format_deferred_raw() produces vm_name=... snapshots=... reason=... since=... rows."""
    since_dt = datetime(2025, 7, 14, 10, 0)
    summaries = [
        DeferredSummary(
            vm_name="vm-home",
            snapshot_count=3,
            reason="apparmor",
            age=timedelta(hours=2),
            since=since_dt,
        ),
    ]
    output = format_deferred_raw(summaries)

    lines = output.split("\n")
    assert len(lines) == 1
    line = lines[0]
    assert "vm_name=vm-home" in line
    assert "snapshots=3" in line
    assert "reason=apparmor" in line
    assert f"since={since_dt.isoformat()}" in line


def test_format_deferred_raw_empty():
    """format_deferred_raw() with empty list returns the no-operations message."""
    output = format_deferred_raw([])
    assert output == "No deferred blockcommit operations"
