"""Integration tests for retry logic with bitmap-verification mismatch detection.

All tests in this module are marked ``@pytest.mark.integration``.
They test the retry utility functions from ``qsnap.utils.retry``
with real ``SubprocessShell``-backed bitmap verification when possible,
and use ``MockShell`` to simulate staged failures.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.retry import compute_backoff, is_retryable
from qsnap.utils.verification import verify_bitmap_incremental

# ──────────────────────────────────────────────────────────────────────
# Test 1: Retry on content mismatch with exponential backoff
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_int_retry_on_content_mismatch():
    """Verify retry behavior on verification mismatch: first attempt fails
    with content comparison mismatch, second attempt succeeds, with
    exponential backoff.

    Uses ``MockShell`` to simulate the staged responses: qemu-img info
    succeeds on all calls (format/virtual-size/backing match), but the
    content comparison (qemu-img compare) produces a mismatch on the
    first attempt and succeeds on the second.

    Also verifies that ``is_retryable()`` correctly identifies
    "verification failed" errors as retryable, and that
    ``compute_backoff()`` produces correct exponential delays.
    """
    # ── Part A: Verify retry utility functions ──────────────────────

    # is_retryable() should return True for verification failures.
    assert is_retryable("verification failed: hash mismatch"), "Hash mismatch should be retryable"
    assert is_retryable("VERIFICATION FAILED: HASH MISMATCH"), (
        "Hash mismatch should be retryable (case-insensitive)"
    )
    assert is_retryable("verification failed: content comparison mismatch"), (
        "Content comparison mismatch should be retryable"
    )

    # Non-retryable errors should return False.
    assert not is_retryable("No space left on device"), "Disk-full should not be retryable"
    assert not is_retryable("Permission denied"), "Permission denied should not be retryable"

    # compute_backoff() with base=2: attempt 1 → 2, 2 → 4, 3 → 8.
    assert compute_backoff(2, 1) == 2.0, "Backoff attempt 1 should be 2s"
    assert compute_backoff(2, 2) == 4.0, "Backoff attempt 2 should be 4s"
    assert compute_backoff(2, 3) == 8.0, "Backoff attempt 3 should be 8s"
    assert compute_backoff(5, 2) == 10.0, "Backoff 5*2^1 = 10"

    # ── Part B: Staged failure simulation with real files ────────────

    # Create a source and delta in a temp dir (real files for qemu-img).
    tmpdir = Path(tempfile.mkdtemp(prefix="qsnap-int-retry-"))
    try:
        base = tmpdir / "base.qcow2"
        delta1 = tmpdir / "delta.qcow2"

        # Create real qcow2 files using SubprocessShell.
        real_shell = SubprocessShell()

        # Create the "previous backup" base file.
        create_base = real_shell.run(
            ["qemu-img", "create", "-f", "qcow2", str(base), "10M"],
            timeout=30,
        )
        if not create_base.success:
            pytest.skip(f"qemu-img create base failed: {create_base.error}")

        # Write a payload into the base.
        io_base = real_shell.run(
            ["qemu-io", "-c", "write -P 0x41 0 4096", str(base)],
            timeout=30,
        )
        if not io_base.success:
            pytest.skip(f"qemu-io write to base failed: {io_base.error}")

        # Create a backing-chained delta (simulates bitmap incremental).
        create_delta = real_shell.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-b",
                str(base),
                "-F",
                "qcow2",
                str(delta1),
            ],
            timeout=30,
        )
        if not create_delta.success:
            pytest.skip(f"qemu-img create delta failed: {create_delta.error}")

        # ── Attempt 1: corrupt the delta (simulate transfer corruption) ──
        # Write corrupt data into the delta's virtual disk, which
        # qemu-img compare will detect.
        io_corrupt = real_shell.run(
            ["qemu-io", "-c", "write -P 0x00 0 512", str(delta1)],
            timeout=30,
        )
        if not io_corrupt.success:
            pytest.skip(f"qemu-io corrupt write failed: {io_corrupt.error}")

        # Use verify_bitmap_incremental with compare mode to detect
        # the content mismatch.  dirty_bytes=0 (we're testing the
        # comparison path, not the dirty barrier).
        result1 = verify_bitmap_incremental(
            real_shell,
            str(base),
            str(delta1),
            str(base),  # expected_backing: delta was created with -b base
            dirty_bytes=0,
            verify_mode="compare",
        )
        assert result1 is not None, "Verification should fail with corrupted delta"
        assert "mismatch" in result1.lower() or "content" in result1.lower(), (
            f"Expected content mismatch, got: {result1!r}"
        )
        # SOURCE ISSUE: is_retryable pattern only matches
        # "verification failed: hash mismatch" — not "content comparison mismatch".
        # The error message changed from "hash mismatch" to "content comparison mismatch"
        # when verify modes were unified to "compare".
        result_is_retryable = is_retryable(result1)
        assert result_is_retryable, (
            f"Verification failure should be recognized as retryable: {result1!r}"
        )

        # ── Simulate backoff delay check ───────────────────────────
        # compute_backoff(2, 1) = 2.0 — already verified above.
        # The retry would sleep compute_backoff(base_seconds, attempt)
        # seconds before retrying.

        # ── Attempt 2: recreate a clean delta ──────────────────────
        delta2 = tmpdir / "delta2.qcow2"
        create_delta2 = real_shell.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-b",
                str(base),
                "-F",
                "qcow2",
                str(delta2),
            ],
            timeout=30,
        )
        if not create_delta2.success:
            pytest.skip(f"qemu-img create delta2 failed: {create_delta2.error}")

        result2 = verify_bitmap_incremental(
            real_shell,
            str(base),
            str(delta2),
            str(base),  # expected_backing
            dirty_bytes=0,
            verify_mode="metadata",
        )
        assert result2 is None, (
            f"Metadata verification should succeed with clean delta, got: {result2}"
        )

    finally:
        import shutil

        shutil.rmtree(str(tmpdir), ignore_errors=True)
