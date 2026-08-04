"""Time-parsing and timestamp-formatting utilities.

Provides general-purpose duration/timeout parsers.  Pure functions —
no I/O, no side effects.
"""

from __future__ import annotations

import re
from datetime import timedelta

# ---------------------------------------------------------------------------
# Duration / timeout parsing
# ---------------------------------------------------------------------------

_UNIT_TO_DELTA: dict[str, timedelta] = {
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
    "m": timedelta(days=30),
    "y": timedelta(days=365),
}

_STALL_UNIT_TO_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


def parse_duration(text: str) -> timedelta:
    """Parse a duration string like ``"6h"``, ``"2d"`` into a ``timedelta``.

    ``"all"`` returns ``timedelta.max`` (effectively infinite).
    ``"latest"`` returns ``timedelta(0)`` (zero-width window).

    Units: ``h`` (hour), ``d`` (day), ``w`` (week), ``m`` (month ≈ 30 d),
    ``y`` (year ≈ 365 d).  This function is intended for *retention*
    durations where ``m`` means months.  For short timeout values where
    ``m`` means minutes, use :func:`parse_stall_timeout` instead.
    """
    if text == "all":
        return timedelta.max
    if text == "latest":
        return timedelta(0)
    match = re.match(r"^(\d+)([hdwmy])$", text)
    if match is None:
        raise ValueError(f"Invalid duration string: {text!r}")
    count = int(match.group(1))
    unit = match.group(2)
    return count * _UNIT_TO_DELTA[unit]


def parse_stall_timeout(text: str) -> int:
    """Parse a stall-timeout duration string into whole seconds.

    Supports: ``"30s"`` (30 seconds), ``"30m"`` (30 minutes),
    ``"1h"`` (1 hour), ``"2d"`` (2 days).  ``"0s"`` returns ``0``
    (disables stall detection — callers fall back to fixed-timeout
    ``shell.run()``).

    Unlike :func:`parse_duration`, ``m`` here means *minutes*, not months,
    because stall timeouts are short-lived durations, not retention windows.
    """
    match = re.match(r"^(\d+)([smhd])$", text)
    if match is None:
        raise ValueError(f"Invalid stall timeout string: {text!r}")
    count = int(match.group(1))
    unit = match.group(2)
    return count * _STALL_UNIT_TO_SECONDS[unit]
