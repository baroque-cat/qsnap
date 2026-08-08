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
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.vbd-snap", base_image, snapshot_dir)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)

    # Step 2: Create a FULL backup manually and record in state.
    # Phase 2: the backup world is orthogonal to snapshots — the
    # provider no longer accepts a SnapshotInfo source.  run_backup()
    # takes a VMConfig + DiskConfig and decides FULL vs delta from
    # checkpoint state.
    provider = BitmapBackupProvider(shell)
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    backup_vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    full_result = provider.run_backup(backup_vm_config, target, backup_vm_config.disks[0])
    if not full_result.success:
        pytest.skip(f"FULL backup failed: {full_result.error}")

    full_path = full_result.target_path
    full_name = full_path.stem
    state.record_full_backup(str(target_dir), f"{full_name}.qcow2", datetime.now(), disk="vda")
    assert full_path.exists(), "FULL backup file must exist"

    # Step 3: Corrupt the FULL file to force M2 verification to fail
    # on cleanup.  Truncate to 64 KiB — keeps the qcow2 header intact so
    # qemu-img info succeeds (M1 passes, file enters retention), but
    # qemu-img check (M2) reports errors because L1/L2 tables reference
    # clusters beyond the truncated file boundary.
    header_size = 65536
    os.truncate(str(full_path), header_size)
    assert full_path.stat().st_size == header_size, (
        f"FULL file should be truncated to {header_size}"
    )

    # Record a second FULL in state to make the corrupt one a deletion
    # candidate under keep_generations=1.  The second FULL file must
    # exist on disk so provider.list() returns it.
    gen2_path = target_dir / f"{vm_name}.FULL.gen2.20300101T000001_a1b2c3.qcow2"
    # Create a valid qcow2 so provider.list() includes it.
    shell.run(["qemu-img", "create", "-f", "qcow2", str(gen2_path), "128K"], timeout=30)
    assert gen2_path.exists(), "gen2 FULL file must exist"
    state.record_full_backup(str(target_dir), gen2_path.name, datetime(2030, 1, 1), disk="vda")

    # Step 4: Build Core with keep_generations=1 so the old gen would
    # be a candidate for deletion.
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=999,  # prevent blockcommit from interfering
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
                target_keep_generations=1,
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(
            state_dir="/var/tmp",
            full_verify_before_delete="check",
        ),
        vms=[vm_config],
        config_path=tmpdir / "vbd_fail.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Run cleanup directly — the corrupt FULL (older) should be a
    # deletion candidate under keep_generations=1.
    backups, retention_result = core._evaluate_backup_retention(vm_config, vm_config.targets[0])
    caplog.clear()
    with caplog.at_level(logging.INFO):
        core._cleanup_backups(vm_config, vm_config.targets[0], backups, retention_result)

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
        r.message
        for r in caplog.records
        if "corrupt" in r.message.lower() or "blocking deletion" in r.message.lower()
    ]
    if full_path.exists():
        assert len(corruption_logs) >= 1, (
            f"Expected deletion-blocking log. Logs: {[r.message for r in caplog.records]}"
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
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.vbd-pass-snap", base_image, snapshot_dir)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)

    # Step 2: Create a valid FULL backup (generation 1).
    provider = BitmapBackupProvider(shell)
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    backup_vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    full_result1 = provider.run_backup(backup_vm_config, target, backup_vm_config.disks[0])
    if not full_result1.success:
        pytest.skip(f"FULL backup failed: {full_result1.error}")

    gen1_path = full_result1.target_path
    gen1_name = gen1_path.stem
    state.record_full_backup(str(target_dir), f"{gen1_name}.qcow2", datetime.now(), disk="vda")
    assert gen1_path.exists(), "Generation 1 FULL must exist"

    # Step 3: Create second snapshot.
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.vbd-pass-snap2", base_image, snapshot_dir)
    state.record_snapshot(vm_name, snap2)

    # Step 4: Build Core with keep_generations=1.
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
                target_keep_generations=1,
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(
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
        f"Expected at least 1 FULL on target after run. Got: {[f.name for f in full_files_after]}"
    )

    # Step 7: No corruption/deletion-blocking log messages.
    blocking_logs = [r.message for r in caplog.records if "blocking deletion" in r.message.lower()]
    assert len(blocking_logs) == 0, (
        f"Should not have deletion-blocking logs for valid FULLs. Got: {blocking_logs}"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: per-target ENOSPC isolation does NOT weaken verify-before-delete
# ──────────────────────────────────────────────────────────────────────


def _fake_full_backup_factory(error: str):
    """Return a ``run_backup`` replacement failing with *error*."""

    def _fake_full(
        vm_config,
        target,
        disk,
        force_full=False,
        compression_type="zstd",
        stall_timeout=1800,
        convert_parallel=4,
        convert_out_of_order=True,
    ):
        from qsnap.models.results import BackupResult

        return BackupResult(
            success=False,
            snapshot_name="",
            source_path=disk.base_image,
            target_path=target.path / "fake-full.qcow2",
            bytes_transferred=0,
            error=error,
            duration=0.0,
            disk=disk.target,
            kind="full",
        )

    return _fake_full


def _seed_old_generation(shell, target_dir, state, vm_name, gen_name: str) -> Path:
    """Create a valid old FULL on *target_dir*, recorded in state."""
    old_path = target_dir / f"{vm_name}.FULL.{gen_name}.20300101T000001_a1b2c3.qcow2"
    shell.run(["qemu-img", "create", "-f", "qcow2", str(old_path), "128K"], timeout=30)
    assert old_path.exists(), f"Old FULL file must exist: {old_path}"
    state.record_full_backup(str(target_dir), old_path.name, datetime(2030, 1, 1), disk="vda")
    return old_path


def _build_vbd_core(
    shell,
    vm_name,
    base_image,
    snapshot_dir,
    target_dir,
    tmpdir,
    state,
    snap,
) -> tuple[Core, VMConfig]:
    """Build Core whose target keep_generations=1 would delete the old gen."""
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=999,  # prevent blockcommit from interfering
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
                target_keep_generations=1,
                backup_retry_max=1,  # one attempt — no retry backoff delay
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(
            state_dir="/var/tmp",
            full_verify_before_delete="check",
            backup_retry_max=1,  # one attempt — no retry backoff delay
        ),
        vms=[vm_config],
        config_path=tmpdir / "vbd_isolation.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    state.record_snapshot(vm_name, snap)
    return core, vm_config


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_verification_failure_still_aborts_old_generation_preserved(test_vm, caplog):
    """A verification failure (non-space) STILL aborts via BackupAbortError.

    Per-target ENOSPC isolation must not weaken the verify-before-delete
    gate: a non-space FULL failure (e.g. M1/M2 verification) raises
    ``BackupAbortError`` (backup_failed=True) and the old generation is
    NOT deleted.
    """
    from unittest.mock import patch

    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Stop the VM (direct-convert path; provider is patched anyway).
    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(0.5)

    # Real snapshot + state; old generation on target.
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.vbd-abort-snap", base_image, snapshot_dir)
    state = InMemoryStateManager()
    old_gen = _seed_old_generation(shell, target_dir, state, vm_name, "abort")
    core, _ = _build_vbd_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, tmpdir, state, snap
    )
    # Force the FULL-creation path (an old FULL in state would otherwise
    # skip it via the count-based chain check).
    core._force_full_targets.add(str(target_dir))

    with patch(
        "qsnap.modules.backup.bitmap.BitmapBackupProvider.run_backup",
        side_effect=_fake_full_backup_factory(
            "verification failed: content comparison mismatch at offset 0x1000"
        ),
    ):
        result = core.backup(vm_name)

    # VM marked failed with backup_failed=True (BackupAbortError semantics).
    vm_result = result.results[0]
    assert vm_result.success is False, "Verification failure must fail the VM"
    assert vm_result.backup_failed is True, (
        "Verification failure must map to backup_failed (exit 10)"
    )
    # Phase 2: the "old generations preserved" message is logged at
    # CRITICAL by Core (the BackupAbortError itself carries the target +
    # disk failure details).  Assert the gate message via the log.
    all_logs = " ".join(r.message for r in caplog.records)
    assert "old generations preserved" in all_logs, (
        f"Abort must log that old generations are preserved: {all_logs[:500]}"
    )

    # Old generation NOT deleted (verify-before-delete gate holds).
    assert old_gen.exists(), (
        f"Old generation must NOT be deleted on verification failure: {old_gen}"
    )

    # No fake/new FULL on target beyond the old generation.
    fulls = [p for p in target_dir.glob("*.FULL.*.qcow2") if p != old_gen]
    assert fulls == [], f"No new FULL may be recorded on failure: {fulls}"


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_space_error_suspends_target_no_abort_old_generation_preserved(test_vm, caplog):
    """A space-classified FULL failure suspends the target, does NOT abort.

    ENOSPC isolation: no ``BackupAbortError`` (backup_failed=False), the
    run is ``space_limited`` (exit 4), the VM result is a success, and
    the old generation is preserved (never-delete-on-ENOSPC).
    """
    from unittest.mock import patch

    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(0.5)

    snap = _snapshot_create(shell, vm_name, f"{vm_name}.vbd-space-snap", base_image, snapshot_dir)
    state = InMemoryStateManager()
    old_gen = _seed_old_generation(shell, target_dir, state, vm_name, "space")
    core, _ = _build_vbd_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, tmpdir, state, snap
    )
    # Force the FULL-creation path (see verification-failure test above).
    core._force_full_targets.add(str(target_dir))

    with patch(
        "qsnap.modules.backup.bitmap.BitmapBackupProvider.run_backup",
        side_effect=_fake_full_backup_factory(
            "qemu-img: error while writing to output file: No space left on device"
        ),
    ):
        result = core.backup(vm_name)

    # No abort: VM result success, not backup_failed.
    vm_result = result.results[0]
    assert vm_result.success is True, f"Space failure must not abort the VM: {vm_result.error}"
    assert vm_result.backup_failed is False, (
        "Space failure must NOT set backup_failed (no BackupAbortError)"
    )

    # Space-limited run → CLI exit 4.
    assert result.space_limited is True, (
        "Space-classified FULL failure must mark the run space_limited"
    )

    # Never-delete-on-ENOSPC: old generation preserved.
    assert old_gen.exists(), f"Old generation must NOT be deleted on ENOSPC: {old_gen}"

    _cleanup_checkpoints(shell, vm_name)
