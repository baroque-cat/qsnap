"""Integration tests for stall detection with real subprocesses.

All tests in this module are marked ``@pytest.mark.integration``.
They use real subprocesses (``sleep``, ``python3``) to exercise
``SubprocessShell.run_with_stall_detection()`` and verify that stalled
processes are killed while slowly-progressing processes are allowed
to complete.

No libvirt daemon is required — these tests only need a standard
Unix environment with ``sleep``, ``python3``, and ``pgrep``.

Run only when explicitly requested::

    poetry run pytest tests/integration/test_stall_detection.py -v -m integration
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from qsnap.shell.subprocess_shell import SubprocessShell


# ── Helpers ──────────────────────────────────────────────────────────


def _assert_no_orphan(marker: str, *, wait: float = 0.5) -> None:
    """Check that no process matching *marker* is still running.

    After a stall-kill, the process may take a brief moment to be fully
    reaped.  *wait* allows a short grace period before checking.
    """
    time.sleep(wait)
    result = subprocess.run(
        ["pgrep", "-f", marker],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0, (
        f"Orphan process matching '{marker}' still running: {result.stdout.decode().strip()}"
    )


# ── Integration: stall detection ─────────────────────────────────────


@pytest.mark.integration
def test_stall_detection_kills_hung_convert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Start a hung process and verify stall detection kills it.

    Uses ``sleep`` (simulating a hung data-transfer command) with an
    output file that never grows.  The stall_timeout is set to a short
    value and the poll interval is patched to 1s so the test completes
    quickly.
    """
    # Speed up the polling loop so the test doesn't wait 60s per poll.
    monkeypatch.setattr("qsnap.shell.subprocess_shell._POLL_INTERVAL", 1)

    output_file = tmp_path / "output.img"
    output_file.touch()  # empty file — never grows

    shell = SubprocessShell()
    stall_timeout = 3
    cmd = ["sleep", "3600"]

    start = time.monotonic()
    result = shell.run_with_stall_detection(
        cmd,
        output_file=output_file,
        stall_timeout=stall_timeout,
    )
    elapsed = time.monotonic() - start

    # Verify the stall was detected and the process was killed.
    assert result.success is False, f"Expected success=False, got: {result}"
    assert "Stall detected" in (result.error or ""), (
        f"Expected 'Stall detected' in error, got: {result.error}"
    )
    assert result.returncode == -1, f"Expected returncode=-1, got: {result.returncode}"

    # Verify the test completed in a reasonable time (should be ~4s:
    # roughly _POLL_INTERVAL=1 + stall_timeout=3, plus overhead).
    assert elapsed < 15, f"Stall detection took too long: {elapsed:.1f}s"

    # Verify no orphan sleep process remains.
    _assert_no_orphan("sleep 3600")


@pytest.mark.integration
def test_slow_progress_not_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run a slowly-progressing command and verify it is NOT killed.

    A Python script appends 1 KB to the output file every 2 seconds
    for ~20 seconds.  With stall_timeout=10s and _POLL_INTERVAL=1s,
    the writes are frequent enough to avoid triggering stall detection.
    """
    # Speed up the polling loop.
    monkeypatch.setattr("qsnap.shell.subprocess_shell._POLL_INTERVAL", 1)

    output_file = tmp_path / "output.img"
    output_file.touch()

    # Script: write 1 KB every 2 seconds, 10 iterations = ~20 seconds.
    iterations = 10
    sleep_per_iteration = 2
    script = (
        "import time\n"
        f"with open({str(output_file)!r}, 'ab') as f:\n"
        f"    for i in range({iterations}):\n"
        "        f.write(b'X' * 1024)\n"
        "        f.flush()\n"
        f"        time.sleep({sleep_per_iteration})\n"
    )
    cmd = ["python3", "-c", script]

    shell = SubprocessShell()
    stall_timeout = 10

    start = time.monotonic()
    result = shell.run_with_stall_detection(
        cmd,
        output_file=output_file,
        stall_timeout=stall_timeout,
    )
    elapsed = time.monotonic() - start

    # Verify the process completed successfully (not killed by stall detection).
    assert result.success is True, (
        f"Expected success=True but got success=False. "
        f"error={result.error}, returncode={result.returncode}\n"
        f"stderr={result.stderr}"
    )
    assert result.returncode == 0, f"Expected returncode=0, got: {result.returncode}"

    # Verify the process ran for roughly the expected duration.
    expected_duration = iterations * sleep_per_iteration
    assert elapsed >= expected_duration * 0.8, (
        f"Process completed too quickly ({elapsed:.1f}s vs expected ~{expected_duration}s)"
    )

    # Verify the output file has grown as expected.
    final_size = output_file.stat().st_size
    expected_size = iterations * 1024
    assert final_size == expected_size, (
        f"Output file size mismatch: {final_size} vs expected {expected_size}"
    )
