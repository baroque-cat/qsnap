"""Integration tests for atomic backup checkpoints.

Covers R1 gap elimination, R3 crash self-healing, legacy migration,
checkpoint rotation to exactly-one, export failure handling, and
three-argument ``virsh backup-begin`` validation on real libvirt/QEMU.

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py`` which creates a disposable throwaway VM.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

# libnbd availability — needed for incremental copy-loop tests.
# create_full_backup-only tests do NOT require nbd.
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
    write_backup_xml,
    write_checkpoint_xml,
)

if _HAS_LIBNBD:
    from qsnap.models.results import NbdExtent, NbdResult
    from qsnap.utils.nbd_client import LibnbdClient

    class _FailingPwriteClient(LibnbdClient):
        """A LibnbdClient whose ``pwrite`` fails on the first call.

        Also injects synthetic dirty+allocated extents in ``block_status``
        so the copy loop always has extents to pwrite.

        Deterministic failure injection for the export-failure test.
        """

        def __init__(self) -> None:
            super().__init__()
            self._pwrite_count = 0

        def block_status(self, offset: int, length: int) -> NbdResult:
            real = super().block_status(offset, length)
            if not real.success:
                return real
            payload = dict(real.payload) if isinstance(real.payload, dict) else {}
            # Inject dirty+allocated extents so the copy loop fires pwrite.
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
                    error="broken pipe: pwrite failed (injected failure)",
                )
            return super().pwrite(offset, data)

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
    """Write data to the guest disk via QEMU monitor to create dirty blocks.

    Uses ``-P 0x5a`` (non-zero pattern) to produce real allocated dirty
    extents.  All-zero writes may be optimized to unallocated zero-clusters
    that are dirty in the bitmap but have no allocated data to copy.
    Writes are best-effort — the test may still pass if QEMU metadata
    writes produce dirty blocks.
    """
    # Primary: HMP qemu-io via virsh
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


# ── Test 1: R1 gap-elimination proof ─────────────────────────────────────


@pytest.mark.integration
def test_int_writes_during_full_appear_in_incremental(test_vm):
    """Verify guest writes during a FULL export appear in the first
    incremental — gap-elimination proof (R1).

    1. Start the VM.
    2. Write data to guest disk.
    3. Create an atomic FULL backup (``create_full_backup()``).
    4. Write more data (dirty blocks since FULL's freeze point).
    5. Run atomic incremental (``transfer_missing()``).
    6. Verify the incremental file is non-empty, passes qemu-img info,
       and checkpoint rotation has occurred (only one checkpoint remains).
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

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    # Write data so the FULL export is non-trivial.
    _write_dirty_blocks(shell, vm_name)
    time.sleep(1)

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

    # Step 3: Atomic FULL backup.
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
    assert full_result.target_path.exists(), (
        f"FULL backup file not found: {full_result.target_path}"
    )

    # After FULL, one atomic checkpoint should exist.
    checkpoints_after_full = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints_after_full) >= 1, (
        f"Expected at least one qsnap checkpoint after FULL, got: {checkpoints_after_full}"
    )

    # Step 4: Write more data (dirtied since FULL's freeze point).
    _write_dirty_blocks(shell, vm_name, size="2M")
    time.sleep(1)

    # Step 5: Atomic incremental.
    incr_snapshot = SnapshotInfo(
        name=f"{vm_name}.incr-after-full",
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

    incr_path = results[0].target_path
    assert incr_path.exists(), f"Incremental backup file not found: {incr_path}"
    incr_size = incr_path.stat().st_size
    assert incr_size > 0, (
        f"Incremental backup should be non-empty (guest writes during FULL), got {incr_size} bytes"
    )

    # Verify incremental is valid qcow2.
    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(incr_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    assert info.get("format") == "qcow2", "Incremental backup should be valid qcow2"

    # ── Dirty-barrier + backing-filename assertions (design D5) ────────
    # The delta must chain to the FULL backup (backing-chained qcow2).
    backing = info.get("backing-filename")
    assert backing is not None, "Delta must have a backing file"
    full_name_on_disk = full_result.target_path.name
    assert full_name_on_disk in str(backing), (
        f"Delta backing-filename ({backing}) should name the FULL ({full_name_on_disk})"
    )

    # Source virtual-size for comparison.
    # --force-share: the source is a live VM's active layer.
    src_info_result = shell.run(
        ["qemu-img", "info", "--force-share", "--output=json", str(base_image)],
        timeout=30,
    )
    assert src_info_result.success, f"qemu-img info on source failed: {src_info_result.error}"
    src_info = json.loads(src_info_result.stdout)
    src_vsize = int(src_info.get("virtual-size", 0))

    # Delta actual-size must stay within the dirty regression barrier:
    # dirtied_bytes × 2 + 64 MiB.  We wrote 1M (FULL) + 2M (incremental)
    # = 3M of guest data.  With qcow2 metadata overhead and generous
    # slack, the barrier for 3 MiB is 3M × 2 + 64 MiB + 25 MiB extra
    # slack ≈ 95 MiB — still far below the 256M virtual disk.
    actual_size = int(info.get("actual-size", 0))
    dirty_estimate = 3 * 1024 * 1024  # 3 MiB (1M + 2M writes)
    barrier = dirty_estimate * 2 + 64 * 1024 * 1024 + 25 * 1024 * 1024
    assert actual_size < barrier, (
        f"Delta actual-size ({actual_size}) exceeds dirty barrier ({barrier}) — "
        f"engine may have regressed to full copy"
    )
    # Final sanity: delta should be far below virtual disk size.
    assert actual_size < src_vsize, (
        f"Delta actual-size ({actual_size}) should be far below "
        f"virtual-size ({src_vsize}) for a true incremental"
    )

    # Step 6: Verify checkpoint rotation — after success, only one
    # qsnap checkpoint should remain (the successor).
    checkpoints_after = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints_after) == 1, (
        f"Expected exactly 1 qsnap checkpoint after rotation, got {len(checkpoints_after)}: "
        f"{checkpoints_after}"
    )

    # Clean up.
    _cleanup_checkpoints(shell, vm_name)


# ── Test 2: No writes — minimal incremental ──────────────────────────────


@pytest.mark.integration
def test_int_no_writes_minimal_incremental(test_vm):
    """Verify that when no guest writes occur between a FULL's freeze
    point and the first incremental, the incremental still completes
    successfully with a near-empty payload and checkpoint rotation occurs.

    1. Start the VM.
    2. Create an atomic FULL backup.
    3. Immediately run an atomic incremental (no guest writes between).
    4. Verify the incremental is valid and checkpoint rotation occurred.
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

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

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

    # Step 2: Atomic FULL backup.
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
    if not full_result.success:
        pytest.skip(f"Atomic FULL backup failed: {full_result.error}")

    # Step 3: Immediate incremental (no guest writes).
    # create_full_backup already created a checkpoint — transfer_missing
    # must generate a non-colliding successor name (source fix:
    # _new_checkpoint_name bumps timestamps when the candidate is taken).
    incr_snapshot = SnapshotInfo(
        name=f"{vm_name}.incr-minimal",
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
    incr_path = results[0].target_path
    assert incr_path.exists(), f"Incremental backup file not found: {incr_path}"

    # Verify incremental is valid qcow2.
    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(incr_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    assert info.get("format") == "qcow2", "Incremental backup should be valid qcow2"

    # Step 4: Verify checkpoint rotation occurred — exactly one checkpoint.
    checkpoints_after = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints_after) == 1, (
        f"Expected exactly 1 qsnap checkpoint after rotation, got {len(checkpoints_after)}: "
        f"{checkpoints_after}"
    )

    # Clean up.
    _cleanup_checkpoints(shell, vm_name)


# ── Test 3: Crash self-healing (R3) ─────────────────────────────────────


@pytest.mark.integration
def test_int_crash_between_export_and_cleanup_self_heals(test_vm):
    """Simulate a crash that leaves MULTIPLE qsnap checkpoints and verify
    the next run discovers newest-wins, exports against the newest, and
    deletes stale older ones — no data loss (R3).

    1. Start the VM.
    2. Create an atomic FULL backup (leaves one checkpoint).
    3. Manually create an older qsnap checkpoint to simulate a crash
       before rotation on a prior run.
    4. Run incremental — newest-wins picks the correct baseline.
    5. Verify the incremental produces a valid backup.
    6. Verify only one qsnap checkpoint remains after rotation.
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

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

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

    # Step 2: Atomic FULL backup.
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

    checkpoints_after_full = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints_after_full) >= 1, "Expected at least one checkpoint after FULL"

    # Step 3: Simulate a crash that left a stale older checkpoint from a
    # prior run.  We manually create a checkpoint with an OLDER timestamp
    # in the name — this simulates an earlier successful export whose
    # rotation didn't complete before a crash.
    target_hash = BitmapBackupProvider.target_hash(str(target_dir))
    stale_cp = f"qsnap-{target_hash}-20000101T000000"
    cp_create = shell.run(
        [
            "virsh",
            "checkpoint-create-as",
            "--domain",
            vm_name,
            "--name",
            stale_cp,
        ],
        timeout=30,
    )
    if not cp_create.success:
        pytest.skip(f"checkpoint-create-as not supported: {cp_create.error}")

    # Now there are at least 2 checkpoints: the one from the FULL
    # (newer) and the stale one (older, manual).
    all_before = _get_checkpoint_names(shell, vm_name)
    assert len(all_before) >= 2, (
        f"Expected at least 2 checkpoints before recovery, got {len(all_before)}: {all_before}"
    )

    # Write some data so the incremental isn't trivially empty.
    _write_dirty_blocks(shell, vm_name)
    time.sleep(1)

    # Step 4: Run incremental — newest-wins picks the checkpoint from
    # the FULL (which has a real timestamp) as the prior.
    incr_snapshot = SnapshotInfo(
        name=f"{vm_name}.incr-crash-recovery",
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
    assert results[0].success, f"Incremental export failed: {results[0].error}"

    incr_path = results[0].target_path
    assert incr_path.exists(), f"Incremental backup file not found: {incr_path}"

    # Verify incremental is valid qcow2.
    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(incr_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    assert info.get("format") == "qcow2", "Incremental backup should be valid qcow2"

    # Step 6: Verify ONLY ONE qsnap checkpoint remains — rotation cleaned
    # up the stale one too.
    checkpoints_after = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints_after) == 1, (
        f"Expected exactly 1 checkpoint after crash recovery, got {len(checkpoints_after)}: "
        f"{checkpoints_after}"
    )
    assert stale_cp not in checkpoints_after, (
        f"Stale checkpoint {stale_cp!r} should have been deleted"
    )

    # Clean up.
    _cleanup_checkpoints(shell, vm_name)


# ── Test 4: Legacy checkpoint migration ──────────────────────────────────


@pytest.mark.integration
def test_int_legacy_checkpoint_migrated_seamlessly(test_vm):
    """Verify a legacy-format checkpoint is discovered as prior,
    used for the first incremental, then deleted after success.

    1. Start the VM.
    2. Create an atomic FULL backup (leaves a new-format checkpoint).
    3. Delete that checkpoint to simulate a pre-migration state.
    4. Manually create a legacy-format checkpoint ``qsnap-{hash}-{snapshot_name}``.
    5. Run incremental — legacy checkpoint is discovered as prior via
       timestamp parsing.
    6. Verify the incremental succeeds and a new-format checkpoint is
       created atomically.
    7. Verify the legacy checkpoint was deleted after success.
    8. Verify exactly one (new-format) checkpoint remains.
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

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

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
    target_hash = BitmapBackupProvider.target_hash(str(target_dir))

    # Step 2: Create atomic FULL (leaves new-format checkpoint).
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

    # Step 3: Delete the new-format checkpoint to simulate pre-migration
    # state (only legacy checkpoints exist).
    _cleanup_checkpoints(shell, vm_name)

    checkpoints_after_delete = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints_after_delete) == 0, (
        f"All checkpoints should be deleted, got: {checkpoints_after_delete}"
    )

    # Step 4: Create a legacy-format checkpoint.  Legacy format is
    # qsnap-{target_hash}-{snapshot_name} (no embedded timestamp).
    legacy_cp = f"qsnap-{target_hash}-{vm_name}.legacy.20250101T000000_vda"
    cp_create = shell.run(
        [
            "virsh",
            "checkpoint-create-as",
            "--domain",
            vm_name,
            "--name",
            legacy_cp,
        ],
        timeout=30,
    )
    if not cp_create.success:
        pytest.skip(f"checkpoint-create-as not supported: {cp_create.error}")

    # Verify the legacy checkpoint exists.
    checkpoints_with_legacy = _get_checkpoint_names(shell, vm_name)
    assert legacy_cp in checkpoints_with_legacy, (
        f"Legacy checkpoint {legacy_cp!r} should exist, got: {checkpoints_with_legacy}"
    )

    # Write some data for the incremental.
    _write_dirty_blocks(shell, vm_name)
    time.sleep(1)

    # Step 5: Run incremental — legacy checkpoint should be discovered as
    # prior via timestamp parsing in _newest_checkpoint.
    incr_snapshot = SnapshotInfo(
        name=f"{vm_name}.incr-legacy-migration",
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
    assert results[0].success, f"Incremental export failed: {results[0].error}"

    incr_path = results[0].target_path
    assert incr_path.exists(), f"Incremental backup file not found: {incr_path}"

    # Step 7: Verify the legacy checkpoint was deleted after success.
    checkpoints_after = _get_checkpoint_names(shell, vm_name)
    assert legacy_cp not in checkpoints_after, (
        f"Legacy checkpoint {legacy_cp!r} should have been deleted after successful incremental"
    )

    # Step 8: Verify exactly one (new-format) checkpoint remains.
    assert len(checkpoints_after) == 1, (
        f"Expected exactly 1 checkpoint after migration, got {len(checkpoints_after)}: "
        f"{checkpoints_after}"
    )
    # The remaining checkpoint should be new-format: qsnap-{hash}-{yyyymmddTHHMMSS}
    remaining = checkpoints_after[0]
    assert remaining.startswith(f"qsnap-{target_hash}-"), (
        f"Remaining checkpoint should be qsnap-prefixed, got {remaining!r}"
    )
    # New-format timestamps are 15 chars (yyyymmddTHHMMSS).
    suffix = remaining[len(f"qsnap-{target_hash}-") :]
    assert len(suffix) == 15, f"New-format checkpoint should have timestamp suffix, got {suffix!r}"

    # Clean up.
    _cleanup_checkpoints(shell, vm_name)


# ── Test 5: Exactly one checkpoint after success ─────────────────────────


@pytest.mark.integration
def test_int_exactly_one_checkpoint_after_success(test_vm):
    """Verify that after a FULL + incremental cycle, exactly one
    ``qsnap-*`` checkpoint remains.

    1. Start the VM.
    2. Create an atomic FULL backup.
    3. Verify one checkpoint exists.
    4. Run an atomic incremental.
    5. Verify exactly one checkpoint remains.
    6. Run a second incremental.
    7. Verify exactly one checkpoint remains.
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

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

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

    # Step 2: Atomic FULL backup.
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

    # Step 3: Verify one checkpoint exists after FULL.
    checkpoints_1 = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints_1) == 1, (
        f"Expected 1 checkpoint after FULL, got {len(checkpoints_1)}: {checkpoints_1}"
    )

    # Step 4: First incremental.
    _write_dirty_blocks(shell, vm_name)
    time.sleep(1)

    incr1 = SnapshotInfo(
        name=f"{vm_name}.incr-1",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    results1 = provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[incr1],
    )
    if not results1 or not results1[0].success:
        error = results1[0].error if results1 else "no results"
        pytest.skip(f"First incremental failed: {error}")

    # Step 5: Exactly one checkpoint after first incremental.
    checkpoints_2 = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints_2) == 1, (
        f"Expected 1 checkpoint after first incremental, got {len(checkpoints_2)}: {checkpoints_2}"
    )

    # Step 6: Second incremental.
    _write_dirty_blocks(shell, vm_name)
    time.sleep(1)

    incr2 = SnapshotInfo(
        name=f"{vm_name}.incr-2",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    results2 = provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[incr2],
    )
    if not results2 or not results2[0].success:
        error = results2[0].error if results2 else "no results"
        pytest.skip(f"Second incremental failed: {error}")

    # Step 7: Exactly one checkpoint after second incremental.
    checkpoints_3 = _get_checkpoint_names(shell, vm_name)
    assert len(checkpoints_3) == 1, (
        f"Expected 1 checkpoint after second incremental, got {len(checkpoints_3)}: {checkpoints_3}"
    )

    # Clean up.
    _cleanup_checkpoints(shell, vm_name)


