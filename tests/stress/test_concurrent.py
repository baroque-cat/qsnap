"""Stress test: lockfile prevents concurrent pipeline runs.

This test verifies that the lockfile mechanism prevents two concurrent
``qsnap run`` invocations from corrupting state or racing on virsh
operations.

Marked ``@pytest.mark.stress`` — requires a libvirt environment.
"""

from __future__ import annotations

import pytest


@pytest.mark.stress
def test_lockfile_prevents_concurrent_runs(stress_env):
    """Verify lockfile prevents concurrent pipeline execution.

    Steps (placeholder — implement when libvirt test environment is
    available):
      1. Start a ``qsnap run`` in a background thread/process.
      2. While the first run is in progress, start a second ``qsnap run``
         against the same VM.
      3. Verify the second run detects the lockfile and exits with a
         clear error message (no crash, no state corruption).
      4. Verify the first run completes successfully.
    """
    pytest.skip("Requires libvirt environment")
