"""Integration tests for ``target_chain_length=None`` — no FULL triggered by count.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

The count-based FULL decision (design D2) uses::

    chain_length = target.target_chain_length
    should_full = chain_length is not None and incremental_count > chain_length

When ``target_chain_length=None``, ``should_full`` MUST be ``False`` regardless
of how many incrementals exist.  The ``or 0`` bug (which would coerce
``None`` to ``0``, triggering a FULL immediately) is avoided by the
``is not None`` guard.  These tests verify both the negative case
(``None`` → no FULL) and the positive case (``chain_length=3`` → FULL
triggered after 4 incrementals).

Run only when explicitly requested::

    poetry run pytest tests/integration/test_target_chain_length_none.py -v -m integration
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

pytestmark = pytest.mark.integration


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


def _build_core(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    target_dir: Path,
    *,
    target_chain_length: int | None = None,
) -> tuple[Core, VMConfig, InMemoryStateManager]:
    """Build a Core instance with configurable ``target_chain_length``."""
    state = InMemoryStateManager()
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=99,
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
                target_chain_length=target_chain_length,
                # Keep 2 FULL generations so a newly created FULL (and
                # its state record) is still observable after the backup
                # retention/cleanup pass of the same run.
                target_keep_generations=2,
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(state_dir="/var/tmp"),
        vms=[vm_config],
        config_path=target_dir / "test_target_chain_length_none.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config, state


# ──────────────────────────────────────────────────────────────────────
# Test 1: None chain_length — NO FULL created regardless of incremental count
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.timeout(3600)
def test_target_chain_length_none_no_full(test_vm, caplog):
    """Create many incrementals with ``target_chain_length=None``, verify NO FULL.

    1. Start VM, set ``target_chain_length=None``.
    2. Create a FULL backup (first backup to target — always FULL).
    3. Record 8 incrementals as deps on the FULL.
    4. Run ``core.run()`` — verify NO new FULL is created
       (``chain_length is not None`` guards the count check).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

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

    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        target_chain_length=None,  # No count-based FULL trigger
    )

    target = vm_config.targets[0]

    # Step 1: Create a FULL backup (first backup to target).  ``run_backup``
    # decides the kind autonomously — no checkpoint exists yet → FULL —
    # and records the state anchor manually below (Core's job in real runs).
    provider = BitmapBackupProvider(shell)
    disk = vm_config.disks[0]
    full_result = provider.run_backup(
        vm_config,
        target,
        disk,
        stall_timeout=300,
    )
    if not full_result.success:
        pytest.skip(f"FULL backup failed: {full_result.error}")

    full_path = full_result.target_path
    full_name = full_path.stem
    state.record_full_backup(str(target_dir), f"{full_name}.qcow2", datetime.now(), disk="vda")

    # Step 2: Record 8 incrementals — well beyond any typical chain_length.
    for i in range(8):
        incr_name = f"{vm_name}.tcl-none-incr{i}"
        state.record_incremental_dependency(str(target_dir), incr_name, full_name)

    deps = state.get_incremental_dependencies(str(target_dir), full_name)
    assert len(deps) == 8, f"Expected 8 deps, got {len(deps)}"

    # Step 3: Create a new snapshot and run core.run().
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.tcl-none-snap", base_image, snapshot_dir)
    state.record_snapshot(vm_name, snap)

    fulls_before = state.get_full_backups(str(target_dir))
    num_fulls_before = len(fulls_before)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    # Step 4: No new FULL should have been created.
    fulls_after = state.get_full_backups(str(target_dir))
    assert len(fulls_after) == num_fulls_before, (
        f"With target_chain_length=None, expected no new FULLs "
        f"(8 incrementals, None chain_length), "
        f"but got before={num_fulls_before}, after={len(fulls_after)}"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: chain_length=3 — FULL IS triggered when incrementals exceed 3
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.timeout(3600)
def test_target_chain_length_three_triggers_full(test_vm, caplog):
    """Create 4 incrementals with ``target_chain_length=3``, verify FULL is created.

    1. Start VM, set ``target_chain_length=3``.
    2. Create a FULL backup (first backup to target).
    3. Record 4 incrementals as deps on the FULL (4 > 3 → True).
    4. Run ``core.run()`` — verify a new FULL IS created.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

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

    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        target_chain_length=3,
    )

    target = vm_config.targets[0]

    # Step 1: Create a FULL backup (first backup to target).
    provider = BitmapBackupProvider(shell)
    disk = vm_config.disks[0]
    full_result = provider.run_backup(
        vm_config,
        target,
        disk,
        stall_timeout=300,
    )
    if not full_result.success:
        pytest.skip(f"FULL backup failed: {full_result.error}")

    full_path = full_result.target_path
    full_name = full_path.stem
    state.record_full_backup(str(target_dir), f"{full_name}.qcow2", datetime.now(), disk="vda")

    # Step 2: Record 4 incrementals (exceeds chain_length=3).
    for i in range(4):
        incr_name = f"{vm_name}.tcl-three-incr{i}"
        state.record_incremental_dependency(str(target_dir), incr_name, full_name)

    deps = state.get_incremental_dependencies(str(target_dir), full_name)
    assert len(deps) == 4, f"Expected 4 deps, got {len(deps)}"

    # Step 3: Create snapshot and run core.run().
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.tcl-three-snap", base_image, snapshot_dir)
    state.record_snapshot(vm_name, snap)

    fulls_before = state.get_full_backups(str(target_dir))
    num_fulls_before = len(fulls_before)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    # Step 4: A new FULL should have been created (4 > 3).
    fulls_after = state.get_full_backups(str(target_dir))
    assert len(fulls_after) > num_fulls_before, (
        f"With target_chain_length=3 and 4 incrementals, expected a new FULL, "
        f"but got before={num_fulls_before}, after={len(fulls_after)}"
    )

    # Check for FULL creation log.
    created_logs = [
        r.message for r in caplog.records if "created FULL" in r.message and vm_name in r.message
    ]
    assert len(created_logs) >= 1, (
        f"Expected 'created FULL' in logs. "
        f"Logs: {[r.message for r in caplog.records if 'FULL' in r.message]}"
    )

    _cleanup_checkpoints(shell, vm_name)
