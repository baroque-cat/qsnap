"""Shared parsing helpers for ``virsh domblklist`` output and timestamps.

These functions were extracted from duplicated implementations in
``snapshot/external.py``, ``change/allocation_detector.py``, and the
backup modules to eliminate code duplication (design D6).

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


def parse_domblklist_disks(stdout: str) -> list[tuple[str, str]]:
    """Return a list of ``(target, source_path)`` tuples for all disks.

    Returns an empty list when no data rows are present.
    """
    return _parse_domblklist_rows(stdout)


def parse_domblklist_path_map(stdout: str) -> dict[str, str]:
    """Return a ``{target: source_path}`` mapping for all disks.

    Parses ``virsh domblklist`` output into a dictionary keyed by disk
    target device name (e.g. ``"vda"``).  Returns an empty dict when no
    data rows are present.
    """
    return dict(_parse_domblklist_rows(stdout))


def parse_domblklist_path_for_disk(stdout: str, disk: str) -> str:
    """Extract the source path for a specific disk target.

    Looks up the row whose target device name equals *disk* and returns
    its source path.

    Raises:
        ValueError: When no data row matches *disk*.
    """
    for target, source in _parse_domblklist_rows(stdout):
        if target == disk:
            return source
    raise ValueError(f"domblklist output contains no row for disk {disk!r}")


def parse_disk_from_snapshot_name(name: str) -> str | None:
    """Extract the disk target from a snapshot or backup filename.

    Snapshot names follow ``{vm}.{YYYYMMDDTHHMMSS}_{disk}_{6hex}.qcow2``
    (e.g. ``myvm.20250713T153123_vda_a1b2c3.qcow2``).  Returns the
    ``{disk}`` segment (e.g. ``"vda"``), or ``None`` when the name does
    not contain a recognizable disk segment.  Anchors on the timestamp
    pattern so VM names containing dots or underscores are handled
    correctly.
    """
    match = re.search(r"\d{8}T\d{6}_([^_]+)_[0-9a-fA-F]{6}", name)
    if match:
        return match.group(1)
    return None


def parse_timestamp(name: str, filepath: Path) -> datetime:
    """Parse a timestamp from a snapshot or backup filename.

    Searches *name* for the unified timestamp pattern
    ``YYYYMMDDTHHMMSS`` (e.g. ``20250713T153123``) — seconds
    resolution, no timezone offset.

    This correctly handles:

    - VM names containing dots (e.g. ``3.Projects_opencode.20250713T153123_vda``)
    - The ``_{disk}`` suffix in snapshot names (e.g. ``_vda``, ``_vdb``)
    - The ``_{6hex}`` collision-resistant suffix (e.g. ``_a1b2c3``)
    - Collision suffixes (e.g. ``_1`` appended to snapshot names)
    - FULL backup names (e.g. ``vm.FULL.20250713T153123_a1b2c3.qcow2``)

    The ``_{disk}``, ``_{6hex}``, and collision suffixes are naturally
    excluded because they do not match the timestamp pattern.

    If no timestamp pattern is found, falls back to the file's ``mtime``,
    and finally to :func:`datetime.now`.

    The function SHALL NOT use ``split(".")`` to extract the timestamp
    segment, as VM names may contain dots.
    """
    # Unified pattern: YYYYMMDDTHHMMSS (seconds resolution, no timezone).
    match = re.search(r"(\d{8}T\d{6})", name)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
        except ValueError:
            pass
    try:
        mtime = filepath.stat().st_mtime
        return datetime.fromtimestamp(mtime)
    except (OSError, ValueError):
        return datetime.now()
