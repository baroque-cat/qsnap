"""Unit tests for pure retry utility functions in qsnap.utils.retry.

Tests cover ``is_retryable()``, ``parse_retry_duration()``, and
``compute_backoff()``.  All functions are pure — no I/O, no side effects.
"""

from __future__ import annotations

import pytest

from qsnap.utils.retry import compute_backoff, is_retryable, parse_retry_duration

# ── is_retryable ───────────────────────────────────────────────────────────


def test_is_retryable_connection_refused():
    """``is_retryable("Connection refused")`` returns True."""
    assert is_retryable("Connection refused") is True


def test_is_retryable_no_route_to_host():
    """``is_retryable("No route to host")`` returns True."""
    assert is_retryable("No route to host") is True


def test_is_retryable_timed_out_case_insensitive():
    """``is_retryable("TIMED OUT")`` returns True (case-insensitive)."""
    assert is_retryable("TIMED OUT") is True


def test_is_retryable_broken_pipe():
    """``is_retryable("broken pipe")`` returns True."""
    assert is_retryable("broken pipe") is True


def test_is_retryable_eof():
    """``is_retryable("EOF")`` returns True."""
    assert is_retryable("EOF") is True


def test_is_retryable_no_space_left_on_device():
    """``is_retryable("No space left on device")`` returns False
    (non-retryable pattern takes precedence)."""
    assert is_retryable("No space left on device") is False


def test_is_retryable_permission_denied():
    """``is_retryable("Permission denied")`` returns False."""
    assert is_retryable("Permission denied") is False


def test_is_retryable_unknown_error():
    """``is_retryable("Some random error")`` returns False (no match)."""
    assert is_retryable("Some random error") is False


# ── parse_retry_duration ───────────────────────────────────────────────────


def test_parse_retry_duration_2s():
    """``parse_retry_duration("2s")`` returns 2."""
    assert parse_retry_duration("2s") == 2


def test_parse_retry_duration_10s():
    """``parse_retry_duration("10s")`` returns 10."""
    assert parse_retry_duration("10s") == 10


def test_parse_retry_duration_invalid_raises():
    """``parse_retry_duration("abc")`` raises ValueError."""
    with pytest.raises(ValueError):
        parse_retry_duration("abc")


def test_parse_retry_duration_invalid_no_suffix():
    """``parse_retry_duration("5")`` (no ``s`` suffix) raises ValueError."""
    with pytest.raises(ValueError):
        parse_retry_duration("5")


# ── compute_backoff ────────────────────────────────────────────────────────


def test_compute_backoff_attempt_1():
    """``compute_backoff(base_seconds=2, attempt=1)`` returns 2.0."""
    assert compute_backoff(2, 1) == 2.0


def test_compute_backoff_attempt_2():
    """``compute_backoff(base_seconds=2, attempt=2)`` returns 4.0."""
    assert compute_backoff(2, 2) == 4.0


def test_compute_backoff_attempt_3():
    """``compute_backoff(base_seconds=2, attempt=3)`` returns 8.0."""
    assert compute_backoff(2, 3) == 8.0


def test_compute_backoff_invalid_attempt():
    """``compute_backoff(base_seconds=2, attempt=0)`` raises ValueError."""
    with pytest.raises(ValueError):
        compute_backoff(2, 0)
