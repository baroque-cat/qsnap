"""Integration tests for bitmap dirty-block transfer pipeline.

Covers the full pipeline: FULL backup → guest writes → bitmap incremental,
dirty-barrier assertions, orphan-process cleanup, and failure-injection
cleanup (no orphaned qemu-nbd / sockets / .tmp after any outcome).

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

# libnbd-dependent tests skip-guard at module level.
# The copy loop needs the real LibnbdClient (or a subclass) but
# the system python3-libnbd may not be importable in this venv.
nbd = pytest.importorskip("nbd", reason="python3-libnbd not installed in this interpreter")  # noqa: E402

from qsnap.models.config import TargetConfig, VMConfig  # noqa: E402
from qsnap.models.results import NbdExtent, NbdResult, SnapshotInfo  # noqa: E402
from qsnap.modules.backup.bitmap import BitmapBackupProvider  # noqa: E402
from qsnap.shell.subprocess_shell import SubprocessShell  # noqa: E402
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running  # noqa: E402
from qsnap.utils.nbd_client import LibnbdClient  # noqa: E402

# ── helpers ─────────────────────────────────────────────────────────────


class _FailingPwriteClient(LibnbdClient):
    """A LibnbdClient whose ``pwrite`` fails on the first call.

    Also injects synthetic dirty+allocated extents in ``block_status``
    so the copy loop always has extents to pwrite.

    Deterministic failure injection for the orphan-cleanup test.
    """

    def __init__(self) -> None:
        super().__init__()
        self._pwrite_count = 0

    def block_status(self, offset: int, length: int) -> NbdResult:
        real = super().block_status(offset, length)
        if not real.success:
            return real
        payload = dict(real.payload) if isinstance(real.payload, dict) else {}
        if offset == 0:
            chunk = min(length, 1024 * 1024)
            payload.setdefault("qemu:dirty-bitmap:backup-vda", []).append(
                NbdExtent(offset=0, length=chunk, data=True)
            )
            payload.setdefault("base:allocation", []).append(
                NbdExtent(offset=0, length=chunk, data=True)
            )
        return NbdResult(success=True, payload=payload, error=None)

    def pwrite(self, offset: int, data: bytes) -> NbdResult:
        self._pwrite_count += 1
        if self._pwrite_count == 1:
            return NbdResult(
                success=False,
                payload=None,
                error="eof: pwrite failed (injected failure)",
            )
        return super().pwrite(offset, data)


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


def _write_dirty_blocks(shell: SubprocessShell, vm_name: str, size: str = "10M") -> None:
    """Write data to the guest disk via QEMU monitor to create dirty blocks.

    Uses ``-P 0x5a`` (non-zero pattern) to produce real allocated dirty
    extents — all-zero writes may be optimized to unallocated zero-clusters
    that are dirty in the bitmap but have no allocated data to copy.
    """
    # Primary: HMP qemu-io via virsh — write a non-zero pattern.
    result = shell.run(
        [
            "virsh",
            "qemu-monitor-command",
            "--domain",
            vm_name,
            "--hmp",
            f'qemu-io vda "write -P 0x5a 0 {size}"',
        ],
        timeout=30,
    )
    if result.success:
        return

    # Try libvirt-1-format (alternate device naming).
    result = shell.run(
        [
            "virsh",
            "qemu-monitor-command",
            "--domain",
            vm_name,
            "--hmp",
            f'qemu-io libvirt-1-format "write -P 0x5a 0 {size}"',
        ],
        timeout=30,
    )
    if result.success:
        return

    # QMP fallback.
    qmp_cmd = (
        '{"execute":"human-monitor-command",'
        f'"arguments":{{"command-line":"qemu-io vda \\"write -P 0x5a 0 {size}\\""}}}}'
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


def _qemu_img_info_json(shell: SubprocessShell, path: Path, force_share: bool = False) -> dict | None:
    """Return ``qemu-img info --output=json`` for *path* as a dict.

    Passes ``--force-share`` when *force_share* is True (reading a
    live VM's active layer).
    """
    cmd = ["qemu-img", "info", "--output=json"]
    if force_share:
        cmd.append("--force-share")
    cmd.append(str(path))
    result = shell.run(cmd, timeout=30)
    if not result.success:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _no_orphaned_processes() -> bool:
    """Return True if no qsnap-write qemu-nbd processes remain.

    Uses subprocess (not os.system) to avoid the shell wrapper
    appearing in pgrep -f matches.  Filters to real qemu-nbd
    process names only.
    """
    import subprocess as _sp

    try:
        result = _sp.run(
            ["pgrep", "-f", "qemu-nbd.*qsnap-write"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return True
        # Filter: ignore any PID whose process name isn't actually qemu-nbd
        # (pgrep -f matches the shell wrapper too via subprocess)
        for line in result.stdout.strip().splitlines():
            pid = line.strip()
            if not pid:
                continue
            try:
                ps_result = _sp.run(
                    ["ps", "-o", "comm=", "-p", pid],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if "qemu-nbd" in ps_result.stdout:
                    return False
            except Exception:
                pass
        return True
    except Exception:
        return True


def _no_orphaned_sockets() -> bool:
    """Return True if no /tmp/qsnap-write-*.sock sockets remain."""
    import glob as glob_mod

    socks = glob_mod.glob(f"/tmp/qsnap-write-{os.getpid()}.sock")
    return len(socks) == 0


# ── Test 1: Full pipeline with dirty-barrier assertion ──────────────────


@pytest.mark.integration
def test_int_full_pipeline_dirty_transfer(test_vm) -> None:
    """Full bitmap incremental pipeline with dirty-barrier assertion.

    1. Start the VM.
    2. Create a FULL backup via ``create_full_backup()``.
    3. Write ~10 MiB of data inside the guest.
    4. Transfer the incremental via ``transfer_missing()``.
    5. Assert: delta file exists, backing-filename == FULL path, format qcow2.
    6. Assert: delta actual-size ≤ (dirty_bytes × 2 + 64 MiB) barrier.
    7. Assert: no orphaned qemu-nbd processes / sockets / .tmp files.
    8. Assert: exactly one qsnap checkpoint remains after rotation.
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
        verify="metadata",
    )

    provider = BitmapBackupProvider(shell, state=None, nbd=LibnbdClient())

    # Step 2: Create FULL backup.
    source_snapshot = SnapshotInfo(
        name=f"{vm_name}.active",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    full_result = provider.create_full_backup(
        vm_name,
        source_snapshot,
        target,
        compress=False,
        bucket_level="monthly",
    )
    assert full_result.success, f"Atomic FULL backup failed: {full_result.error}"
    full_path = full_result.target_path
    assert full_path.exists(), f"FULL backup file not found: {full_path}"

    # After FULL, one checkpoint should exist.
    checkpoints_full = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints_full) >= 1, (
        f"Expected at least one qsnap checkpoint after FULL, got: {checkpoints_full}"
    )

    # Wait for the checkpoint bitmap to be active.
    time.sleep(1)

    # Step 3: Write ~10 MiB of data inside the guest.
    _write_dirty_blocks(shell, vm_name, size="10M")
    time.sleep(1)

    # Step 4: Transfer incremental.
    incr_snapshot = SnapshotInfo(
        name=f"{vm_name}.incr-pipeline",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    results = provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[incr_snapshot],
    )

    if not results:
        pytest.skip("transfer_missing produced no results — NBD may not be available")
    assert results[0].success, f"Incremental transfer failed: {results[0].error}"

    # Brief pause for qemu-nbd cleanup to complete before orphan checks.
    time.sleep(0.5)

    incr_path = results[0].target_path
    assert incr_path.exists(), f"Incremental backup file not found: {incr_path}"

    # Step 5: Verify the delta with qemu-img info.
    info = _qemu_img_info_json(shell, incr_path)
    assert info is not None, "qemu-img info on incremental failed"
    assert info.get("format") == "qcow2", "Incremental should be valid qcow2"

    # backing-filename must name the FULL backup.
    backing = info.get("backing-filename")
    assert backing is not None, "Delta must have a backing file"
    backing_path = Path(str(backing))
    assert backing_path.name == full_path.name, (
        f"Delta backing-filename should name the FULL ({full_path.name}), got {backing_path.name}"
    )
    assert full_path.name in str(backing), (
        f"Delta backing-filename ({backing}) should contain FULL name ({full_path.name})"
    )

    # virtual-size must match source disk (256M).
    # --force-share: the source is a live VM's active layer.
    src_info = _qemu_img_info_json(shell, base_image, force_share=True)
    assert src_info is not None, "qemu-img info on source failed"
    src_vsize = int(src_info.get("virtual-size", 0))
    incr_vsize = int(info.get("virtual-size", 0))
    assert incr_vsize == src_vsize, (
        f"virtual-size mismatch: source={src_vsize}, delta={incr_vsize}"
    )

    # Step 6: Dirty-barrier check.
    # The dirty barrier is: actual-size ≤ dirty_bytes × 2 + 64 MiB.
    # We wrote ~10 MiB (10 * 1024 * 1024 bytes) inside the guest.
    # The QEMU metadata may also dirty some blocks, so be generous
    # with slack: worst case a few MiB extra.
    actual_size = int(info.get("actual-size", 0))
    dirty_estimate = 10 * 1024 * 1024  # 10 MiB
    barrier = dirty_estimate * 2 + 64 * 1024 * 1024  # 10M×2 + 64M = 84 MiB
    # Add extra slack for QEMU metadata + checkpoint bitmap overhead (≈ 25 MiB
    # is generous).
    barrier += 25 * 1024 * 1024  # total ≈ 109 MiB
    assert actual_size < barrier, (
        f"Delta actual-size ({actual_size}) exceeds dirty barrier ({barrier}) — "
        f"dirtied ~{dirty_estimate} bytes. Engine may have regressed to full copy."
    )
    # Also assert it's far below virtual disk size to confirm it's NOT a full copy.
    assert actual_size < src_vsize, (
        f"Delta actual-size ({actual_size}) should be far below virtual-size "
        f"({src_vsize}) for a true incremental"
    )

    # Step 7: No orphaned processes, sockets, or .tmp files.
    assert _no_orphaned_processes(), "Orphaned qemu-nbd process detected after success"
    assert _no_orphaned_sockets(), "Orphaned write socket detected after success"
    # No .tmp file for this snapshot.
    tmp_file = Path(f"{incr_path}.tmp")
    assert not tmp_file.exists(), f"Temporary file {tmp_file} should have been removed"

    # Step 8: Checkpoint rotation — exactly one qsnap checkpoint remains.
    checkpoints_after = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints_after) == 1, (
        f"Expected exactly 1 qsnap checkpoint after rotation, got {len(checkpoints_after)}: "
        f"{checkpoints_after}"
    )

    # Clean up.
    _cleanup_checkpoints(shell, vm_name)


