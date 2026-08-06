"""Integration tests for rollback + retry after failed FULL backup.

Verifies that when a FULL backup fails verification (M1/M2), the
rollback mechanism deletes the broken FULL file, its checkpoint, and
its state records — and that a subsequent retry can succeed.

All tests are marked ``@pytest.mark.integration``.  Run only when
explicitly requested::

    poetry run pytest tests/integration/test_rollback_retry.py -v -m integration
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
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


def _list_checkpoints(shell: SubprocessShell, vm_name: str) -> set[str]:
    """Return the set of ``qsnap-*`` checkpoint names for *vm_name*."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return set()
    return {
        line.strip()
        for line in result.stdout.strip().splitlines()
        if line.strip().startswith("qsnap-")
    }


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
# Test 1: Rollback deletes broken FULL file and checkpoint
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_rollback_deletes_broken_full_and_checkpoint(test_vm, caplog):
    """FULL fails after transfer — rollback deletes FULL file + checkpoint.

    1. Start VM, create snapshot.
    2. Set verify_after_create="check" so M2 runs.
    3. Patch ``verify_full_backup`` to force a verification failure — the
       rollback path runs deterministically.
    4. Verify: broken FULL file is deleted from target.
    5. Verify: the failed attempt's successor checkpoint (created
       atomically by backup-begin) is deleted by exact name while any
       pre-existing baseline remains.
    6. Verify: rollback log message appears.
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

    # Capture the checkpoint list before the run — any pre-existing
    # baseline must survive the rollback untouched.
    cps_before = _list_checkpoints(shell, vm_name)

    # Create snapshot and build Core with verification enabled.
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.rollback-snap", base_image, snapshot_dir)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)

    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(
            state_dir="/var/tmp",
            full_verify_after_create="check",
            full_verify_before_delete="check",
        ),
        vms=[vm_config],
        config_path=tmpdir / "rollback.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Step 3: Force a verification failure so the rollback runs
    # deterministically (design D1 — exact-name checkpoint deletion).
    caplog.clear()
    with (
        caplog.at_level(logging.INFO),
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: forced test failure",
        ),
    ):
        core.run(vm_name)

    all_logs = " ".join(r.message for r in caplog.records)

    # Rollback must have occurred.
    assert "rolled back" in all_logs.lower(), (
        f"Expected 'rolled back' in logs with forced verification failure. Logs: {all_logs[:500]}"
    )

    # Step 4: Broken FULL file deleted.
    full_files_after = sorted(target_dir.glob("*.FULL.*.qcow2"))
    assert full_files_after == [], (
        f"Broken FULL file must be deleted after rollback, got {full_files_after}"
    )

    # Step 5: No FULL in state for the failed attempt.
    fulls_in_state = state.get_full_backups(str(target_dir))
    assert fulls_in_state == [], (
        f"Failed FULL attempt must not be recorded in state, got {fulls_in_state}"
    )

    # The failed attempt's successor checkpoint was deleted by exact name
    # (the INFO log names it), and any pre-existing baseline remains.
    deleted_msgs = [
        r.message
        for r in caplog.records
        if "deleted checkpoint" in r.message and "after failed FULL" in r.message
    ]
    assert deleted_msgs, (
        f"Expected an exact-name 'deleted checkpoint ... after failed FULL' "
        f"log. Logs: {all_logs[:500]}"
    )
    successor = (
        deleted_msgs[0].split("deleted checkpoint ", 1)[1].split(" after failed FULL", 1)[0].strip()
    )
    assert successor.startswith("qsnap-"), f"Unexpected successor name: {successor!r}"

    cps_after = _list_checkpoints(shell, vm_name)
    assert successor not in cps_after, (
        f"Successor checkpoint {successor} must be deleted after rollback, got {cps_after}"
    )
    for cp in cps_before:
        assert cp in cps_after, (
            f"Pre-existing baseline checkpoint {cp} must survive the rollback, got {cps_after}"
        )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 1b: Stopped-VM FULL failure deletes NO checkpoint
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_stopped_vm_failed_full_deletes_no_checkpoint(test_vm, caplog):
    """Stopped-VM FULL failure rolls back without deleting any checkpoint.

    1. Seed a ``qsnap-*`` baseline checkpoint via a prior running-VM FULL.
    2. Destroy the VM — the next FULL uses the stopped-VM direct-convert
       path, which creates NO checkpoint (design D1).
    3. Force a verification failure (patched ``verify_full_backup``).
    4. Assert: zero ``virsh checkpoint-delete`` commands were issued during
       the rollback, and the pre-seeded baseline is still listed.
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

    # Step 1: Seed a baseline checkpoint via a successful running-VM FULL.
    state = InMemoryStateManager()
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(
            state_dir="/var/tmp",
            full_verify_after_create="check",
        ),
        vms=[vm_config],
        config_path=tmpdir / "stopped_rollback.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    seed_result = core.run(vm_name)
    assert seed_result.results[0].success, (
        f"Seed running-VM FULL failed: {seed_result.results[0].error}"
    )
    baselines = _list_checkpoints(shell, vm_name)
    assert len(baselines) >= 1, f"Expected a seeded baseline checkpoint, got {baselines}"

    # Fresh snapshot for the stopped-VM FULL source.
    fresh_snap = _snapshot_create(
        shell, vm_name, f"{vm_name}.stopped-source", base_image, snapshot_dir
    )
    state.record_snapshot(vm_name, fresh_snap)

    # Step 2: Destroy the VM — the next FULL takes the stopped-VM path.
    destroy = shell.run(["virsh", "destroy", vm_name], timeout=30)
    assert destroy.success, f"virsh destroy failed: {destroy.error}"
    time.sleep(0.5)
    assert not is_vm_running(shell, vm_name), "VM must be stopped for this test"

    # Force a new FULL (the stopped-VM direct-convert path).
    core._force_full_targets.add(str(target_dir))

    # Spy on shell.run to record any virsh checkpoint-delete issued during
    # the rollback.
    delete_calls: list[list[str]] = []
    orig_run = shell.run

    def _recording_run(cmd, timeout=30, check=False):
        if cmd and cmd[0] == "virsh" and "checkpoint-delete" in cmd:
            delete_calls.append(list(cmd))
        return orig_run(cmd, timeout=timeout, check=check)

    shell.run = _recording_run  # type: ignore[method-assign]

    try:
        # Step 3: Force verification failure — rollback runs but has no
        # checkpoint to delete (the stopped-VM FULL created none).
        caplog.clear()
        with (
            caplog.at_level(logging.INFO),
            patch(
                "qsnap.core.verify_full_backup",
                return_value="verification failed: forced test failure",
            ),
        ):
            failed_result = core.backup(vm_name)

        assert not failed_result.results[0].success, "Expected the FULL attempt to fail"
        all_logs = " ".join(r.message for r in caplog.records)
        assert "rolled back" in all_logs.lower(), (
            f"Expected rollback to run. Logs: {all_logs[:500]}"
        )
    finally:
        shell.run = orig_run  # type: ignore[method-assign]

    # Step 4a: Zero virsh checkpoint-delete calls during the rollback.
    assert delete_calls == [], (
        f"Stopped-VM FULL rollback must not issue any virsh checkpoint-delete, got {delete_calls}"
    )

    # Step 4b: The pre-seeded baseline is still listed.
    cps_after = _list_checkpoints(shell, vm_name)
    for baseline in baselines:
        assert baseline in cps_after, (
            f"Pre-seeded baseline checkpoint {baseline} must remain listed, got {cps_after}"
        )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Retry after rollback succeeds
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_retry_after_rollback_succeeds(test_vm, caplog):
    """First FULL attempt fails, second succeeds — final state has one valid FULL.

    1. Start VM, create snapshot.
    2. Create a FULL manually — simulate a successful transfer that
       passes verification.
    3. Record the FULL in state.
    4. Verify the FULL file exists and is a valid qcow2.
    5. Verify exactly one FULL is recorded in state.
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

    # Create snapshot.
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.retry-snap", base_image, snapshot_dir)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)

    # Step 1: Build Core with retry enabled.
    target = TargetConfig(
        path=target_dir,
        compress=False,
        verify="off",
        backup_retry_max=3,
        backup_retry_base="1s",
    )
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(
            state_dir="/var/tmp",
        ),
        vms=[vm_config],
        config_path=tmpdir / "retry.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Step 2: Run core.run() — creates a FULL backup.
    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    # Step 3: Verify FULL file exists and is valid.
    full_files = sorted(target_dir.glob("*.FULL.*.qcow2"))
    assert len(full_files) >= 1, (
        f"Expected at least one FULL backup file on target. Contents: {list(target_dir.iterdir())}"
    )

    # Step 4: Verify FULL is recorded in state.
    fulls_in_state = state.get_full_backups(str(target_dir))
    assert len(fulls_in_state) >= 1, (
        f"Expected at least one FULL in state, got {len(fulls_in_state)}"
    )

    # Verify the FULL file is a valid qcow2.
    info_result = shell.run(
        ["qemu-img", "info", str(full_files[0])],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed on FULL: {info_result.error}"
    assert "qcow2" in info_result.stdout.lower(), (
        f"FULL file should be qcow2, got: {info_result.stdout[:200]}"
    )

    _cleanup_checkpoints(shell, vm_name)
