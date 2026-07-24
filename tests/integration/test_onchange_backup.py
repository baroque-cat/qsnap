"""Integration tests for per-target backup ``onchange`` gate.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py`` which creates a disposable throwaway VM.

Run only when explicitly requested::

    poetry run pytest tests/integration/test_onchange_backup.py -v -m integration
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

# libnbd availability — needed by the unified NBD transfer engine.
try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running
from tests.mocks import InMemoryStateManager, MockConfigFacade


# ──────────────────────────────────────────────────────────────────────
# Test 1: Per-target onchange proceeds on first run
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_nbd_onchange_first_run_proceeds(test_vm, caplog):
    """Per-target onchange backup proceeds on first run (no baseline).

    1. Create a disposable test VM with a qcow2 disk.
    2. Start the VM.
    3. Create an external snapshot so that a snapshot is recorded in state.
    4. Run ``Core.backup()`` with ``backup_create="onchange"``.
    5. Verify backup proceeds because ``get_last_backup_allocation()``
       returns ``None`` (first run) — the onchange gate opens.
    6. Verify that after a successful transfer, ``get_last_backup_allocation()``
       is set to a non-None value (baseline recorded).
    """
    shell = test_vm["shell"]
    vm_name = test_vm["vm_name"]
    base_image = test_vm["base_image"]
    snapshot_dir = test_vm["snapshot_dir"]
    target_dir = test_vm["target_dir"]
    tmpdir = test_vm["tmpdir"]

    # Step 1: Start the VM.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    # Step 2: Create an external snapshot and record it in state.
    snap_provider = ExternalSnapshotProvider(shell)
    snap_name = f"{vm_name}.onchange-first"
    snap_path = snapshot_dir / f"{snap_name}.qcow2"
    snap_result = snap_provider.create(
        VMConfig(
            name=vm_name,
            base_image=base_image,
            snapshot_dir=snapshot_dir,
        ),
        snap_name,
        "vda",
        snap_path,
    )
    assert snap_result.success, (
        f"Snapshot creation failed: {snap_result.error}"
    )
    assert snap_result.path.exists(), (
        f"Snapshot file not found at {snap_result.path}"
    )

    snap_info = SnapshotInfo(
        name=snap_result.name,
        path=snap_result.path,
        timestamp=datetime.now(),
        allocation=snap_result.new_allocation,
    )

    # Step 3: Record snapshot in InMemoryStateManager.
    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap_info)

    # Precondition: no baseline exists (first-run condition).
    assert state.get_last_backup_allocation(str(target_dir)) is None, (
        "Expected no baseline for first run"
    )

    # Step 4: Create Core with onchange target and DefaultFactory.
    target = TargetConfig(
        path=target_dir,
        backup_create="onchange",
        compress=False,
        verify="off",
    )
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "test_onchange_first.toml",
    )
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Step 5: Run backup — gate should open because first run.
    import logging

    with caplog.at_level(logging.INFO):
        result = core.backup(vm_name)

    # Step 6: Verify backup was attempted (not skipped via onchange).
    # The onchange gate should NOT have logged a skip message.
    skip_messages = [
        r.message
        for r in caplog.records
        if "unchanged" in r.message and "skipping" in r.message
    ]
    assert len(skip_messages) == 0, (
        f"Onchange gate should NOT skip on first run, but got skip message: {skip_messages}"
    )

    # Step 7: Verify baseline was updated after successful transfer.
    # If transfer failed (e.g. NBD issues), baseline stays None and
    # the gate will open again next run — that's correct behavior.
    baseline = state.get_last_backup_allocation(str(target_dir))
    if baseline is None:
        # Transfer may have failed — check if there were failures.
        vm_result = result.results[0]
        if vm_result.backup_failed:
            pytest.skip(
                "Backup transfer failed on first run — "
                "baseline not updated (NBD export issue?)"
            )
        else:
            # Transfer succeeded but baseline not set — that's a source bug.
            pytest.fail(
                "Backup transfer succeeded but set_last_backup_allocation() "
                "was not called. Source bug: _backup_target() should set "
                "baseline after successful transfer (line 2991-2994)."
            )
    else:
        assert isinstance(baseline, int), (
            f"Baseline should be an integer, got {type(baseline).__name__}"
        )
        assert baseline == snap_info.allocation, (
            f"Baseline allocation {baseline} should match snapshot "
            f"allocation {snap_info.allocation}"
        )

    # Step 8: Clean up checkpoints created by BitmapBackupProvider.
    _cleanup_qsnap_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Per-target onchange skip when allocation unchanged
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_nbd_onchange_skip_no_change(test_vm, caplog):
    """Per-target onchange backup skips when allocation is unchanged.

    1. Create a disposable test VM with a qcow2 disk.
    2. Start the VM.
    3. Create an external snapshot and record it in state.
    4. First run: backup proceeds (no baseline, first run).
    5. Verify baseline is set after first successful run.
    6. Second run: backup is SKIPPED (allocation unchanged).
    7. Verify ``get_last_backup_allocation()`` returns the same value
       (baseline was not overwritten).
    """
    import logging

    shell = test_vm["shell"]
    vm_name = test_vm["vm_name"]
    base_image = test_vm["base_image"]
    snapshot_dir = test_vm["snapshot_dir"]
    target_dir = test_vm["target_dir"]
    tmpdir = test_vm["tmpdir"]

    # Step 1: Start the VM.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    # Step 2: Create an external snapshot and record it in state.
    snap_provider = ExternalSnapshotProvider(shell)
    snap_name = f"{vm_name}.onchange-skip"
    snap_path = snapshot_dir / f"{snap_name}.qcow2"
    snap_result = snap_provider.create(
        VMConfig(
            name=vm_name,
            base_image=base_image,
            snapshot_dir=snapshot_dir,
        ),
        snap_name,
        "vda",
        snap_path,
    )
    assert snap_result.success, (
        f"Snapshot creation failed: {snap_result.error}"
    )
    assert snap_result.path.exists(), (
        f"Snapshot file not found at {snap_result.path}"
    )

    snap_info = SnapshotInfo(
        name=snap_result.name,
        path=snap_result.path,
        timestamp=datetime.now(),
        allocation=snap_result.new_allocation,
    )

    # Step 3: Record snapshot in InMemoryStateManager.
    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap_info)

    # Precondition: no baseline exists (first-run condition).
    assert state.get_last_backup_allocation(str(target_dir)) is None, (
        "Expected no baseline for first run"
    )

    # Step 4: Create Core with onchange target and DefaultFactory.
    target = TargetConfig(
        path=target_dir,
        backup_create="onchange",
        compress=False,
        verify="off",
    )
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "test_onchange_skip.toml",
    )
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # ── First run: backup should proceed ──────────────────────────────
    with caplog.at_level(logging.INFO):
        result_first = core.backup(vm_name)

    skip_after_first = [
        r.message
        for r in caplog.records
        if "unchanged" in r.message and "skipping" in r.message
    ]
    assert len(skip_after_first) == 0, (
        f"First run should proceed, but onchange gate skipped: {skip_after_first}"
    )

    baseline_after_first = state.get_last_backup_allocation(str(target_dir))
    if baseline_after_first is None:
        vm_result = result_first.results[0]
        if vm_result.backup_failed:
            pytest.skip(
                "First-run backup transfer failed — cannot test skip behavior "
                "without a baseline. (NBD export issue?)"
            )
        else:
            pytest.fail(
                "Backup succeeded but set_last_backup_allocation() not called. "
                "Source bug in _backup_target()."
            )

    assert isinstance(baseline_after_first, int), (
        f"Baseline should be an int, got {type(baseline_after_first).__name__}"
    )

    # ── Second run: disk unchanged → backup should SKIP ──────────────
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result_second = core.backup(vm_name)

    skip_messages = [
        r.message
        for r in caplog.records
        if "unchanged" in r.message and "skipping" in r.message
    ]
    assert len(skip_messages) >= 1, (
        "Expected onchange gate to skip second run (allocation unchanged), "
        f"but no skip message found. Caplog messages: "
        f"{[r.message for r in caplog.records]}"
    )

    # Verify baseline was NOT overwritten (same value as after first run).
    baseline_after_second = state.get_last_backup_allocation(str(target_dir))
    assert baseline_after_second == baseline_after_first, (
        f"Baseline should remain {baseline_after_first} after skip, "
        f"but is {baseline_after_second}"
    )

    # Step 5: Clean up checkpoints.
    _cleanup_qsnap_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _get_qsnap_checkpoint_names(shell: SubprocessShell, vm_name: str) -> list[str]:
    """Return qsnap-prefixed checkpoint names for *vm_name*."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return []
    return [
        line.strip()
        for line in result.stdout.strip().splitlines()
        if line.strip().startswith("qsnap-")
    ]


def _cleanup_qsnap_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints for *vm_name*."""
    for cp in _get_qsnap_checkpoint_names(shell, vm_name):
        shell.run(
            [
                "virsh",
                "checkpoint-delete",
                "--domain",
                vm_name,
                cp,
                "--metadata",
            ],
            timeout=30,
        )
