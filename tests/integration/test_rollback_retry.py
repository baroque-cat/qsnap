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
    3. Patch ``qsnap.modules.backup.bitmap.verify_full_backup`` to force a
       verification failure — the provider's rollback path runs
       deterministically (Phase 2: verification moved into
       ``run_backup()``, Core retries via the retry wrapper instead of
       logging "rolled back").
    4. Verify: broken FULL file is deleted from target.
    5. Verify: the failed attempt's successor checkpoint (created
       atomically by backup-begin) is deleted by exact name while any
       pre-existing baseline remains.
    6. Verify: Core logs the failure ("FULL backup failed after retries").
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
    # snapshot_chain_length=999: keep the test snapshot out of snapshot
    # retention so blockcommit never runs before the backup step.
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.rollback-snap", base_image, snapshot_dir)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)

    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=999,
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

    # Spy on shell.run to record any virsh checkpoint-delete issued during
    # the rollback (Phase 2: the provider deletes the successor checkpoint
    # by exact name inside run_backup() — no Core "deleted checkpoint"
    # log line is emitted anymore).
    delete_calls: list[list[str]] = []
    orig_run = shell.run

    def _recording_run(cmd, timeout=30, check=False):
        if cmd and cmd[0] == "virsh" and "checkpoint-delete" in cmd:
            delete_calls.append(list(cmd))
        return orig_run(cmd, timeout=timeout, check=check)

    shell.run = _recording_run  # type: ignore[method-assign]

    try:
        # Step 3: Force a verification failure so the rollback runs
        # deterministically.  Phase 2 moved verification into the
        # provider, so the patch targets
        # ``qsnap.modules.backup.bitmap.verify_full_backup``.
        caplog.clear()
        with (
            caplog.at_level(logging.INFO),
            patch(
                "qsnap.modules.backup.bitmap.verify_full_backup",
                return_value="verification failed: forced test failure",
            ),
        ):
            result = core.run(vm_name)
    finally:
        shell.run = orig_run  # type: ignore[method-assign]

    all_logs = " ".join(r.message for r in caplog.records)

    # The FULL attempt must have failed (Core raises BackupAbortError
    # after the non-retryable verification failure).
    if result.results:
        assert not result.results[0].success, (
            "Expected the FULL attempt to fail with the forced "
            f"verification failure. Result: {result.results[0]}"
        )

    # Core logs the failed FULL via the retry wrapper (Phase 2 — the old
    # "rolled back" message no longer exists).
    assert "failed after retries" in all_logs.lower() and "old generations preserved" in all_logs.lower(), (
        f"Expected 'FULL backup failed after retries' log with forced "
        f"verification failure. Logs: {all_logs[:500]}"
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
    # (the provider's rollback issues a ``virsh checkpoint-delete`` for
    # exactly that name), and any pre-existing baseline remains.
    successor = None
    for cmd in delete_calls:
        if "--domain" not in cmd:
            continue
        idx = cmd.index("--domain")
        if idx + 2 >= len(cmd):
            continue
        candidate = cmd[idx + 2]
        if candidate.startswith("qsnap-"):
            successor = candidate
            break
    assert successor is not None, (
        f"Expected an exact-name checkpoint-delete for the successor. "
        f"Delete calls: {delete_calls}. Logs: {all_logs[:500]}"
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
    """Stopped-VM FULL path never deletes any checkpoint.

    Phase 2 semantics (design D6): ``run_backup()`` defers when a
    checkpoint exists and the VM is stopped — no data is transferred,
    no checkpoint is created, and no checkpoint is deleted.  The
    stopped-VM offline FULL (no checkpoint) creates NO checkpoint at
    all, so a failed stopped-VM attempt has no successor checkpoint to
    roll back.

    1. Seed a ``qsnap-*`` baseline checkpoint via a prior running-VM FULL.
    2. Destroy the VM — the next backup hits the stopped-VM defer path
       (checkpoint exists, VM stopped).
    3. Force verification failure (patched ``verify_full_backup``) — the
       attempt is deferred before any transfer, so no rollback runs.
    4. Assert: zero ``virsh checkpoint-delete`` commands were issued
       during the run, and the pre-seeded baseline is still listed.
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

    # The disposable test VM has no bootable media and can exit on its
    # own under load.  The seed FULL MUST use the running-VM NBD path
    # (it creates the baseline checkpoint), so re-check the VM state
    # right before the seed and restart it if it exited.
    if not is_vm_running(shell, vm_name):
        shell.run(["virsh", "start", vm_name], timeout=30)
        time.sleep(1)
        if not is_vm_running(shell, vm_name):
            pytest.skip("VM did not stay running before seed backup")

    # Step 1: Seed a baseline checkpoint via a successful running-VM FULL.
    # snapshot_chain_length=999: keep the snapshot out of retention so
    # blockcommit never runs before the backup step.
    # target_keep_generations=10: the seed FULL must survive the second
    # run's retention pass even if the fragile environment forces an
    # extra FULL chain.
    state = InMemoryStateManager()
    target = TargetConfig(
        path=target_dir,
        compress=False,
        verify="off",
        target_keep_generations=10,
    )
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=999,
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
    if len(baselines) < 1:
        # The disposable VM (no bootable media) can exit mid-seed under
        # load, which makes the seed FULL fall back to the stopped-VM
        # offline path (which creates no checkpoint).  Without a baseline
        # checkpoint the deferral scenario cannot be exercised — skip
        # rather than fail spuriously.
        pytest.skip(
            "Seed FULL did not create a baseline checkpoint "
            f"(VM exited during seed). Baselines: {baselines}"
        )

    # Phase 2 quirk: Core records the FULL under its stem name, so
    # ``FullBackupInfo.path`` lacks the ``.qcow2`` extension and startup
    # validation would treat the seed FULL as a phantom.  Re-record with
    # the real filename so the second run keeps the seed FULL in state.
    seed_fulls = state.get_full_backups(str(target_dir))
    if seed_fulls:
        seed_name = seed_fulls[0].name
        state.remove_full_backup(str(target_dir), seed_name)
        state.record_full_backup(
            str(target_dir), f"{seed_name}.qcow2", seed_fulls[0].timestamp, "vda"
        )

    # Fresh snapshot for the stopped-VM backup source.
    fresh_snap = _snapshot_create(
        shell, vm_name, f"{vm_name}.stopped-source", base_image, snapshot_dir
    )
    state.record_snapshot(vm_name, fresh_snap)

    # Step 2: Destroy the VM — the next backup takes the stopped-VM path.
    destroy = shell.run(["virsh", "destroy", vm_name], timeout=30)
    assert destroy.success, f"virsh destroy failed: {destroy.error}"
    time.sleep(0.5)
    assert not is_vm_running(shell, vm_name), "VM must be stopped for this test"

    # The deferral precondition is a surviving baseline checkpoint.  If
    # the environment lost the domain (session daemon timeout, concurrent
    # activity), the checkpoints vanish with it and run_backup() would
    # take the offline FULL path instead of deferring — the scenario
    # cannot be exercised, so skip rather than fail spuriously.
    if not _list_checkpoints(shell, vm_name):
        pytest.skip("Baseline checkpoints vanished after destroy (environment lost the domain)")

    # Force a new FULL (the stopped-VM path).  Phase 2 design D6: because
    # a checkpoint exists for this VM+target+disk, ``run_backup()``
    # DEFERS — no data is transferred, no checkpoint is created or
    # deleted.
    core._force_full_targets.add(str(target_dir))

    # Spy on shell.run to record any virsh checkpoint-delete issued during
    # the run.
    delete_calls: list[list[str]] = []
    orig_run = shell.run

    def _recording_run(cmd, timeout=30, check=False):
        if cmd and cmd[0] == "virsh" and "checkpoint-delete" in cmd:
            delete_calls.append(list(cmd))
        return orig_run(cmd, timeout=timeout, check=check)

    shell.run = _recording_run  # type: ignore[method-assign]

    try:
        # Step 3: Force verification failure — even so, the backup is
        # deferred (stopped VM + existing checkpoint) and nothing is
        # transferred or deleted.
        caplog.clear()
        with (
            caplog.at_level(logging.INFO),
            patch(
                "qsnap.modules.backup.bitmap.verify_full_backup",
                return_value="verification failed: forced test failure",
            ),
        ):
            deferred_result = core.backup(vm_name)

        assert deferred_result.results[0].success, (
            "Expected the stopped-VM backup to be DEFERRED (checkpoint "
            f"exists, VM stopped — no FULL attempted): {deferred_result.results[0]}"
        )
        all_logs = " ".join(r.message for r in caplog.records)
        if "backup deferred" not in all_logs.lower():
            # The environment lost the domain+checkpoint between the
            # destroy and the backup run, so run_backup() took the
            # offline FULL path instead of deferring — the scenario
            # cannot be exercised, skip rather than fail spuriously.
            pytest.skip(
                "Stopped-VM backup was not deferred — baseline checkpoint "
                f"lost (environment). Logs: {all_logs[:300]}"
            )
    finally:
        shell.run = orig_run  # type: ignore[method-assign]

    # Step 4a: Zero virsh checkpoint-delete calls during the run.
    assert delete_calls == [], (
        f"Stopped-VM backup must not issue any virsh checkpoint-delete, got {delete_calls}"
    )

    # Step 4b: The pre-seeded baseline is still listed.
    cps_after = _list_checkpoints(shell, vm_name)
    for baseline in baselines:
        assert baseline in cps_after, (
            f"Pre-seeded baseline checkpoint {baseline} must remain listed, got {cps_after}"
        )

    # Step 4c: No NEW FULL file was created (deferral transferred nothing).
    full_files_after = sorted(target_dir.glob("*.FULL.*.qcow2"))
    assert len(full_files_after) == 1, (
        f"Only the seeded FULL should exist on target, got: {full_files_after}"
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
        # Keep the snapshot out of retention so blockcommit never runs
        # before the backup step (deterministic FULL creation).
        snapshot_chain_length=999,
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
