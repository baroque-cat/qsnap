"""Unit tests for ``verify_bitmap_incremental()``.

All tests use ``MockShell`` for full isolation — zero real qemu-img calls.

IMPORTANT: ``verify_bitmap_incremental()`` issues BOTH
``qemu-img info`` commands (source and delta) before parsing JSON.
Tests that need the function to reach a specific check must mock
**both** info commands, even when only one is expected to fail.
"""

from __future__ import annotations

import json
import logging

import pytest

from qsnap.models.results import ShellResult
from qsnap.utils.verification import verify_bitmap_incremental
from tests.mocks.mock_shell import MockShell

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_SRC = "/var/lib/libvirt/snapshots/testvm/snap4.qcow2"
_DELTA = "/mnt/backup/testvm/testvm.20250721.qcow2"
_BACKING = "/mnt/backup/testvm/testvm.20250720.qcow2"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _info_json(
    virtual_size: int = 67108864,
    actual_size: int = 1048576,
    fmt: str = "qcow2",
    backing_filename: str | None = _BACKING,
) -> str:
    """Build a minimal qemu-img info JSON dict as a string."""
    info: dict[str, object] = {
        "virtual-size": virtual_size,
        "actual-size": actual_size,
        "format": fmt,
        "filename": "/var/lib/libvirt/snapshots/testvm/snap4.qcow2",
        "cluster-size": 65536,
    }
    if backing_filename is not None:
        info["backing-filename"] = backing_filename
    return json.dumps(info)


def _success(stdout: str) -> ShellResult:
    """Shortcut: successful ``ShellResult`` with given stdout."""
    return ShellResult(success=True, stdout=stdout, stderr="", returncode=0, error=None)


def _fail(error_msg: str = "No such file") -> ShellResult:
    """Shortcut: failed ``ShellResult``."""
    return ShellResult(success=False, stdout="", stderr=error_msg, returncode=1, error=error_msg)


def _mock_both_info(
    shell: MockShell,
    source_stdout: str | None = None,
    delta_stdout: str | None = None,
    source_fail: str | None = None,
    delta_fail: str | None = None,
    **delta_info_kwargs: object,
) -> None:
    """Register expectations for both ``qemu-img info`` commands.

    The source command always uses ``--force-share``; the delta does not.
    """
    # source (always has --force-share)
    src_result = _fail(source_fail) if source_fail else _success(source_stdout or _info_json())
    shell.expect(r"qemu-img info --force-share.*").returns(src_result)

    # delta (NO --force-share — use more specific pattern)
    dlt_stdout = delta_stdout if delta_stdout is not None else _info_json(**delta_info_kwargs)  # type: ignore[arg-type]
    dlt_result = _fail(delta_fail) if delta_fail else _success(dlt_stdout)
    shell.expect(r"qemu-img info --output=json.*" + _DELTA).returns(dlt_result)


def _mock_compare(
    shell: MockShell, *, success: bool = True, error_msg: str = "Images differ"
) -> None:
    """Register a ``qemu-img compare`` expectation."""
    result = (
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
        if success
        else ShellResult(
            success=False,
            stdout="",
            stderr=error_msg,
            returncode=1,
            error=error_msg,
        )
    )
    shell.expect(r"qemu-img compare -q --force-share.*").returns(result)


# ===================================================================
# Tests
# ===================================================================


@pytest.mark.unit
def test_off_mode_returns_none_no_shell_calls() -> None:
    """verify_mode='off' returns None and issues NO shell commands."""
    shell = MockShell()
    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="off",
    )
    assert result is None
    # off mode short-circuits before any shell call — if a command had
    # been issued, the MockShell (with zero expectations) would raise
    # "No mock configured", causing a different error.


# ── Source / delta info failures ────────────────────────────────────────


@pytest.mark.unit
def test_source_info_failure_returns_error() -> None:
    """Source info failure returns a descriptive error."""
    shell = MockShell()
    _mock_both_info(shell, source_fail="Permission denied")

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="metadata",
    )
    assert result is not None
    assert result.startswith("verification failed: ")
    assert "cannot get source info" in result
    assert "Permission denied" in result


