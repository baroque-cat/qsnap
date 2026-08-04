"""Unit tests for stateless standalone-image conversion helpers.

Tests ``convert_to_standalone``, ``verify_standalone_image``, and
``convert_with_retry`` from ``qsnap.utils.convert``.  All external
commands go through a mocked ``IShell`` — zero real ``qemu-img`` or
filesystem I/O.  Retry tests monkeypatch ``time.sleep`` in the
convert module to avoid real delays.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from qsnap.models.results import ShellResult
from qsnap.utils.convert import (
    convert_to_standalone,
    convert_with_retry,
    verify_standalone_image,
)
from tests.mocks.mock_shell import MockShell

# ── Shared test paths ──────────────────────────────────────────────────────

_SOURCE = Path("/tmp/src.qcow2")
_OUTPUT = Path("/tmp/out.qcow2")

# ── ShellResult factories ──────────────────────────────────────────────────


def _ok_result(stdout: str = "") -> ShellResult:
    return ShellResult(success=True, stdout=stdout, stderr="", returncode=0, error=None)


def _fail_result(error: str = "conversion failed") -> ShellResult:
    return ShellResult(success=False, stdout="", stderr=error, returncode=1, error=error)


# ═════════════════════════════════════════════════════════════════════════════
# convert_to_standalone
# ═════════════════════════════════════════════════════════════════════════════


def test_convert_to_standalone_success(clean_shell: MockShell) -> None:
    """Successful ``qemu-img convert`` returns a successful ShellResult."""
    clean_shell.expect("qemu-img convert").returns(_ok_result())

    result = convert_to_standalone(clean_shell, _SOURCE, _OUTPUT)

    assert result.success is True
    assert result.error is None
    expected_convert = f"qemu-img convert --force-share -O qcow2 {_SOURCE} {_OUTPUT}"
    assert expected_convert in clean_shell.call_history[-1]


def test_convert_failure_removes_partial(clean_shell: MockShell) -> None:
    """A failed ``qemu-img convert`` best-effort removes the partial output."""
    clean_shell.expect("qemu-img convert").returns(_fail_result())
    clean_shell.expect("rm -f").returns(_ok_result())

    result = convert_to_standalone(clean_shell, _SOURCE, _OUTPUT)

    assert result.success is False
    # Verify rm -f was called for the output path.
    rm_calls = [c for c in clean_shell.call_history if "rm " in c]
    assert len(rm_calls) == 1
    assert f"rm -f {_OUTPUT}" in rm_calls[0]


def test_convert_failures_returned_not_raised(clean_shell: MockShell) -> None:
    """Expected conversion failures return a failed ShellResult — no exception."""
    clean_shell.expect("qemu-img convert").returns(_fail_result("disk full"))
    # Also expect the follow-up rm -f for partial cleanup.
    clean_shell.expect("rm -f").returns(_ok_result())

    # Must not raise.
    result = convert_to_standalone(clean_shell, _SOURCE, _OUTPUT)

    assert result.success is False
    assert "disk full" in (result.error or "")


# ═════════════════════════════════════════════════════════════════════════════
# verify_standalone_image
# ═════════════════════════════════════════════════════════════════════════════


def test_verify_standalone_image_passes(clean_shell: MockShell) -> None:
    """When virtual sizes match and ``qemu-img check`` is clean, return None."""
    info_json = json.dumps({"virtual-size": 1073741824})
    check_json = json.dumps({"errors": 0, "corruptions": 0, "leaks": 2})

    # Both source and output info probes return the same virtual-size.
    clean_shell.expect("qemu-img info").returns(_ok_result(info_json))
    clean_shell.expect("qemu-img check").returns(_ok_result(check_json))

    result = verify_standalone_image(clean_shell, _SOURCE, _OUTPUT)

    assert result is None


def test_verify_m1_virtual_size_mismatch(clean_shell: MockShell) -> None:
    """When virtual sizes differ, return an M1 error string."""
    source_json = json.dumps({"virtual-size": 1073741824})
    output_json = json.dumps({"virtual-size": 536870912})

    # Differentiate the two info calls by path so MockShell returns
    # distinct results (both are ``qemu-img info --force-share --output=json``).
    clean_shell.expect(f"qemu-img info.*{_SOURCE}").returns(_ok_result(source_json))
    clean_shell.expect(f"qemu-img info.*{_OUTPUT}").returns(_ok_result(output_json))

    result = verify_standalone_image(clean_shell, _SOURCE, _OUTPUT)

    assert result is not None
    assert "M1 failed" in result
    assert "virtual-size mismatch" in result


def test_verify_m2_corrupted_output(clean_shell: MockShell) -> None:
    """When virtual sizes match but ``qemu-img check`` reports
    errors or corruptions, return an M2 error string."""
    info_json = json.dumps({"virtual-size": 1073741824})
    check_json = json.dumps({"errors": 1, "corruptions": 0, "leaks": 0})

    clean_shell.expect("qemu-img info").returns(_ok_result(info_json))
    clean_shell.expect("qemu-img check").returns(_ok_result(check_json))

    result = verify_standalone_image(clean_shell, _SOURCE, _OUTPUT)

    assert result is not None
    assert "M2 failed" in result
    assert "1 errors" in result


# ═════════════════════════════════════════════════════════════════════════════
# convert_with_retry
# ═════════════════════════════════════════════════════════════════════════════


def test_convert_with_retry_transient_then_success(clean_shell: MockShell) -> None:
    """First convert fails with a retryable error, second succeeds.
    ``time.sleep`` in the convert module is monkeypatched to avoid real delays.

    Call sequence (retry_max=3):
      attempt 1: convert (shell) → internal rm (shell) — both fail/rm_
      attempt 2: external rm (shell) → sleep → convert (shell) → success
    """
    convert_fail = _fail_result("Connection refused")
    rm_ok = _ok_result()
    convert_ok = _ok_result("done")

    # 4 shell calls: convert-fail → internal-rm → external-rm → convert-ok
    responses = [convert_fail, rm_ok, rm_ok, convert_ok]
    call_idx: list[int] = [0]

    def _side_effect(cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        i = call_idx[0]
        call_idx[0] += 1
        if i < len(responses):
            return responses[i]
        return _fail_result(f"Unexpected call #{i}")

    with (
        patch.object(clean_shell, "run", side_effect=_side_effect),
        patch("qsnap.utils.convert.time.sleep") as mock_sleep,
    ):
        result = convert_with_retry(clean_shell, _SOURCE, _OUTPUT, retry_max=3, retry_base="2s")

    assert result.success is True
    assert call_idx[0] == 4  # 4 shell.run calls before result
    # compute_backoff(2, 1) = 2.0 — only one retry, so one sleep call.
    mock_sleep.assert_called_once_with(2.0)


def test_convert_with_retry_non_retryable_fails(clean_shell: MockShell) -> None:
    """A non-retryable first failure returns immediately with no retry.

    ``convert_to_standalone`` internally removes partial output on failure,
    so there are two shell calls before the non-retryable error causes an
    immediate return.
    """
    convert_fail = _fail_result("Permission denied")
    rm_ok = _ok_result()

    # 2 shell calls: convert → internal rm (convert_to_standalone cleans up)
    responses = [convert_fail, rm_ok]
    call_idx: list[int] = [0]

    def _side_effect(cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        i = call_idx[0]
        call_idx[0] += 1
        if i < len(responses):
            return responses[i]
        return _fail_result(f"Unexpected call #{i}")

    with (
        patch.object(clean_shell, "run", side_effect=_side_effect),
        patch("qsnap.utils.convert.time.sleep") as mock_sleep,
    ):
        result = convert_with_retry(clean_shell, _SOURCE, _OUTPUT, retry_max=3, retry_base="2s")

    assert result.success is False
    assert "Permission denied" in (result.error or "")
    assert call_idx[0] == 2  # convert + internal cleanup; no retry loop entered
    mock_sleep.assert_not_called()


def test_convert_with_retry_exhausted(clean_shell: MockShell) -> None:
    """When all retry_max attempts fail with retryable errors, the last
    failed ShellResult is returned.  ``time.sleep`` is monkeypatched.

    Call sequence (retry_max=3):
      attempt 1: convert (shell) → internal rm (shell)
      attempt 2: external rm (shell) → sleep → convert → internal rm
      attempt 3: external rm (shell) → sleep → convert → internal rm
    """
    convert_fail = _fail_result("Connection refused")
    rm_ok = _ok_result()

    # 8 shell calls: 3 converts + 5 rm (2 internal + 2 external + 1 last internal)
    responses = [
        convert_fail,  # attempt 1: convert
        rm_ok,  # attempt 1: internal rm
        rm_ok,  # attempt 2: external rm
        convert_fail,  # attempt 2: convert
        rm_ok,  # attempt 2: internal rm
        rm_ok,  # attempt 3: external rm
        convert_fail,  # attempt 3: convert
        rm_ok,  # attempt 3: internal rm
    ]
    call_idx: list[int] = [0]

    def _side_effect(cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        i = call_idx[0]
        call_idx[0] += 1
        if i < len(responses):
            return responses[i]
        return _fail_result(f"Unexpected call #{i}")

    with (
        patch.object(clean_shell, "run", side_effect=_side_effect),
        patch("qsnap.utils.convert.time.sleep") as mock_sleep,
    ):
        result = convert_with_retry(clean_shell, _SOURCE, _OUTPUT, retry_max=3, retry_base="2s")

    assert result.success is False
    assert "Connection refused" in (result.error or "")
    assert call_idx[0] == 8  # 3 converts + 5 cleanup calls
    # compute_backoff(2, 1)=2.0 for attempt 2, compute_backoff(2, 2)=4.0 for attempt 3.
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(2.0)
    mock_sleep.assert_any_call(4.0)


# ═════════════════════════════════════════════════════════════════════════════
# Statelessness / no mutations
# ═════════════════════════════════════════════════════════════════════════════


def test_convert_helpers_stateless_no_mutations(clean_shell: MockShell) -> None:
    """Convert helpers issue only qemu-img/rm commands via IShell — no
    virsh, no state manager interaction."""
    info_json = json.dumps({"virtual-size": 1073741824})
    check_json = json.dumps({"errors": 0, "corruptions": 0})

    clean_shell.expect("qemu-img info").returns(_ok_result(info_json))
    clean_shell.expect("qemu-img check").returns(_ok_result(check_json))
    clean_shell.expect("qemu-img convert").returns(_ok_result())

    # Exercise all three public helpers.
    convert_to_standalone(clean_shell, _SOURCE, _OUTPUT)
    verify_standalone_image(clean_shell, _SOURCE, _OUTPUT)

    history = clean_shell.call_history

    # No virsh in sight.
    virsh_calls = [c for c in history if "virsh" in c]
    assert len(virsh_calls) == 0, f"Unexpected virsh calls: {virsh_calls}"

    # Only expected tools: qemu-img or rm.
    allowed_prefixes = ("qemu-img", "rm ")
    for cmd_str in history:
        assert any(cmd_str.startswith(p) for p in allowed_prefixes), (
            f"Unexpected command not qemu-img/rm: {cmd_str}"
        )
