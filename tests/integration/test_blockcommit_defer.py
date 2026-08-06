"""Integration tests for adaptive lifecycle fork (design D2/D4/D5/D6).

Verifies:
- Live commit of non-active snapshots while VM is running (virsh mode)
- Active layer deferred with reason "vm_running" (running VM)
- Deferred queue drains via qemu-img after VM shutdown (files deleted,
  chain shortened, VM bootable)
- XML-referenced tip excluded from offline commit and deferred with
  reason "active_layer" (VM stays bootable)

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import RetentionResult, SnapshotInfo
from qsnap.shell.subprocess_shell import SubprocessShell
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_state import InMemoryStateManager

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _build_core(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    target_dir: Path,
    lifecycle_mode: str = "virsh",
) -> tuple[Core, VMConfig, InMemoryStateManager]:
    """Build a Core instance with InMemoryStateManager and DefaultFactory."""
    state = InMemoryStateManager()
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=24,
        lifecycle_mode=lifecycle_mode,
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(),
        vms=[vm_config],
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config, state


def _get_oldest(state: InMemoryStateManager, vm_name: str) -> SnapshotInfo:
    """Return the oldest SnapshotInfo from state."""
    snapshots = state.get_snapshots(vm_name)
    assert snapshots, "No snapshots in state"
    return snapshots[0]


def _get_newest(state: InMemoryStateManager, vm_name: str) -> SnapshotInfo:
    """Return the newest SnapshotInfo from state."""
    snapshots = state.get_snapshots(vm_name)
    assert snapshots, "No snapshots in state"
    return snapshots[-1]


def _backing_points_to(file_path: Path, expected_base: Path, shell: SubprocessShell) -> bool:
    """Check via qemu-img info whether *file_path*'s backing points to *expected_base*."""
    result = shell.run(
        ["qemu-img", "info", "--force-share", "--output=json", str(file_path)],
        timeout=30,
    )
    if not result.success:
        return False
    data = json.loads(result.stdout)
    backing = data.get("full-backing-filename") or data.get("backing-filename", "")
    if not backing or not isinstance(backing, str):
        return False
    if not os.path.isabs(backing):
        backing = os.path.join(os.path.dirname(str(file_path)), backing)
    return os.path.realpath(backing) == os.path.realpath(str(expected_base))


def _backing_chain_length(tip_path: Path, shell: SubprocessShell) -> int | None:
    """Return the backing chain length from *tip_path*, or None on failure."""
    result = shell.run(
        [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            str(tip_path),
        ],
        timeout=30,
    )
    if not result.success:
        return None
    chain = json.loads(result.stdout)
    return len(chain) if isinstance(chain, list) else None


def _vm_is_running(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True if VM is running."""
    result = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    return result.success and "running" in result.stdout.lower()


def _vm_is_shut_off(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True if VM is shut off."""
    result = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    return result.success and "shut off" in result.stdout.lower()


def _get_active_layer(shell: SubprocessShell, vm_name: str) -> str | None:
    """Return the active layer path from virsh domblklist."""
    result = shell.run(["virsh", "domblklist", "--domain", vm_name], timeout=30)
    if not result.success:
        return None
    # Parse: "vda    /path/to/file.qcow2"
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0].startswith("vd"):
            return parts[1]
    return None


def _chain_contains_path(tip_path: Path, needle: Path, shell: SubprocessShell) -> bool:
    """Check whether *needle* appears in the backing chain of *tip_path*."""
    result = shell.run(
        [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            str(tip_path),
        ],
        timeout=30,
    )
    if not result.success:
        return False
    chain = json.loads(result.stdout)
    needle_real = os.path.realpath(str(needle))
    for entry in chain:
        filename = entry.get("filename", "")
        if os.path.realpath(filename) == needle_real:
            return True
    return False


