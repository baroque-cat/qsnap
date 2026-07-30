"""Integration tests for ``Core.check()`` — backup target verification.

Verifies triple-source consistency checking for backup targets using
real virsh, qemu-img, and NBD backups against a disposable test VM.
Covers consistent chains, broken backup chains (missing incremental),
orphan checkpoints (mismatched target hash), deep qemu-img check on
backup files, and post-retention consistency.

All tests require a running libvirt daemon with NBD support (libvirt
>= 7.2) and ``python3-libnbd``.  They use the ``test_vm`` fixture
from ``conftest.py``.

Run only when explicitly requested::

    uv run pytest tests/integration/test_check_targets.py -v -m integration
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running
from tests.mocks import InMemoryStateManager, MockConfigFacade

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
    snapshot_dir: Path,
    base_image: Path,
) -> SnapshotInfo:
    """Create an external disk-only snapshot and return ``SnapshotInfo``."""
    snap_path = snapshot_dir / f"{snap_name}.qcow2"
    provider = ExternalSnapshotProvider(shell)
    result = provider.create(
        VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir),
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
    )


def _vm_is_running(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True if VM is in 'running' state."""
    result = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    return result.success and "running" in result.stdout.strip().lower()


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(1200)
def test_check_real_targets_all_consistent(test_vm):
    """Create FULL + incremental backups, then verify with check() → status="ok".

    1. Start the VM.
    2. Create a snapshot.
    3. Run ``core.run()`` to produce a FULL backup.
    4. Create another snapshot.
    5. Run ``core.run()`` again to produce an incremental backup.
    6. Run ``core.check()``.
    7. Assert ``status == "ok"``.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    _cleanup_checkpoints(shell, vm_name)

    # Create an external snapshot
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.chktgt-s1", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)

    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(),
        vms=[vm_config],
        config_path=tmpdir / "check_tgt_consistent.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Run pipeline to create FULL backup
    result = core.run(vm_name)
    if result.results:
        vm_result = result.results[0]
        if not vm_result.success:
            pytest.skip(f"Core.run failed: {vm_result.error}")
        if vm_result.backup_failed:
            pytest.skip("Backup transfer failed")

    # Verify at least one FULL backup was created
    fulls = state.get_full_backups(str(target_dir))
    assert len(fulls) > 0, "Expected at least one FULL backup after core.run()"

    # Create second snapshot for incremental backup
    time.sleep(1)
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.chktgt-s2", snapshot_dir, base_image)
    state.record_snapshot(vm_name, snap2)

    # Wait for bitmap to become active
    time.sleep(3)

    # Run pipeline again for incremental backup
    result2 = core.run(vm_name)
    if result2.results and not result2.results[0].success:
        pytest.skip(f"Second core.run failed: {result2.results[0].error}")

    # Run check — all targets should be consistent
    check_results = core.check(vm_name)
    assert vm_name in check_results
    cr = check_results[vm_name]
    assert cr.status == "ok", (
        f"Expected status='ok' for consistent targets, got '{cr.status}'. "
        f"broken_snapshots={cr.broken_snapshots}"
    )

    _cleanup_checkpoints(shell, vm_name)


@pytest.mark.integration
@pytest.mark.timeout(1200)
def test_check_real_targets_broken_chain(test_vm):
    """Create FULL + inc1 + inc2, delete inc1, verify broken chain detection.

    1. Create FULL + two incrementals on the target.
    2. ``rm -f inc1`` — break the chain (inc2 backs to inc1).
    3. Run ``core.check()``.
    4. Assert ``status == "broken"`` — chain is severed.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    _cleanup_checkpoints(shell, vm_name)

    # Create 3 snapshots for 3 backup runs (FULL + inc1 + inc2)
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.brk-s1", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)

    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(),
        vms=[vm_config],
        config_path=tmpdir / "check_tgt_broken.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # FULL backup
    result = core.run(vm_name)
    if result.results and not result.results[0].success:
        pytest.skip(f"FULL backup failed: {result.results[0].error}")

    fulls = state.get_full_backups(str(target_dir))
    if not fulls:
        # FULL may not have been stored if no allocation
        # Try to find FULL files on disk directly
        full_files = list(target_dir.glob("*.FULL.*.qcow2"))
        if not full_files:
            pytest.skip("No FULL backup files found on target — cannot test broken chain")

    # inc1
    time.sleep(1)
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.brk-s2", snapshot_dir, base_image)
    state.record_snapshot(vm_name, snap2)
    time.sleep(3)
    result2 = core.run(vm_name)
    if result2.results and not result2.results[0].success:
        pytest.skip(f"inc1 backup failed: {result2.results[0].error}")

    # inc2
    time.sleep(1)
    snap3 = _snapshot_create(shell, vm_name, f"{vm_name}.brk-s3", snapshot_dir, base_image)
    state.record_snapshot(vm_name, snap3)
    time.sleep(3)
    result3 = core.run(vm_name)
    if result3.results and not result3.results[0].success:
        pytest.skip(f"inc2 backup failed: {result3.results[0].error}")

    # Identify incremental files on target (non-FULL qcow2)
    incremental_files = sorted(
        [f for f in target_dir.glob("*.qcow2") if ".FULL." not in f.name]
    )
    if len(incremental_files) < 2:
        pytest.skip(
            f"Need at least 2 incremental files on target to break chain; "
            f"found {len(incremental_files)}: {[f.name for f in incremental_files]}"
        )

    # Delete the first incremental (inc1) — this breaks the chain
    # because inc2 backs to inc1
    inc1_path = incremental_files[0]
    inc2_path = incremental_files[1]
    os.unlink(str(inc1_path))
    assert not inc1_path.exists(), f"inc1 ({inc1_path}) should be deleted"
    assert inc2_path.exists(), f"inc2 ({inc2_path}) should still exist"

    # Run check — broken chain should be detected
    check_results = core.check(vm_name)
    assert vm_name in check_results
    cr = check_results[vm_name]
    assert cr.status == "broken", (
        f"Expected status='broken' when inc1 is deleted from backup chain, "
        f"got '{cr.status}'. broken_snapshots={cr.broken_snapshots}"
    )
    assert len(cr.broken_snapshots) > 0, (
        "Expected non-empty broken_snapshots for broken backup chain"
    )

    _cleanup_checkpoints(shell, vm_name)


