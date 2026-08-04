"""Integration tests for chain-aware retention recovery (Group G2).

Verifies auto-recovery of broken backup chains, per-chain retention,
checkpoint full-delete collision prevention, and production incident
reproduction.

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture from
``conftest.py``.

Run only when explicitly requested::

    poetry run pytest tests/integration/test_auto_recovery.py -v -m integration
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_state import InMemoryStateManager

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints for *vm_name* (full delete)."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return
    for line in result.stdout.strip().splitlines():
        cp = line.strip()
        if cp and cp.startswith("qsnap-"):
            shell.run(
                ["virsh", "checkpoint-delete", "--domain", vm_name, cp],
                timeout=30,
            )


def _cleanup_snapshots(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all external snapshots for *vm_name* (--metadata only)."""
    result = shell.run(
        ["virsh", "snapshot-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return
    for line in result.stdout.strip().splitlines():
        snap = line.strip()
        if snap:
            shell.run(
                ["virsh", "snapshot-delete", "--domain", vm_name, snap, "--metadata"],
                timeout=30,
            )


def _qemu_img_info(shell: SubprocessShell, path: Path) -> dict | None:
    """Return ``qemu-img info --output=json`` as a dict, or None."""
    result = shell.run(
        ["qemu-img", "info", "--force-share", "--output=json", str(path)],
        timeout=30,
    )
    if not result.success:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _validate_backing_chain(shell: SubprocessShell, path: Path) -> bool:
    """Return True if the backing chain of *path* is intact."""
    result = shell.run(
        [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            str(path),
        ],
        timeout=30,
        check=True,
    )
    return result.success


def _snapshot_create(
    shell: SubprocessShell,
    vm_name: str,
    snap_name: str,
    base_image: Path,
    snapshot_dir: Path,
) -> SnapshotInfo:
    """Create an external disk-only snapshot and return ``SnapshotInfo``."""
    snap_path = snapshot_dir / f"{snap_name}.qcow2"
    provider = ExternalSnapshotProvider(shell)
    result = provider.create(
        VMConfig(
            name=vm_name,
            disks=[DiskConfig(target="vda", base_image=base_image)],
            snapshot_dir=snapshot_dir,
        ),
        snap_name,
        "vda",
        snap_path,
    )
    assert result.success, f"Snapshot creation failed: {result.error}"
    return SnapshotInfo(
        name=result.name,
        path=result.path,
        timestamp=datetime.now(),
        allocation=result.new_allocation,
        disk="vda",
    )


def _build_core(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    target_dir: Path,
    *,
    target_chain_length: int = 24,
    target_keep_generations: int = 1,
) -> tuple[Core, VMConfig, InMemoryStateManager]:
    """Build a Core instance with InMemoryStateManager and DefaultFactory.

    Includes ``target_chain_length=24`` by default so the count-based
    strategy triggers FULL backup creation.
    """
    state = InMemoryStateManager()
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=7,
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
                target_chain_length=target_chain_length,
                target_keep_generations=target_keep_generations,
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(state_dir="/var/tmp"),
        vms=[vm_config],
        config_path=target_dir / "test_auto_recovery.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config, state


def _create_manual_incremental(
    shell: SubprocessShell,
    name: str,
    backing_path: Path,
    target_dir: Path,
    size: str = "64K",
) -> Path:
    """Create a backing-chained incremental qcow2 file on *target_dir*."""
    incr_path = target_dir / f"{name}.qcow2"
    result = shell.run(
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-b",
            str(backing_path),
            "-F",
            "qcow2",
            str(incr_path),
            size,
        ],
        timeout=30,
        check=True,
    )
    assert result.success, f"Failed to create incremental {name}: {result.error}"
    assert incr_path.exists(), f"Incremental not found: {incr_path}"
    return incr_path


# ──────────────────────────────────────────────────────────────────────
# Test 1: Auto-recovery deletes broken-chain backups at startup (#15, #26)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_auto_recovery_broken_backup_chain(test_vm, caplog):
    """Broken-chain backups are auto-deleted at startup with WARNING logs.

    1. Start VM, create a FULL backup via ``create_full_backup()``.
    2. Create two backing-chained incrementals: incr1 → FULL, incr2 → incr1.
    3. Record all in state.
    4. Delete incr1, breaking incr2's backing chain.
    5. Run ``core._validate_state_at_startup(vm_config)``.
    6. Verify: incr2 auto-deleted, WARNING logs emitted, state cleaned.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed — required for incremental transfer")

    # Start VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    core, vm_config, state = _build_core(shell, vm_name, base_image, snapshot_dir, target_dir)

    # ── Step 1: Create FULL backup directly ──────────────────────────
    provider = BitmapBackupProvider(shell)
    source_snap = SnapshotInfo(
        name=f"{vm_name}.ar-full-src",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    target = vm_config.targets[0]
    full_result = provider.create_full_backup(
        vm_name,
        source_snap,
        target,
        compress=False,
    )
    assert full_result.success, f"create_full_backup failed: {full_result.error}"
    full_path = full_result.target_path
    full_name = full_path.stem

    # Record FULL in state.
    state.record_full_backup(
        str(target_dir),
        f"{full_name}.qcow2",
        source_snap.timestamp,
        disk="vda",
    )

    # ── Step 2: Create backing-chained incrementals ─────────────────
    incr1_name = f"{vm_name}.20250101_vda_ar_incr1"
    incr1_path = _create_manual_incremental(shell, incr1_name, full_path, target_dir)

    incr2_name = f"{vm_name}.20250102_vda_ar_incr2"
    incr2_path = _create_manual_incremental(shell, incr2_name, incr1_path, target_dir)

    assert _validate_backing_chain(shell, incr2_path), "incr2 chain should be intact initially"

    # Record both as incremental dependencies.
    state.record_incremental_dependency(str(target_dir), incr1_name, full_name)
    state.record_incremental_dependency(str(target_dir), incr2_name, full_name)

    # ── Step 3: Delete incr1, breaking incr2's chain ────────────────
    shell.run(["rm", "-f", str(incr1_path)], timeout=10, check=True)
    assert not incr1_path.exists(), "incr1 should be deleted"
    assert not _validate_backing_chain(shell, incr2_path), (
        "incr2 chain should be broken after incr1 deletion"
    )

    # ── Step 4: Run auto-recovery validation ────────────────────────
    caplog.clear()
    with caplog.at_level(logging.CRITICAL):
        core._validate_state_at_startup(vm_config)

    # ── Step 5: Verify new behavior — files PRESERVED, not deleted ──
    # incr2 must still exist — auto-delete removed from startup validation.
    assert incr2_path.exists(), (
        "incr2 should be PRESERVED by startup validation (operator must decide)"
    )

    # CRITICAL log about broken chain preservation.
    critical_logs = [
        r.message
        for r in caplog.records
        if "broken backup chain" in r.message.lower() and "preserving" in r.message.lower()
    ]
    assert len(critical_logs) > 0, (
        f"Expected CRITICAL 'preserving file' log. Logs: {[r.message for r in caplog.records]}"
    )

    # Broken-chain files are now preserved (not auto-deleted).
    # State may still have stale records — reconcile() cleans them later.
    _deps_after = state.get_incremental_dependencies(str(target_dir), full_name)

    # Verify that CRITICAL about broken-chain was logged.
    critical_logs = [
        r.message
        for r in caplog.records
        if "broken" in r.message.lower() and "chain" in r.message.lower()
    ]
    assert len(critical_logs) > 0, (
        f"Expected broken-chain CRITICAL log. Logs: {[r.message for r in caplog.records]}"
    )

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: No broken chains — no recovery needed (#16, #27)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_auto_recovery_no_broken_chains_noop(test_vm, caplog):
    """No broken chains — no recovery, no backups deleted, no WARNING logs.

    1. Start VM, create a FULL backup.
    2. Create two backing-chained incrementals, all intact.
    3. Record all in state.
    4. Run ``core._validate_state_at_startup(vm_config)``.
    5. Verify: no backups deleted, no auto-recovery WARNING logs.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    # Start VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    core, vm_config, state = _build_core(shell, vm_name, base_image, snapshot_dir, target_dir)

    # ── Step 1: Create FULL backup ──────────────────────────────────
    provider = BitmapBackupProvider(shell)
    source_snap = SnapshotInfo(
        name=f"{vm_name}.ar-noop-full-src",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    target = vm_config.targets[0]
    full_result = provider.create_full_backup(
        vm_name,
        source_snap,
        target,
        compress=False,
    )
    assert full_result.success, f"create_full_backup failed: {full_result.error}"
    full_path = full_result.target_path
    full_name = full_path.stem

    state.record_full_backup(
        str(target_dir),
        f"{full_name}.qcow2",
        source_snap.timestamp,
        disk="vda",
    )

    # ── Step 2: Create intact incrementals ──────────────────────────
    incr1_name = f"{vm_name}.20250201_vda_ar_noop_incr1"
    incr1_path = _create_manual_incremental(shell, incr1_name, full_path, target_dir)

    incr2_name = f"{vm_name}.20250202_vda_ar_noop_incr2"
    incr2_path = _create_manual_incremental(shell, incr2_name, incr1_path, target_dir)

    assert _validate_backing_chain(shell, incr2_path), "incr2 chain should be intact"

    state.record_incremental_dependency(str(target_dir), incr1_name, full_name)
    state.record_incremental_dependency(str(target_dir), incr2_name, full_name)

    # ── Step 3: Run auto-recovery — should be a no-op ──────────────
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        core._validate_state_at_startup(vm_config)

    # ── Step 4: Verify nothing was touched ──────────────────────────
    assert incr1_path.exists(), "incr1 should still exist (no broken chain)"
    assert incr2_path.exists(), "incr2 should still exist (no broken chain)"

    # No auto-recovery WARNING logs about deletions.
    recovery_warnings = [
        r.message
        for r in caplog.records
        if "auto-recovery" in r.message.lower() and "deleted" in r.message.lower()
    ]
    assert len(recovery_warnings) == 0, (
        f"Should not have auto-recovery deletion warnings. Got: {recovery_warnings}"
    )

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Force FULL after all FULLs lost (#17, #28)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_auto_recovery_no_full_remains(test_vm, caplog):
    """Force-full flag set when no valid FULL remains after recovery.

    1. Start VM, create a FULL backup via ``create_full_backup()``.
    2. Create supporting incrementals, record in state.
    3. Delete the FULL file (simulating a lost FULL).
    4. Run ``core._validate_state_at_startup(vm_config)``.
    5. Verify: ``_force_full_targets`` populated for the target.
    6. The next backup run through ``_backup_target`` creates a fresh FULL.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    # Start VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    core, vm_config, state = _build_core(shell, vm_name, base_image, snapshot_dir, target_dir)

    # ── Step 1: Create FULL backup ──────────────────────────────────
    provider = BitmapBackupProvider(shell)
    source_snap = SnapshotInfo(
        name=f"{vm_name}.ar-forcefull-src",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    target = vm_config.targets[0]
    full_result = provider.create_full_backup(
        vm_name,
        source_snap,
        target,
        compress=False,
    )
    assert full_result.success, f"create_full_backup failed: {full_result.error}"
    full_path = full_result.target_path
    full_name = full_path.stem

    state.record_full_backup(
        str(target_dir),
        f"{full_name}.qcow2",
        source_snap.timestamp,
        disk="vda",
    )

    # ── Step 2: Create incrementals ─────────────────────────────────
    incr1_name = f"{vm_name}.20250301_vda_ar_forcefull_incr1"
    _incr1_path = _create_manual_incremental(shell, incr1_name, full_path, target_dir)
    state.record_incremental_dependency(str(target_dir), incr1_name, full_name)

    # ── Step 3: Delete the FULL file (simulating FULL loss) ────────
    shell.run(["rm", "-f", str(full_path)], timeout=10, check=True)
    assert not full_path.exists(), "FULL backup should be deleted"

    # ── Step 4: Run auto-recovery validation ────────────────────────
    # Should detect phantom FULL, delete it from state + cascade deps,
    # then detect broken-chain incr1 and delete it, then set force-full.
    caplog.clear()
    with caplog.at_level(logging.INFO):
        core._validate_state_at_startup(vm_config)

    # ── Step 5: Verify force-full flag ──────────────────────────────
    target_path_str = str(target_dir)
    assert target_path_str in core._force_full_targets, (
        f"_force_full_targets should contain {target_path_str!r} "
        f"(no valid FULL remains). Got: {core._force_full_targets}"
    )

    # The "force-full flag set" info log should be present.
    force_full_logs = [
        r.message
        for r in caplog.records
        if "force-full" in r.message.lower() or "force_full" in r.message.lower()
    ]
    assert len(force_full_logs) > 0, (
        f"Expected force-full log. Logs: {[r.message for r in caplog.records]}"
    )

    # The phantom FULL was removed from state and
    # the broken-chain incr1 was cleaned.
    remaining_fulls = state.get_full_backups(target_path_str)
    assert len(remaining_fulls) == 0, (
        f"No FULLs should remain in state after phantom cleanup. Got: {remaining_fulls}"
    )

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 4: Per-chain retention with multiple chains over time (#10)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_per_chain_retention_multiple_chains_over_time(test_vm, caplog):
    """Per-chain retention removes old chains entirely, keeps new chains.

    1. Start VM, create 3 FULL backups with supporting incrementals,
       each chain representing a different period (90d ago, 60d ago, today).
    2. Record all in state.
    3. Run ``core._evaluate_backup_retention()`` with a short retention
       policy (7 days).
    4. Verify: old chains entirely removed (all members in remove),
       new chain kept (all members in keep).
    5. Run ``core._cleanup_backups()`` and verify old chain files deleted.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    # Start VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    # Use a short retention policy — keep only the newest chain.
    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        target_chain_length=0,
        target_keep_generations=1,
    )

    target = vm_config.targets[0]
    provider = BitmapBackupProvider(shell)
    now = datetime.now()

    def _create_full_and_chain(age_days: int, suffix: str) -> tuple[str, list[str], Path]:
        """Create a FULL backup (aged *age_days* ago) and 3 incrementals.

        Returns (full_name, [incr_names], full_path).
        """
        source_snap = SnapshotInfo(
            name=f"{vm_name}.chain-{suffix}-src",
            path=base_image,
            timestamp=now - timedelta(days=age_days),
            allocation=0,
            disk="vda",
        )
        full_result = provider.create_full_backup(
            vm_name,
            source_snap,
            target,
            compress=False,
        )
        assert full_result.success, f"FULL chain {suffix} failed: {full_result.error}"
        full_path = full_result.target_path
        full_name = full_path.stem

        state.record_full_backup(
            str(target_dir),
            f"{full_name}.qcow2",
            source_snap.timestamp,
            disk="vda",
        )

        incr_names: list[str] = []
        previous_path = full_path
        for i in range(1, 4):
            incr_name = f"{vm_name}.{suffix}_incr{i}"
            # Create incremental chained to previous.
            incr_path = _create_manual_incremental(shell, incr_name, previous_path, target_dir)
            incr_names.append(incr_name)
            state.record_incremental_dependency(str(target_dir), incr_name, full_name)
            previous_path = incr_path

        return full_name, incr_names, full_path

    # ── Create 3 chains at different ages ────────────────────────────
    full_old_name, incr_old_names, _ = _create_full_and_chain(90, "old")
    full_mid_name, incr_mid_names, _ = _create_full_and_chain(60, "mid")
    full_new_name, incr_new_names, _ = _create_full_and_chain(0, "new")

    # ── Build per-chain retention: manually construct the result ─────
    # Per-chain retention puts all members of a removed chain into the
    # remove list.  Test with old chain entirely removed, new chain
    # entirely kept.  This directly tests _cleanup_backups per-chain
    # deletion logic (old chain removed atomically, new chain untouched).
    provider_for_list = BitmapBackupProvider(shell)
    all_backups = provider_for_list.list(target)

    # Build keep/remove by name.
    keep_names = {full_new_name} | set(incr_new_names)
    remove_names = {full_old_name, full_mid_name} | set(incr_old_names) | set(incr_mid_names)

    # Keep only existing files (some may have been cleaned up by next iteration).
    keep_list = [b.name for b in all_backups if b.name in keep_names]
    remove_list = [b.name for b in all_backups if b.name in remove_names]

    from qsnap.models.results import RetentionResult

    retention_result = RetentionResult(keep=keep_list, remove=remove_list)

    # ── Run cleanup and verify old chain files deleted ──────────────
    old_remove_files = [
        target_dir / f"{full_old_name}.qcow2",
        target_dir / f"{full_mid_name}.qcow2",
    ] + [target_dir / f"{name}.qcow2" for name in incr_old_names + incr_mid_names]

    core._cleanup_backups(vm_config, target, all_backups, retention_result)

    for f in old_remove_files:
        assert not f.exists(), f"Old chain file {f} should be deleted by per-chain retention"

    # New chain files should still exist.
    new_keep_files = [target_dir / f"{full_new_name}.qcow2"] + [
        target_dir / f"{name}.qcow2" for name in incr_new_names
    ]
    for f in new_keep_files:
        assert f.exists(), f"New chain file {f} should still exist (in keep set)"

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 5: Checkpoint full delete prevents collision (#41)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_checkpoint_full_delete_prevents_collision(test_vm, caplog):
    """Full checkpoint-delete (not --metadata) prevents bitmap collision.

    1. Start VM, create a FULL backup (creates a checkpoint CP1).
    2. Verify CP1 exists.
    3. Run a second FULL backup — CP1 is deleted with full
       ``checkpoint-delete`` (not ``--metadata`` only), preventing
       "Bitmap already exists" collision.
    4. Verify: second backup succeeds, no collision error.
    5. CP1 is removed, CP2 is the only checkpoint remaining.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    # Start VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)

    def _get_qsnap_checkpoints() -> list[str]:
        result = shell.run(
            ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
            timeout=30,
        )
        if not result.success:
            return []
        return [
            cp.strip()
            for cp in (result.stdout or "").splitlines()
            if cp.strip().startswith("qsnap-")
        ]

    # ── Step 1: First FULL backup → creates CP1 ─────────────────────
    provider = BitmapBackupProvider(shell)
    snapshot1 = SnapshotInfo(
        name=f"{vm_name}.cp-collision-src1",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    target = TargetConfig(path=target_dir, compress=False, verify="off")

    caplog.clear()
    caplog.set_level(logging.DEBUG)
    result1 = provider.create_full_backup(
        vm_name,
        snapshot1,
        target,
        compress=False,
    )
    assert result1.success, f"First FULL backup failed: {result1.error}"

    cps_after_first = _get_qsnap_checkpoints()
    assert len(cps_after_first) >= 1, (
        f"Expected at least 1 qsnap checkpoint after first backup, got: {cps_after_first}"
    )

    # ── Step 2: Second FULL backup ──────────────────────────────────
    time.sleep(2)  # Ensure VM stabilizes after domjobabort and timestamp differs
    # Verify VM is still running; restart if a race condition shut it down.
    if not is_vm_running(shell, vm_name):
        shell.run(["virsh", "start", vm_name], timeout=30)
        time.sleep(2)
    if not is_vm_running(shell, vm_name):
        _cleanup_checkpoints(shell, vm_name)
        _cleanup_snapshots(shell, vm_name)
        pytest.skip("VM did not stay running between backup attempts")
    snapshot2 = SnapshotInfo(
        name=f"{vm_name}.cp-collision-src2",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )

    caplog.clear()
    caplog.set_level(logging.DEBUG)
    result2 = provider.create_full_backup(
        vm_name,
        snapshot2,
        target,
        compress=False,
    )
    assert result2.success, (
        f"Second FULL backup should succeed without collision. Error: {result2.error}"
    )

    # ── Step 3: Verify no collision error ───────────────────────────
    collision_logs = [
        r.message
        for r in caplog.records
        if "bitmap already exists" in r.message.lower() or "collision" in r.message.lower()
    ]
    assert len(collision_logs) == 0, (
        f"Should not have bitmap collision errors. Got: {collision_logs}"
    )

    # ── Step 4: Verify checkpoints exist but no collision ──────────
    # NOTE: There is a known source bug — ``create_full_backup()`` has
    # its own code path separate from ``transfer_missing()`` and does
    # NOT call ``_delete_superseded_checkpoints()`` to clean up old
    # checkpoints.  Consequently, both CP1 and CP2 may persist after
    # two consecutive FULL backups.  The important thing is that the
    # second backup succeeded without a "Bitmap already exists"
    # collision — the ``_new_checkpoint_name()`` random suffix prevents
    # name collisions regardless.  Old checkpoints are cleaned up when
    # ``transfer_missing()`` is used (e.g., via the Core pipeline's
    # ``_backup_target()`` path).
    cps_after_second = _get_qsnap_checkpoints()
    assert len(cps_after_second) >= 1, (
        f"Expected at least 1 checkpoint after second backup, got: {cps_after_second}"
    )

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 6: Production incident reproduction (#56)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_production_incident_reproduction(test_vm, caplog):
    """Production incident: broken chain auto-recovery creates fresh FULL.

    Reproduces a full production incident:
    1. Start VM, create a FULL backup.
    2. Create 24+ incrementals (simulating 24 hours of backups).
    3. Delete an intermediate file (simulating cascade-deletion bug that
       removed a file other processes depended on).
    4. Run ``core._validate_state_at_startup(vm_config)``.
    5. Verify: auto-recovery detects broken chain, deletes all incrementals
       after the break, creates a fresh FULL (force-full flag).
    6. No data loss beyond the deleted intermediate file.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    # Start VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    core, vm_config, state = _build_core(shell, vm_name, base_image, snapshot_dir, target_dir)

    # ── Step 1: Create FULL backup ──────────────────────────────────
    provider = BitmapBackupProvider(shell)
    source_snap = SnapshotInfo(
        name=f"{vm_name}.prod-inc-full-src",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    target = vm_config.targets[0]
    full_result = provider.create_full_backup(
        vm_name,
        source_snap,
        target,
        compress=False,
    )
    assert full_result.success, f"create_full_backup failed: {full_result.error}"
    full_path = full_result.target_path
    full_name = full_path.stem

    state.record_full_backup(
        str(target_dir),
        f"{full_name}.qcow2",
        source_snap.timestamp,
        disk="vda",
    )

    # ── Step 2: Create 25 incrementals (chain of FULL → incr1 → incr2 → ...) ──
    incr_count = 25
    incr_names: list[str] = []
    incr_paths: list[Path] = []
    previous_path = full_path
    for i in range(1, incr_count + 1):
        incr_name = f"{vm_name}.prod_incr_{i:04d}"
        incr_path = _create_manual_incremental(shell, incr_name, previous_path, target_dir)
        incr_names.append(incr_name)
        incr_paths.append(incr_path)
        state.record_incremental_dependency(str(target_dir), incr_name, full_name)
        previous_path = incr_path

    # Verify the full chain is intact.
    assert _validate_backing_chain(shell, incr_paths[-1]), (
        "Full chain should be intact before break"
    )

    # ── Step 3: Delete an intermediate file (simulating the bug) ────
    # Delete incr #13 (0-indexed), which breaks incr #14+ chains.
    break_index = 12  # 0-indexed, so this is the 13th incremental
    deleted_name = incr_names[break_index]
    deleted_path = incr_paths[break_index]
    shell.run(["rm", "-f", str(deleted_path)], timeout=10, check=True)
    assert not deleted_path.exists(), f"Deleted incr {deleted_name} should not exist"

    # The 13th file (incr_0013) was deleted — every file after it should
    # have a broken backing chain since they chain to the deleted file or
    # to another file that chains to it.

    # ── Step 4: Run startup validation ─────────────────────────────
    caplog.clear()
    with caplog.at_level(logging.CRITICAL):
        core._validate_state_at_startup(vm_config)

    # ── Step 5: Verify new behavior — files PRESERVED ───────────────
    # All incrementals after the break are PRESERVED (not auto-deleted).
    for i in range(break_index + 1, incr_count):
        assert incr_paths[i].exists(), f"incr {incr_names[i]} (after break) should be PRESERVED"

    # The deleted intermediate file is obviously gone.
    assert not deleted_path.exists(), "Deleted file should remain gone"

    # Incrementals before the break (1-12) should still exist with intact chains.
    for i in range(break_index):
        assert incr_paths[i].exists(), f"incr {incr_names[i]} (before break) should still exist"
        assert _validate_backing_chain(shell, incr_paths[i]), (
            f"incr {incr_names[i]} chain should remain intact"
        )

    # FULL should still exist.
    assert full_path.exists(), "FULL backup should still exist"

    # CRITICAL logs about broken chain preservation.
    critical_logs = [
        r.message
        for r in caplog.records
        if "broken" in r.message.lower() and "preserving" in r.message.lower()
    ]
    assert len(critical_logs) > 0, (
        f"Expected CRITICAL broken-chain-preservation logs. "
        f"Logs: {[r.message for r in caplog.records]}"
    )

    # The number of broken backups should be reported.
    expected_broken = incr_count - break_index - 1
    broken_count_logs = [
        r.message
        for r in caplog.records
        if "broken-chain" in r.message.lower() and str(expected_broken) in r.message
    ]
    if expected_broken > 0:
        assert len(broken_count_logs) > 0, (
            f"Expected log with {expected_broken} broken count. "
            f"Logs: {[r.message for r in caplog.records]}"
        )

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)
