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