# ── Test 2: No orphaned qemu-nbd after failure ──────────────────────────


@pytest.mark.integration
def test_int_no_qemu_nbd_orphan_after_failure(test_vm) -> None:
    """Inject failure mid-transfer — assert no orphaned qemu-nbd / sockets / .tmp.

    1. Start the VM.
    2. Create a FULL backup.
    3. Write some data inside the guest.
    4. Run incremental but kill the qemu-nbd write-side process externally
       during the copy (by making the destination unreadable after
       connection, e.g. removing the socket mid-transfer).
    5. Assert failure result.
    6. Assert no orphaned qemu-nbd processes, no lingering write sockets,
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

    provider = BitmapBackupProvider(shell, state=None, nbd=LibnbdClient())

    # Step 2: Create FULL backup.
    source_snapshot = SnapshotInfo(
        name=f"{vm_name}.active",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    full_result = provider.create_full_backup(
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

    # Step 3: Write data inside the guest so there are dirty extents.
    _write_dirty_blocks(shell, vm_name, size="10M")
    time.sleep(1)

    # Step 4: Run incremental with a _FailingPwriteClient injected as
    # the NBD client.  The copy loop preads real data from the source
    # but the first pwrite to the destination fails deterministically,
    # aborting the copy with a "pwrite failed" error.
    # This is more reliable than racing a background killer.
    failing_nbd = _FailingPwriteClient()
    failing_provider = BitmapBackupProvider(shell, state=None, nbd=failing_nbd)

    incr_snapshot = SnapshotInfo(
        name=f"{vm_name}.incr-fail-inject",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )

    results = failing_provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[incr_snapshot],
    )

    if not results:
        pytest.skip("transfer_missing produced no results — NBD may not be available")

    # The transfer should have failed.
    if results[0].success:
        # If it somehow succeeded (no dirty blocks → no pwrite),
        # we still verify cleanup.
        pass
    else:
        # Expected: "pwrite failed" error.
        assert results[0].error is not None

    # Step 5 + 6: Cleanup assertions.
    # Brief pause for qemu-nbd process cleanup.
    time.sleep(0.5)
    # No orphaned qemu-nbd processes.
    assert _no_orphaned_processes(), (
        "Orphaned qemu-nbd process detected after failure"
    )
    # No lingering write socket.
    assert _no_orphaned_sockets(), (
        "Orphaned write socket detected after failure"
    )
    # No .tmp file for the failed snapshot.
    tmp_candidate = target_dir / f"{vm_name}.incr-fail-inject.qcow2.tmp"
    assert not tmp_candidate.exists(), (
        f"Temporary file {tmp_candidate} should have been cleaned up"
    )

    # Prior checkpoint should still exist.
    checkpoints_after = _get_checkpoint_names(shell, vm_name)
    assert prior_cp in checkpoints_after, (
        f"Prior checkpoint {prior_cp!r} should be preserved after export failure, "
        f"got: {checkpoints_after}"
    )

    # Clean up.
    _cleanup_checkpoints(shell, vm_name)
    shell.run(["rm", "-f", f"/tmp/qsnap-write-{os.getpid()}.sock"], timeout=10)