@pytest.mark.unit
def test_delta_info_failure_returns_error() -> None:
    """Delta info failure returns a descriptive error."""
    shell = MockShell()
    _mock_both_info(shell, delta_fail="No such file")

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="metadata",
    )
    assert result is not None
    assert result.startswith("verification failed: ")
    assert "cannot get delta info" in result
    assert "No such file" in result


# ── JSON parse failures ─────────────────────────────────────────────────


@pytest.mark.unit
def test_source_json_parse_failure_returns_error() -> None:
    """Source stdout is not valid JSON → parse error.

    Both info commands succeed, but the source JSON is malformed.
    """
    shell = MockShell()
    _mock_both_info(shell, source_stdout="not json")

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="metadata",
    )
    assert result is not None
    assert result.startswith("verification failed: ")
    assert "cannot parse source info JSON" in result


@pytest.mark.unit
def test_delta_json_parse_failure_returns_error() -> None:
    """Delta stdout is not valid JSON → parse error."""
    shell = MockShell()
    _mock_both_info(shell, delta_stdout="not json either")

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="metadata",
    )
    assert result is not None
    assert result.startswith("verification failed: ")
    assert "cannot parse delta info JSON" in result


# ── Format check ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_non_qcow2_format_fails_verification() -> None:
    """Delta format != 'qcow2' returns a format error."""
    shell = MockShell()
    _mock_both_info(shell, fmt="raw")

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="metadata",
    )
    assert result is not None
    assert result.startswith("verification failed: ")
    assert "expected format qcow2, got raw" in result


# ── Virtual-size mismatch ───────────────────────────────────────────────


@pytest.mark.unit
def test_virtual_size_mismatch_fails_verification() -> None:
    """Source and delta virtual-size must match exactly."""
    shell = MockShell()
    _mock_both_info(shell, virtual_size=134217728)  # delta differs

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="metadata",
    )
    assert result is not None
    assert result.startswith("verification failed: ")
    assert "virtual-size mismatch" in result
    assert "expected=67108864" in result
    assert "got=134217728" in result


# ── Backing-filename check ──────────────────────────────────────────────


@pytest.mark.unit
def test_wrong_backing_file_fails_verification() -> None:
    """Backing-filename mismatch fails BEFORE any compare command.

    Uses verify='hash' tier to prove the backing check short-circuits
    before qemu-img compare is ever issued.
    """
    shell = MockShell()
    _mock_both_info(shell, backing_filename="/wrong/path.qcow2")

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=100 * 1024 * 1024,
        verify_mode="hash",
    )
    assert result is not None
    assert result.startswith("verification failed: ")
    assert "backing-filename mismatch" in result
    assert _BACKING in result
    assert "/wrong/path.qcow2" in result

    # No compare expectation was registered → if the function tried to
    # call qemu-img compare, it would fail with "No mock configured".
    # Our test already passed, proving backing check short-circuited.


# ── Barrier checks ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_delta_within_barrier_passes() -> None:
    """actual-size within dirty_bytes×2 + 64 MiB barrier passes."""
    dirty_bytes = 100 * 1024 * 1024  # 100 MiB
    actual_size = 150 * 1024 * 1024  # 150 MiB < 100×2 + 64 = 264 MiB

    shell = MockShell()
    _mock_both_info(shell, actual_size=actual_size)

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=dirty_bytes,
        verify_mode="metadata",
    )
    assert result is None


@pytest.mark.unit
def test_full_size_incremental_fails_barrier() -> None:
    """actual-size exceeding the barrier fails with regression message."""
    dirty_bytes = 1 * 1024 * 1024  # 1 MiB
    actual_size = 200 * 1024 * 1024  # 200 MiB ≫ 66 MiB barrier

    shell = MockShell()
    _mock_both_info(shell, actual_size=actual_size)

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=dirty_bytes,
        verify_mode="metadata",
    )
    assert result is not None
    assert result.startswith("verification failed: ")
    assert "exceeds dirty-data barrier" in result
    assert "engine regressed to full copy" in result


@pytest.mark.unit
def test_barrier_slack_absorbs_qcow2_metadata_exact() -> None:
    """dirty_bytes=0, actual-size=64 MiB exactly → passes."""
    slack = 64 * 1024 * 1024

    shell = MockShell()
    _mock_both_info(shell, actual_size=slack)

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="metadata",
    )
    assert result is None


