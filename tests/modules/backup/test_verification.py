"""Unit tests for backup verification logic (``verify_backup``).

Tests cover the three verification levels (``off``, ``metadata``,
``full``) using ``MockShell`` to simulate ``qemu-img info`` and
``qemu-img compare`` commands.  No real I/O occurs — all shell calls
are intercepted by ``MockShell``.

Verification levels (``TargetConfig.verify``):
- ``"off"``: no verification — returns ``None`` immediately.
- ``"metadata"``: ``qemu-img info`` consistency check (format,
  virtual-size exact match, actual-size ±10% tolerance).
- ``"full"``: metadata check + ``qemu-img compare -q`` byte-level
  comparison (timeout 7200s).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from qsnap.models.results import ShellResult
from qsnap.modules.backup.verification import verify_backup

# ── Constants ─────────────────────────────────────────────────────────────

_SOURCE_PATH = "/source.qcow2"
_TARGET_PATH = "/target.qcow2"

_QCOW2_INFO = {
    "format": "qcow2",
    "virtual-size": 1073741824,
    "actual-size": 1048576,
}


# ── Helpers ───────────────────────────────────────────────────────────────


def _ok_result(stdout: str = "") -> ShellResult:
    """A successful ShellResult with the given stdout."""
    return ShellResult(
        success=True,
        stdout=stdout,
        stderr="",
        returncode=0,
        error=None,
    )


def _fail_result(error: str = "command failed") -> ShellResult:
    """A failed ShellResult."""
    return ShellResult(
        success=False,
        stdout="",
        stderr=error,
        returncode=1,
        error=error,
    )


# ──────────────────────────────────────────────────────────────────────────
# Metadata verification
# ──────────────────────────────────────────────────────────────────────────


def test_metadata_verification_passes(mock_shell):
    """When both source and target return qcow2 JSON with matching
    virtual-size and actual-size within 10% tolerance,
    ``verify_backup(..., "metadata")`` returns ``None``.
    """
    mock_shell.expect(r"qemu-img info").returns(
        _ok_result(stdout=json.dumps(_QCOW2_INFO))
    )

    result = verify_backup(
        mock_shell, _SOURCE_PATH, _TARGET_PATH, "metadata"
    )

    assert result is None


def test_metadata_verification_wrong_format(mock_shell):
    """When the target format is ``"raw"`` instead of ``"qcow2"``,
    ``verify_backup`` returns an error string containing ``"format"``.
    """
    source_info = dict(_QCOW2_INFO)
    target_info = {**_QCOW2_INFO, "format": "raw"}

    mock_shell.expect(r"qemu-img info.*source\.qcow2").returns(
        _ok_result(stdout=json.dumps(source_info))
    )
    mock_shell.expect(r"qemu-img info.*target\.qcow2").returns(
        _ok_result(stdout=json.dumps(target_info))
    )

    result = verify_backup(
        mock_shell, _SOURCE_PATH, _TARGET_PATH, "metadata"
    )

    assert result is not None
    assert "format" in result


def test_metadata_verification_size_mismatch(mock_shell):
    """When the target virtual-size differs from the source,
    ``verify_backup`` returns an error string containing
    ``"virtual-size"`` or ``"size"``.
    """
    source_info = dict(_QCOW2_INFO)
    target_info = {**_QCOW2_INFO, "virtual-size": 2147483648}

    mock_shell.expect(r"qemu-img info.*source\.qcow2").returns(
        _ok_result(stdout=json.dumps(source_info))
    )
    mock_shell.expect(r"qemu-img info.*target\.qcow2").returns(
        _ok_result(stdout=json.dumps(target_info))
    )

    result = verify_backup(
        mock_shell, _SOURCE_PATH, _TARGET_PATH, "metadata"
    )

    assert result is not None
    assert "virtual-size" in result or "size" in result


# ──────────────────────────────────────────────────────────────────────────
# Full verification
# ──────────────────────────────────────────────────────────────────────────


def test_full_verification_passes(mock_shell):
    """When metadata checks pass and ``qemu-img compare`` succeeds,
    ``verify_backup(..., "full")`` returns ``None``.
    """
    mock_shell.expect(r"qemu-img info").returns(
        _ok_result(stdout=json.dumps(_QCOW2_INFO))
    )
    mock_shell.expect(r"qemu-img compare").returns(_ok_result())

    result = verify_backup(
        mock_shell, _SOURCE_PATH, _TARGET_PATH, "full"
    )

    assert result is None


def test_full_verification_detects_corruption(mock_shell):
    """When ``qemu-img compare`` fails (non-zero exit),
    ``verify_backup(..., "full")`` returns an error string mentioning
    the comparison failure.
    """
    mock_shell.expect(r"qemu-img info").returns(
        _ok_result(stdout=json.dumps(_QCOW2_INFO))
    )
    mock_shell.expect(r"qemu-img compare").returns(_fail_result())

    result = verify_backup(
        mock_shell, _SOURCE_PATH, _TARGET_PATH, "full"
    )

    assert result is not None
    assert "compare" in result or "comparison" in result


# ──────────────────────────────────────────────────────────────────────────
# Verification off
# ──────────────────────────────────────────────────────────────────────────


def test_no_verification_when_verify_off(mock_shell):
    """When ``verify_mode`` is ``"off"``, ``verify_backup`` returns
    ``None`` immediately without making any shell calls.
    """
    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        result = verify_backup(
            mock_shell, _SOURCE_PATH, _TARGET_PATH, "off"
        )

    assert result is None
    assert shell_spy.call_count == 0


# ──────────────────────────────────────────────────────────────────────────
# Risk / configuration
# ──────────────────────────────────────────────────────────────────────────


def test_risk_full_verification_timeout_7200s(mock_shell):
    """When ``verify_mode`` is ``"full"``, the ``qemu-img compare``
    command is called with ``timeout=7200`` (2 hours).
    """
    mock_shell.expect(r"qemu-img info").returns(
        _ok_result(stdout=json.dumps(_QCOW2_INFO))
    )
    mock_shell.expect(r"qemu-img compare").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        verify_backup(mock_shell, _SOURCE_PATH, _TARGET_PATH, "full")

    # Find the compare call and verify its timeout
    compare_calls = [
        call
        for call in shell_spy.call_args_list
        if "qemu-img compare" in " ".join(call.args[0])
    ]
    assert len(compare_calls) == 1
    assert compare_calls[0].kwargs.get("timeout") == 7200


def test_risk_full_verification_not_default(make_target):
    """The default ``TargetConfig.verify`` is ``"metadata"``, not
    ``"full"`` — full verification is opt-in due to its cost.
    """
    target = make_target()

    assert target.verify == "metadata"
    assert target.verify != "full"