# ──────────────────────────────────────────────────────────────────────
# Test 1: Live commit of non-active snapshots while VM running (virsh)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_live_commit_non_active_while_running_integration(test_vm):
    """Live commit of non-active snapshot via virsh blockcommit.

    1. Start VM, create snap1, snap2, snap3 (snap3 = active layer).
    2. Invoke blockcommit with remove = {snap1}.
    3. Assert: snap1 file DELETED from disk; snap2's backing points
       to base image; VM still running with snap3 as active layer;
       NO deferred entries created.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Start VM
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    # Build Core with lifecycle_mode="virsh"
    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        lifecycle_mode="virsh",
    )

    # Create 3 snapshots (snap3 = active)
    for i in range(3):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(1.1)

    snapshots = state.get_snapshots(vm_name)
    assert len(snapshots) == 3, f"Expected 3 snapshots, got {len(snapshots)}"

    snap1 = snapshots[0]
    snap2 = snapshots[1]
    snap3 = snapshots[2]

    snap1_path = snap1.path
    snap2_path = snap2.path
    assert snap1_path.exists(), f"snap1 file should exist before commit: {snap1_path}"
    assert snap2_path.exists(), f"snap2 file should exist: {snap2_path}"

    # Record snap2's backing before commit
    backing_before = _backing_points_to(snap2_path, snap1_path, shell)
    assert backing_before, "snap2 should point to snap1 before commit"

    # Invoke blockcommit with remove = {snap1}
    retention = RetentionResult(
        keep=[snap2.name, snap3.name],
        remove=[snap1.name],
    )
    core._blockcommit_snapshots(vm_config, retention)

    # --- Assertions ---

    # (a) snap1 file DELETED from disk
    assert not snap1_path.exists(), (
        f"snap1 file should be deleted by live commit, but still exists: {snap1_path}"
    )

    # (b) snap2's backing now points to the base image
    assert _backing_points_to(snap2_path, base_image, shell), (
        "snap2's backing should point to base image after snap1 committed"
    )

    # (c) VM still running
    assert _vm_is_running(shell, vm_name), "VM should still be running"

    # (d) snap3 is the active layer per domblklist
    active = _get_active_layer(shell, vm_name)
    assert active is not None, "domblklist should return active layer"
    assert os.path.realpath(active) == os.path.realpath(str(snap3.path)), (
        f"Active layer should be snap3 ({snap3.path}), got {active}"
    )

    # (e) NO deferred entries created
    deferred = state.get_deferred_operations(vm_name)
    assert len(deferred) == 0, f"Expected no deferred entries, got {len(deferred)}"

    # (f) snap1 removed from state
    remaining_snapshots = state.get_snapshots(vm_name)
    remaining_names = {s.name for s in remaining_snapshots}
    assert snap1.name not in remaining_names, "snap1 should be removed from state"
    assert snap2.name in remaining_names, "snap2 should remain in state"
    assert snap3.name in remaining_names, "snap3 should remain in state"


# ──────────────────────────────────────────────────────────────────────
# Test 2: Active layer deferred when in remove set (running VM, virsh)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_active_layer_deferred_running_integration(test_vm):
    """Active layer (snap3) deferred with reason "vm_running", old committed.

    1. Start VM, create snap1, snap2, snap3 (snap3 = active).
    2. Invoke blockcommit with remove = {snap1, snap3}.
    3. Assert: snap1 committed live (file deleted); snap3 deferred with
       reason "vm_running"; no "requires active flag" error; chain intact;
       VM healthy.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Start VM
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        lifecycle_mode="virsh",
    )

    # Create 3 snapshots
    for i in range(3):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(1.1)

    snapshots = state.get_snapshots(vm_name)
    assert len(snapshots) == 3, f"Expected 3 snapshots, got {len(snapshots)}"

    snap1 = snapshots[0]
    snap3 = snapshots[2]

    snap1_path = snap1.path
    snap3_path = snap3.path
    assert snap1_path.exists(), f"snap1 file should exist: {snap1_path}"
    assert snap3_path.exists(), f"snap3 file should exist: {snap3_path}"

    # Invoke blockcommit with remove = {snap1, snap3}
    retention = RetentionResult(
        keep=[snapshots[1].name],
        remove=[snap1.name, snap3.name],
    )
    core._blockcommit_snapshots(vm_config, retention)

    # --- Assertions ---

    # (a) snap1 committed live — file deleted
    assert not snap1_path.exists(), (
        f"snap1 should be deleted by live commit, but still exists: {snap1_path}"
    )

    # (b) snap3 deferred — file still exists
    assert snap3_path.exists(), (
        f"snap3 (active layer) should be deferred, file still exist: {snap3_path}"
    )

    # (c) Deferred queue contains snap3 with reason "vm_running"
    deferred = state.get_deferred_operations(vm_name)
    assert len(deferred) == 1, f"Expected 1 deferred entry, got {len(deferred)}"
    assert deferred[0].reason == "vm_running", (
        f"Expected reason 'vm_running', got {deferred[0].reason!r}"
    )
    assert snap3.name in deferred[0].snapshots, (
        f"Deferred entry should contain snap3, got {deferred[0].snapshots}"
    )

    # (d) VM still running and healthy
    assert _vm_is_running(shell, vm_name), "VM should still be running"

    # (e) Chain intact — snap2 should still point to base
    snap2 = snapshots[1]
    assert _backing_points_to(snap2.path, base_image, shell), (
        "snap2 should point to base image after snap1 commit"
    )

    # (f) snap1 removed from state, snap3 still in state (deferred)
    remaining = state.get_snapshots(vm_name)
    remaining_names = {s.name for s in remaining}
    assert snap1.name not in remaining_names, "snap1 should be removed from state"
    assert snap3.name in remaining_names, "snap3 (deferred) should still be in state"