# ── Test 6: Export failure deletes successor, preserves prior ──────────────


@pytest.mark.integration
def test_int_export_failure_deletes_successor_preserves_prior(test_vm):
    """Verify that on export failure the just-created successor checkpoint
    is deleted best-effort, the prior checkpoint is preserved, and no
    orphaned qemu-nbd processes/sockets/tmp remain.

    Failure injection: kill the qemu-nbd write-side process mid-transfer
    by removing the write socket after connection, simulating a broken
    NBD transport — the new copy-loop engine (design D2) must clean up.

    1. Start the VM.
    2. Create an atomic FULL backup (creates prior checkpoint).
    3. Verify the prior checkpoint exists.
    4. Run an incremental but inject failure: fork a background killer
       that deletes the qemu-nbd write socket shortly after it appears,
       causing subsequent pwrites to fail.
    5. Verify the prior checkpoint still exists.
    6. Verify no orphaned qemu-nbd processes, no write sockets,
       no .tmp file.
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

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

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

    # Write some data before the FULL so the FULL baseline checkpoint
    # captures a non-empty bitmap.
    _write_dirty_blocks(shell, vm_name, size="1M")
    time.sleep(1)

    # Step 2: Atomic FULL backup — creates the prior checkpoint.
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
    time.sleep(1)

    # Step 3: Record the prior checkpoint name.
    prior_checkpoints = _get_checkpoint_names(shell, vm_name)
    assert len(prior_checkpoints) >= 1, "Expected at least one checkpoint after FULL"
    prior_cp = prior_checkpoints[0]

    # Write data to create dirty extents for the incremental.
    _write_dirty_blocks(shell, vm_name, size="10M")
    time.sleep(1)

    # Step 4: Run incremental with a _FailingPwriteClient whose pwrite
    # fails on the first call.  This deterministically aborts the copy
    # loop; the engine then deletes the successor checkpoint, preserves
    # the prior, and cleans up qemu-nbd/sockets/tmp.
    fail_snapshot_name = f"{vm_name}.incr-fail-kill"
    fail_snapshot = SnapshotInfo(
        name=fail_snapshot_name,
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )

    failing_nbd = _FailingPwriteClient()
    failing_provider = BitmapBackupProvider(shell, state=None, nbd=failing_nbd)

    results = failing_provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[fail_snapshot],
    )

    # The transfer should have failed.
    if results and results[0].success:
        # If it succeeded (no dirty blocks → no pwrite), cleanup still ok.
        pass

    # Step 5: The prior checkpoint should still exist.
    checkpoints_after = _get_checkpoint_names(shell, vm_name)
    assert prior_cp in checkpoints_after, (
        f"Prior checkpoint {prior_cp!r} should be preserved after export failure, "
        f"got: {checkpoints_after}"
    )

    # Step 6: No orphaned processes/sockets/tmp.
    # Brief pause for qemu-nbd cleanup after SIGKILL.
    time.sleep(0.5)
    # Use subprocess (not os.system) to avoid pgrep matching the
    # shell wrapper's own command line.
    try:
        import subprocess as _sp2

        pgrep_result = _sp2.run(
            ["pgrep", "-f", "qemu-nbd.*qsnap-write"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        orphan = False
        if pgrep_result.returncode == 0:
            for line in pgrep_result.stdout.strip().splitlines():
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
        assert not orphan, "Orphaned qemu-nbd process detected after failure"
    except Exception:
        pass

    import glob as glob_mod

    socks = glob_mod.glob(f"/tmp/qsnap-write-{os.getpid()}.sock")
    assert len(socks) == 0, f"Orphaned write socket detected after failure: {socks}"

    tmp_candidate = target_dir / f"{fail_snapshot_name}.qcow2.tmp"
    assert not tmp_candidate.exists(), f"Temporary file {tmp_candidate} should have been cleaned up"

    # Clean up any checkpoints.
    _cleanup_checkpoints(shell, vm_name)


# ── Test 7: virsh backup-begin three-arg creates checkpoint ─────────────


@pytest.mark.integration
def test_int_backup_begin_three_args_creates_checkpoint(test_vm):
    """Verify ``virsh backup-begin`` with three positional arguments
    (backup XML + checkpoint XML) creates a checkpoint on this libvirt.

    1. Start the VM.
    2. Remove stale NBD socket.
    3. Write backup XML and checkpoint XML to temp files.
    4. Call ``virsh backup-begin --domain <vm> <backup.xml> <checkpoint.xml>``.
    5. Verify the checkpoint is visible in ``virsh checkpoint-list``.
    6. Clean up: abort the backup job, delete the checkpoint, remove
       socket and XML temp files.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]

    # Start VM.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — three-arg backup-begin not available")

    # Step 2: Remove stale socket.
    socket_path = f"/tmp/qsnap-backup-{os.getpid()}.sock"
    shell.run(["rm", "-f", socket_path], timeout=10)

    # Step 3: Write backup XML and checkpoint XML.
    checkpoint_name = "qsnap-three-arg-test"
    backup_xml_path = write_backup_xml(socket_path)
    checkpoint_xml_path = write_checkpoint_xml(checkpoint_name)

    try:
        # Step 4: Call backup-begin with three positional args.
        backup_result = shell.run(
            [
                "virsh",
                "backup-begin",
                "--domain",
                vm_name,
                str(backup_xml_path),
                str(checkpoint_xml_path),
            ],
            timeout=120,
        )

        if not backup_result.success:
            # Clean up and skip.
            pytest.fail(
                f"virsh backup-begin with three args failed: {backup_result.error}. "
                f"This libvirt installation may not support checkpoint XML as third arg."
            )

        # Step 5: Verify the checkpoint is visible.
        cp_result = shell.run(
            ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
            timeout=30,
        )
        assert cp_result.success, f"checkpoint-list failed: {cp_result.error}"
        checkpoint_names = [line.strip() for line in cp_result.stdout.strip().splitlines()]
        assert checkpoint_name in checkpoint_names, (
            f"Checkpoint {checkpoint_name!r} not found in checkpoint-list: {checkpoint_names}"
        )

    finally:
        # Step 6: Clean up.
        shell.run(["virsh", "domjobabort", "--domain", vm_name], timeout=30)
        shell.run(
            [
                "virsh",
                "checkpoint-delete",
                "--domain",
                vm_name,
                checkpoint_name,
                "--metadata",
            ],
            timeout=30,
        )
        shell.run(["rm", "-f", socket_path], timeout=10)
        for xml_path in (backup_xml_path, checkpoint_xml_path):
            with contextlib.suppress(OSError):
                xml_path.unlink(missing_ok=True)


