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


def test_is_retryable_hash_mismatch():
    """``is_retryable("verification failed: hash mismatch")`` returns False.

    Hash mismatch is no longer a retryable verification error.
    Only content comparison mismatch is retryable among verification errors.
    """
    assert is_retryable("verification failed: hash mismatch") is False


def test_is_retryable_format_verification_error():
    """``is_retryable("verification failed: expected format qcow2, got raw")``
    returns False.

    Format verification errors are deterministic — no amount of retrying
    will convert a raw image to qcow2, so they must NOT be retried.
    """
    assert is_retryable("verification failed: expected format qcow2, got raw") is False


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


# ── Content comparison mismatch retry ─────────────────────────────────────


def test_content_comparison_mismatch_retried():
    """``is_retryable("verification failed: content comparison mismatch")`` returns True.

    Content comparison mismatches may indicate transient transfer
    corruption that a retry can fix — they are the only verification
    errors that ARE retryable.
    """
    assert is_retryable("verification failed: content comparison mismatch") is True


# ── Format verification error not retried ──────────────────────────────────


def test_format_verification_error_not_retried():
    """``is_retryable("verification failed: format mismatch")`` returns False.

    Format verification errors are deterministic — no amount of retrying
    will fix a format-type mismatch, so they must NOT be retried.
    The word "mismatch" without the "content comparison" prefix does NOT
    match the retryable pattern.
    """
    assert is_retryable("verification failed: format mismatch") is False


# ── Retry disabled when backup_retry_max is zero ──────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_retry_disabled_when_backup_retry_max_zero(
    make_vm_config,
    make_target,
    mock_shell,
    mock_state,
    mock_factory,
):
    """When ``backup_retry_max=0``, ``_execute_with_retry`` calls the operation
    exactly once — no retry loop is entered.

    This verifies the fast-path: ``_execute_with_retry`` checks
    ``max_retries <= 0`` and returns ``operation()`` immediately without
    entering the retry loop, regardless of the error type.
    """
    from qsnap.core import Core
    from tests.mocks import MockConfigFacade

    target = make_target(backup_retry_max=0, backup_retry_base="2s")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    call_count = 0

    def operation():
        nonlocal call_count
        call_count += 1
        # Return a failure — but max_retries=0 means no retry should happen
        from types import SimpleNamespace

        return SimpleNamespace(success=False, error="Connection refused")

    core._execute_with_retry(operation, target)

    # Operation is called exactly once — no retry loop
    assert call_count == 1
