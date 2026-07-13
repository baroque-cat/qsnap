"""Output formatters for the qsnap CLI.

Supports four output formats (mirroring btrbk):
  table  — human-readable aligned columns with uppercase headers (default)
  long   — extended table with all available columns
  raw    — key=value pairs, space-separated, machine-readable
  col:   — custom column selection (e.g. ``col:name,path,timestamp``)
"""

from __future__ import annotations


def format_table(rows: list[dict[str, str]], columns: list[str]) -> str:
    """Format *rows* as an aligned table with uppercase headers."""
    if not rows:
        return ""
    widths: dict[str, int] = {}
    for col in columns:
        widths[col] = len(col)
        for row in rows:
            widths[col] = max(widths[col], len(row.get(col, "")))
    header = "  ".join(col.upper().ljust(widths[col]) for col in columns)
    lines = [header]
    for row in rows:
        line = "  ".join(row.get(col, "").ljust(widths[col]) for col in columns)
        lines.append(line)
    return "\n".join(lines)


def format_raw(rows: list[dict[str, str]], columns: list[str]) -> str:
    """Format *rows* as space-separated ``key=value`` pairs."""
    if not rows:
        return ""
    lines: list[str] = []
    for row in rows:
        pairs = " ".join(f"{col}={row.get(col, '')}" for col in columns)
        lines.append(pairs)
    return "\n".join(lines)


def format_output(
    rows: list[dict[str, str]],
    columns: list[str],
    fmt: str,
) -> str:
    """Dispatch to the appropriate formatter based on *fmt*.

    Recognised values: ``table``, ``long``, ``raw``, ``col:<columns>``.
    Falls back to ``table`` for unknown formats.
    """
    if fmt == "raw":
        return format_raw(rows, columns)
    if fmt == "long":
        all_cols: list[str] = list(dict.fromkeys(c for row in rows for c in row))
        return format_table(rows, all_cols)
    if fmt.startswith("col:"):
        col_spec = fmt[4:]
        cols = [c.strip() for c in col_spec.split(",")]
        return format_table(rows, cols)
    return format_table(rows, columns)
