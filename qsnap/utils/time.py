"""Timestamp formatting utilities for snapshot naming.

Maps ``GlobalConfig.timestamp_format`` values to ``strftime`` format strings
and formats ``datetime`` objects.  Pure functions — no I/O, no side effects.

Supported format values:
  short    → %Y%m%d            (e.g. 20250713)
  long     → %Y%m%dT%H%M       (e.g. 20250713T1531)  — default
  long-iso → %Y%m%dT%H%M%S%z   (e.g. 20250713T153123+0200)
"""

from __future__ import annotations

from datetime import datetime

_TIMESTAMP_FORMATS: dict[str, str] = {
    "short": "%Y%m%d",
    "long": "%Y%m%dT%H%M",
    "long-iso": "%Y%m%dT%H%M%S%z",
}


def resolve_format(fmt: str) -> str:
    """Resolve a ``timestamp_format`` config value to a ``strftime`` string.

    Unknown values fall back to ``"long"``.
    """
    return _TIMESTAMP_FORMATS.get(fmt, _TIMESTAMP_FORMATS["long"])


def format_snapshot_timestamp(dt: datetime, fmt: str) -> str:
    """Format *dt* as a snapshot timestamp string using the configured *fmt*.

    For ``long-iso``, the datetime is converted to local timezone before
    formatting so that the offset is always present.
    """
    fmt_str = resolve_format(fmt)
    if fmt == "long-iso":
        return dt.astimezone().strftime(fmt_str)
    return dt.strftime(fmt_str)
