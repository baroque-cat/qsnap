"""Unit tests for the blockjob probe classifier in qsnap.utils.blockjob.

Tests verify ``classify_blockjob_output`` against the blockjob-protocol
spec: ``success=False`` always classifies as ``"error"``, empty/"No current
block job" output classifies as ``"none"``, job-describing output
(block job / block copy / block commit / block pull / active block)
classifies as ``"active"``, and any other non-empty output classifies as
``"error"``.  Both stdout and stderr are inspected (libvirt reports a
throttled blockcommit's progress on stderr while stdout may carry only a
bandwidth line).  The helper is pure — no I/O, no side effects, deterministic.
"""

from __future__ import annotations

import pytest

from qsnap.utils.blockjob import classify_blockjob_output

# ── no current block job / empty output → "none" ────────────────────────────


@pytest.mark.parametrize(
    "stdout",
    [
        "No current block job\n",
        "no current block job",
        "NO CURRENT BLOCK JOB",
        "",
        "   \n",
        "\t\n",
    ],
)
def test_classify_no_current_job_returns_none(stdout: str) -> None:
    """Empty output and ``"No current block job"`` (case-insensitive) → ``"none"``."""
    assert classify_blockjob_output(stdout) == "none"


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        ("", "No current block job"),
        # The "No current block job" marker takes precedence over active
        # markers per the documented ordering — even when stderr reports a
        # job in progress.
        ("No current block job", "Block Commit: [ 1 %]"),
    ],
)
def test_classify_stderr_no_job_returns_none(stdout: str, stderr: str) -> None:
    """``"No current block job"`` on either stream → ``"none"``; the marker
    wins over active markers regardless of which stream carries it.
    """
    assert classify_blockjob_output(stdout, stderr=stderr) == "none"


# ── job-describing output → "active" ────────────────────────────────────────


@pytest.mark.parametrize(
    "stdout",
    [
        # Canonical virsh ``blockjob`` job descriptions
        "Block job: type=blockcommit\nJob: 1048576/2097152\n",
        "Block job: active\n",
        "Active block job exists",
        "Block Copy: [ 33 %]\n",
        "Block Commit: [ 42 %]",
        "Block Pull: [100 %]",
        # Case-insensitivity of every marker
        "BLOCK JOB: type=blockcopy\n",
        "block COPY: [ 50 %]\n",
        "bLoCk CoMmIt: [ 10 %]",
        "ACTIVE BLOCK job in progress",
        "Block job: type=blockcommit\nJob: 2097152/2097152\n",
        "Block Commit: [100 %]\n",
    ],
)
def test_classify_active_job_output_returns_active(stdout: str) -> None:
    """Job-describing output containing any marker (case-insensitive) → ``"active"``."""
    assert classify_blockjob_output(stdout) == "active"


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        # libvirt reports a throttled blockcommit's progress on stderr while
        # stdout may carry only a bandwidth line — both streams are inspected.
        ("Bandwidth limit: 1 bytes/s (1.000 B/s)", "Block Commit: [ 0.19 %]"),
        ("", "Block Copy: [ 33 %]"),
        ("Bandwidth limit: 0 B/s", "Block Commit: [ 42 %]"),
        # Case-insensitivity applies to the stderr stream too.
        ("", "ACTIVE BLOCK COMMIT: [ 12 %]"),
    ],
)
def test_classify_stderr_job_output_returns_active(stdout: str, stderr: str) -> None:
    """Job-describing markers on stderr (stdout holding only a bandwidth
    line) → ``"active"``.
    """
    assert classify_blockjob_output(stdout, stderr=stderr) == "active"


# ── failed command → "error" ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "No current block job\n",
        "Block job: type=blockcommit\n",
        "garbage output\n",
    ],
)
def test_classify_failed_command_returns_error(stdout: str) -> None:
    """``success=False`` (non-zero exit, timeout, missing binary) → ``"error"``
    regardless of stdout content.
    """
    assert classify_blockjob_output(stdout, success=False) == "error"


def test_classify_failed_command_ignores_streams() -> None:
    """``success=False`` wins regardless of what either stream carries."""
    assert (
        classify_blockjob_output(
            "Bandwidth limit: 1 bytes/s",
            stderr="Block Commit: [ 50 %]",
            success=False,
        )
        == "error"
    )


# ── unclassifiable non-empty output → "error" ───────────────────────────────


@pytest.mark.parametrize(
    "stdout",
    [
        "garbage output\n",
        "error: failed to connect to the hypervisor\n",
        "No such domain\n",
        "libvirt:  error : operation failed\n",
        "   unknown output   ",
    ],
)
def test_classify_unclassifiable_output_returns_error(stdout: str) -> None:
    """Any other non-empty output with ``success=True`` → ``"error"``."""
    assert classify_blockjob_output(stdout, success=True) == "error"


# ── purity / determinism ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "stdout",
    [
        "No current block job\n",
        "Block job: type=blockcommit\nJob: 1048576/2097152\n",
        "garbage output\n",
        "",
    ],
)
def test_classify_is_pure(stdout: str) -> None:
    """The classifier is deterministic — same input twice yields the same
    output and the call has no side effects (no I/O, no state).
    """
    first = classify_blockjob_output(stdout)
    second = classify_blockjob_output(stdout)
    assert first == second
    assert isinstance(first, str)
    # Keyword ``success`` also has no effect on determinism.
    assert classify_blockjob_output(stdout, success=True) == second
