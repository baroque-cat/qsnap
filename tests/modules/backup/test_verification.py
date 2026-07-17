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

import hashlib
import json
from unittest.mock import patch

from qsnap.models.results import ShellResult
from qsnap.modules.backup.verification import _file_sha256, verify_backup

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
    mock_shell.expect(r"qemu-img info").returns(_ok_result(stdout=json.dumps(_QCOW2_INFO)))

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        result = verify_backup(mock_shell, _SOURCE_PATH, _TARGET_PATH, "metadata")

    assert result is None

    # Verify source-side qemu-img info includes --force-share (design D5)
    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
    source_cmds = [cmd for cmd in all_cmds if "qemu-img info" in cmd and _SOURCE_PATH in cmd]
    target_cmds = [cmd for cmd in all_cmds if "qemu-img info" in cmd and _TARGET_PATH in cmd]
    assert len(source_cmds) == 1
    assert "--force-share" in source_cmds[0], (
        f"Source-side qemu-img info must include --force-share, got: {source_cmds[0]}"
    )
    # Target-side info does NOT use --force-share (backup is not locked)
    assert len(target_cmds) == 1
    assert "--force-share" not in target_cmds[0], (
        f"Target-side qemu-img info must NOT include --force-share, got: {target_cmds[0]}"
    )


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

    result = verify_backup(mock_shell, _SOURCE_PATH, _TARGET_PATH, "metadata")

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

    result = verify_backup(mock_shell, _SOURCE_PATH, _TARGET_PATH, "metadata")

    assert result is not None
    assert "virtual-size" in result or "size" in result


# ──────────────────────────────────────────────────────────────────────────
# Full verification
# ──────────────────────────────────────────────────────────────────────────


def test_full_verification_passes(mock_shell):
    """When metadata checks pass and ``qemu-img compare`` succeeds,
    ``verify_backup(..., "full")`` returns ``None``.
    """
    mock_shell.expect(r"qemu-img info").returns(_ok_result(stdout=json.dumps(_QCOW2_INFO)))
    mock_shell.expect(r"qemu-img compare").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        result = verify_backup(mock_shell, _SOURCE_PATH, _TARGET_PATH, "full")

    assert result is None

    # qemu-img compare must NOT include --force-share (data-copying op, design D5)
    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
    compare_cmds = [cmd for cmd in all_cmds if "qemu-img compare" in cmd]
    assert len(compare_cmds) == 1
    assert "--force-share" not in compare_cmds[0], (
        f"qemu-img compare must NOT include --force-share, got: {compare_cmds[0]}"
    )
    # Source-side info uses --force-share
    source_info_cmds = [cmd for cmd in all_cmds if "qemu-img info" in cmd and _SOURCE_PATH in cmd]
    assert len(source_info_cmds) == 1
    assert "--force-share" in source_info_cmds[0]


