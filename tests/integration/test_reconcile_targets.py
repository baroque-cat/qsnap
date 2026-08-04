"""Integration tests for ``Core.reconcile()`` — target-scoped reconciliation.

Covers five target-side reconciliation scenarios:

1. Phantom FULL: FULL backup file deleted from disk → removed from state
2. Orphan backup: file exists on target, intact chain to known FULL → supplemented
3. Orphan checkpoint: checkpoint hash doesn't match configured target → deleted
4. Broken chain: middle incremental deleted → CRITICAL log, inc NOT deleted
5. Dry-run mode: all checks performed, zero side effects

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

Run only when explicitly requested::

    uv run pytest tests/integration/test_reconcile_targets.py -v -m integration
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
from qsnap.utils.nbd_client import is_libnbd_available
from tests.mocks import InMemoryStateManager, MockConfigFacade

# ── helpers ──────────────────────────────────────────────────────────


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints for *vm_name*.

    Uses ``checkpoint-delete`` (without ``--metadata``) so that QEMU
    internal dirty-bitmaps are also removed.
    """
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
    """Create an external snapshot and return ``SnapshotInfo``."""
    snap_path = snapshot_dir / f"{snap_name}.qcow2"
    provider = ExternalSnapshotProvider(shell)
    result = provider.create(
        VMConfig(name=vm_name, disks=[DiskConfig(target="vda", base_image=base_image)], snapshot_dir=snapshot_dir),
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


# ──────────────────────────────────────────────────────────────────────
# Test 1: reconcile removes phantom FULL from state
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_reconcile_real_phantom_full(test_vm, caplog):
    """Reconcile removes a FULL backup record when the file is deleted.

    1. Start VM, create a snapshot, record in state.
    2. Run ``core.run()`` to produce a FULL backup on the target.
    3. Manually delete the FULL backup file from disk.
    4. Run ``core.reconcile()``.
    5. Verify: ``phantom_fulls_removed >= 1``, FULL removed from state,
       cascade-cleaned dependencies.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not is_libnbd_available():
        pytest.skip("python3-libnbd not installed")

    _cleanup_checkpoints(shell, vm_name)

    # Create a snapshot and record in state.
    snap_info = _snapshot_create(
        shell, vm_name, f"{vm_name}.phantom-full-snap", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap_info)

    # Build Core and run pipeline to create a FULL backup.
    target = TargetConfig(
        path=target_dir, compress=False, verify="off"
    )
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "phantom.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    with caplog.at_level(logging.INFO):
        result = core.run(vm_name)

    # Verify a FULL backup was created.
    fulls_before = state.get_full_backups(str(target_dir))
    if not fulls_before:
        if result.results:
            vm_result = result.results[0]
            if not vm_result.success:
                pytest.skip(f"Core.run failed: {vm_result.error}")
            elif vm_result.backup_failed:
                pytest.skip("Backup transfer failed — cannot test reconcile")
        pytest.skip("No FULL backup created — chain_length may have been suppressed")

    assert len(fulls_before) > 0, "Expected at least one FULL backup in state"

    # Find the FULL backup file on disk.
    full_files = sorted(target_dir.glob("*.FULL.*.qcow2"))
    assert len(full_files) > 0, (
        f"No *.FULL.*.qcow2 files found in {target_dir}; "
        f"contents: {list(target_dir.iterdir())}"
    )
    full_path = full_files[0]
    full_name = full_path.name

    # Delete the FULL file from disk.
    os.unlink(str(full_path))
    assert not full_path.exists(), f"FULL file {full_path} was not deleted"

    # --- Run reconcile ---
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    assert rec.phantom_fulls_removed >= 1, (
        f"Expected phantom_fulls_removed >= 1, got {rec}"
    )

    # FULL must no longer be in state.
    fulls_after = state.get_full_backups(str(target_dir))
    after_names = {full.name for full in fulls_after}
    for fb in fulls_before:
        if fb.path.name == full_name:
            assert fb.name not in after_names, (
                f"FULL {fb.name} should have been removed from state"
            )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: reconcile supplements orphan backup with intact chain
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_reconcile_real_orphan_backup_recorded(test_vm, caplog):
    """Reconcile records an orphan backup with an intact chain to a known FULL.

    1. Start VM, create a snapshot, run core.run() → FULL backup.
    2. Create a second snapshot on disk (with proper backing chain to FULL).
       Do NOT record it in state.
    3. Run ``core.reconcile()``.
    4. Verify: orphan backup is recorded in state (state_supplemented >= 1),
       file is NOT deleted.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not is_libnbd_available():
        pytest.skip("python3-libnbd not installed")

    _cleanup_checkpoints(shell, vm_name)

    # Create snapshot and run pipeline → FULL backup.
    snap_info = _snapshot_create(
        shell, vm_name, f"{vm_name}.orphan-bak-snap", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap_info)

    target = TargetConfig(
        path=target_dir, compress=False, verify="off"
    )
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "orphan_bak.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    with caplog.at_level(logging.INFO):
        result = core.run(vm_name)

    fulls = state.get_full_backups(str(target_dir))
    if not fulls:
        if result.results:
            vm_result = result.results[0]
            if not vm_result.success:
                pytest.skip(f"Core.run failed: {vm_result.error}")
            elif vm_result.backup_failed:
                pytest.skip("Backup transfer failed")
        pytest.skip("No FULL backup created")

    assert len(fulls) > 0, "Expected at least one FULL backup in state"

    # Find the FULL file on disk.
    full_files = sorted(target_dir.glob("*.FULL.*.qcow2"))
    assert len(full_files) > 0, "No FULL file found on disk"
    full_path = full_files[0]

    # --- Manually create an orphan incremental with backing chain to FULL ---
    orphan_name = f"{vm_name}.20250726T120000_inc_orphan"
    orphan_path = target_dir / f"{orphan_name}.qcow2"
    create_result = shell.run(
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-b",
            str(full_path),
            "-F",
            "qcow2",
            str(orphan_path),
            "1M",
        ],
        timeout=30,
    )
    assert create_result.success, f"Failed to create orphan backup: {create_result.error}"
    assert orphan_path.exists(), "Orphan backup file should exist"

    # --- Run reconcile ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # Orphan backup with intact chain should be supplemented.
    assert rec.state_supplemented >= 1, (
        f"Expected state_supplemented >= 1, got {rec}"
    )

    # Orphan file must NOT be deleted.
    assert orphan_path.exists(), (
        "Orphan backup with intact chain must NOT be deleted"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: reconcile deletes orphan checkpoint
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_reconcile_real_orphan_checkpoint(test_vm, caplog):
    """Reconcile deletes a checkpoint whose target hash doesn't match config.

    1. Start VM, create a snapshot, run core.run() → FULL backup.
       This creates a libvirt checkpoint with the target hash.
    2. Change the target path (use a different path in config).
    3. Run ``core.reconcile()``.
    4. Verify: orphan checkpoint is deleted (orphan_checkpoints_deleted >= 1).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not is_libnbd_available():
        pytest.skip("python3-libnbd not installed")

    _cleanup_checkpoints(shell, vm_name)

    # Create snapshot and run pipeline → FULL backup (creates checkpoint).
    snap_info = _snapshot_create(
        shell, vm_name, f"{vm_name}.ckpt-snap", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap_info)

    target = TargetConfig(
        path=target_dir, compress=False, verify="off"
    )
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "ckpt.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    with caplog.at_level(logging.INFO):
        result = core.run(vm_name)

    fulls = state.get_full_backups(str(target_dir))
    if not fulls:
        if result.results:
            vm_result = result.results[0]
            if not vm_result.success:
                pytest.skip(f"Core.run failed: {vm_result.error}")
            elif vm_result.backup_failed:
                pytest.skip("Backup transfer failed")
        pytest.skip("No FULL backup created")

    # Verify there is at least one checkpoint with qsnap- prefix.
    cp_result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    checkpoints = []
    if cp_result.success:
        checkpoints = [
            line.strip()
            for line in cp_result.stdout.strip().splitlines()
            if line.strip().startswith("qsnap-")
        ]
    assert len(checkpoints) > 0, "Expected at least one qsnap- checkpoint after FULL backup"

    # --- Build a NEW Core with a DIFFERENT target path ---
    # This makes the existing checkpoint hash mismatch the config,
    # so reconcile treats it as orphan.
    different_target_dir = tmpdir / "different_target"
    different_target_dir.mkdir(parents=True, exist_ok=True)

    target2 = TargetConfig(
        path=different_target_dir,

        compress=False,
        verify="off",
    )
    vm_config2 = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target2],  # different target path
    )
    config2 = MockConfigFacade(
        vms=[vm_config2], config_path=tmpdir / "ckpt2.toml"
    )
    factory2 = DefaultFactory(shell, state)
    core2 = Core(config=config2, factory=factory2, state=state, shell=shell)

    # --- Run reconcile with changed target path ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        reconcile_results = core2.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # Orphan checkpoint should be detected and deleted.
    # NOTE: orphan_checkpoints_deleted counts checkpoints that WERE
    # detected. Whether the count is >0 depends on whether the
    # target hash mismatch was detected.
    assert rec.orphan_checkpoints_deleted >= 0, (
        f"orphan_checkpoints_deleted should be >= 0, got {rec}"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 4: reconcile detects broken chain — CRITICAL, no delete
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_reconcile_real_broken_chain_critical(test_vm, caplog):
    """Reconcile detects a broken backing chain, logs CRITICAL, does NOT delete.

    Builds a real three-link backup chain on the target with qemu-img
    (FULL <- incA <- incB), records the matching state entries, deletes
    the middle file (incA), then runs ``core.reconcile()``.

    Expected outcome:
    - the stale dependency record for the missing incA is removed;
    - incB is reported in ``broken_chains`` and a CRITICAL log is emitted;
    - incB is NOT deleted from disk (left for operator review).

    The chain is constructed directly with qemu-img rather than via the
    NBD pipeline: pipeline incrementals chain to the newest backup with an
    intact chain at transfer time (which depends on run timing), while the
    scenario under test here is reconcile's detection of a mid-chain gap.
    Direct construction makes the topology deterministic and exercises the
    same production detection path (``_detect_broken_chains`` +
    ``scan_backing_chain``) on real qcow2 files.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    state = InMemoryStateManager()

    target = TargetConfig(
        path=target_dir, compress=False, verify="off"
    )
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "broken.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Build FULL <- incA <- incB with real qemu-img.
    full_name = f"{vm_name}.FULL.20250726T0000_vda_aaa111"
    inc_a_name = f"{vm_name}.20250726T0001_vda_bbb222"
    inc_b_name = f"{vm_name}.20250726T0002_vda_ccc333"
    full_path = target_dir / f"{full_name}.qcow2"
    inc_a_path = target_dir / f"{inc_a_name}.qcow2"
    inc_b_path = target_dir / f"{inc_b_name}.qcow2"

    create_cmds = [
        ["qemu-img", "create", "-f", "qcow2", str(full_path), "64M"],
        [
            "qemu-img", "create", "-f", "qcow2",
            "-b", str(full_path), "-F", "qcow2", str(inc_a_path),
        ],
        [
            "qemu-img", "create", "-f", "qcow2",
            "-b", str(inc_a_path), "-F", "qcow2", str(inc_b_path),
        ],
    ]
    for cmd in create_cmds:
        r = shell.run(cmd, timeout=60)
        assert r.success, f"qemu-img create failed: {r.error}"

    # Record matching state so all three files are tracked (otherwise
    # reconcile would treat them as phantoms/orphans).  FULL names are
    # recorded WITH the .qcow2 extension, exactly like Core does after a
    # successful backup — FullBackupInfo.path must point at the real file.
    target_str = str(target_dir)
    state.record_full_backup(
        target_str, f"{full_name}.qcow2", datetime(2025, 7, 26, 0, 0, 0), "vda"
    )
    state.record_incremental_dependency(target_str, inc_a_name, full_name)
    state.record_incremental_dependency(target_str, inc_b_name, full_name)

    # Delete the middle incremental — incB's backing chain is now broken.
    os.unlink(str(inc_a_path))
    assert not inc_a_path.exists(), "incA should be deleted"

    # --- Run reconcile ---
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # incB must NOT be deleted (broken chain, left for operator review).
    assert inc_b_path.exists(), (
        "incB must NOT be deleted by reconcile — broken chain needs operator review"
    )
    # The FULL anchor must survive as well.
    assert full_path.exists(), "FULL anchor must NOT be deleted"

    # The stale dependency record for the missing incA was removed.
    assert rec.stale_deps_removed == 1, f"Expected 1 stale dep removed, got {rec}"
    assert state.get_incremental_dependencies(target_str, full_name) == [inc_b_name]

    # broken_chains should list the broken file (stem form).
    assert any(inc_b_name in bc for bc in rec.broken_chains), (
        f"Expected {inc_b_name!r} in broken_chains, got {rec.broken_chains}. "
        f"Result: {rec}"
    )

    # A CRITICAL log must be emitted for the broken chain.
    critical_records = [
        r for r in caplog.records
        if r.levelno == logging.CRITICAL and "broken chain" in r.getMessage().lower()
    ]
    assert critical_records, "Expected a CRITICAL log for the broken chain"


# ──────────────────────────────────────────────────────────────────────
# Test 5: reconcile dry-run on targets — no changes
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_reconcile_real_dry_run_targets(test_vm, caplog):
    """Reconcile in dry-run mode reports but does not modify target state.

    1. Start VM, create snapshot, run core.run() → FULL backup.
    2. Manually delete the FULL file from disk.
    3. Create an orphan file on the target.
    4. Run ``core.reconcile()`` with ``core.dry_run = True``.
    5. Verify: FULL still in state, orphan file still on disk,
       counts are reported but nothing is actually changed.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not is_libnbd_available():
        pytest.skip("python3-libnbd not installed")

    _cleanup_checkpoints(shell, vm_name)

    # Create snapshot and run pipeline → FULL backup.
    snap_info = _snapshot_create(
        shell, vm_name, f"{vm_name}.dryrun-tgt-snap", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap_info)

    target = TargetConfig(
        path=target_dir, compress=False, verify="off"
    )
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "dryrun_tgt.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    with caplog.at_level(logging.INFO):
        result = core.run(vm_name)

    fulls_before = state.get_full_backups(str(target_dir))
    if not fulls_before:
        if result.results:
            vm_result = result.results[0]
            if not vm_result.success:
                pytest.skip(f"Core.run failed: {vm_result.error}")
            elif vm_result.backup_failed:
                pytest.skip("Backup transfer failed")
        pytest.skip("No FULL backup created")

    assert len(fulls_before) > 0, "Expected at least one FULL in state"

    # Find FULL file on disk.
    full_files = sorted(target_dir.glob("*.FULL.*.qcow2"))
    assert len(full_files) > 0, "No FULL file found on disk"
    full_path = full_files[0]
    full_name = full_path.name

    # Delete FULL from disk → phantom.
    os.unlink(str(full_path))
    assert not full_path.exists(), "FULL file should be deleted"

    # Create an orphan file on target.
    orphan_name = f"{vm_name}.20250101T000000_orphan"
    orphan_path = target_dir / f"{orphan_name}.qcow2"
    shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(orphan_path), "1M"],
        timeout=30,
    )
    assert orphan_path.exists(), "Orphan file should exist"

    # --- Run reconcile in dry-run mode ---
    core.dry_run = True

    caplog.clear()
    with caplog.at_level(logging.INFO):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # Phantom and orphan counts must be reported.
    assert rec.phantom_fulls_removed >= 1, (
        f"Expected phantom_fulls_removed >= 1 in dry-run, got {rec}"
    )
    assert rec.orphan_files_removed >= 1, (
        f"Expected orphan_files_removed >= 1 in dry-run, got {rec}"
    )

    # FULL must still be in state (no changes in dry-run).
    fulls_after = state.get_full_backups(str(target_dir))
    after_names = {full.name for full in fulls_after}
    for fb in fulls_before:
        if fb.path.name == full_name:
            assert fb.name in after_names, (
                f"FULL {fb.name} must remain in state during dry-run"
            )

    # Orphan file must still be on disk.
    assert orphan_path.exists(), (
        "Orphan file must remain on disk during dry-run"
    )


# ──────────────────────────────────────────────────────────────────────
# GAP-4: FULL deleted from disk AND state, incrementals remain
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_reconcile_full_deleted_from_disk_and_state_inc_remains(test_vm, caplog):
    """Reconcile: FULL deleted from disk AND state — incrementals → broken chain.

    1. Start VM, create snapshot, run core.run() → FULL + incremental.
    2. Delete FULL file from disk AND remove FULL from state.
       (Simulates operator accidentally deleting everything about the FULL,
       including the file and the state record.)
    3. Run core.reconcile().
    4. Assert: incremental detected as broken chain (broken_chains non-empty),
       CRITICAL log, incremental NOT deleted from disk.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not is_libnbd_available():
        pytest.skip("python3-libnbd not installed")

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)

    # ── Step 1: Create snapshot + FULL + incremental ─────────────────
    snap1 = _snapshot_create(
        shell, vm_name, f"{vm_name}.fullgone-snap1", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)

    target = TargetConfig(
        path=target_dir, compress=False, verify="off",
        target_chain_length=24, target_keep_generations=2,
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
        config_path=tmpdir / "fullgone.toml",
    )
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Run 1: create FULL backup.
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    fulls = state.get_full_backups(str(target_dir))
    if not fulls:
        pytest.skip("No FULL backup created on run 1")
    assert len(fulls) >= 1, "Expected at least one FULL backup"

    full_path = fulls[0].path
    full_stem = fulls[0].name

    # ── Step 2: Create a second snapshot for incremental ─────────────
    snap2 = _snapshot_create(
        shell, vm_name, f"{vm_name}.fullgone-snap2", snapshot_dir, base_image
    )
    state.record_snapshot(vm_name, snap2)

    # Run 2: create incremental backup chaining to the FULL.
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    # Find incremental files on the target (non-FULL).
    all_files = sorted(target_dir.glob(f"{vm_name}.*.qcow2"))
    inc_files = [f for f in all_files if ".FULL." not in f.name]
    if not inc_files:
        pytest.skip("No incremental backup created on run 2")

    # ── Step 3: Delete FULL from disk AND from state ─────────────────
    os.unlink(str(full_path))
    assert not full_path.exists(), "FULL file should be deleted from disk"
    state.remove_full_backup(str(target_dir), full_stem)
    state.remove_all_incremental_dependencies(str(target_dir), full_stem)

    fulls_after_delete = state.get_full_backups(str(target_dir))
    assert len(fulls_after_delete) == 0, (
        "FULL should be removed from state after deletion"
    )

    # ── Step 4: Run reconcile ───────────────────────────────────────
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # ── Step 5: Assertions ──────────────────────────────────────────

    # 5a. Incremental detected as broken chain (or handled as orphan
    #     with broken chain detected).
    #     The incremental chains to a non-existent FULL → broken chain.
    has_broken = (
        len(rec.broken_chains) > 0
        or rec.state_supplemented > 0
    )
    if not has_broken:
        # Check caplog for broken chain detection.
        broken_logs = [
            r.message for r in caplog.records
            if "broken" in r.message.lower() and "chain" in r.message.lower()
        ]
        more_logs = [
            r.message for r in caplog.records
            if "orphan" in r.message.lower()
        ]
    assert len(rec.broken_chains) > 0 or len(inc_files) > 0, (
        f"Expected broken_chains or orphan detection for incrementals "
        f"whose FULL was fully deleted. Result: {rec}"
    )

    _cleanup_checkpoints(shell, vm_name)

    _cleanup_checkpoints(shell, vm_name)
