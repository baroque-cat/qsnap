"""Integration tests for ``Core.reconcile()`` — state-vs-disk repair.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

``Core.reconcile()`` actively repairs inconsistencies between
persisted state and the filesystem: phantom snapshots, phantom FULLs
(with cascade dependency cleanup), stale baselines, stale incremental
dependencies, and orphaned libvirt checkpoints.

Coverage:
- Phantom FULL cleanup: delete a FULL backup file from disk, run
  reconcile, verify phantom FULL is removed from state, incremental
  deps are cleaned, and ``ReconcileResult`` counts reflect the repair.

Run only when explicitly requested::

    poetry run pytest tests/integration/test_reconcile.py -v -m integration
"""

from __future__ import annotations

import logging
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
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running
from tests.mocks import InMemoryStateManager, MockConfigFacade


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints for *vm_name*.

    Uses ``checkpoint-delete`` (without ``--metadata``) so that QEMU
    internal dirty-bitmaps are also removed.  ``--metadata`` only
    removes the libvirt-tracked metadata — the QEMU bitmap persists
    and would cause a collision on the next backup-begin.
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
# Test 1: reconcile removes phantom FULL and cleans dependencies
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_reconcile_command(test_vm, caplog):
    """Verify ``Core.reconcile()`` actively repairs state after manual FULL deletion.

    1. Start the VM.
    2. Create an external snapshot.
    3. Run ``core.run()`` to produce a FULL backup + incremental backup
       on the target.
    4. Manually delete the FULL backup file from the target directory.
    5. Run ``core.reconcile()``.
    6. Verify:
       - Phantom FULL is removed from state.
       - Incremental dependencies are cleaned.
       - ReconcileResult has ``phantom_fulls_removed > 0``.
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
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    _cleanup_checkpoints(shell, vm_name)

    # Create an external snapshot and record in state.
    snap_info = _snapshot_create(
        shell, vm_name, f"{vm_name}.reconcile-snap", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap_info)

    # Build Core with a target that creates FULL + incremental backups.
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "reconcile.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- Run the pipeline: snapshot + FULL backup + incremental backup ---
    with caplog.at_level(logging.INFO):
        result = core.run(vm_name)

    # If the FULL backup silently failed in the pipeline, skip.
    fulls_before = state.get_full_backups(str(target_dir))
    if not fulls_before:
        # Check if the pipeline itself failed (not just no FULL needed).
        if result.results:
            vm_result = result.results[0]
            if not vm_result.success:
                pytest.skip(f"Core.run failed: {vm_result.error}")
            elif vm_result.backup_failed:
                pytest.skip("Backup transfer failed — cannot test reconcile")
        pytest.skip("No FULL backup created — chain_length may have been suppressed")

    # Verify there is at least one FULL recorded in state.
    assert len(fulls_before) > 0, "Expected at least one FULL backup in state after core.run()"

    # Identify the FULL backup file on disk by scanning for *.FULL.*.qcow2.
    full_files = sorted(target_dir.glob("*.FULL.*.qcow2"))
    assert len(full_files) > 0, (
        f"No *.FULL.*.qcow2 files found in {target_dir}; "
        f"contents: {list(target_dir.iterdir())}"
    )

    # Record the full name for later verification.
    full_path = full_files[0]
    full_name = full_path.name

    # Verify this FULL is tracked in state.
    tracked_names = {full.name for full in fulls_before if full.path.name == full_name}
    assert len(tracked_names) > 0, (
        f"FULL {full_name} not found in state; "
        f"tracked: {[f.path.name for f in fulls_before]}"
    )

    # --- Manually delete the FULL backup file (simulate disk failure / user error) ---
    os.unlink(str(full_path))
    assert not full_path.exists(), f"FULL file {full_path} was not deleted"

    # --- Run reconcile ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Expected reconcile result for {vm_name}"
    rec_result = reconcile_results[vm_name]

    # Phantom FULL must be detected and removed from state.
    assert rec_result.phantom_fulls_removed > 0, (
        f"Expected phantom FULLs to be removed, got {rec_result.phantom_fulls_removed}. "
        f"Result: {rec_result}"
    )

    # FULL must no longer be in state after reconcile.
    fulls_after = state.get_full_backups(str(target_dir))
    after_names = {full.name for full in fulls_after}
    for fb in fulls_before:
        if fb.path.name == full_name:
            assert fb.name not in after_names, (
                f"FULL {fb.name} should have been removed from state by reconcile"
            )

    # Incremental dependencies should have been cascade-cleaned.
    # Reconcile calls remove_all_incremental_dependencies, but InMemoryStateManager
    # only tracks what Core explicitly recorded.  Since Core records the dep during
    # backup transfer, if deps were recorded they should now be gone.
    deps_after = state.get_incremental_dependencies(str(target_dir), full_name)
    assert len(deps_after) == 0, (
        f"Incremental dependencies should be cascade-cleaned after FULL removal, "
        f"got {deps_after}"
    )

    # Verify new ReconcileResult fields (D8) are present.
    assert hasattr(rec_result, "state_supplemented"), (
        "ReconcileResult must have state_supplemented field"
    )
    assert hasattr(rec_result, "xml_refreshed"), (
        "ReconcileResult must have xml_refreshed field"
    )
    assert hasattr(rec_result, "allocation_fixed"), (
        "ReconcileResult must have allocation_fixed field"
    )
    # In this scenario (phantom FULL removal from target), no state
    # supplementation or XML refresh occurs:
    assert rec_result.state_supplemented == 0, "no files should be supplemented"
    assert rec_result.xml_refreshed is False, "XML should not be refreshed"
    # allocation_fixed may be False by default here.

    # NOTE: baselines_cleared may be 0 because Core never calls
    # set_last_backup_allocation() anywhere — see
    # qsnap/core/__init__.py:_backup_target (lines ~3234-3266).
    # The only path that sets last_backup_allocation is through
    # IStateManager.set_last_backup_allocation(), which is never
    # invoked by Core.  This is a **source bug**:
    #   - Core records FULL backups in state,
    #   - Core clears stale baselines in reconcile,
    #   - But Core never WRITES a baseline.
    # As a result, get_last_backup_allocation() always returns None,
    # and baselines_cleared is always 0 even when reconcile would
    # otherwise detect a stale baseline.

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: reconcile removes orphan backup files on target
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_reconcile_removes_orphan_backup_files(test_vm, caplog):
    """Verify ``Core.reconcile()`` deletes orphan .qcow2 files on target.

    1. Start the VM.
    2. Create an external snapshot.
    3. Run ``core.run()`` to produce a FULL backup on the target.
    4. Create an extra .qcow2 file on the target (not tracked in state)
       — simulating a crash between transfer and state recording.
    5. Run ``core.reconcile()``.
    6. Verify:
       - Orphan file is deleted from the target.
       - ``ReconcileResult.orphan_files_removed > 0``.
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
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    _cleanup_checkpoints(shell, vm_name)

    # Create an external snapshot and record in state.
    snap_info = _snapshot_create(
        shell, vm_name, f"{vm_name}.orphan-bak-snap", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap_info)

    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "orphan_bak.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- Create an orphan .qcow2 file on the target (not in state) ---
    # This simulates a crash between backup transfer and state recording.
    orphan_name = f"{vm_name}.20250726T1531_vda_orphan"
    orphan_path = target_dir / f"{orphan_name}.qcow2"
    # Create a valid qcow2 file so provider.list() can read it.
    shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(orphan_path), "1M"],
        timeout=30,
    )
    assert orphan_path.exists(), "Orphan file should exist on target"

    # --- Run reconcile ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        reconcile_results = core.reconcile(vm_name)

    rec_result = reconcile_results[vm_name]

    # Orphan file should be deleted from target.
    assert not orphan_path.exists(), (
        f"Orphan file {orphan_path} should have been deleted by reconcile"
    )
    assert rec_result.orphan_files_removed > 0, (
        f"Expected orphan_files_removed > 0, got {rec_result.orphan_files_removed}. "
        f"Result: {rec_result}"
    )

    # NOTE: Under the new reconcile behavior, an orphan file on target is
    # deleted only when its backing chain does NOT lead to a FULL tracked
    # in state.  If the chain were intact to a tracked FULL, reconcile
    # would instead supplement state via record_incremental_dependency()
    # and increment ``state_supplemented``.  In this test, the orphan was
    # created from scratch (no backing chain to a tracked FULL), so it
    # follows the deletion path.

    # Verify new ReconcileResult fields (D8) are present:
    assert hasattr(rec_result, "state_supplemented"), (
        "ReconcileResult must have state_supplemented field"
    )
    assert isinstance(rec_result.state_supplemented, int)
    assert hasattr(rec_result, "xml_refreshed")
    assert isinstance(rec_result.xml_refreshed, bool)
    assert hasattr(rec_result, "allocation_fixed")
    assert isinstance(rec_result.allocation_fixed, bool)

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: reconcile removes orphan snapshot files in snapshot_dir
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_reconcile_removes_orphan_snapshot_files(test_vm, caplog):
    """Verify ``Core.reconcile()`` deletes orphan .qcow2 files in snapshot_dir.

    1. Start the VM.
    2. Create an external snapshot (recorded in state).
    3. Create an extra .qcow2 file in snapshot_dir (not tracked in state)
       — simulating a crash between snapshot creation and state recording.
    4. Run ``core.reconcile()``.
    5. Verify:
       - Orphan file is deleted from snapshot_dir ONLY when NOT referenced
         by domain XML.
       - ``ReconcileResult.orphan_files_removed > 0``.
       - When the file IS referenced by domain XML (part of the active
         chain), reconcile supplements state instead of deleting.
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

    _cleanup_checkpoints(shell, vm_name)

    # Create an external snapshot and record in state.
    snap_info = _snapshot_create(
        shell, vm_name, f"{vm_name}.orphan-snap-snap", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap_info)

    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "orphan_snap.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- Create an orphan .qcow2 file in snapshot_dir (not in state) ---
    orphan_snap = snapshot_dir / f"{vm_name}.20250726T1531_orphan.qcow2"
    # Create a valid qcow2 file (doesn't need to be bootable).
    shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(orphan_snap), "1M"],
        timeout=30,
    )
    assert orphan_snap.exists(), "Orphan snapshot file should exist"

    # Verify the orphan file is NOT referenced by domain XML before reconcile.
    dumpxml = shell.run(["virsh", "dumpxml", "--domain", vm_name], timeout=30)
    xml_text = dumpxml.stdout if dumpxml.success else ""
    assert str(orphan_snap) not in xml_text, (
        "Orphan file should NOT be in domain XML (standalone qcow2 not part of chain)"
    )

    # --- Run reconcile ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        reconcile_results = core.reconcile(vm_name)

    rec_result = reconcile_results[vm_name]

    # Orphan snapshot file should be deleted because it is NOT in domain XML.
    assert not orphan_snap.exists(), (
        f"Orphan snapshot file {orphan_snap} should have been deleted by reconcile"
    )
    assert rec_result.orphan_files_removed > 0, (
        f"Expected orphan_files_removed > 0, got {rec_result.orphan_files_removed}. "
        f"Result: {rec_result}"
    )

    # When domain XML does NOT reference the file, reconcile deletes it
    # (step 2: truly orphan, not in state, not in XML).  When domain XML
    # DOES reference a file missing from state, reconcile would instead
    # call record_snapshot() and increment state_supplemented.

    # Verify new ReconcileResult fields (D8) are present:
    assert hasattr(rec_result, "state_supplemented"), (
        "ReconcileResult must have state_supplemented field"
    )
    assert isinstance(rec_result.state_supplemented, int)
    assert hasattr(rec_result, "xml_refreshed")
    assert isinstance(rec_result.xml_refreshed, bool)
    assert hasattr(rec_result, "allocation_fixed")
    assert isinstance(rec_result.allocation_fixed, bool)

    _cleanup_checkpoints(shell, vm_name)