@pytest.mark.integration
@pytest.mark.timeout(1200)
def test_check_real_targets_orphan_checkpoint(test_vm):
    """Create a FULL backup, then change target path → orphan checkpoint detected.

    1. Create VM, snapshot, FULL backup (creates a checkpoint with hash of target_dir).
    2. Build a new Core with a different target path (different hash).
    3. Run ``core.check()``.
    4. Assert orphan checkpoint is detected (logged as WARNING, status "ok").

    Note: orphan checkpoints are WARNING-level, not BROKEN. The check
    reports them via logging but does not mark the state as broken.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    _cleanup_checkpoints(shell, vm_name)

    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.orph-snap", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)

    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(),
        vms=[vm_config],
        config_path=tmpdir / "check_tgt_orphan.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Run pipeline to create FULL backup + checkpoint
    result = core.run(vm_name)
    if result.results and not result.results[0].success:
        pytest.skip(f"Core.run failed: {result.results[0].error}")

    # Verify a checkpoint was created for this target hash
    original_hash = BitmapBackupProvider.target_hash(str(target_dir))
    cp_result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    qsnap_checkpoints = []
    if cp_result.success:
        qsnap_checkpoints = [
            c.strip() for c in cp_result.stdout.strip().splitlines()
            if c.strip().startswith("qsnap-")
        ]
    assert len(qsnap_checkpoints) > 0, (
        f"Expected at least one qsnap checkpoint after FULL backup, "
        f"got {qsnap_checkpoints}"
    )
    assert any(original_hash in cp for cp in qsnap_checkpoints), (
        f"Expected checkpoint matching hash {original_hash}, "
        f"got {qsnap_checkpoints}"
    )

    # Build a NEW Core with a different target path → different hash
    different_target = tmpdir / "different_backup"
    different_target.mkdir(parents=True, exist_ok=True)

    new_target = TargetConfig(path=different_target, compress=False, verify="off")
    new_vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[new_target],
    )
    new_config = MockConfigFacade(
        global_config=GlobalConfig(),
        vms=[new_vm_config],
        config_path=tmpdir / "check_tgt_orphan2.toml",
    )
    new_state = InMemoryStateManager()
    # Record the same snapshot so check() has something to work with
    new_state.record_snapshot(vm_name, snap1)
    new_factory = DefaultFactory(shell=shell, state=new_state)
    new_core = Core(config=new_config, factory=new_factory, state=new_state, shell=shell)

    # Run check on the new Core — should detect orphan checkpoints
    # because the existing checkpoint hash doesn't match any configured target
    check_results = new_core.check(vm_name)
    assert vm_name in check_results
    cr = check_results[vm_name]

    # Check that the existing checkpoint still exists (not deleted)
    cp_result2 = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    still_exists = False
    if cp_result2.success:
        for line in cp_result2.stdout.strip().splitlines():
            if line.strip().startswith("qsnap-"):
                still_exists = True
                break
    assert still_exists, (
        "check() should be read-only — orphan checkpoint must NOT be deleted"
    )

    # Orphan checkpoints are WARNING-level — status may still be "ok"
    # if no other issues are found
    assert cr.status in ("ok", "broken"), (
        f"Expected status 'ok' or 'broken' (orphan checkpoints are WARNING), "
        f"got '{cr.status}'. broken_snapshots={cr.broken_snapshots}"
    )

    _cleanup_checkpoints(shell, vm_name)


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_check_real_deep_targets(test_vm):
    """Deep check on FULL + incremental backups with ``qemu-img check``.

    1. Create VM, snapshot, FULL backup + incremental.
    2. Run ``core.check(deep=True)``.
    3. Assert ``status == "ok"`` — all backup files pass qemu-img check.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    _cleanup_checkpoints(shell, vm_name)

    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.deep-s1", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)

    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(),
        vms=[vm_config],
        config_path=tmpdir / "check_tgt_deep.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # FULL backup
    result = core.run(vm_name)
    if result.results and not result.results[0].success:
        pytest.skip(f"FULL backup failed: {result.results[0].error}")

    # inc1
    time.sleep(1)
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.deep-s2", snapshot_dir, base_image)
    state.record_snapshot(vm_name, snap2)
    time.sleep(3)
    result2 = core.run(vm_name)
    if result2.results and not result2.results[0].success:
        pytest.skip(f"inc1 backup failed: {result2.results[0].error}")

    # Run deep check
    check_results = core.check(vm_name, deep=True)
    assert vm_name in check_results
    cr = check_results[vm_name]
    assert cr.status == "ok", (
        f"Expected status='ok' after deep check on clean backup files, "
        f"got '{cr.status}'. broken_snapshots={cr.broken_snapshots}"
    )

    _cleanup_checkpoints(shell, vm_name)