def test_full_verification_detects_corruption(mock_shell):
    """When ``qemu-img compare`` fails (non-zero exit),
    ``verify_backup(..., "full")`` returns an error string mentioning
    the comparison failure.
    """
    mock_shell.expect(r"qemu-img info").returns(_ok_result(stdout=json.dumps(_QCOW2_INFO)))
    mock_shell.expect(r"qemu-img compare").returns(_fail_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        result = verify_backup(mock_shell, _SOURCE_PATH, _TARGET_PATH, "full")

    assert result is not None
    assert "compare" in result or "comparison" in result

    # qemu-img compare must NOT include --force-share (data-copying op, design D5)
    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
    compare_cmds = [cmd for cmd in all_cmds if "qemu-img compare" in cmd]
    assert len(compare_cmds) == 1
    assert "--force-share" not in compare_cmds[0], (
        f"qemu-img compare must NOT include --force-share, got: {compare_cmds[0]}"
    )
    # Source-side info uses --force-share
    source_info_cmds = [cmd for cmd in all_cmds if "qemu-img info" in cmd and _SOURCE_PATH in cmd]
    assert len(source_info_cmds) == 1
    assert "--force-share" in source_info_cmds[0]


# ──────────────────────────────────────────────────────────────────────────
# Verification off
# ──────────────────────────────────────────────────────────────────────────


def test_no_verification_when_verify_off(mock_shell):
    """When ``verify_mode`` is ``"off"``, ``verify_backup`` returns
    ``None`` immediately without making any shell calls.
    """
    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        result = verify_backup(mock_shell, _SOURCE_PATH, _TARGET_PATH, "off")

    assert result is None
    assert shell_spy.call_count == 0


# ──────────────────────────────────────────────────────────────────────────
# Risk / configuration
# ──────────────────────────────────────────────────────────────────────────


def test_risk_full_verification_timeout_7200s(mock_shell):
    """When ``verify_mode`` is ``"full"``, the ``qemu-img compare``
    command is called with ``timeout=7200`` (2 hours).
    """
    mock_shell.expect(r"qemu-img info").returns(_ok_result(stdout=json.dumps(_QCOW2_INFO)))
    mock_shell.expect(r"qemu-img compare").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        verify_backup(mock_shell, _SOURCE_PATH, _TARGET_PATH, "full")

    # Find the compare call and verify its timeout
    compare_calls = [
        call for call in shell_spy.call_args_list if "qemu-img compare" in " ".join(call.args[0])
    ]
    assert len(compare_calls) == 1
    assert compare_calls[0].kwargs.get("timeout") == 7200

    # qemu-img compare must NOT include --force-share
    compare_cmd = " ".join(compare_calls[0].args[0])
    assert "--force-share" not in compare_cmd, (
        f"qemu-img compare must NOT include --force-share, got: {compare_cmd}"
    )


def test_risk_full_verification_not_default(make_target):
    """The default ``TargetConfig.verify`` is ``"metadata"``, not
    ``"full"`` — full verification is opt-in due to its cost.
    """
    target = make_target()

    assert target.verify == "metadata"
    assert target.verify != "full"


# ──────────────────────────────────────────────────────────────────────────
# Hash verification (verify_mode="hash")
# ──────────────────────────────────────────────────────────────────────────


def test_hash_verification_match_passes(mock_shell, tmp_path):
    """When ``verify_mode="hash"`` and the target file's SHA-256 matches
    ``expected_hash``, ``verify_backup`` returns ``None`` (pass).

    A real temp file is created with known content so ``_file_sha256`` can
    compute its actual digest.  The ``qemu-img info`` mock returns valid
    qcow2 metadata so the preceding metadata check also passes.
    """
    target_file = tmp_path / "target.qcow2"
    content = b"qsnap backup hash verification test content"
    target_file.write_bytes(content)
    expected_hash = hashlib.sha256(content).hexdigest()

    mock_shell.expect(r"qemu-img info").returns(_ok_result(stdout=json.dumps(_QCOW2_INFO)))

    result = verify_backup(
        mock_shell,
        _SOURCE_PATH,
        str(target_file),
        "hash",
        expected_hash=expected_hash,
    )

    assert result is None


def test_hash_verification_mismatch_fails(mock_shell, tmp_path):
    """When ``verify_mode="hash"`` and the target file's SHA-256 does NOT
    match ``expected_hash``, ``verify_backup`` returns an error string
    containing ``"hash"``.
    """
    target_file = tmp_path / "target.qcow2"
    target_file.write_bytes(b"actual backup content")

    wrong_hash = "b" * 64  # does not match the real hash

    mock_shell.expect(r"qemu-img info").returns(_ok_result(stdout=json.dumps(_QCOW2_INFO)))

    result = verify_backup(
        mock_shell,
        _SOURCE_PATH,
        str(target_file),
        "hash",
        expected_hash=wrong_hash,
    )

    assert result is not None
    assert "hash" in result.lower()


def test_hash_verification_skipped_when_no_expected_hash(mock_shell):
    """When ``verify_mode="hash"`` but ``expected_hash`` is ``None``, hash
    verification is skipped — ``verify_backup`` returns ``None`` after the
    metadata check passes.

    No real file is needed because ``_file_sha256`` is never called.
    """
    mock_shell.expect(r"qemu-img info").returns(_ok_result(stdout=json.dumps(_QCOW2_INFO)))

    result = verify_backup(
        mock_shell,
        _SOURCE_PATH,
        _TARGET_PATH,
        "hash",
        expected_hash=None,
    )

    assert result is None


def test_file_sha256_computes_hash(tmp_path):
    """``_file_sha256(path)`` returns a 64-character lowercase hex string
    matching the SHA-256 digest of the file's contents.

    Uses a real temp file with known content to verify the actual hash
    computation (no mocking).
    """
    test_file = tmp_path / "test_data.bin"
    content = b"qsnap test content for sha256 hashing"
    test_file.write_bytes(content)

    result = _file_sha256(test_file)

    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)
    assert result == hashlib.sha256(content).hexdigest()


