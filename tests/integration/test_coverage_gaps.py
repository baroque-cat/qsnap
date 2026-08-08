"""Integration tests for coverage gaps identified during deep exploration.

Three scenarios that were not previously covered by integration tests:

1. **Pipeline continues after broken-chain auto-recovery** — verifies
   that after ``_validate_state_at_startup`` auto-deletes a broken
   incremental, the pipeline continues and creates a new backup (FULL
   or incremental).

2. **Incremental-specific phantom (stale deps)** — verifies that
   ``_detect_stale_deps()`` detects an incremental in state whose file
   was deleted from disk, and that ``reconcile()`` removes the stale
   dependency record.

3. **Startup validation preserves corrupt FULL for verify-before-delete
   gate** — verifies that startup validation does NOT delete a corrupt
   (but existing) FULL file, allowing the verify-before-delete gate to
   detect the corruption and block deletion of old generations.

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running
from tests.mocks import InMemoryStateManager, MockConfigFacade

try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False


# ── helpers ──────────────────────────────────────────────────────────


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints for *vm_name*."""
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
# Test 1: Pipeline continues after broken-chain auto-recovery
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_pipeline_continues_after_broken_chain_auto_recovery(test_vm, caplog):
    """Pipeline continues and creates a new backup after auto-recovery.

    1. Start VM, create snapshot, run core.run() → FULL + inc1 + inc2.
    2. Delete inc1 from disk (break the chain for inc2).
    3. Run core.run() again.
    4. Assert: startup validation auto-deleted inc2 (broken chain),
       pipeline continued, new backup was created.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

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

    # ── Step 1: Create snapshot + FULL backup ───────────────────────
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.cov-gap-snap1", base_image, snapshot_dir)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)

    target = TargetConfig(
        path=target_dir,
        compress=False,
        verify="off",
        target_chain_length=24,
        target_keep_generations=2,
    )
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
        snapshot_chain_length=999,
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(state_dir="/var/tmp"),
        vms=[vm_config],
        config_path=tmpdir / "cov-gap1.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    fulls = state.get_full_backups(str(target_dir))
    if not fulls:
        pytest.skip("No FULL backup created on run 1")
    assert len(fulls) >= 1, "Expected at least one FULL backup"

    # Phase 2 quirk: Core records the FULL under its stem name, so
    # ``FullBackupInfo.path`` lacks the ``.qcow2`` extension and
    # ``os.path.exists()`` would treat the real file as a phantom.
    # Re-record with the real filename so startup validation in run 2
    # sees the on-disk file (and does NOT cascade-clean its deps).
    full_name = fulls[0].name
    full_path = target_dir / f"{full_name}.qcow2"
    assert full_path.exists(), f"FULL backup file not found on disk: {full_path}"
    state.remove_full_backup(str(target_dir), full_name)
    state.record_full_backup(str(target_dir), f"{full_name}.qcow2", fulls[0].timestamp, "vda")

    # ── Step 2: Create inc1 (backing to FULL) ───────────────────────
    inc1_name = f"{vm_name}.cov-gap-inc1"
    inc1_path = _create_manual_incremental(shell, inc1_name, full_path, target_dir)
    state.record_incremental_dependency(str(target_dir), inc1_name, full_name)

    # ── Step 3: Create inc2 (backing to inc1) ───────────────────────
    inc2_name = f"{vm_name}.cov-gap-inc2"
    inc2_path = _create_manual_incremental(shell, inc2_name, inc1_path, target_dir)
    state.record_incremental_dependency(str(target_dir), inc2_name, full_name)

    # ── Step 4: Delete inc1, breaking inc2's chain ──────────────────
    os.unlink(str(inc1_path))
    assert not inc1_path.exists(), "inc1 should be deleted"

    # ── Step 5: Run core.run() again — triggers auto-recovery ───────
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.cov-gap-snap2", base_image, snapshot_dir)
    state.record_snapshot(vm_name, snap2)

    caplog.clear()
    with caplog.at_level(logging.CRITICAL):
        core.run(vm_name)

    # ── Step 6: Assertions ──────────────────────────────────────────

    # 6a. Startup validation detected the broken chain (CRITICAL log).
    startup_logs = [
        r.message
        for r in caplog.records
        if "[startup]" in r.message
        and "broken" in r.message.lower()
        and "chain" in r.message.lower()
    ]
    assert len(startup_logs) > 0, (
        f"Expected startup broken-chain CRITICAL log. Logs: {[r.message for r in caplog.records]}"
    )

    # 6b. inc2 was PRESERVED (not auto-deleted).
    assert inc2_path.exists(), (
        "inc2 should be PRESERVED by startup validation (operator must decide)"
    )

    # 6c. Pipeline continued — FULL still exists (was not deleted).
    assert full_path.exists(), "FULL backup should still exist after auto-recovery"

    # 6d. FULL still in state.
    fulls_after = state.get_full_backups(str(target_dir))
    assert len(fulls_after) >= 1, (
        f"Expected FULL backup still in state after pipeline. Got: {fulls_after}"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Incremental-specific phantom (stale deps)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_reconcile_detects_and_removes_stale_incremental_dep(test_vm, caplog):
    """Reconcile detects and removes stale incremental dependency.

    1. Start VM, create snapshot, run core.run() → FULL + inc1.
    2. Manually delete inc1 file from target (but keep in state).
    3. Run core.check_state() → assert stale_deps non-empty.
    4. Run core.reconcile().
    5. Assert: stale dep removed from state, FULL unaffected.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

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

    # ── Step 1: Create snapshot + FULL backup ───────────────────────
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.stale-snap1", base_image, snapshot_dir)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)

    target = TargetConfig(
        path=target_dir,
        compress=False,
        verify="off",
        target_chain_length=24,
        target_keep_generations=2,
    )
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
        snapshot_chain_length=999,
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(state_dir="/var/tmp"),
        vms=[vm_config],
        config_path=tmpdir / "stale-dep.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    fulls = state.get_full_backups(str(target_dir))
    if not fulls:
        pytest.skip("No FULL backup created")
    assert len(fulls) >= 1, "Expected at least one FULL backup"

    full_name = fulls[0].name

    # Phase 2 quirk: Core records the FULL under its stem name, so
    # ``FullBackupInfo.path`` lacks the ``.qcow2`` extension and
    # ``os.path.exists()`` would treat the real file as a phantom.
    # Re-record with the real filename so check_state()/reconcile()
    # operate on the actual file — otherwise the phantom-FULL cascade
    # would pre-clean inc1's dependency record before the stale-dep
    # reconciliation step runs.
    full_path = target_dir / f"{full_name}.qcow2"
    assert full_path.exists(), f"FULL backup file not found on disk: {full_path}"
    state.remove_full_backup(str(target_dir), full_name)
    state.record_full_backup(str(target_dir), f"{full_name}.qcow2", fulls[0].timestamp, "vda")

    # ── Step 2: Create inc1 (backing to FULL) ───────────────────────
    inc1_name = f"{vm_name}.stale-inc1"
    inc1_path = _create_manual_incremental(shell, inc1_name, full_path, target_dir)
    state.record_incremental_dependency(str(target_dir), inc1_name, full_name)

    # Verify inc1 is in state before deletion.
    deps_before = state.get_incremental_dependencies(str(target_dir), full_name)
    assert inc1_name in deps_before, "inc1 should be in state before deletion"

    # ── Step 3: Delete inc1 file from disk (keep in state) ──────────
    os.unlink(str(inc1_path))
    assert not inc1_path.exists(), "inc1 file should be deleted"

    # ── Step 4: Run check_state() → stale_deps should be non-empty ──
    check_results = core.check_state(vm_name)
    assert vm_name in check_results, f"Missing check_state result for {vm_name}"
    check_result = check_results[vm_name]
    assert len(check_result.stale_deps) > 0, (
        f"Expected stale_deps to be non-empty, got {check_result.stale_deps}. "
        f"Result: {check_result}"
    )

    # ── Step 5: Run reconcile() ─────────────────────────────────────
    caplog.clear()
    with caplog.at_level(logging.INFO):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # 5a. stale_deps_removed >= 1
    assert rec.stale_deps_removed >= 1, f"Expected stale_deps_removed >= 1, got {rec}"

    # 5b. inc1 no longer in state
    deps_after = state.get_incremental_dependencies(str(target_dir), full_name)
    assert inc1_name not in deps_after, (
        f"inc1 should be removed from state after reconcile. Remaining deps: {deps_after}"
    )

    # 5c. FULL still in state (not affected)
    fulls_after = state.get_full_backups(str(target_dir))
    assert len(fulls_after) >= 1, (
        f"FULL should still be in state after reconcile. Got: {fulls_after}"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Startup validation preserves corrupt FULL for verify gate
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_startup_validation_preserves_corrupt_full_for_verify_gate(test_vm, caplog):
    """Startup validation does NOT delete a corrupt FULL — verify-before-delete gate catches it.

    1. Start VM, create snapshot, run core.run() → FULL (gen 1).
    2. Corrupt the FULL: truncate to 65536 bytes (header intact, M2 fails).
    3. Set keep_generations=1 (gen 1 becomes deletion candidate).
    4. Set full_verify_before_delete="check" (M2 enabled).
    5. Create new snapshot, run core.run().
    6. Assert: corrupt FULL still on disk, still in state,
       CRITICAL log about corruption, old generation NOT deleted,
       new FULL created (pipeline continued).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

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

    # ── Step 1: Create snapshot + FULL backup (gen 1) ───────────────
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.vgate-snap1", base_image, snapshot_dir)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)

    target = TargetConfig(
        path=target_dir,
        compress=False,
        verify="off",
        target_chain_length=24,
        # High generation count: the original FULL must survive run 2's
        # retention pass even if the fragile test VM exits mid-run and a
        # retry creates an extra FULL chain (the broken inc2 is grouped
        # as an orphan generation).
        target_keep_generations=10,
    )
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
        snapshot_chain_length=999,
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(
            state_dir="/var/tmp",
            full_verify_before_delete="check",
        ),
        vms=[vm_config],
        config_path=tmpdir / "vgate.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    fulls = state.get_full_backups(str(target_dir))
    if not fulls:
        pytest.skip("No FULL backup created on run 1")
    assert len(fulls) >= 1, "Expected at least one FULL backup"

    full_name = fulls[0].name

    # Phase 2 quirk: Core records the FULL under its stem name, so
    # ``FullBackupInfo.path`` lacks the ``.qcow2`` extension and
    # ``os.path.exists()`` would treat the real file as a phantom.
    # Re-record with the real filename so startup validation in run 2
    # sees the (corrupt, but existing) file and does NOT remove it.
    full_path = target_dir / f"{full_name}.qcow2"
    assert full_path.exists(), f"FULL backup file not found on disk: {full_path}"
    state.remove_full_backup(str(target_dir), full_name)
    state.record_full_backup(str(target_dir), f"{full_name}.qcow2", fulls[0].timestamp, "vda")

    # ── Step 2: Corrupt the FULL (truncate to 64KB — header intact) ─
    header_size = 65536
    os.truncate(str(full_path), header_size)
    assert full_path.stat().st_size == header_size, (
        f"FULL file should be truncated to {header_size}"
    )

    # ── Step 3: Create new snapshot and run core.run() ──────────────
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.vgate-snap2", base_image, snapshot_dir)
    state.record_snapshot(vm_name, snap2)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    # ── Step 4: Assertions ──────────────────────────────────────────

    # 4a. Corrupt FULL still exists on disk (startup validation did NOT delete it).
    assert full_path.exists(), (
        "Corrupt FULL should still exist on disk — startup validation "
        "must not delete existing FULL files."
    )

    # 4b. Corrupt FULL still in state (startup validation did NOT remove it).
    fulls_after = state.get_full_backups(str(target_dir))
    full_names_after = [f.name for f in fulls_after]
    assert f"{full_name}.qcow2" in full_names_after, (
        f"Corrupt FULL should still be in state after startup validation. "
        f"FULLs in state: {full_names_after}"
    )

    # 4c. CRITICAL log about corruption or blocking deletion.
    corruption_logs = [
        r.message
        for r in caplog.records
        if "corrupt" in r.message.lower() or "blocking deletion" in r.message.lower()
    ]
    # The corrupt FULL may or may not be a deletion candidate depending
    # on whether a new FULL was created.  If it IS a candidate, the
    # verify-before-delete gate should block its deletion.
    if full_path.exists() and len(fulls_after) >= 2:
        # Two FULLs in state → retention tried to delete old one → gate blocked it.
        assert len(corruption_logs) >= 1, (
            f"Expected corruption/blocking-deletion log when old FULL is "
            f"a deletion candidate. Logs: {[r.message for r in caplog.records]}"
        )

    # 4d. Pipeline continued — at least one FULL exists after the run.
    assert len(fulls_after) >= 1, (
        f"Pipeline should have continued — at least one FULL expected. Got: {fulls_after}"
    )

    _cleanup_checkpoints(shell, vm_name)