# ──────────────────────────────────────────────────────────────────────
# Test 3: Deferred blockcommit executes after VM shutdown (strengthened)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_deferred_blockcommit_executes_after_shutdown_integration(test_vm):
    """Deferred blockcommit drains after VM shutdown via qemu-img executor.

    1. Start VM, create 2 snapshots with lifecycle_mode="qemu-img".
    2. Manually add a deferred entry for the oldest snapshot.
    3. Destroy the VM.
    4. Call _check_deferred_operations.
    5. Strengthened assertions: committed file DELETED from disk;
       backing chain on tip SHORTENED and intact; committed names
       removed from IStateManager; virsh start SUCCEEDS afterwards.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Step 1: Start the VM.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    # Build Core with lifecycle_mode="qemu-img" so the fork defers
    # everything when running.
    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        lifecycle_mode="qemu-img",
    )

    # Create 2 snapshots.
    for i in range(2):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(1.1)

    snapshots_in_state = state.get_snapshots(vm_name)
    assert len(snapshots_in_state) == 2, f"Expected 2 snapshots, got {len(snapshots_in_state)}"

    oldest = snapshots_in_state[0]
    newest = snapshots_in_state[1]
    oldest_path = oldest.path
    newest_path = newest.path
    assert oldest_path.exists(), f"Oldest file should exist: {oldest_path}"
    assert newest_path.exists(), f"Newest file should exist: {newest_path}"

    # Record chain length on tip before drain.
    chain_len_before = _backing_chain_length(newest_path, shell)
    assert chain_len_before is not None, "Should be able to get chain length"
    # With base ← snap1 ← snap2, chain should be 3
    assert chain_len_before >= 2, f"Chain should have at least 2 entries, got {chain_len_before}"

    # Step 2: Manually add a deferred blockcommit entry for the oldest.
    state.add_deferred_blockcommit(
        vm_name,
        "vda",
        snapshots=[oldest.name],
        reason="vm_running",
    )

    deferred_before = state.get_deferred_operations(vm_name)
    assert len(deferred_before) == 1, "Deferred entry should exist before shutdown"

    # Step 3: Destroy the VM.
    destroy_result = shell.run(["virsh", "destroy", vm_name], timeout=30)
    assert destroy_result.success, f"virsh destroy failed: {destroy_result.error}"
    time.sleep(0.5)
    assert _vm_is_shut_off(shell, vm_name), "VM should be shut off"

    # Step 4: Execute deferred operations.
    core._check_deferred_operations(vm_config)

    # Step 5: Strengthened assertions.

    # (a) Committed snapshot file DELETED from disk (Bug #4 regression guard).
    assert not oldest_path.exists(), (
        f"Committed snapshot file should be deleted, but still exists: {oldest_path}"
    )

    # (b) Backing chain on the tip is SHORTENED and intact.
    chain_len_after = _backing_chain_length(newest_path, shell)
    assert chain_len_after is not None, "Should be able to get chain length after commit"
    assert chain_len_after < chain_len_before, (
        f"Chain should be shorter after commit: was {chain_len_before}, now {chain_len_after}"
    )
    # The tip (newest) should now point directly to base image.
    assert _backing_points_to(newest_path, base_image, shell), (
        "Tip snapshot should point to base image after oldest committed"
    )

    # (c) Deferred queue is cleared.
    deferred_after = state.get_deferred_operations(vm_name)
    assert len(deferred_after) == 0, (
        f"Deferred queue should be empty after drain, got {len(deferred_after)}"
    )

    # (d) Committed name removed from IStateManager.
    remaining = state.get_snapshots(vm_name)
    remaining_names = {s.name for s in remaining}
    assert oldest.name not in remaining_names, (
        f"Committed snapshot {oldest.name} should be removed from state"
    )
    assert newest.name in remaining_names, f"Tip snapshot {newest.name} should remain in state"

    # (e) Verify virsh start SUCCEEDS directly (source fix D8).
    start_after = shell.run(["virsh", "start", vm_name], timeout=30)
    assert start_after.success, (
        f"virsh start should succeed after deferred commit, got: {start_after.error}"
    )
    # Verify it's running, then stop it for cleanup.
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running after start"
    shell.run(["virsh", "destroy", vm_name], timeout=30)


# ──────────────────────────────────────────────────────────────────────
# Test 4: XML-referenced tip excluded from offline commit, VM bootable
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_xml_tip_excluded_offline_vm_boots_integration(test_vm):
    """XML-referenced tip deferred with "active_layer", VM remains bootable.

    1. Start VM, create snap1, snap2 (snap2 = tip/active).
    2. virsh destroy.
    3. Invoke blockcommit with remove = {snap1, snap2}.
    4. Assert: snap1 committed offline (file deleted, chain shortened);
       snap2 (XML-tip) deferred with "active_layer" and file still exists;
       virsh start succeeds — domain boots off snap2.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Step 1: Start VM and create 2 snapshots.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        lifecycle_mode="virsh",
    )

    for i in range(2):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(1.1)

    snapshots = state.get_snapshots(vm_name)
    assert len(snapshots) == 2, f"Expected 2 snapshots, got {len(snapshots)}"

    snap1 = snapshots[0]
    snap2 = snapshots[1]  # This is the active layer / XML tip
    snap1_path = snap1.path
    snap2_path = snap2.path
    assert snap1_path.exists(), f"snap1 file should exist: {snap1_path}"
    assert snap2_path.exists(), f"snap2 file should exist: {snap2_path}"

    chain_len_before = _backing_chain_length(snap2_path, shell)
    assert chain_len_before is not None and chain_len_before >= 2, (
        f"Chain should have at least 2 entries, got {chain_len_before}"
    )

    # Step 2: Destroy the VM.
    destroy_result = shell.run(["virsh", "destroy", vm_name], timeout=30)
    assert destroy_result.success, f"virsh destroy failed: {destroy_result.error}"
    time.sleep(0.5)
    assert _vm_is_shut_off(shell, vm_name), "VM should be shut off"

    # Step 3: Invoke blockcommit with remove = {snap1, snap2}.
    retention = RetentionResult(
        keep=[],
        remove=[snap1.name, snap2.name],
    )
    core._blockcommit_snapshots(vm_config, retention)

    # --- Assertions ---

    # (a) snap1 committed offline — file deleted.
    assert not snap1_path.exists(), (
        f"snap1 should be deleted by offline commit, but still exists: {snap1_path}"
    )

    # (b) snap2 (XML-tip) deferred — file still exists.
    assert snap2_path.exists(), (
        f"snap2 (XML tip) should be deferred, file still exist: {snap2_path}"
    )

    # (c) Deferred entry for snap2 with reason "active_layer".
    deferred = state.get_deferred_operations(vm_name)
    assert len(deferred) == 1, f"Expected 1 deferred entry, got {len(deferred)}"
    assert deferred[0].reason == "active_layer", (
        f"Expected reason 'active_layer', got {deferred[0].reason!r}"
    )
    assert snap2.name in deferred[0].snapshots, (
        f"Deferred entry should contain snap2, got {deferred[0].snapshots}"
    )

    # (d) Chain shortened — tip (snap2) now points directly to base image
    #     (since snap1 was committed into base and snap2 was pivoted).
    chain_len_after = _backing_chain_length(snap2_path, shell)
    assert chain_len_after is not None, "Should be able to get chain length after commit"
    assert chain_len_after < chain_len_before, (
        f"Chain should be shorter after commit: was {chain_len_before}, now {chain_len_after}"
    )
    assert _backing_points_to(snap2_path, base_image, shell), (
        "Tip snapshot should point to base image after snap1 committed"
    )

    # (e) snap1 removed from state, snap2 still in state (deferred).
    remaining = state.get_snapshots(vm_name)
    remaining_names = {s.name for s in remaining}
    assert snap1.name not in remaining_names, "snap1 should be removed from state"
    assert snap2.name in remaining_names, "snap2 (deferred tip) should remain in state"

    # (f) Verify virsh start SUCCEEDS directly (source fix D8).
    start_after = shell.run(["virsh", "start", vm_name], timeout=30)
    assert start_after.success, (
        f"virsh start should succeed with tip intact, got: {start_after.error}"
    )
    # Verify it's running, then stop.
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running after start"
    shell.run(["virsh", "destroy", vm_name], timeout=30)


