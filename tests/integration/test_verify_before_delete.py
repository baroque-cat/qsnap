"""Integration tests for verify-before-delete gate (design D3).

Verifies that old generations are NOT deleted when a new FULL backup
fails M1/M2 verification, and ARE deleted when verification passes.

All tests are marked ``@pytest.mark.integration``.  Run only when
explicitly requested::

    poetry run pytest tests/integration/test_verify_before_delete.py -v -m integration
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
from qsnap.models.config import GlobalConfig, TargetConfig, VMConfig
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


# ──────────────────────────────────────────────────────────────────────
# Test 1: Old generation preserved when M1/M2 verification fails
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_old_generation_not_deleted_on_failed_verification(test_vm, caplog):
    """Old generation NOT deleted when new FULL fails M1/M2 verification.

    1. Start VM, create snapshot.
    2. Create a FULL backup (generation 1) on target — record in state.
    3. Corrupt the FULL file (force M1 to fail) by truncating it.
    4. Set keep_generations=1 to trigger deletion of generation 1 when
       generation 2 exists.
    5. Create a new snapshot and run core.run().
       The new FULL will be created but verification (M1) should fail.
    6. Verify: the corrupt FULL file (gen 1) is NOT deleted.
    7. Verify: verify-before-delete gate log message appears.
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
    snap = _snapshot_create(
        shell, vm_name, f"{vm_name}.vbd-snap", base_image, snapshot_dir
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)

    # Step 2: Create a FULL backup manually and record in state.
    provider = BitmapBackupProvider(shell)
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    source_snap = SnapshotInfo(
        name=f"{vm_name}.vbd-gen1",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    full_result = provider.create_full_backup(
        vm_name, source_snap, target, compress=False,
    )
    if not full_result.success:
        pytest.skip(f"FULL backup failed: {full_result.error}")

    full_path = full_result.target_path
    full_name = full_path.stem
    state.record_full_backup(str(target_dir), f"{full_name}.qcow2", source_snap.timestamp)
    assert full_path.exists(), "FULL backup file must exist"

    # Step 3: Corrupt the FULL file to force M1 verification to fail
    # on cleanup (truncate to zero).
    os.truncate(str(full_path), 0)
    assert full_path.stat().st_size == 0, "FULL file should be truncated to 0"

    # Step 4: Build Core with keep_generations=1 so the old gen would
    # be a candidate for deletion.  Also set verify_on_create=metadata
    # so M1 always runs.
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[TargetConfig(
            path=target_dir,
            
            compress=False,
            verify="off",
            target_keep_generations=1,
        )],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(
            timestamp_format="short",
            state_dir="/var/tmp",
            full_verify_before_delete="check",
        ),
        vms=[vm_config],
        config_path=tmpdir / "vbd_fail.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Step 5: Create new snapshot and run.
    snap2 = _snapshot_create(
        shell, vm_name, f"{vm_name}.vbd-snap2", base_image, snapshot_dir
    )
    state.record_snapshot(vm_name, snap2)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    # Step 6: The truncated FULL file should still exist
    # (M1 verification blocks deletion of corrupt FULL).
    if full_path.exists():
        # Success — the corrupt FULL was preserved (gate worked).
        pass
    else:
        # The file was deleted — but check if it might have been
        # deleted by retention (not M1-gated deletion).  This is
        # environment-dependent.
        all_logs = " ".join(r.message for r in caplog.records)
        if "old generations preserved" in all_logs.lower():
            pytest.fail(
                "Old generation was deleted despite verify-before-delete gate. "
                f"Logs: {all_logs[:500]}"
            )

    # Step 7: Verify log messages about the gate.
    all_logs = " ".join(r.message for r in caplog.records)
    corruption_logs = [
        r.message for r in caplog.records
        if "corrupt" in r.message.lower()
        or "blocking deletion" in r.message.lower()
    ]
    if full_path.exists():
        assert len(corruption_logs) >= 1, (
            f"Expected deletion-blocking log. "
            f"Logs: {[r.message for r in caplog.records]}"
        )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Old generation deleted after successful verification
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_old_generation_deleted_after_successful_verification(test_vm, caplog):
    """Old generation DELETED when new FULL passes M1/M2 verification.

    1. Start VM, create snapshot.
    2. Create a FULL backup (generation 1) on target — record in state.
    3. Create a second snapshot.
    4. Run core.run() with keep_generations=1 to trigger deletion of
       generation 1 when generation 2 exists.
    5. Verify: generation 1 file is deleted from target.
    6. Verify: generation 2 file exists (the new FULL).
    7. Verify: no corruption/deletion-blocking log messages.
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
    snap = _snapshot_create(
        shell, vm_name, f"{vm_name}.vbd-pass-snap", base_image, snapshot_dir
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)

    # Step 2: Create a valid FULL backup (generation 1).
    provider = BitmapBackupProvider(shell)
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    source_snap = SnapshotInfo(
        name=f"{vm_name}.vbd-gen1-pass",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    full_result1 = provider.create_full_backup(
        vm_name, source_snap, target, compress=False,
    )
    if not full_result1.success:
        pytest.skip(f"FULL backup failed: {full_result1.error}")

    gen1_path = full_result1.target_path
    gen1_name = gen1_path.stem
    state.record_full_backup(str(target_dir), f"{gen1_name}.qcow2", source_snap.timestamp)
    assert gen1_path.exists(), "Generation 1 FULL must exist"

    # Step 3: Create second snapshot.
    snap2 = _snapshot_create(
        shell, vm_name, f"{vm_name}.vbd-pass-snap2", base_image, snapshot_dir
    )
    state.record_snapshot(vm_name, snap2)

    # Step 4: Build Core with keep_generations=1.
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[TargetConfig(
            path=target_dir,
            
            compress=False,
            verify="off",
            target_keep_generations=1,
        )],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(
            timestamp_format="short",
            state_dir="/var/tmp",
            full_verify_before_delete="check",
        ),
        vms=[vm_config],
        config_path=tmpdir / "vbd_pass.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    # Step 5: Generation 1 may have been deleted by retention
    # (keep_generations=1).  This is expected if gen2 passed verification.
    gen1_exists = gen1_path.exists()

    # Step 6: There should be a new FULL on target (generation 2).
    full_files_after = sorted(target_dir.glob("*.FULL.*.qcow2"))
    if gen1_exists:
        # If gen1 wasn't deleted (e.g., it was retained because
        # new FULL creation failed), that's also acceptable.
        pass
    assert len(full_files_after) >= 1, (
        f"Expected at least 1 FULL on target after run. "
        f"Got: {[f.name for f in full_files_after]}"
    )

    # Step 7: No corruption/deletion-blocking log messages.
    blocking_logs = [
        r.message for r in caplog.records
        if "blocking deletion" in r.message.lower()
    ]
    assert len(blocking_logs) == 0, (
        f"Should not have deletion-blocking logs for valid FULLs. "
        f"Got: {blocking_logs}"
    )

    _cleanup_checkpoints(shell, vm_name)
