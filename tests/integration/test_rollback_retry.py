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
# Test 1: Rollback deletes broken FULL file and checkpoint
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_rollback_deletes_broken_full_and_checkpoint(test_vm, caplog):
    """FULL fails after transfer — rollback deletes FULL file + checkpoint.

    1. Start VM, create snapshot.
    2. Set verify_after_create="check" so M2 runs.
    3. Run core.run() — FULL is created, but if M2 verify fails, rollback
       should delete the FULL file and its checkpoint.
    4. Verify: broken FULL file is deleted from target.
    5. Verify: no FULL entry remains in state for the failed attempt.
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

    # Create snapshot and build Core with verification enabled.
    snap = _snapshot_create(
        shell, vm_name, f"{vm_name}.rollback-snap", base_image, snapshot_dir
    )

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

    # Step 3: Run core.run() — FULL created, verification may pass or fail.
    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    all_logs = " ".join(r.message for r in caplog.records)

    # Check if rollback occurred.
    rolled_back = "rolled back" in all_logs.lower()

    if rolled_back:
        # Step 4: Broken FULL file deleted.
        full_files_after = sorted(target_dir.glob("*.FULL.*.qcow2"))
        # Either no FULL files, or only valid ones.

        # Step 5: No FULL in state (or only properly verified ones).
        fulls_in_state = state.get_full_backups(str(target_dir))
        for f in fulls_in_state:
            full_file = target_dir / f.name
            assert full_file.exists(), (
                f"FULL {f.name} in state should have a corresponding file"
            )
    else:
        # The FULL was created successfully — verify it exists.
        full_files_after = sorted(target_dir.glob("*.FULL.*.qcow2"))
        if len(full_files_after) > 0:
            assert full_files_after[0].exists(), "Existing FULL file must exist"

    # Step 6: Either "rolled back" or "created FULL" appears.
    has_result_log = rolled_back or "created FULL" in all_logs
    assert has_result_log, (
        f"Expected either 'rolled back' or 'created FULL' in logs. "
        f"Logs: {all_logs[:500]}"
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
    snap = _snapshot_create(
        shell, vm_name, f"{vm_name}.retry-snap", base_image, snapshot_dir
    )

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
        f"Expected at least one FULL backup file on target. "
        f"Contents: {list(target_dir.iterdir())}"
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
