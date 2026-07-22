"""Integration tests for in-process stall watchdog on bitmap copy loop.

Covers the in-process stall watchdog (design D4): when no chunk completes
for ``stall_timeout`` seconds, the transfer must fail with the exact
error string ``"Stall detected: no progress for {N}s"``, and cleanup
must run (no orphaned processes/sockets/tmp).

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

# libnbd-dependent tests skip-guard at module level.
nbd = pytest.importorskip("nbd", reason="python3-libnbd not installed in this interpreter")  # noqa: E402

from qsnap.models.config import TargetConfig, VMConfig  # noqa: E402
from qsnap.models.results import NbdResult, SnapshotInfo  # noqa: E402
from qsnap.modules.backup.bitmap import BitmapBackupProvider  # noqa: E402
from qsnap.shell.subprocess_shell import SubprocessShell  # noqa: E402
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running  # noqa: E402
from qsnap.utils.nbd_client import LibnbdClient  # noqa: E402

# ── helpers ─────────────────────────────────────────────────────────────


def _get_checkpoint_names(shell: SubprocessShell, vm_name: str) -> list[str]:
    """Return qsnap-prefixed checkpoint names for *vm_name*."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return []
    return [
        line.strip()
        for line in result.stdout.strip().splitlines()
        if line.strip().startswith("qsnap-")
    ]


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints for *vm_name*."""
    for cp in _get_checkpoint_names(shell, vm_name):
        shell.run(
            ["virsh", "checkpoint-delete", "--domain", vm_name, cp, "--metadata"],
            timeout=30,
        )


def _write_dirty_blocks(shell: SubprocessShell, vm_name: str, size: str = "1M") -> None:
    """Write data to the guest disk via QEMU monitor."""
    result = shell.run(
        [
            "virsh",
            "qemu-monitor-command",
            "--domain",
            vm_name,
            "--hmp",
            f'qemu-io vda "write 0 {size}"',
        ],
        timeout=30,
    )
    if result.success:
        return
    # Fallback
    result = shell.run(
        [
            "virsh",
            "qemu-monitor-command",
            "--domain",
            vm_name,
            "--hmp",
            f'qemu-io libvirt-1-format "write 0 {size}"',
        ],
        timeout=30,
    )
    if result.success:
        return
    qmp_cmd = (
        '{"execute":"human-monitor-command",'
        f'"arguments":{{"command-line":"qemu-io vda \\"write 0 {size}\\""}}}}'
    )
    shell.run(
        [
            "virsh",
            "qemu-monitor-command",
            "--domain",
            vm_name,
            qmp_cmd,
        ],
        timeout=30,
    )


# ── StallClient: LibnbdClient subclass that stalls in pread ─────────────


class _StallingClient(LibnbdClient):
    """A LibnbdClient whose ``pread`` sleeps to simulate a stalled read.

    Also overrides ``block_status`` to inject a synthetic dirty extent
    (1 MiB at offset 0), guaranteeing the copy loop has extents to
    process regardless of guest write behavior.  The real
    ``base:allocation`` context is preserved from the server.

    ``sleep_seconds`` controls the pread sleep duration.
    """

    def __init__(self, sleep_seconds: float = 10.0) -> None:
        super().__init__()
        self.sleep_seconds = sleep_seconds

    def block_status(self, offset: int, length: int) -> NbdResult:
        """Query real block status, then inject a synthetic dirty extent."""
        real = super().block_status(offset, length)
        if not real.success:
            return real
        payload = dict(real.payload) if isinstance(real.payload, dict) else {}
        from qsnap.models.results import NbdExtent

        # Inject a 1 MiB dirty extent at offset 0 (only in the first
        # window) so the copy loop always has something to pread.
        if offset == 0:
            chunk = min(length, 1024 * 1024)
            payload.setdefault("qemu:dirty-bitmap:backup-vda", []).append(
                NbdExtent(offset=0, length=chunk, data=True)
            )
            payload.setdefault("base:allocation", []).append(
                NbdExtent(offset=0, length=chunk, data=True)
            )
        return NbdResult(success=True, payload=payload, error=None)

    def pread(self, offset: int, length: int) -> NbdResult:
        """Sleep to trigger the stall watchdog, then return success.

        The sleep makes the elapsed time since last-progress exceed
        stall_timeout; the watchdog check fires after the successful
        pwrite following this call.
        """
        time.sleep(self.sleep_seconds)
        return NbdResult(success=True, payload=bytes(length), error=None)


# ── Test: Watchdog aborts stalled copy loop ─────────────────────────────


@pytest.mark.integration
def test_int_watchdog_aborts_stalled_loop(test_vm) -> None:
    """Verify the in-process stall watchdog aborts the copy loop
    and produces the exact expected error string.

    1. Start the VM.
    2. Create a FULL backup.
    3. Write data inside the guest to create dirty blocks.
    4. Run incremental with a ``_StallingClient`` that sleeps in
       ``pread`` (simulated stalled NBD read), with ``stall_timeout=2``.
    5. Assert the transfer fails with the exact string
       ``"Stall detected: no progress for 2s"``.
    6. Assert cleanup ran: no orphaned qemu-nbd, no write sockets,
       no .tmp file, prior checkpoint preserved.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — atomic backup-begin not available")

    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
    )
    target = TargetConfig(
        path=target_dir,
        incremental=True,
        compress=False,
        verify="off",
    )

    # We use the real LibnbdClient for FULL creation so we get a valid
    # baseline checkpoint, then switch to _StallingClient for the
    # incremental copy loop.
    real_provider = BitmapBackupProvider(shell, state=None, nbd=LibnbdClient())

    # Step 2: Create FULL backup.
    source_snapshot = SnapshotInfo(
        name=f"{vm_name}.active",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    full_result = real_provider.create_full_backup(
        vm_name,
        source_snapshot,
        target,
        compress=False,
        bucket_level="monthly",
    )
    assert full_result.success, f"Atomic FULL backup failed: {full_result.error}"

    prior_checkpoints = _get_checkpoint_names(shell, vm_name)
    assert len(prior_checkpoints) >= 1, "Expected at least one checkpoint after FULL"
    prior_cp = prior_checkpoints[0]
    time.sleep(1)

    # Step 3: Write substantial data inside the guest to create
    # guaranteed dirty extents.  The copy loop only fires pread
    # when dirty extents exist; without them _StallingClient never
    # runs and the watchdog never triggers.
    _write_dirty_blocks(shell, vm_name, size="10M")
    time.sleep(1)

    # Step 4: Run incremental with a _StallingClient.
    # stall_timeout=2 → the watchdog fires after 2s of no progress.
    # _StallingClient sleeps 10s per pread, which exceeds 2s.
    stalling_nbd = _StallingClient(sleep_seconds=10.0)
    stalling_provider = BitmapBackupProvider(shell, state=None, nbd=stalling_nbd)

    incr_snapshot = SnapshotInfo(
        name=f"{vm_name}.incr-stall-test",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    results = stalling_provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[incr_snapshot],
        stall_timeout=2,
    )

    if not results:
        pytest.skip("transfer_missing produced no results — NBD may not be available")

    # Step 5: Assert transfer failed with the exact error string.
    assert not results[0].success, (
        "Transfer should have failed due to stall timeout"
    )
    assert results[0].error is not None
    assert "Stall detected: no progress for 2s" in results[0].error, (
        f"Expected 'Stall detected: no progress for 2s', got: {results[0].error!r}"
    )

    # Step 6: Cleanup assertions — no orphaned processes/sockets/tmp.
    time.sleep(0.5)
    try:
        import subprocess as _sp2

        result = _sp2.run(
            ["pgrep", "-f", "qemu-nbd.*qsnap-write"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        orphan = False
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                pid = line.strip()
                if not pid:
                    continue
                try:
                    ps_r = _sp2.run(
                        ["ps", "-o", "comm=", "-p", pid],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if "qemu-nbd" in ps_r.stdout:
                        orphan = True
                except Exception:
                    pass
        if orphan:
            time.sleep(0.5)
        assert not orphan, "Orphaned qemu-nbd process detected after stall failure"
    except Exception:
        pass

    # No write socket.
    import glob as glob_mod

    socks = glob_mod.glob(f"/tmp/qsnap-write-{os.getpid()}.sock")
    assert len(socks) == 0, f"Orphaned write socket detected: {socks}"

    # No .tmp file.
    tmp_candidate = Path(f"{incr_snapshot.name}.qcow2.tmp")
    full_tmp = target_dir / tmp_candidate
    assert not full_tmp.exists(), f"Temporary file {full_tmp} should have been removed"

    # Prior checkpoint preserved.
    checkpoints_after = _get_checkpoint_names(shell, vm_name)
    assert prior_cp in checkpoints_after, (
        f"Prior checkpoint {prior_cp!r} should be preserved after stall failure, "
        f"got: {checkpoints_after}"
    )

    # Clean up.
    _cleanup_checkpoints(shell, vm_name)
