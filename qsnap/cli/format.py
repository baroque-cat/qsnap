"""Output formatters for the qsnap CLI.

Supports four output formats (mirroring btrbk):
  table  — human-readable aligned columns with uppercase headers (default)
  long   — extended table with all available columns
  raw    — key=value pairs, space-separated, machine-readable
  col:   — custom column selection (e.g. ``col:name,path,timestamp``)
"""

from __future__ import annotations

from qsnap.models.results import DeferredSummary


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


def _format_age(age_td) -> str:
    """Format a timedelta as a short human-readable age string."""
    total_seconds = int(age_td.total_seconds())
    if total_seconds >= 86400:
        return f"{total_seconds // 86400}d"
    if total_seconds >= 3600:
        return f"{total_seconds // 3600}h"
    if total_seconds >= 60:
        return f"{total_seconds // 60}m"
    return f"{total_seconds}s"


def format_deferred_table(summaries: list[DeferredSummary]) -> str:
    """Format deferred blockcommit summaries as a table.

    Columns: VM, SNAPSHOTS, REASON, AGE — sorted by age descending
    (oldest deferred operation first).
    """
    if not summaries:
        return "No deferred blockcommit operations"

    sorted_summaries = sorted(summaries, key=lambda s: s.age, reverse=True)
    rows: list[dict[str, str]] = []
    for s in sorted_summaries:
        rows.append(
            {
                "vm": s.vm_name,
                "snapshots": str(s.snapshot_count),
                "reason": s.reason,
                "age": _format_age(s.age),
            }
        )
    columns = ["vm", "snapshots", "reason", "age"]
    return format_table(rows, columns)


def format_deferred_raw(summaries: list[DeferredSummary]) -> str:
    """Format deferred blockcommit summaries as raw key=value pairs.

    Format: ``vm_name=... snapshots=... reason=... since=...``
    """
    if not summaries:
        return "No deferred blockcommit operations"

    sorted_summaries = sorted(summaries, key=lambda s: s.age, reverse=True)
    lines: list[str] = []
    for s in sorted_summaries:
        lines.append(
            f"vm_name={s.vm_name} "
            f"snapshots={s.snapshot_count} "
            f"reason={s.reason} "
            f"since={s.since.isoformat()}"
        )
    return "\n".join(lines)
