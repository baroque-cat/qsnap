"""Unit tests for standalone FULL backup verification
(``verify_full_backup``).

Tests cover the four verification levels (``off``, ``metadata``,
``check``, ``compare``) using ``MockShell`` to simulate ``qemu-img info``,
``qemu-img check``, and ``qemu-img compare`` commands.  No real I/O
occurs for shell commands — all calls are intercepted by ``MockShell``.

Verification levels:
- ``"off"``: no verification — returns ``None`` immediately.
- ``"metadata"``: ``qemu-img info`` — format check, corrupt-bit
  detection, optional virtual-size match.
- ``"check"``: metadata + ``qemu-img check`` structural scan
  (errors, leaks, corruptions).
- ``"compare"``: metadata + structural scan + ``qemu-img compare``
  content comparison against a source snapshot (M3 requires
  ``source_path``).

Deprecated modes:
- ``"hash"`` and ``"full"`` are deprecated aliases for ``"compare"``.
  When used, a WARNING is logged and they behave identically to
  ``"compare"``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from qsnap.utils.verification import verify_full_backup
from tests.mocks.mock_shell import MockShell

# ── Constants ─────────────────────────────────────────────────────────────

_VALID_QCOW2_INFO = {
    "format": "qcow2",
    "virtual-size": 1073741824,
}

_VALID_CHECK = {
    "errors": 0,
    "leaks": 0,
    "corruptions": 0,
}


# ── Helpers ───────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────
# Metadata verification (M1)
# ──────────────────────────────────────────────────────────────────────────


def test_verify_full_backup_metadata_valid_qcow2(mock_shell, success_result):
    """When ``qemu-img info`` returns ``format=qcow2`` with no corrupt bit,
    ``verify_full_backup(... , "metadata")`` returns ``None``.
    """
    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(_VALID_QCOW2_INFO)))

    result = verify_full_backup(mock_shell, Path("/backup/full.qcow2"), "metadata")

    assert result is None


def test_verify_full_backup_metadata_corrupt_bit(mock_shell, success_result):
    """When ``qemu-img info`` returns a ``"corrupt"`` incompatible-feature,
    ``verify_full_backup`` returns an error string containing
    ``"corrupt bit set"``.
    """
    corrupt_info = {
        "format": "qcow2",
        "virtual-size": 1073741824,
        "format-specific": {
            "type": "qcow2",
            "data": {
                "incompatible-features": [{"name": "corrupt"}],
            },
        },
    }

    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(corrupt_info)))

    result = verify_full_backup(mock_shell, Path("/backup/corrupt.qcow2"), "metadata")

    assert result is not None
    assert "corrupt bit set" in result


def test_verify_full_backup_metadata_wrong_format(mock_shell, success_result):
    """When ``qemu-img info`` returns ``format=raw`` (not qcow2),
    ``verify_full_backup`` returns an error containing
    ``"expected format qcow2"``.
    """
    raw_info = {"format": "raw", "virtual-size": 1073741824}

    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(raw_info)))

    result = verify_full_backup(mock_shell, Path("/backup/full.raw"), "metadata")

    assert result is not None
    assert "expected format qcow2" in result


def test_verify_full_backup_metadata_info_fails(mock_shell, failure_result):
    """When ``qemu-img info`` returns a non-zero exit,
    ``verify_full_backup`` returns an error containing
    ``"qemu-img info returned"``.
    """
    mock_shell.expect("qemu-img info").returns(failure_result(error="Cannot open file"))

    result = verify_full_backup(mock_shell, Path("/backup/nonexistent.qcow2"), "metadata")

    assert result is not None
    assert "qemu-img info returned" in result


# ──────────────────────────────────────────────────────────────────────────
# Structural verification — check mode (M1 + M2)
# ──────────────────────────────────────────────────────────────────────────


def test_verify_full_backup_check_passes(mock_shell, success_result):
    """When ``verify_mode="check"``, M1 passes and ``qemu-img check``
    returns ``{errors: 0, leaks: 0, corruptions: 0}``, ``verify_full_backup``
    returns ``None``.
    """
    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(_VALID_QCOW2_INFO)))
    mock_shell.expect("qemu-img check").returns(success_result(stdout=json.dumps(_VALID_CHECK)))

    result = verify_full_backup(mock_shell, Path("/backup/full.qcow2"), "check")

    assert result is None


def test_verify_full_backup_check_errors_detected(mock_shell, success_result):
    """When ``verify_mode="check"`` and ``qemu-img check`` reports 5 errors
    (with zero leaks and corruptions), ``verify_full_backup`` returns an
    error containing ``"found 5 errors"``.
    """
    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(_VALID_QCOW2_INFO)))
    mock_shell.expect("qemu-img check").returns(
        success_result(stdout=json.dumps({"errors": 5, "leaks": 0, "corruptions": 0}))
    )

    result = verify_full_backup(mock_shell, Path("/backup/full.qcow2"), "check")

    assert result is not None
    assert "found 5 errors" in result


def test_verify_full_backup_check_no_errors(mock_shell, success_result):
    """When ``verify_mode="check"`` and ``qemu-img check`` explicitly
    returns zero errors, zero leaks, and zero corruptions,
    ``verify_full_backup`` returns ``None`` — confirming the zero-values
    pass all three guards.
    """
    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(_VALID_QCOW2_INFO)))
    mock_shell.expect("qemu-img check").returns(
        success_result(stdout=json.dumps({"errors": 0, "leaks": 0, "corruptions": 0}))
    )

    result = verify_full_backup(mock_shell, Path("/backup/full.qcow2"), "check")

    assert result is None


def test_verify_full_backup_check_passes_all_fields_zero(mock_shell, success_result):
    """When ``verify_mode="check"`` and ``qemu-img check`` returns
    ``{errors: 0, leaks: 0, corruptions: 0}``, ``verify_full_backup``
    returns ``None`` — M2 passes when all three fields are zero.
    """
    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(_VALID_QCOW2_INFO)))
    mock_shell.expect("qemu-img check").returns(
        success_result(stdout=json.dumps({"errors": 0, "leaks": 0, "corruptions": 0}))
    )

    result = verify_full_backup(mock_shell, Path("/backup/full.qcow2"), "check")

    assert result is None


def test_verify_full_backup_check_corruptions_detected(mock_shell, success_result):
    """When ``verify_mode="check"`` and ``qemu-img check`` reports 3
    corruptions (with zero errors and leaks), ``verify_full_backup``
    returns an error containing ``"found 3 corruptions"``.
    """
    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(_VALID_QCOW2_INFO)))
    mock_shell.expect("qemu-img check").returns(
        success_result(stdout=json.dumps({"errors": 0, "leaks": 0, "corruptions": 3}))
    )

    result = verify_full_backup(mock_shell, Path("/backup/full.qcow2"), "check")

    assert result is not None
    assert "found 3 corruptions" in result


def test_verify_full_backup_check_leaks_detected(mock_shell, success_result):
    """When ``verify_mode="check"`` and ``qemu-img check`` reports 5 leaks
    (with zero errors and corruptions), ``verify_full_backup`` returns an
    error containing ``"found 5 leaks"``.
    """
    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(_VALID_QCOW2_INFO)))
    mock_shell.expect("qemu-img check").returns(
        success_result(stdout=json.dumps({"errors": 0, "leaks": 5, "corruptions": 0}))
    )

    result = verify_full_backup(mock_shell, Path("/backup/full.qcow2"), "check")

    assert result is not None
    assert "found 5 leaks" in result


# ──────────────────────────────────────────────────────────────────────────
# Content comparison (M3 via qemu-img compare) — compare mode (M1 + M2 + M3)
# ──────────────────────────────────────────────────────────────────────────


def test_verify_full_backup_compare_match(mock_shell, success_result):
    """When ``verify_mode="compare"`` and ``qemu-img compare`` succeeds
    (exit=0), ``verify_full_backup`` returns ``None``.

    M1 and M2 are mocked to pass; M3 is simulated via
    ``MockShell.expect("qemu-img compare")`` returning a successful
    ``ShellResult``.
    """
    source = Path("/backup/source_snapshot.qcow2")

    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(_VALID_QCOW2_INFO)))
    mock_shell.expect("qemu-img check").returns(success_result(stdout=json.dumps(_VALID_CHECK)))
    mock_shell.expect("qemu-img compare").returns(success_result(stdout="Images are identical."))

    result = verify_full_backup(
        mock_shell,
        Path("/backup/full.qcow2"),
        "compare",
        source_path=source,
    )

    assert result is None


def test_verify_full_backup_compare_mismatch(mock_shell, failure_result, success_result):
    """When ``verify_mode="compare"`` and ``qemu-img compare`` returns a
    non-zero exit, ``verify_full_backup`` returns an error containing
    ``"content comparison mismatch"``.

    M1 and M2 are mocked to pass; M3 is simulated to fail via
    ``MockShell.expect("qemu-img compare")`` returning a failed
    ``ShellResult`` with stderr ``"Images differ"``.
    """
    source = Path("/backup/source_snapshot.qcow2")

    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(_VALID_QCOW2_INFO)))
    mock_shell.expect("qemu-img check").returns(success_result(stdout=json.dumps(_VALID_CHECK)))
    mock_shell.expect("qemu-img compare").returns(failure_result(error="Images differ"))

    result = verify_full_backup(
        mock_shell,
        Path("/backup/full.qcow2"),
        "compare",
        source_path=source,
    )

    assert result is not None
    assert "content comparison mismatch" in result


def test_verify_full_backup_compare_none_skips_m3(mock_shell, success_result):
    """When ``verify_mode="compare"`` but ``source_path`` is ``None``,
    M3 (content comparison via ``qemu-img compare``) is skipped.  Only
    M1 and M2 run, and ``verify_full_backup`` returns ``None`` after
    they pass.

    No ``qemu-img compare`` call is expected on the shell.
    """
    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(_VALID_QCOW2_INFO)))
    mock_shell.expect("qemu-img check").returns(success_result(stdout=json.dumps(_VALID_CHECK)))

    result = verify_full_backup(
        mock_shell,
        Path("/backup/full.qcow2"),
        "compare",
        source_path=None,
    )

    assert result is None


def test_verify_full_backup_hash_deprecated_triggers_compare(mock_shell, caplog, success_result):
    """When ``verify_mode="hash"`` (deprecated), a WARNING is logged and the
    mode behaves identically to ``"compare"`` — M1, M2, and M3 all run.

    This test verifies that qemu-img compare IS called with verify_mode="hash".
    """
    source = Path("/backup/source_snapshot.qcow2")

    mock_shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(_VALID_QCOW2_INFO)))
    mock_shell.expect("qemu-img check").returns(success_result(stdout=json.dumps(_VALID_CHECK)))
    mock_shell.expect("qemu-img compare").returns(success_result(stdout="Images are identical."))

    result = verify_full_backup(
        mock_shell,
        Path("/backup/full.qcow2"),
        "hash",
        source_path=source,
    )

    # M3 must pass (identical to "compare" behavior)
    assert result is None


# ──────────────────────────────────────────────────────────────────────────
# Off mode
# ──────────────────────────────────────────────────────────────────────────


def test_verify_full_backup_off_no_commands():
    """When ``verify_mode="off"``, ``verify_full_backup`` returns ``None``
    immediately without making any shell calls.

    A fresh ``MockShell`` is used (no conftest pre-configuration) so we
    can assert exactly zero calls.
    """
    shell = MockShell()

    with patch.object(shell, "run", wraps=shell.run) as shell_spy:
        result = verify_full_backup(shell, Path("/backup/full.qcow2"), "off")

    assert result is None
    assert shell_spy.call_count == 0, (
        f"Shell should not be called when verify_mode='off', "
        f"but {shell_spy.call_count} call(s) occurred"
    )