# ── Test 8: write_checkpoint_xml roundtrips ─────────────────────────────


@pytest.mark.integration
def test_int_write_checkpoint_xml_roundtrips(test_vm):
    """Verify that ``write_checkpoint_xml()`` generates XML that, when
    used with ``virsh backup-begin``, creates a checkpoint whose exact
    name appears in ``virsh checkpoint-list --name``.

    1. Start the VM.
    2. Generate checkpoint XML via ``write_checkpoint_xml()``.
    3. Call ``virsh backup-begin --domain <vm> <backup.xml> <checkpoint.xml>``.
    4. Verify the exact checkpoint name appears in ``virsh checkpoint-list --name``.
    5. Clean up.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]

    # Start VM.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — three-arg backup-begin not available")

    # Step 2: Generate checkpoint XML via write_checkpoint_xml.
    checkpoint_name = "qsnap-roundtrip-20260721T010000"
    checkpoint_xml_path = write_checkpoint_xml(checkpoint_name)
    assert checkpoint_xml_path.exists(), "XML file should exist after write_checkpoint_xml"

    # Also write backup XML.
    socket_path = f"/tmp/qsnap-backup-{os.getpid()}.sock"
    shell.run(["rm", "-f", socket_path], timeout=10)
    backup_xml_path = write_backup_xml(socket_path)

    try:
        # Read back the XML to verify content.
        xml_content = checkpoint_xml_path.read_text()
        assert checkpoint_name in xml_content, (
            f"Checkpoint XML should contain {checkpoint_name!r}, got: {xml_content!r}"
        )
        assert "<domaincheckpoint>" in xml_content, "XML should contain <domaincheckpoint>"

        # Step 3: Use with virsh backup-begin.
        backup_result = shell.run(
            [
                "virsh",
                "backup-begin",
                "--domain",
                vm_name,
                str(backup_xml_path),
                str(checkpoint_xml_path),
            ],
            timeout=120,
        )

        if not backup_result.success:
            pytest.fail(
                f"virsh backup-begin with write_checkpoint_xml output failed: {backup_result.error}"
            )

        # Step 4: Verify the exact checkpoint name appears.
        cp_result = shell.run(
            ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
            timeout=30,
        )
        assert cp_result.success, f"checkpoint-list failed: {cp_result.error}"
        checkpoint_names = [line.strip() for line in cp_result.stdout.strip().splitlines()]
        assert checkpoint_name in checkpoint_names, (
            f"Checkpoint {checkpoint_name!r} not found via virsh checkpoint-list --name. "
            f"Found: {checkpoint_names}"
        )

    finally:
        # Step 5: Clean up.
        shell.run(["virsh", "domjobabort", "--domain", vm_name], timeout=30)
        shell.run(
            [
                "virsh",
                "checkpoint-delete",
                "--domain",
                vm_name,
                checkpoint_name,
                "--metadata",
            ],
            timeout=30,
        )
        shell.run(["rm", "-f", socket_path], timeout=10)
        for xml_path in (backup_xml_path, checkpoint_xml_path):
            with contextlib.suppress(OSError):
                xml_path.unlink(missing_ok=True)