@pytest.mark.unit
def test_barrier_slack_exceeded_by_one_byte_fails() -> None:
    """dirty_bytes=0, actual-size=64 MiB + 1 → fails."""
    slack = 64 * 1024 * 1024

    shell = MockShell()
    _mock_both_info(shell, actual_size=slack + 1)

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="metadata",
    )
    assert result is not None
    assert "exceeds dirty-data barrier" in result


# ── Hash tier ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_hash_tier_logs_live_source_warning(caplog: pytest.LogCaptureFixture) -> None:
    """verify='hash' logs a WARNING about live-source caveat before compare."""
    shell = MockShell()
    _mock_both_info(shell)
    _mock_compare(shell, success=True)

    with caplog.at_level(logging.WARNING):
        result = verify_bitmap_incremental(
            shell=shell,
            source_path=_SRC,
            delta_path=_DELTA,
            expected_backing=_BACKING,
            dirty_bytes=0,
            verify_mode="hash",
        )

    assert result is None
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("results may be unreliable" in w for w in warnings)


@pytest.mark.unit
def test_hash_tier_compare_failure_mismatch_error() -> None:
    """Hash-tier compare failure (not lock-related) → mismatch error."""
    shell = MockShell()
    _mock_both_info(shell)
    _mock_compare(shell, success=False, error_msg="Content mismatch at offset 0x1000")

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="hash",
    )
    assert result is not None
    assert result.startswith("verification failed: ")
    assert "content comparison mismatch" in result
    assert "Content mismatch at offset 0x1000" in result


# ── Full tier ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_full_tier_compare_failure_mismatch_error() -> None:
    """Full-tier compare failure (not lock-related) → mismatch error."""
    shell = MockShell()
    _mock_both_info(shell)
    _mock_compare(shell, success=False, error_msg="Images differ")

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="full",
    )
    assert result is not None
    assert result.startswith("verification failed: ")
    assert "content comparison mismatch" in result
    assert "Images differ" in result


# ── Lock-conflict variant ───────────────────────────────────────────────


@pytest.mark.unit
def test_lock_conflict_error_detected() -> None:
    """Lock/shared error in compare → suggests verify='metadata'."""
    shell = MockShell()
    _mock_both_info(shell)
    _mock_compare(shell, success=False, error_msg="Failed to get shared lock on image")

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="full",
    )
    assert result is not None
    assert result.startswith("verification failed: ")
    assert "lock conflict" in result
    assert "use verify='metadata' for live sources" in result


# ── metadata / check tiers do NOT run compare ───────────────────────────


@pytest.mark.unit
def test_metadata_tier_does_not_run_compare() -> None:
    """verify='metadata' runs (a)–(d) only — no qemu-img compare."""
    shell = MockShell()
    _mock_both_info(shell)
    # No compare mock → calling compare would fail.

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="metadata",
    )
    assert result is None


@pytest.mark.unit
def test_check_tier_does_not_run_compare() -> None:
    """verify='check' behaves like metadata — no compare."""
    shell = MockShell()
    _mock_both_info(shell)

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="check",
    )
    assert result is None


# ── Error string prefix ─────────────────────────────────────────────────


@pytest.mark.unit
def test_all_errors_start_with_verification_failed_prefix() -> None:
    """Every error path prepends 'verification failed: '."""
    shell = MockShell()
    _mock_both_info(shell, fmt="raw")

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=0,
        verify_mode="metadata",
    )
    assert result is not None
    assert result.startswith("verification failed: ")


# ── Happy paths ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_metadata_tier_happy_path_returns_none() -> None:
    """Well-formed delta, correct backing, within-barrier → None."""
    shell = MockShell()
    _mock_both_info(shell)

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=10 * 1024 * 1024,
        verify_mode="metadata",
    )
    assert result is None


@pytest.mark.unit
def test_hash_tier_happy_path_returns_none() -> None:
    """Hash tier + successful compare → None."""
    shell = MockShell()
    _mock_both_info(shell)
    _mock_compare(shell, success=True)

    result = verify_bitmap_incremental(
        shell=shell,
        source_path=_SRC,
        delta_path=_DELTA,
        expected_backing=_BACKING,
        dirty_bytes=10 * 1024 * 1024,
        verify_mode="hash",
    )
    assert result is None
