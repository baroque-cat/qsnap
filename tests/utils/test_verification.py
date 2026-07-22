"""Unit tests for qsnap.utils.verification — shared verification functions.

Tests verify that backup verification functions are importable from the
shared utility module (not from a domain sub-package), and that deprecated
verify_mode values are handled correctly.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from qsnap.models.results import ShellResult
from qsnap.utils.verification import verify_full_backup
from tests.mocks.mock_shell import MockShell

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


def test_deprecated_hash_verify_mode_triggers_all_tiers() -> None:
    """When ``verify_mode="hash"`` (the now-deprecated tier), the function
    emits a WARNING and behaves like ``"compare"`` — running M1 (metadata),
    M2 (check), and M3 (compare) tiers.

    The deprecated alias is remapped to ``"compare"`` at the top of the
    function, so all three verification tiers execute.
    """
    shell = MockShell()
    shell.expect("qemu-img info").returns(_ok_result(stdout=json.dumps(_VALID_QCOW2_INFO)))
    shell.expect("qemu-img check").returns(_ok_result(stdout=json.dumps(_VALID_CHECK)))
    shell.expect("qemu-img compare").returns(_ok_result())

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
