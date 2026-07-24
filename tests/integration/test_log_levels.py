"""Integration tests for log level behavior in ``SubprocessShell``.

Covers the shell-abstraction spec requirement: probing calls with
``check=True`` log failures at DEBUG level, NOT ERROR level
(spec: shell-abstraction delta; design D10 — we do not want probe
failures cluttering ERROR logs).

All tests are marked ``@pytest.mark.integration``.  They use the real
``SubprocessShell`` instance (no mocking of the shell itself).  The
``caplog`` fixture captures Python logging output.

Run only when explicitly requested::

    poetry run pytest tests/integration/test_log_levels.py -v -m integration
"""

from __future__ import annotations

import logging

import pytest

from qsnap.shell.subprocess_shell import SubprocessShell


# ──────────────────────────────────────────────────────────────────────
# Test 1: probe failure logged at DEBUG, not ERROR
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_probe_failure_logged_at_debug_not_error(caplog):
    """Verify that a shell command failing with ``check=True`` is logged
    at DEBUG level, not ERROR level.

    The spec requires probing calls (``check=True``) to use DEBUG
    because probe failures are expected — e.g. the compress driver
    probe always exits non-zero even when the driver is present.
    These should never pollute ERROR-level logs.

    Test approach:
    1. Create a real ``SubprocessShell`` instance.
    2. Run a command that will definitely fail (``['false']``) with
       ``check=True``.
    3. Capture all log records emitted during the call.
    4. Verify that any failure log records are at DEBUG level, never
       ERROR (or above).
    """
    shell = SubprocessShell()

    with caplog.at_level(logging.DEBUG, logger="qsnap.shell"):
        result = shell.run(["false"], timeout=10, check=True)

    # The command should fail (exit code 1).
    assert not result.success, (
        f"Expected 'false' command to fail, got success={result.success!r}"
    )

    # Collect log records emitted by the shell logger.
    shell_records = [
        r for r in caplog.records
        if r.name == "qsnap.shell.subprocess_shell"
    ]

    assert len(shell_records) > 0, (
        "Expected at least one log record from qsnap.shell — none found. "
        f"All records: {[(r.name, r.levelname, r.message) for r in caplog.records]}"
    )

    # Every shell log record for this command must be at DEBUG or lower.
    for record in shell_records:
        assert record.levelno <= logging.DEBUG, (
            f"Shell log record at level {record.levelname} ({record.levelno}) "
            f"when check=True was used.  Expected DEBUG (10) or lower.  "
            f"Message: {record.message!r}"
        )

    # Explicitly verify no ERROR or WARNING records were emitted.
    error_or_warning = [
        r for r in shell_records
        if r.levelno >= logging.WARNING
    ]
    assert len(error_or_warning) == 0, (
        f"Found {len(error_or_warning)} WARNING/ERROR log records when "
        f"check=True.  Only DEBUG is expected for probing calls.  "
        f"Records: {[(r.levelname, r.message) for r in error_or_warning]}"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 2: compress driver probe specifically logs at DEBUG
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_compress_probe_logged_at_debug(caplog):
    """Verify that the real ``qemu-nbd --image-opts driver=compress``
    probe (which always fails with exit code 1) logs at DEBUG level.

    This is the actual compress driver probe command used by Core's
    ``_validate_environment`` with ``check=True``.  Even though the
    command always returns non-zero (the driver=compress requires a
    ``file`` parameter), the error message from the probe is how we
    determine whether the driver is available.

    The log must NOT contain ERROR-level entries for this probe.
    """
    import shutil

    if not shutil.which("qemu-nbd"):
        pytest.skip("qemu-nbd binary not found in PATH")

    shell = SubprocessShell()

    with caplog.at_level(logging.DEBUG, logger="qsnap.shell"):
        result = shell.run(
            ["qemu-nbd", "--image-opts", "driver=compress"],
            timeout=10,
            check=True,
        )

    # The compress probe always exits non-zero — that is expected.
    assert not result.success, (
        f"Compress driver probe should fail (needs file= parameter), "
        f"got success={result.success!r}"
    )

    # The stderr should contain text indicating the driver was found
    # (not "Unknown driver").
    err_text = (result.stderr or result.error or "").lower()
    assert "unknown driver" not in err_text, (
        f"Expected compress driver to be available, but got 'Unknown driver'. "
        f"stderr: {result.stderr!r}"
    )

    # Verify all shell log records are at DEBUG level.
    shell_records = [
        r for r in caplog.records
        if r.name == "qsnap.shell.subprocess_shell"
    ]
    for record in shell_records:
        assert record.levelno <= logging.DEBUG, (
            f"Compress probe log at level {record.levelname} "
            f"(expected DEBUG). Message: {record.message!r}"
        )

    error_records = [
        r for r in shell_records
        if r.levelno >= logging.WARNING
    ]
    assert len(error_records) == 0, (
        f"Compress probe produced {len(error_records)} WARNING/ERROR "
        f"log records. Expected only DEBUG."
    )
