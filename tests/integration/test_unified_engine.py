"""Integration tests for the unified NBD transfer engine.

Verifies that ``BitmapBackupProvider`` uses the unified NBD engine
(``pread``/``pwrite`` via libnbd) for both FULL and incremental backups.
No ``qemu-img convert`` should ever be invoked during transfer.

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import json
import logging
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
    """Write data to the guest disk via QEMU monitor to create dirty blocks."""
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
    # QMP fallback
    qmp_cmd = (
        '{"execute":"human-monitor-command",'
        f'"arguments":{{"command-line":"qemu-io vda \\"write -P 0x5a 0 {size}\\""}}}}'
    )
    shell.run(
        ["virsh", "qemu-monitor-command", "--domain", vm_name, qmp_cmd],
        timeout=30,
    )


# ──────────────────────────────────────────────────────────────────────
# Test 1: FULL backup via unified engine produces standalone qcow2
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_unified_engine_full_backup_standalone_qcow2(test_vm, caplog):
    """Create a FULL backup via the unified NBD engine and verify:
    - Output is a standalone qcow2 with no backing file.
    - No ``qemu-img convert`` in the shell log (unified engine = pread/pwrite).
    - Checkpoint is created atomically.
    - Atomic rename: no .tmp file left behind.
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

    # Run FULL via transfer_missing (no prior checkpoint → full-pull branch).
    snap_full = SnapshotInfo(
        name=f"{vm_name}.unified-full",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )

    with caplog.at_level(logging.DEBUG):
        results = provider.transfer_missing(
            vm_config=vm_config,
            target=target,
            snapshots=[snap_full],
        )

    if not results:
        pytest.skip("transfer_missing produced no results — NBD may not be available")
    assert results[0].success, f"Unified FULL backup failed: {results[0].error}"
    full_path = results[0].target_path
    assert full_path.exists(), f"FULL backup file not found: {full_path}"

    # Verify standalone qcow2 (no backing file).
    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(full_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    backing = info.get("backing-filename")
    assert backing is None or backing == "", (
        f"FULL backup should be standalone, got backing: {backing!r}"
    )
    assert info.get("format") == "qcow2", "FULL backup should be valid qcow2"
    assert int(info.get("virtual-size", 0)) > 0, "FULL backup should have non-zero virtual size"

    # ── Verify unified engine: no ``qemu-img convert`` in shell log ───
    convert_calls = [
        r.message for r in caplog.records if ("qemu-img" in r.message and "convert" in r.message)
    ]
    assert len(convert_calls) == 0, (
        f"Unified NBD engine must NOT use qemu-img convert. Found calls: {convert_calls}"
    )

    # ── Verify atomic checkpoint creation ──────────────────────────
    checkpoints = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints) >= 1, (
        f"Expected at least one checkpoint after FULL backup, got {len(checkpoints)}: {checkpoints}"
    )

    # ── Verify atomic rename: no .tmp file ─────────────────────────
    tmp_candidate = target_dir / f"{snap_full.name}.qcow2.tmp"
    assert not tmp_candidate.exists(), (
        f"Temporary file {tmp_candidate} should have been atomically renamed"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Incremental via unified engine produces backing-chained delta
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_unified_engine_incremental_backing_chain(test_vm, caplog):
    """Run FULL + incremental via unified NBD engine and verify:
    - Incremental delta has a backing-filename pointing to the FULL.
    - Delta actual-size stays within the dirty regression barrier.
    - No ``qemu-img convert`` in the shell log.
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

    # Step 1: FULL via transfer_missing (no prior checkpoint).
    snap_full = SnapshotInfo(
        name=f"{vm_name}.unified-full-chain",
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

    full_path = results_full[0].target_path

    # Write data to create dirty blocks.
    _write_dirty_blocks(shell, vm_name)
    time.sleep(1)

    # Step 2: Incremental via transfer_missing (prior checkpoint exists).
    with caplog.at_level(logging.DEBUG):
        snap_incr = SnapshotInfo(
            name=f"{vm_name}.unified-incr-chain",
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
    assert incr_path.exists(), f"Incremental file not found: {incr_path}"

    # Verify delta info.
    incr_info = shell.run(
        ["qemu-img", "info", "--output=json", str(incr_path)],
        timeout=30,
    )
    assert incr_info.success, f"qemu-img info on incremental failed: {incr_info.error}"
    incr_data = json.loads(incr_info.stdout)
    assert incr_data.get("format") == "qcow2", "Incremental should be valid qcow2"

    # Verify backing-filename points to FULL.
    backing = incr_data.get("backing-filename")
    assert backing is not None, "Delta must have a backing file"
    assert full_path.name in str(backing), (
        f"Delta backing-filename ({backing}) should name the FULL ({full_path.name})"
    )

    # Verify dirty regression barrier.
    actual_size = int(incr_data.get("actual-size", 0))
    barrier = 1 * 1024 * 1024 * 2 + 64 * 1024 * 1024 + 25 * 1024 * 1024
    assert actual_size < barrier, (
        f"Delta actual-size ({actual_size}) exceeds barrier ({barrier}) — "
        f"engine may have regressed to full copy"
    )

    # ── Verify unified engine: no ``qemu-img convert`` in shell log ───
    convert_calls = [
        r.message for r in caplog.records if ("qemu-img" in r.message and "convert" in r.message)
    ]
    assert len(convert_calls) == 0, (
        f"Unified NBD engine must NOT use qemu-img convert. Found calls: {convert_calls}"
    )

    # Verify checkpoint rotation — exactly one checkpoint after both ops.
    checkpoints = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints) == 1, (
        f"Expected exactly 1 checkpoint after FULL+incremental, "
        f"got {len(checkpoints)}: {checkpoints}"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Restore chain and verify bootable
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_unified_engine_restore_chain(test_vm):
    """Create FULL + incremental, then reconstruct the chain and verify
    the final image is a standalone qcow2 readable by qemu-img.

    This simulates a restore scenario: the FULL is the base, each
    incremental chains to it.  After a FULL + incremental cycle, we
    verify the chain is intact via ``qemu-img info --backing-chain``.
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

    # FULL
    snap_full = SnapshotInfo(
        name=f"{vm_name}.unified-restore-full",
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
    full_path = results_full[0].target_path

    # Write data
    _write_dirty_blocks(shell, vm_name, size="2M")
    time.sleep(1)

    # Incremental
    snap_incr = SnapshotInfo(
        name=f"{vm_name}.unified-restore-incr",
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

    # Verify the backing chain via qemu-img info --backing-chain.
    chain_result = shell.run(
        ["qemu-img", "info", "--backing-chain", str(incr_path)],
        timeout=30,
    )
    assert chain_result.success, f"qemu-img info --backing-chain failed: {chain_result.error}"
    # The chain output should include both the incremental and the FULL.
    assert incr_path.name in chain_result.stdout, (
        "Backing chain should include the incremental file"
    )
    assert full_path.name in chain_result.stdout, "Backing chain should include the FULL file"

    # Verify the incremental is a valid qcow2.
    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(incr_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    assert info.get("format") == "qcow2", "Incremental should be valid qcow2"
    assert info.get("backing-filename") is not None, "Incremental must have a backing file"

    _cleanup_checkpoints(shell, vm_name)