def test_metadata_mode_unchanged_after_hash_addition(mock_shell):
    """``verify_backup(verify_mode="metadata")`` still works as before —
    the hash branch is NOT entered even when ``expected_hash`` is provided.

    Patches ``_file_sha256`` and asserts it was never called, proving the
    hash code path is exclusive to ``verify_mode="hash"``.
    """
    mock_shell.expect(r"qemu-img info").returns(_ok_result(stdout=json.dumps(_QCOW2_INFO)))

    with patch("qsnap.modules.backup.verification._file_sha256") as mock_sha:
        result = verify_backup(
            mock_shell,
            _SOURCE_PATH,
            _TARGET_PATH,
            "metadata",
            expected_hash="a" * 64,  # provided but must be ignored
        )

    assert result is None
    mock_sha.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────
# Force-share verification tests (design D5)
# ──────────────────────────────────────────────────────────────────────────


def test_source_side_info_uses_force_share_on_active_layer(mock_shell):
    """Source-side ``qemu-img info`` includes ``--force-share`` because
    the source may be the active layer of a running VM, which has an
    exclusive write lock (design D5).

    The target-side ``qemu-img info`` does NOT use ``--force-share``
    because the backup is not locked by a running VM.
    """
    mock_shell.expect(r"qemu-img info").returns(_ok_result(stdout=json.dumps(_QCOW2_INFO)))

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        result = verify_backup(mock_shell, _SOURCE_PATH, _TARGET_PATH, "metadata")

    assert result is None

    # Separate source-side and target-side info calls
    all_calls = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
    source_info_calls = [cmd for cmd in all_calls if "qemu-img info" in cmd and _SOURCE_PATH in cmd]
    target_info_calls = [cmd for cmd in all_calls if "qemu-img info" in cmd and _TARGET_PATH in cmd]

    assert len(source_info_calls) == 1, (
        f"Expected exactly 1 source-side qemu-img info call, got {len(source_info_calls)}"
    )
    assert "--force-share" in source_info_calls[0], (
        f"Source-side qemu-img info must include --force-share, got: {source_info_calls[0]}"
    )

    assert len(target_info_calls) == 1, (
        f"Expected exactly 1 target-side qemu-img info call, got {len(target_info_calls)}"
    )
    assert "--force-share" not in target_info_calls[0], (
        f"Target-side qemu-img info must NOT include --force-share, got: {target_info_calls[0]}"
    )


def test_full_verification_live_source_logs_warning(mock_shell):
    """When ``verify_mode="full"``, a WARNING is logged before
    ``qemu-img compare`` to alert the user that the compare may fail
    if the source is a live VM active layer (design D5).

    ``qemu-img compare`` is still executed without ``--force-share``
    because it is a data-copying operation.
    """
    mock_shell.expect(r"qemu-img info").returns(_ok_result(stdout=json.dumps(_QCOW2_INFO)))
    mock_shell.expect(r"qemu-img compare").returns(_ok_result())

    import logging
    from qsnap.modules.backup import verification as ver_module

    with patch.object(ver_module.logger, "warning") as mock_warning:
        result = verify_backup(mock_shell, _SOURCE_PATH, _TARGET_PATH, "full")

    assert result is None

    # Verify the warning was logged
    mock_warning.assert_called_once()
    warning_msg = mock_warning.call_args[0][0]
    assert "Full verification" in warning_msg or "qemu-img compare" in warning_msg
    assert "lock" in warning_msg.lower()
    assert "verify" in warning_msg.lower()


def test_full_verification_live_source_lock_conflict(mock_shell):
    """When ``verify_mode="full"`` and ``qemu-img compare`` on a locked
    active layer fails with a lock conflict, ``verify_backup`` returns an
    error string.

    Note: The current implementation returns a generic error message
    ("verification failed: data comparison mismatch"). It does NOT
    currently detect the specific lock-conflict condition or recommend
    ``verify=metadata``. This is the behavior being tested.
    """
    mock_shell.expect(r"qemu-img info").returns(_ok_result(stdout=json.dumps(_QCOW2_INFO)))
    mock_shell.expect(r"qemu-img compare").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="qemu-img: Could not open '/source.qcow2': Failed to get shared \"write\" lock",
            returncode=1,
            error="qemu-img: Could not open '/source.qcow2': Failed to get shared \"write\" lock",
        )
    )

    result = verify_backup(mock_shell, _SOURCE_PATH, _TARGET_PATH, "full")

    assert result is not None
    assert "verification failed" in result
    # The compare failed because of the lock conflict
    assert "compare" in result or "comparison" in result