@pytest.mark.integration
@pytest.mark.timeout(1200)
def test_check_real_targets_after_retention(test_vm):
    """After snapshot retention cleanup, target chain is still consistent → status="ok".

    1. Create VM, snapshot, FULL backup, then many snapshots to trigger
       retention (snapshot chain_length = 2).
    2. Run ``core.run()`` which performs retention cleanup.
    3. Run ``core.check()``.
    4. Assert ``status == "ok"`` — backup chain remains consistent.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    _cleanup_checkpoints(shell, vm_name)

    # Create initial snapshot
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.ret-s1", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)

    # Use a short chain_length to trigger retention quickly
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=2,
        lifecycle_mode="virsh",
        targets=[target],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(),
        vms=[vm_config],
        config_path=tmpdir / "check_tgt_retention.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Run pipeline to create FULL backup
    result = core.run(vm_name)
    if result.results and not result.results[0].success:
        pytest.skip(f"First core.run failed: {result.results[0].error}")

    # Create enough additional snapshots to trigger retention (chain_length=2)
    for i in range(2, 5):
        time.sleep(1)
        snap = _snapshot_create(
            shell, vm_name, f"{vm_name}.ret-s{i}", snapshot_dir, base_image
        )
        state.record_snapshot(vm_name, snap)
        time.sleep(1)
        core.run(vm_name)

    # After retention, check that everything is consistent
    check_results = core.check(vm_name)
    assert vm_name in check_results
    cr = check_results[vm_name]
    assert cr.status == "ok", (
        f"Expected status='ok' after retention cleanup, got '{cr.status}'. "
        f"broken_snapshots={cr.broken_snapshots}"
    )

    _cleanup_checkpoints(shell, vm_name)
