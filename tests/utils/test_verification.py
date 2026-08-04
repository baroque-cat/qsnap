"""Unit tests for qsnap.utils.verification — shared verification functions.

Tests verify that backup verification functions are importable from the
shared utility module (not from a domain sub-package), and that deprecated
verify_mode values are handled correctly.  Also covers the shared
``deep_verify_base_image`` and ``scan_backing_chain`` helpers added during
the dedup-bugfix-cleanup change.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.models.results import CommitResult, ShellResult
from qsnap.utils.verification import (
    deep_verify_base_image,
    scan_backing_chain,
    verify_full_backup,
)
from tests.mocks.mock_shell import MockShell

# ── Helpers ───────────────────────────────────────────────────────────────


_VALID_QCOW2_INFO = {
    "format": "qcow2",
    "virtual-size": 1073741824,
}

_VALID_CHECK = {
    "errors": 0,
    "leaks": 0,
    "corruptions": 0,
}


def test_verify_full_backup_imported_from_utils() -> None:
    """``verify_full_backup`` is importable from ``qsnap.utils.verification``
    and is callable.
    """
    assert callable(verify_full_backup)


# ── Deprecated verify="hash" handling ─────────────────────────────────────


def test_deprecated_hash_verify_mode_triggers_all_tiers(success_result) -> None:
    """When ``verify_mode="hash"`` (the now-deprecated tier), the function
    emits a WARNING and behaves like ``"compare"`` — running M1 (metadata),
    M2 (check), and M3 (compare) tiers.

    The deprecated alias is remapped to ``"compare"`` at the top of the
    function, so all three verification tiers execute.
    """
    shell = MockShell()
    shell.expect("qemu-img info").returns(success_result(stdout=json.dumps(_VALID_QCOW2_INFO)))
    shell.expect("qemu-img check").returns(success_result(stdout=json.dumps(_VALID_CHECK)))
    shell.expect("qemu-img compare").returns(success_result())

    with patch.object(shell, "run", wraps=shell.run) as shell_spy:
        result = verify_full_backup(
            shell,
            Path("/backup/full.qcow2"),
            "hash",
            source_path=Path("/backup/source.qcow2"),
        )

    assert result is None
    # All three tiers (M1 info, M2 check, M3 compare) should have run.
    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
    assert len(all_cmds) == 3, (
        f"Expected 3 calls (info+check+compare), but got {len(all_cmds)}: {all_cmds}"
    )
    assert "qemu-img info" in all_cmds[0]
    assert "qemu-img check" in all_cmds[1]
    assert "qemu-img compare" in all_cmds[2]


# ── deep_verify_base_image ─────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_deep_verify_base_image_passes_clean(clean_shell, success_result) -> None:
    """When ``qemu-img check`` returns zero corruptions/errors/leaks, the
    function returns ``None`` (pass).
    """
    clean_shell.expect("qemu-img check").returns(
        success_result(stdout=json.dumps({"corruptions": 0, "errors": 0, "leaks": 0}))
    )
    result = deep_verify_base_image(clean_shell, Path("/tmp/base.qcow2"))
    assert result is None


@pytest.mark.unit
@pytest.mark.mock
def test_deep_verify_base_image_fails_corruptions(clean_shell, success_result) -> None:
    """When ``qemu-img check`` returns non-zero corruptions, the function
    returns ``CommitResult(success=False)`` with an error containing
    "corruptions".
    """
    clean_shell.expect("qemu-img check").returns(
        success_result(stdout=json.dumps({"corruptions": 2, "errors": 0, "leaks": 0}))
    )
    result = deep_verify_base_image(clean_shell, Path("/tmp/base.qcow2"))
    assert isinstance(result, CommitResult)
    assert result.success is False
    assert "corruptions" in result.error
    assert "2" in result.error


@pytest.mark.unit
@pytest.mark.mock
def test_deep_verify_base_image_fails_errors(clean_shell, success_result) -> None:
    """When ``qemu-img check`` returns non-zero errors, the function returns
    ``CommitResult(success=False)`` with an error containing "errors".
    """
    clean_shell.expect("qemu-img check").returns(
        success_result(stdout=json.dumps({"corruptions": 0, "errors": 1, "leaks": 0}))
    )
    result = deep_verify_base_image(clean_shell, Path("/tmp/base.qcow2"))
    assert isinstance(result, CommitResult)
    assert result.success is False
    assert "errors" in result.error
    assert "1" in result.error


@pytest.mark.unit
@pytest.mark.mock
def test_deep_verify_base_image_qemu_img_check_fails(
    clean_shell,
    failure_result,
) -> None:
    """When ``shell.run()`` for ``qemu-img check`` returns ``success=False``,
    the function returns ``CommitResult(success=False)`` with an error
    containing "qemu-img check failed".
    """
    clean_shell.expect("qemu-img check").returns(failure_result(error="No space left on device"))
    result = deep_verify_base_image(clean_shell, Path("/tmp/base.qcow2"))
    assert isinstance(result, CommitResult)
    assert result.success is False
    assert "qemu-img check failed" in result.error


@pytest.mark.unit
@pytest.mark.mock
def test_deep_verify_base_image_json_parse_fails(clean_shell, success_result) -> None:
    """When ``qemu-img check`` returns invalid JSON, the function returns
    ``CommitResult(success=False)`` with an error containing "parse".
    """
    clean_shell.expect("qemu-img check").returns(success_result(stdout="not valid json {{{"))
    result = deep_verify_base_image(clean_shell, Path("/tmp/base.qcow2"))
    assert isinstance(result, CommitResult)
    assert result.success is False
    assert "parse" in result.error.lower()


# ── scan_backing_chain ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_scan_backing_chain_intact_chain(clean_shell, success_result) -> None:
    """When a 2-file qcow2 chain is intact (all files exist, all qcow2,
    references consistent, no cycles), ``ChainScanResult.success`` is True
    and ``broken_files`` is empty.
    """
    chain = [
        {
            "format": "qcow2",
            "filename": "/tmp/child.qcow2",
            "backing-filename": "/tmp/parent.qcow2",
        },
        {
            "format": "qcow2",
            "filename": "/tmp/parent.qcow2",
        },
    ]
    clean_shell.expect("qemu-img info --force-share --backing-chain").returns(
        success_result(stdout=json.dumps(chain))
    )
    # All ``test -f`` calls succeed (generic expectation).
    clean_shell.expect("test -f").returns(success_result())

    result = scan_backing_chain(clean_shell, Path("/tmp/child.qcow2"))

    assert result.success is True
    assert result.error is None
    assert result.paths == {"/tmp/child.qcow2", "/tmp/parent.qcow2"}
    assert result.broken_files == []


@pytest.mark.unit
@pytest.mark.mock
def test_scan_backing_chain_missing_file(clean_shell, success_result) -> None:
    """When one file in the chain does not exist on disk (``test -f`` fails),
    ``broken_files`` contains that file path.
    """
    chain = [
        {
            "format": "qcow2",
            "filename": "/tmp/child.qcow2",
            "backing-filename": "/tmp/missing.qcow2",
        },
        {
            "format": "qcow2",
            "filename": "/tmp/missing.qcow2",
        },
    ]
    clean_shell.expect("qemu-img info --force-share --backing-chain").returns(
        success_result(stdout=json.dumps(chain))
    )
    # Override the generic ``test -f`` for just the missing file.
    clean_shell.expect_first("test -f /tmp/missing.qcow2").returns(
        ShellResult(success=False, stdout="", stderr="", returncode=1, error=None)
    )
    clean_shell.expect("test -f").returns(success_result())

    result = scan_backing_chain(clean_shell, Path("/tmp/child.qcow2"))

    assert result.success is True
    # Both files are flagged: the child because its backing-filename
    # (/tmp/missing.qcow2) does not exist, and the missing file itself
    # because ``test -f`` fails for it directly.
    assert set(result.broken_files) == {"/tmp/child.qcow2", "/tmp/missing.qcow2"}


@pytest.mark.unit
@pytest.mark.mock
def test_scan_backing_chain_non_qcow2(clean_shell, success_result) -> None:
    """When one file has ``format: "raw"``, ``broken_files`` contains its
    path (the file-existence check is skipped for non-qcow2 entries).
    """
    chain = [
        {
            "format": "qcow2",
            "filename": "/tmp/child.qcow2",
            "backing-filename": "/tmp/raw_file.img",
        },
        {
            "format": "raw",
            "filename": "/tmp/raw_file.img",
        },
    ]
    clean_shell.expect("qemu-img info --force-share --backing-chain").returns(
        success_result(stdout=json.dumps(chain))
    )
    clean_shell.expect("test -f").returns(success_result())

    result = scan_backing_chain(clean_shell, Path("/tmp/child.qcow2"))

    assert result.success is True
    assert result.broken_files == ["/tmp/raw_file.img"]


@pytest.mark.unit
@pytest.mark.mock
def test_scan_backing_chain_command_fails(clean_shell, failure_result) -> None:
    """When ``qemu-img info`` returns ``success=False``,
    ``ChainScanResult.success`` is False and the error contains
    "qemu-img info failed".
    """
    clean_shell.expect("qemu-img info --force-share --backing-chain").returns(
        failure_result(error="permission denied")
    )
    result = scan_backing_chain(clean_shell, Path("/tmp/child.qcow2"))

    assert result.success is False
    assert result.error is not None
    assert "qemu-img info failed" in result.error
    assert result.paths == set()
    assert result.broken_files == []


@pytest.mark.unit
@pytest.mark.mock
def test_scan_backing_chain_cycle_detected(clean_shell, success_result) -> None:
    """When the same file appears twice in the chain,
    ``broken_files`` contains a "cycle detected" entry.
    """
    chain = [
        {
            "format": "qcow2",
            "filename": "/tmp/loop.qcow2",
            "backing-filename": "/tmp/loop.qcow2",
        },
        {
            "format": "qcow2",
            "filename": "/tmp/loop.qcow2",
        },
    ]
    clean_shell.expect("qemu-img info --force-share --backing-chain").returns(
        success_result(stdout=json.dumps(chain))
    )
    clean_shell.expect("test -f").returns(success_result())

    result = scan_backing_chain(clean_shell, Path("/tmp/loop.qcow2"))

    assert result.success is True
    cycle_entries = [f for f in result.broken_files if "cycle detected" in f]
    assert len(cycle_entries) >= 1


@pytest.mark.unit
@pytest.mark.mock
def test_scan_backing_chain_backing_mismatch(clean_shell, success_result) -> None:
    """When ``backing-filename`` does NOT match the next entry's ``filename``,
    ``broken_files`` contains a "backing-filename mismatch" entry.
    """
    chain = [
        {
            "format": "qcow2",
            "filename": "/tmp/child.qcow2",
            "backing-filename": "/tmp/wrong_parent.qcow2",
        },
        {
            "format": "qcow2",
            "filename": "/tmp/parent.qcow2",
        },
    ]
    clean_shell.expect("qemu-img info --force-share --backing-chain").returns(
        success_result(stdout=json.dumps(chain))
    )
    # All files (including the wrong backing) exist on disk.
    clean_shell.expect("test -f").returns(success_result())

    result = scan_backing_chain(clean_shell, Path("/tmp/child.qcow2"))

    assert result.success is True
    mismatch_entries = [f for f in result.broken_files if "mismatch" in f]
    assert len(mismatch_entries) >= 1
    assert "child.qcow2" in mismatch_entries[0]


@pytest.mark.unit
@pytest.mark.mock
def test_scan_backing_chain_legacy_image_key(clean_shell, success_result) -> None:
    """When the JSON uses the legacy ``"image"`` key instead of
    ``"filename"``, the function works correctly and returns the paths.
    """
    chain = [
        {
            "format": "qcow2",
            "image": "/tmp/child.qcow2",
            "backing-filename": "/tmp/parent.qcow2",
        },
        {
            "format": "qcow2",
            "image": "/tmp/parent.qcow2",
        },
    ]
    clean_shell.expect("qemu-img info --force-share --backing-chain").returns(
        success_result(stdout=json.dumps(chain))
    )
    clean_shell.expect("test -f").returns(success_result())

    result = scan_backing_chain(clean_shell, Path("/tmp/child.qcow2"))

    assert result.success is True
    assert result.paths == {"/tmp/child.qcow2", "/tmp/parent.qcow2"}
    assert result.broken_files == []


@pytest.mark.unit
@pytest.mark.mock
def test_scan_backing_chain_new_filename_key(clean_shell, success_result) -> None:
    """When the JSON uses the QEMU 11.0+ ``"filename"`` key, the function
    works correctly and returns the paths.
    """
    chain = [
        {
            "format": "qcow2",
            "filename": "/tmp/child.qcow2",
            "backing-filename": "/tmp/parent.qcow2",
        },
        {
            "format": "qcow2",
            "filename": "/tmp/parent.qcow2",
        },
    ]
    clean_shell.expect("qemu-img info --force-share --backing-chain").returns(
        success_result(stdout=json.dumps(chain))
    )
    clean_shell.expect("test -f").returns(success_result())

    result = scan_backing_chain(clean_shell, Path("/tmp/child.qcow2"))

    assert result.success is True
    assert result.paths == {"/tmp/child.qcow2", "/tmp/parent.qcow2"}
    assert result.broken_files == []