# ──────────────────────────────────────────────────────────────────────
# Test 5: Dry-run predicts deferred blockcommit drain without mutation
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_dry_run_deferred_drain_prediction(test_vm):
    """Dry-run predicts deferred blockcommit drain read-only; no execution.

    1. Start VM, create 2 snapshots with lifecycle_mode="virsh".
    2. Manually add a deferred blockcommit entry for the oldest
       (non-active) snapshot.
    3. Set core.dry_run = True and run the pipeline.
    4. Assert: at least one blockcommit prediction with a disk field exists.
    5. Assert: deferred queue is UNCHANGED after the dry run.
    6. Assert: no blockcommit was executed (files + chain intact).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Step 1: Start VM.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    # Build Core with lifecycle_mode="virsh" so the non-active snapshot
    # is committable live (the deferred-drain prediction path uses
    # the same adaptive fork as the real path).
    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        lifecycle_mode="virsh",
    )

    # Create 2 snapshots (snap0 = oldest non-active, snap1 = active tip).
    for i in range(2):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(1.1)

    snapshots = state.get_snapshots(vm_name)
    assert len(snapshots) == 2, f"Expected 2 snapshots, got {len(snapshots)}"

    oldest = snapshots[0]
    newest = snapshots[1]
    oldest_path = oldest.path
    newest_path = newest.path
    assert oldest_path.exists(), f"Oldest snapshot should exist: {oldest_path}"
    assert newest_path.exists(), f"Newest snapshot should exist: {newest_path}"

    # Record chain length on the tip before the dry run.
    chain_len_before = _backing_chain_length(newest_path, shell)
    assert chain_len_before is not None, "Should be able to get chain length"
    assert chain_len_before >= 2, f"Chain should have at least 2 entries, got {chain_len_before}"

    # Step 2: Add a deferred blockcommit entry for the oldest snapshot.
    state.add_deferred_blockcommit(
        vm_name,
        "vda",
        snapshots=[oldest.name],
        reason="vm_running",
    )

    deferred_before = state.get_deferred_operations(vm_name)
    assert len(deferred_before) == 1, "Deferred entry should exist before dry-run"

    # Step 3: Set dry_run = True and run the pipeline.
    core.dry_run = True
    result = core.run(vm_name)

    # --- Assertions ---

    # (a) Result carries dry_run=True and no actions were executed.
    assert result.dry_run is True, f"Expected result.dry_run=True, got {result.dry_run}"
    assert result.actions == [], f"Expected no actions in dry-run mode, got {result.actions}"

    # (b) At least one blockcommit prediction with a per-disk ``disk`` field.
    blockcommit_preds = [p for p in result.predictions if p.action == "blockcommit"]
    assert len(blockcommit_preds) > 0, (
        f"Expected at least one blockcommit prediction, "
        f"got predictions={[(p.action, p.name, p.disk) for p in result.predictions]}"
    )
    for pred in blockcommit_preds:
        assert pred.disk is not None, f"blockcommit prediction missing disk field: {pred}"

    # (c) Deferred queue UNCHANGED after dry run (no queue rewrite).
    deferred_after = state.get_deferred_operations(vm_name)
    assert len(deferred_after) == len(deferred_before), (
        f"Deferred queue size changed after dry-run: "
        f"before={len(deferred_before)}, after={len(deferred_after)}"
    )
    assert deferred_after[0].snapshots == deferred_before[0].snapshots, (
        f"Deferred entry snapshots changed after dry-run: "
        f"before={deferred_before[0].snapshots}, after={deferred_after[0].snapshots}"
    )

    # (d) No blockcommit was executed — overlay file still exists on disk.
    assert oldest_path.exists(), (
        f"Oldest snapshot should still exist after dry-run (no real blockcommit): {oldest_path}"
    )

    # (e) Backing chain unchanged.
    chain_len_after = _backing_chain_length(newest_path, shell)
    assert chain_len_after is not None, "Should be able to get chain length after dry-run"
    assert chain_len_after == chain_len_before, (
        f"Backing chain length should be unchanged after dry-run: "
        f"was {chain_len_before}, now {chain_len_after}"
    )

    # Cleanup: VM still running (dry-run is read-only), destroy it.
    assert _vm_is_running(shell, vm_name), "VM should still be running after dry-run"
    shell.run(["virsh", "destroy", vm_name], timeout=30)


# ──────────────────────────────────────────────────────────────────────
# Test 6: Offline-commit ENOSPC defers (reason "enospc"), then drains
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_offline_commit_enospc_defers_then_drains_integration(test_vm, caplog):
    """Offline ``qemu-img commit`` ENOSPC defers with reason "enospc".

    Design D4: a space-classified blockcommit failure is deferred (not a
    VM abort).  The snapshots stay in state so the next run can retry.

    1. Start VM, create 2 snapshots (snap0 = oldest, snap1 = active).
       Record them in state with ``disk="vda"`` (the ENOSPC deferral path
       is scoped per disk).
    2. Destroy the VM → offline commit path (qemu-img).
    3. Patch ``shell.run`` so the ``qemu-img commit`` invocation returns
       "No space left on device" (simulating a full snapshot filesystem —
       no root/loopback mount is available in the test environment).
    4. Run ``core.prune()`` → the commit is deferred with reason
       "enospc"; NO RuntimeError / VM abort; snapshot files and state
       records intact; the run is ``space_limited``.
    5. Unpatch and drain the deferred queue → a REAL offline commit
       executes: the oldest snapshot file is deleted, the chain is
       shortened, and the queue is empty.
    """
    from unittest.mock import patch

    from tests.helpers import snapshot_create

    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Step 1: Start VM and create 2 snapshots (recorded with disk="vda").
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        lifecycle_mode="virsh",
    )

    # chain_length=1 → with 2 snapshots the oldest is a removal candidate
    # (chain_length=24, the helper default, would remove nothing).  Core
    # reads the VM from the config facade, so rebuild the facade with the
    # replaced config.
    from dataclasses import replace

    vm_config = replace(vm_config, snapshot_chain_length=1)
    config = MockConfigFacade(
        global_config=GlobalConfig(),
        vms=[vm_config],
    )
    core._config = config

    import secrets

    for i in range(2):
        hex_sfx = secrets.token_hex(3)
        snap = snapshot_create(
            shell,
            vm_name,
            f"{vm_name}.enospc-{i}-{hex_sfx}",
            "vda",
            snapshot_dir,
            base_image,
        )
        state.record_snapshot(vm_name, snap)
        time.sleep(0.6)

    snapshots = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    assert len(snapshots) == 2, f"Expected 2 snapshots, got {len(snapshots)}"
    oldest, newest = snapshots[0], snapshots[1]
    oldest_path = oldest.path
    newest_path = newest.path
    assert oldest_path.exists(), f"Oldest file should exist: {oldest_path}"
    assert newest_path.exists(), f"Newest file should exist: {newest_path}"

    chain_len_before = _backing_chain_length(newest_path, shell)
    assert chain_len_before is not None and chain_len_before >= 2

    # Step 2: Destroy the VM → offline commit path.
    destroy_result = shell.run(["virsh", "destroy", vm_name], timeout=30)
    assert destroy_result.success, f"virsh destroy failed: {destroy_result.error}"
    time.sleep(0.5)
    assert _vm_is_shut_off(shell, vm_name), "VM should be shut off"

    # Step 3: Simulate ENOSPC on the offline qemu-img commit.
    from qsnap.models.results import ShellResult

    orig_run = shell.run

    def _enospc_run(cmd, timeout=30, check=False):
        if cmd and cmd[:2] == ["qemu-img", "commit"]:
            return ShellResult(
                success=False,
                stdout="",
                stderr="qemu-img: error while writing to output file: No space left on device",
                returncode=1,
                error="qemu-img: error while writing to output file: No space left on device",
            )
        return orig_run(cmd, timeout=timeout, check=check)

    with patch.object(shell, "run", side_effect=_enospc_run):
        caplog.clear()
        with caplog.at_level(logging.INFO):
            result = core.prune(vm_name)

    # No VM abort: the per-VM result is successful (deferral is not a failure).
    assert result.results[0].success, (
        f"ENOSPC commit deferral must not abort the VM: {result.results[0].error}"
    )
    # Space-limited run drives exit code 4.
    assert result.space_limited is True, "ENOSPC-deferred commit must mark the run space_limited"

    # Deferred entry with reason "enospc" holds the oldest snapshot.
    deferred = state.get_deferred_operations(vm_name)
    assert len(deferred) == 1, f"Expected 1 deferred entry, got {len(deferred)}"
    assert deferred[0].reason == "enospc", f"Expected reason 'enospc', got {deferred[0].reason!r}"
    assert oldest.name in deferred[0].snapshots, (
        f"Deferred entry must contain the oldest snapshot, got {deferred[0].snapshots}"
    )

    # Snapshot records and files are INTACT (never-delete-on-ENOSPC).
    remaining = state.get_snapshots(vm_name)
    assert {s.name for s in remaining} == {oldest.name, newest.name}, (
        "ENOSPC must not remove snapshot state records"
    )
    assert oldest_path.exists(), f"Oldest file must survive ENOSPC: {oldest_path}"
    assert newest_path.exists(), f"Newest file must survive ENOSPC: {newest_path}"

    # Step 4: free space (unpatch) → next run drains with a REAL commit.
    # The deferred queue is drained by ``_check_deferred_operations`` at
    # the start of the next pipeline run (``prune`` itself only evaluates
    # retention + lifecycle; it does not consult the queue).
    core._check_deferred_operations(vm_config)

    # Oldest file committed (deleted), chain shortened, queue empty.
    assert not oldest_path.exists(), f"Oldest snapshot must be committed after drain: {oldest_path}"
    chain_len_after = _backing_chain_length(newest_path, shell)
    assert chain_len_after is not None and chain_len_after < chain_len_before, (
        f"Chain must be shorter after drain: was {chain_len_before}, now {chain_len_after}"
    )
    assert state.get_deferred_operations(vm_name) == [], (
        "Deferred queue must be empty after the drain"
    )

    # Cleanup: restart and stop the VM for teardown (fixture destroys
    # and undefines the domain afterwards).
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(1)
    shell.run(["virsh", "destroy", vm_name], timeout=30)
