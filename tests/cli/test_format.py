from qsnap.cli.format import format_output, format_raw, format_table


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
