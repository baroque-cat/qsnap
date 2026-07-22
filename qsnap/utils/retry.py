"""Pure retry utility functions for backup transfer retry logic.

These functions are pure — no I/O, no side effects, no Core dependency.
They are extracted as testable pure functions so that retryability
detection and backoff calculation can be unit-tested in isolation.
"""

from __future__ import annotations

import re

# Error patterns that indicate a transient (retryable) failure.
# Verification mismatches ("content comparison mismatch" and the
# legacy "hash mismatch") are the ONLY verification errors that are
# retryable — a mismatch may indicate transient transfer corruption
# that a retry can fix.  Other verification errors (format mismatch,
# virtual-size mismatch) are deterministic and must NOT be retried
# (they don't match any pattern here, so they're non-retryable by
# default).
_RETRYABLE_PATTERNS = [
    "connection refused",
    "no route to host",
    "timed out",
    "broken pipe",
    "eof",
    "verification failed: content comparison mismatch",
    "verification failed: hash mismatch",
]

# Error patterns that indicate a permanent (non-retryable) failure.
# These are checked first — if an error matches both a retryable and
# non-retryable pattern, it is treated as non-retryable.
_NON_RETRYABLE_PATTERNS = [
    "no space left on device",
    "permission denied",
]


def is_retryable(error: str) -> bool:
    """Check whether *error* indicates a transient, retryable failure.

    Returns ``True`` for errors like "Connection refused", "No route
    to host", "timed out", "broken pipe", "EOF", and verification
    mismatches ("content comparison mismatch" and the legacy
    "hash mismatch") (case-insensitive).  Verification mismatches are
    the only verification errors that are retryable — they may indicate
    transient transfer corruption that a retry can fix.

    Returns ``False`` for "No space left on device", "Permission
    denied", and any error that does not match a retryable pattern.
    Verification errors other than mismatches (e.g., format errors,
    virtual-size mismatch) are deterministic and NOT retried.

    Non-retryable patterns take precedence: if an error matches both
    a retryable and a non-retryable pattern, it is non-retryable.
    """
    lower = error.lower()

    # Check non-retryable patterns first (they take precedence).
    for pattern in _NON_RETRYABLE_PATTERNS:
        if pattern in lower:
            return False

    # Check retryable patterns.
    return any(pattern in lower for pattern in _RETRYABLE_PATTERNS)


def compute_backoff(base_seconds: int, attempt: int) -> float:
    """Compute exponential backoff delay for *attempt* (1-indexed).

    Returns ``base_seconds * 2^(attempt - 1)``.  For example, with
    ``base_seconds=2``: attempt 1 → 2, attempt 2 → 4, attempt 3 → 8.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    return float(base_seconds * (2 ** (attempt - 1)))


def parse_retry_duration(raw: str) -> int:
    """Convert a duration string like ``"2s"`` to seconds.

    Accepted format: ``"<integer>s"`` (e.g. ``"1s"``, ``"5s"``, ``"10s"``).
    Raises ``ValueError`` on invalid format.
    """
    match = re.match(r"^(\d+)s$", raw.strip())
    if not match:
        raise ValueError(
            f"Invalid duration string: {raw!r}. Expected format like '1s', '5s', '10s'."
        )
    return int(match.group(1))
