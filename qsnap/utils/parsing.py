"""Shared parsing helpers for ``virsh domblklist`` output and timestamps.

These functions were extracted from duplicated implementations in
``snapshot/external.py``, ``change/allocation_detector.py``, and
``backup/file_copy.py`` to eliminate code duplication (design D6).

All functions are pure — no I/O except ``parse_timestamp`` which reads
file metadata (``stat().st_mtime``) as a fallback.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


def _parse_domblklist_rows(stdout: str) -> list[tuple[str, str]]:
    """Parse ``virsh domblklist`` output into ``(target, source_path)`` rows.

    Skips header lines (``Target   Source`` and separator dashes).
    Returns an empty list when no data rows are present.
    """
    rows: list[tuple[str, str]] = []
    for line in stdout.strip().splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0] == "Target" or line.startswith("-"):
            continue
        rows.append((parts[0], parts[-1]))
    return rows


def parse_domblklist_path(stdout: str) -> str:
    """Extract the source path (last column) of the first data row.

    Raises:
        ValueError: When the output contains no data rows.
    """
    rows = _parse_domblklist_rows(stdout)
    if not rows:
        raise ValueError("domblklist output contains no data rows")
    return rows[0][1]


def parse_domblklist_target(stdout: str) -> str:
    """Extract the target device name (first column) of the first data row.

    Raises:
        ValueError: When the output contains no data rows.
    """
    rows = _parse_domblklist_rows(stdout)
    if not rows:
        raise ValueError("domblklist output contains no data rows")
    return rows[0][0]


def parse_domblklist_disks(stdout: str) -> list[tuple[str, str]]:
    """Return a list of ``(target, source_path)`` tuples for all disks.

    Returns an empty list when no data rows are present.
    """
    return _parse_domblklist_rows(stdout)


def parse_timestamp(name: str, filepath: Path) -> datetime:
    """Parse a timestamp from the filename suffix.

    Attempts to parse ``%Y%m%dT%H%M%S`` from the last ``.``-separated
    segment of *name*.  Falls back to the file's ``mtime`` and finally
    to ``datetime.now()``.
    """
    name_parts = name.split(".")
    if len(name_parts) >= 2:
        ts_str = name_parts[-1]
        try:
            return datetime.strptime(ts_str, "%Y%m%dT%H%M%S")
        except ValueError:
            pass
    try:
        mtime = filepath.stat().st_mtime
        return datetime.fromtimestamp(mtime)
    except (OSError, ValueError):
        return datetime.now()


# ── Rate-limit parsing ────────────────────────────────────────────────────

_RATE_LIMIT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGTkmgt])\s*$")
_RATE_LIMIT_MULTIPLIERS = {
    "k": 1024,
    "m": 1024**2,
    "g": 1024**3,
    "t": 1024**4,
}


def parse_rate_limit(value: str) -> int:
    """Parse a rate-limit string into bytes-per-second as an ``int``.

    Accepted forms:
        ``"no"`` or ``"0"`` → ``0`` (unlimited)
        ``"500K"`` → ``512_000`` (500 × 1024)
        ``"100M"`` → ``104_857_600`` (100 × 1024²)
        ``"1G"``   → ``1_073_741_824`` (1 × 1024³)

    Suffix is case-insensitive and **required** for non-zero values.
    A bare integer like ``"500"`` is **invalid** (ambiguous unit).

    Raises:
        ValueError: When *value* is not a recognised rate-limit string.
    """
    if not isinstance(value, str):
        raise ValueError(f"rate_limit must be a string, got {type(value).__name__}")
    cleaned = value.strip().lower()
    if cleaned in ("no", "0", ""):
        return 0
    match = _RATE_LIMIT_RE.match(value)
    if match is None:
        raise ValueError(
            f"Invalid rate_limit value: {value!r}. "
            f"Expected 'no', or a number with a unit suffix (K, M, G, T), "
            f"e.g. '500K', '100M', '1G'."
        )
    number = float(match.group(1))
    suffix = match.group(2).lower()
    return int(number * _RATE_LIMIT_MULTIPLIERS[suffix])


def rate_limit_to_kib(value: str) -> int:
    """Parse a rate-limit string and return KiB/s (``parse_rate_limit(value) // 1024``).

    Returns ``0`` when *value* is ``"no"`` or ``"0"``.
    """
    return parse_rate_limit(value) // 1024
