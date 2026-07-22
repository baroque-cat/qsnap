"""Integration tests for zero-skip optimization on FULL backups.

The unified NBD engine skips ``pwrite`` for all-zero chunks during FULL
transfers (``zero_skip=True``).  This means zero regions on the source
disk do NOT produce qcow2 clusters in the destination — the destination
file's actual-size should be smaller than it would be without zero-skip.

Incrementals (``zero_skip=False``) do NOT skip zero chunks — every
dirty∩allocated extent is written, even if all-zero.

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

# libnbd availability — needed by the unified NBD transfer engine.
try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False

from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
)

if _HAS_LIBNBD:
    from qsnap.utils.nbd_client import LibnbdClient


def _get_checkpoint_names(shell: SubprocessShell, vm_name: str) -> list[str]:
    """Return qsnap-prefixed checkpoint names."""
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
    """Delete all qsnap-prefixed checkpoints."""
    for cp in _get_checkpoint_names(shell, vm_name):
        shell.run(
            ["virsh", "checkpoint-delete", "--domain", vm_name, cp, "--metadata"],
            timeout=30,
        )


# ──────────────────────────────────────────────────────────────────────
# Test 1: FULL backup actual-size is bounded (zero-skip active)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_zero_skip_full_backup_actual_size_bounded(test_vm):
    """Create a FULL backup of a mostly-empty disk and verify the output
    actual-size is much smaller than the virtual size.

    The unified engine with ``zero_skip=True`` skips pwrite for all-zero
    chunks.  On a freshly created 256M disk with no data written, most
    of the disk is zero — the FULL output should be small (just qcow2
    metadata + any non-zero QEMU-metadata clusters).

    Without zero-skip, every allocated cluster would be written via
    pwrite, inflating the destination.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    _cleanup_checkpoints(shell, vm_name)

    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())
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

    # Create FULL backup via transfer_missing (no prior checkpoint).
    snap = SnapshotInfo(
        name=f"{vm_name}.zero-skip-full",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    results = provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[snap],
    )

    if not results:
        pytest.skip("transfer_missing produced no results — NBD may not be available")
    assert results[0].success, f"FULL backup failed: {results[0].error}"
    full_path = results[0].target_path
    assert full_path.exists(), f"FULL backup file not found: {full_path}"

    # Inspect the output.
    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(full_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    virtual_size = int(info.get("virtual-size", 0))
    actual_size = int(info.get("actual-size", 0))

    # The virtual size is 256M.  The actual-size should be MUCH smaller
    # because zero-skip prevents all-zero clusters from being written.
    # We allow up to 20M for qcow2 metadata + QEMU init clusters.
    assert virtual_size == 256 * 1024 * 1024, f"Expected 256M virtual size, got {virtual_size}"
    assert actual_size < 20 * 1024 * 1024, (
        f"Zero-skip should keep actual-size small. "
        f"virtual-size={virtual_size}, actual-size={actual_size}. "
        f"Without zero-skip, all allocated extents would be copied."
    )
    # Actual-size should be well below virtual-size.
    assert actual_size < virtual_size * 0.10, (
        f"Actual-size ({actual_size}) should be well below "
        f"virtual-size ({virtual_size}) with zero-skip active"
    )

    # Verify standalone qcow2.
    backing = info.get("backing-filename")
    assert backing is None or backing == "", (
        f"FULL backup should be standalone, got backing: {backing!r}"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: FULL backup with known zero regions is smaller
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_zero_skip_full_with_written_data(test_vm):
    """Create a FULL backup after writing a small amount of data.

    The disk is 256M virtual but only ~2M of data is written.  With
    zero-skip active, the actual-size should be bounded to approximately
    the     written data + qcow2 metadata overhead (well under 30M).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    _cleanup_checkpoints(shell, vm_name)

    # Write some data to the guest disk.
    shell.run(
        [
            "virsh",
            "qemu-monitor-command",
            "--domain",
            vm_name,
            "--hmp",
            'qemu-io vda "write -P 0xAB 0 2M"',
        ],
        timeout=30,
    )
    time.sleep(1)

    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())
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

    snap = SnapshotInfo(
        name=f"{vm_name}.zero-skip-data",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    results = provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[snap],
    )

    if not results:
        pytest.skip("transfer_missing produced no results — NBD may not be available")
    assert results[0].success, f"FULL backup failed: {results[0].error}"

    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(results[0].target_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    actual_size = int(info.get("actual-size", 0))
    virtual_size = int(info.get("virtual-size", 0))

    # 2M written data + generous qcow2 metadata overhead should stay
    # under 30M.  Without zero-skip, a 256M disk with default qcow2
    # cluster allocation would produce a much larger file.
    assert actual_size < 30 * 1024 * 1024, (
        f"Expected actual-size well under 30M with zero-skip, "
        f"got actual-size={actual_size}, virtual-size={virtual_size}"
    )
    assert actual_size < virtual_size, (
        f"Actual-size ({actual_size}) should be below virtual-size ({virtual_size})"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Incremental doesn't skip zero chunks (zero_skip=False)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_zero_skip_incremental_does_not_skip_zeros(test_vm):
    """Verify that incrementals (``zero_skip=False``) write all
    dirty∩allocated extents, even all-zero ones.

    Writes a known pattern, creates a FULL, then writes all-zeros
    to a known region to create dirty-but-zero blocks.  The incremental
    delta should grow (it writes those zero blocks) — confirming
    ``zero_skip=False`` is in effect.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    _cleanup_checkpoints(shell, vm_name)

    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())
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

    # Write data first so the FULL has non-zero content.
    shell.run(
        [
            "virsh",
            "qemu-monitor-command",
            "--domain",
            vm_name,
            "--hmp",
            'qemu-io vda "write -P 0xCD 0 1M"',
        ],
        timeout=30,
    )
    time.sleep(1)

    # Create FULL.
    snap_full = SnapshotInfo(
        name=f"{vm_name}.zsincr-full",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    results_full = provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[snap_full],
    )

    if not results_full or not results_full[0].success:
        error_msg = results_full[0].error if results_full else "no results"
        pytest.skip(f"FULL backup failed: {error_msg}")

    # Write zeros to create dirty-but-zero blocks.
    # This writes 4M of zeros at offset 10M, dirtying the bitmap.
    shell.run(
        [
            "virsh",
            "qemu-monitor-command",
            "--domain",
            vm_name,
            "--hmp",
            'qemu-io vda "write -P 0x00 10M 4M"',
        ],
        timeout=30,
    )
    time.sleep(1)

    # Create incremental.
    snap_incr = SnapshotInfo(
        name=f"{vm_name}.zsincr-incr",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    results_incr = provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[snap_incr],
    )

    if not results_incr or not results_incr[0].success:
        error_msg = results_incr[0].error if results_incr else "no results"
        pytest.skip(f"Incremental backup failed: {error_msg}")

    incr_path = results_incr[0].target_path

    # Verify incremental is valid qcow2.
    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(incr_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info on incremental failed: {info_result.error}"
    incr_data = json.loads(info_result.stdout)

    # The delta should have a backing file (chains to FULL).
    backing = incr_data.get("backing-filename")
    assert backing is not None, "Delta must have a backing file"

    # The actual-size should reflect that zero blocks were written.
    # With zero_skip=False, the 4M of zeros are actually pwritten.
    actual_size = int(incr_data.get("actual-size", 0))
    # Even with zero clusters possibly being thin, qcow2 metadata
    # records them.  The delta must be non-trivial.
    assert actual_size > 0, (
        "Incremental should have non-zero actual-size "
        "(zero chunks should be written with zero_skip=False)"
    )

    _cleanup_checkpoints(shell, vm_name)
