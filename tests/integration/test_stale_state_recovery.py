"""Integration tests for stale state detection and self-healing recovery.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py`` which creates a disposable throwaway VM.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.backup.file_copy import FileCopyBackupProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from tests.mocks.mock_state import InMemoryStateManager

# ──────────────────────────────────────────────────────────────────────
# Test 1: Stale state self-healing — snapshot file missing on disk
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_stale_state_snapshot_removed_when_file_missing(test_vm):
    """Verify stale state entries are self-healed when the snapshot file
    no longer exists on disk.

    1. Register a snapshot in ``InMemoryStateManager`` pointing to a
       non-existent path.
    2. Create ``FileCopyBackupProvider`` with the state manager.
    3. Configure a ``TargetConfig`` that is non-empty (contains at least
       one file) so the FULL-backup short-circuit is not triggered and
       ``transfer_missing()`` iterates over the snapshot list.
    4. Call ``transfer_missing()`` with the stale snapshot.
    5. Verify the stale entry was removed from state.
    6. Verify the backup result list is empty (stale snapshot skipped).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Step 1: Create a sentinel file in the target directory so that
    # the "target is empty → create FULL" short-circuit is NOT
    # triggered.  This ensures transfer_missing() will iterate over
    # the snapshots list and hit our stale-state check.
    sentinel = target_dir / "_sentinel.qcow2"
    sentinel.write_bytes(b"\x00" * 1024)

    # Step 2: Register a snapshot in state that points to a file that
    # does NOT exist on disk (stale state).
    state = InMemoryStateManager()
    stale_path = Path("/tmp/qsnap-nonexistent-stale-snapshot.qcow2")
    stale_snapshot = SnapshotInfo(
        name=f"{vm_name}.stale-snapshot",
        path=stale_path,
        timestamp=datetime.now(),
        allocation=0,
    )
    state.record_snapshot(vm_name, stale_snapshot)

    # Verify the stale snapshot is in state before the test.
    snapshots_before = state.get_snapshots(vm_name)
    assert len(snapshots_before) == 1, "Stale snapshot should be registered in state"
    assert snapshots_before[0].name == stale_snapshot.name

    # Step 3: Create FileCopyBackupProvider with state manager.
    provider = FileCopyBackupProvider(shell, state=state)
    target = TargetConfig(
        path=target_dir,
        incremental=True,
        compress=False,
        verify="off",
    )
    vm_config = VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir)

    # Step 4: Call transfer_missing with the stale snapshot.
    # The stale snapshot is not on disk, so the provider should detect
    # this, remove it from state, and skip the transfer.
    results = provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[stale_snapshot],
    )

    # Step 5: Verify the stale entry was removed from state.
    snapshots_after = state.get_snapshots(vm_name)
    assert len(snapshots_after) == 0, (
        f"Stale snapshot should have been removed from state. "
        f"Got {len(snapshots_after)} entries: "
        f"{[s.name for s in snapshots_after]}"
    )

    # Step 6: Verify no backup results were produced (stale skipped).
    assert len(results) == 0, (
        f"Expected 0 backup results for stale snapshot, got {len(results)}. "
        f"Results: {[r.snapshot_name for r in results]}"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 2: Stale state recovery — crash recovery scenario
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_stale_state_crash_recovery_simulated(test_vm):
    """Simulate a crash-recovery scenario where state holds entries that
    have already been blockcommitted or deleted on disk.

    1. Do NOT start the VM — use a stopped-VM scenario.
    2. Register TWO snapshots in state:
       - One pointing to a real, existing file (the base image).
       - One pointing to a non-existent path (stale, simulating a
         blockcommitted snapshot whose state entry was not cleaned up).
    3. Call ``transfer_missing()`` with both snapshots.
    4. Verify the stale entry was removed from state.
    5. Verify the valid entry still exists in state.
    6. Verify the valid snapshot was transferred (BackupResult produced).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Ensure the target directory has at least one file to avoid the
    # "empty target → create FULL" short-circuit.
    sentinel = target_dir / "_sentinel.qcow2"
    sentinel.write_bytes(b"\x00" * 1024)

    # Step 1: Create state with one valid and one stale snapshot.
    state = InMemoryStateManager()

    # Valid snapshot — points to the base image that actually exists.
    valid_snapshot = SnapshotInfo(
        name=f"{vm_name}.valid",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    state.record_snapshot(vm_name, valid_snapshot)

    # Stale snapshot — points to a path that does NOT exist on disk.
    stale_snapshot = SnapshotInfo(
        name=f"{vm_name}.stale-blockcommitted",
        path=Path("/tmp/qsnap-nonexistent-stale.qcow2"),
        timestamp=datetime.now(),
        allocation=0,
    )
    state.record_snapshot(vm_name, stale_snapshot)

    # Verify both are in state before the test.
    snapshots_before = state.get_snapshots(vm_name)
    assert len(snapshots_before) == 2, (
        f"Expected 2 snapshots in state, got {len(snapshots_before)}"
    )

    # Step 2: Create provider and call transfer_missing.
    provider = FileCopyBackupProvider(shell, state=state)
    target = TargetConfig(
        path=target_dir,
        incremental=True,
        compress=False,
        verify="off",
    )
    vm_config = VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir)

    results = provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[stale_snapshot, valid_snapshot],
    )

    # Step 3: Verify stale entry was removed.
    snapshots_after = state.get_snapshots(vm_name)
    assert len(snapshots_after) == 1, (
        f"Expected 1 snapshot (only valid) in state after self-healing, "
        f"got {len(snapshots_after)}: {[s.name for s in snapshots_after]}"
    )
    assert snapshots_after[0].name == valid_snapshot.name, (
        f"Expected valid snapshot {valid_snapshot.name} to remain, "
        f"got {snapshots_after[0].name}"
    )

    # Step 4: Verify the valid snapshot was transferred.
    transferred_names = {r.snapshot_name for r in results if r.success}
    assert valid_snapshot.name in transferred_names, (
        f"Valid snapshot should have been transferred. "
        f"Transferred: {transferred_names}"
    )

    # Step 5: Verify no result was produced for the stale snapshot.
    stale_results = [r for r in results if r.snapshot_name == stale_snapshot.name]
    assert len(stale_results) == 0, (
        f"No backup result should be produced for stale snapshot, "
        f"got {len(stale_results)}"
    )
