"""Integration tests for count-based FULL backup decision.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

The count-based FULL decision (design D2) is: create a new FULL when
the incremental count in the newest chain exceeds ``target_chain_length``
(strictly greater), or when no FULLs exist (first backup to target).

Run only when explicitly requested::

    poetry run pytest tests/integration/test_count_based_full.py -v -m integration
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import pytest

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


def _build_core(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    target_dir: Path,
    *,
    target_chain_length: int = 3,
) -> tuple[Core, VMConfig, InMemoryStateManager]:
    """Build a Core instance for count-based FULL tests."""
    state = InMemoryStateManager()
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=99,
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
                target_chain_length=target_chain_length,
                # Keep both FULL generations so the count-based FULL
                # decision — not retention pruning — is what the tests
                # exercise.  With the default keep_generations=1 the
                # post-transfer cleanup can delete a same-second FULL
                # chain, making the assertions order-dependent.
                target_keep_generations=2,
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(state_dir="/var/tmp"),
        vms=[vm_config],
        config_path=target_dir / "test_count_based_full.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config, state


# ──────────────────────────────────────────────────────────────────────
# Test 1: FULL created when incrementals exceed chain_length
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_full_created_when_incrementals_exceed_chain_length(test_vm, caplog):
    """Create target_chain_length+1 incrementals, verify a new FULL is created.

    1. Start VM, set target_chain_length=2.
    2. Create a FULL backup (first backup to target — always FULL).
    3. Record 2 incrementals as deps on the FULL.
    4. Create a 3rd incremental dep — this makes incremental_count=3
       which exceeds chain_length=2 (3 > 2 → true).
    5. Run core.run() — verify a new FULL is created.
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

    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        target_chain_length=2,
    )

    target = vm_config.targets[0]

    # Step 1: First backup to target — always creates FULL.
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.cbfull-s1", base_image, snapshot_dir)
    state.record_snapshot(vm_name, snap)

    # Create a FULL backup directly to set up state.  Phase 2 replaced
    # create_full_backup(vm_name, source_snapshot, target, ...) with the
    # orthogonal run_backup(vm_config, target, disk, ...) — the disk is
    # taken from the VM config for the snapshot's disk.  The SnapshotInfo
    # below is retained only as the anchor metadata recorded in state.
    provider = BitmapBackupProvider(shell)
    source_snap = SnapshotInfo(
        name=f"{vm_name}.cbfull-anchor",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    anchor_disk = vm_config.get_disk(source_snap.disk)
    assert anchor_disk is not None, f"Disk {source_snap.disk} must be configured"
    full_result = provider.run_backup(
        vm_config,
        target,
        anchor_disk,
    )
    if not full_result.success:
        pytest.skip(f"FULL backup failed: {full_result.error}")

    full_path = full_result.target_path
    full_name = full_path.stem
    state.record_full_backup(
        str(target_dir), f"{full_name}.qcow2", source_snap.timestamp, disk="vda"
    )

    # Step 2: Record 3 incrementals as deps on the FULL (exceeds chain_length=2).
    for i in range(3):
        incr_name = f"{vm_name}.cbfull_incr{i}"
        state.record_incremental_dependency(str(target_dir), incr_name, full_name)

    # Verify incremental_count=3 > chain_length=2
    deps = state.get_incremental_dependencies(str(target_dir), full_name)
    assert len(deps) >= 3, f"Expected >= 3 incrementals, got {len(deps)}"

    # Step 3: Create a new snapshot and run core.run().
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.cbfull-s2", base_image, snapshot_dir)
    state.record_snapshot(vm_name, snap2)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    # Step 4: Verify a new FULL was created.
    all_fulls = state.get_full_backups(str(target_dir))
    assert len(all_fulls) >= 2, (
        f"Expected at least 2 FULLs (original + new), got {len(all_fulls)}. "
        f"Fulls: {[(f.name, f.timestamp) for f in all_fulls]}"
    )

    # Every recorded FULL must carry the .qcow2 extension and its path
    # must resolve to the physical file on disk
    # (fix-full-backup-state-extension).
    for full in all_fulls:
        assert full.name.endswith(".qcow2"), (
            f"FULL state entry must carry .qcow2 extension, got {full.name!r}"
        )
        assert full.path.exists(), f"FULL state entry path must exist on disk: {full.path}"

    # Check for FULL creation log.
    created_logs = [
        r.message for r in caplog.records if "created FULL" in r.message and vm_name in r.message
    ]
    assert len(created_logs) >= 1, (
        f"Expected 'created FULL' in logs. "
        f"Logs: {[r.message for r in caplog.records if 'FULL' in r.message]}"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: FULL NOT created when incrementals within chain_length
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_full_not_created_when_incrementals_within_chain_length(test_vm, caplog):
    """Create target_chain_length-1 incrementals, verify NO new FULL.

    1. Start VM, set target_chain_length=5.
    2. Create a FULL backup (first backup to target).
    3. Record 4 incrementals as deps on the FULL (within chain_length=5).
    4. Run core.run() — verify NO new FULL is created
       (incremental_count=4, chain_length=5, 4 > 5 is False).
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

    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        target_chain_length=5,
    )

    target = vm_config.targets[0]

    # Step 1: Create FULL backup (first backup to target).  Phase 2:
    # the orthogonal run_backup(vm_config, target, disk) replaces
    # create_full_backup(vm_name, source_snapshot, target, ...).
    provider = BitmapBackupProvider(shell)
    source_snap = SnapshotInfo(
        name=f"{vm_name}.cbfull-nofull-anchor",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    anchor_disk = vm_config.get_disk(source_snap.disk)
    assert anchor_disk is not None, f"Disk {source_snap.disk} must be configured"
    full_result = provider.run_backup(
        vm_config,
        target,
        anchor_disk,
    )
    if not full_result.success:
        pytest.skip(f"FULL backup failed: {full_result.error}")

    full_path = full_result.target_path
    full_name = full_path.stem
    state.record_full_backup(
        str(target_dir), f"{full_name}.qcow2", source_snap.timestamp, disk="vda"
    )

    # Step 2: Record 4 incrementals (within chain_length=5).
    for i in range(4):
        incr_name = f"{vm_name}.cbfull_no_full_incr{i}"
        state.record_incremental_dependency(str(target_dir), incr_name, full_name)

    # Verify count is within chain_length: 4 > 5 is False
    deps = state.get_incremental_dependencies(str(target_dir), full_name)
    assert len(deps) == 4, f"Expected 4 deps, got {len(deps)}"

    # Step 3: Create snapshot and run core.run().
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.cbfull-nofull", base_image, snapshot_dir)
    state.record_snapshot(vm_name, snap)

    fulls_before = state.get_full_backups(str(target_dir))
    num_fulls_before = len(fulls_before)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    # Step 4: No new FULL should have been created.
    fulls_after = state.get_full_backups(str(target_dir))
    assert len(fulls_after) == num_fulls_before, (
        f"Expected no new FULLs (chain_length=5, incrementals=4), "
        f"but got before={num_fulls_before}, after={len(fulls_after)}"
    )

    # The surviving FULL record must carry the .qcow2 extension and its
    # path must resolve to the physical file on disk
    # (fix-full-backup-state-extension).
    for full in fulls_after:
        assert full.name.endswith(".qcow2"), (
            f"FULL state entry must carry .qcow2 extension, got {full.name!r}"
        )
        assert full.path.exists(), f"FULL state entry path must exist on disk: {full.path}"

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: First backup to target always creates FULL
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_first_backup_to_target_always_creates_full(test_vm, caplog):
    """Verify the first backup to an empty target always creates a FULL.

    1. Start VM.
    2. Create external snapshot, record in state.
    3. Run core.run() with a target that has no prior FULLs.
    4. Verify a FULL backup file is created on the target.
    5. Verify the FULL is recorded in state.
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

    # Step 1: Create snapshot.
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.first-full", base_image, snapshot_dir)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)

    # Step 2: Build Core with an empty target (no prior FULLs in state).
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "first_full.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Step 3: Run pipeline — first backup to empty target.
    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    # Step 4: Verify FULL file was created on target.
    full_files = sorted(target_dir.glob("*.FULL.*.qcow2"))
    assert len(full_files) >= 1, (
        f"Expected at least one FULL backup file on target, "
        f"got {len(full_files)}. Contents: {list(target_dir.iterdir())}"
    )

    # Step 5: Verify FULL recorded in state.
    fulls_in_state = state.get_full_backups(str(target_dir))
    assert len(fulls_in_state) >= 1, f"Expected FULL recorded in state, got {len(fulls_in_state)}"

    # The recorded FULL entry must carry the .qcow2 extension and its
    # path must resolve to the physical file on disk
    # (fix-full-backup-state-extension).
    assert fulls_in_state[0].name.endswith(".qcow2"), (
        f"FULL state entry must carry .qcow2 extension, got {fulls_in_state[0].name!r}"
    )
    assert fulls_in_state[0].path.exists(), (
        f"FULL state entry path must exist on disk: {fulls_in_state[0].path}"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 4: Dry-run does not create FULL
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_dry_run_does_not_create_full(test_vm, caplog):
    """Verify dry_run=True logs the intent but does NOT create a FULL backup.

    1. Start VM, create snapshot.
    2. Configure target with no prior FULLs (would normally trigger FULL).
    3. Set core._dry_run = True.
    4. Run core.run() — verify the dry-run log message appears.
    5. Verify no FULL backup file was actually created on target.
    6. Verify no FULL recorded in state.
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

    # Step 1: Create snapshot.
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.dryrun-full", base_image, snapshot_dir)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)

    # Steps 2-3: Build Core with dry_run=True.
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "dryrun_full.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    core.dry_run = True

    # Step 4: Run pipeline in dry-run mode.
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result = core.run(vm_name)

    # Verify dry-run log message.
    dry_run_logs = [r.message for r in caplog.records if "Would create FULL backup" in r.message]
    assert len(dry_run_logs) >= 1, (
        f"Expected 'Would create FULL backup' in dry-run logs. "
        f"Logs: {[r.message for r in caplog.records if 'FULL' in r.message]}"
    )

    # Verify the dry-run log carries the transfer detail.  Phase 2's
    # message no longer prints chain_length= — the planned FULL decision
    # is logged with kind + disk + method instead.
    full_predict_logs = [
        r.message
        for r in caplog.records
        if "Would create FULL backup" in r.message
        and "method=NBD" in r.message
        and "disk vda" in r.message
    ]
    assert len(full_predict_logs) >= 1, (
        f"Expected 'Would create FULL backup' with method/disk detail in dry-run logs. "
        f"Logs: {[r.message for r in caplog.records if 'Would create' in r.message]}"
    )

    # ---- dry-run PipelineResult predictions assertions ----
    assert result.dry_run is True, f"Expected result.dry_run=True, got {result.dry_run}"
    assert result.actions == [], f"Expected no actions in dry-run mode, got {result.actions}"
    assert len(result.predictions) > 0, (
        f"Expected predictions in dry-run mode, got {result.predictions}"
    )
    assert any("FULL" in p.name for p in result.predictions), (
        f"Expected a FULL backup prediction in dry-run mode, "
        f"got {[(p.action, p.name, p.disk) for p in result.predictions]}"
    )

    # Step 5: No FULL files created on target.
    full_files = sorted(target_dir.glob("*.FULL.*.qcow2"))
    assert len(full_files) == 0, (
        f"Expected no FULL files in dry-run mode, got {[f.name for f in full_files]}"
    )

    # Step 6: No FULL recorded in state.
    fulls_in_state = state.get_full_backups(str(target_dir))
    assert len(fulls_in_state) == 0, (
        f"Expected no FULLs in state during dry-run, got {len(fulls_in_state)}"
    )

    _cleanup_checkpoints(shell, vm_name)
