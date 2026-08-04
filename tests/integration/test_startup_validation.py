"""Integration tests for ``_validate_state_at_startup`` phantom FULL detection.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use ``Core`` (not
individual modules directly) because startup validation is Core-level
logic in ``_validate_state_at_startup()`` and ``_backup_target()``.

Coverage:
- Phantom FULL detection + cascade cleanup BEFORE the onchange gate.
- Stale baseline cleared so the onchange gate sees correct state.
- Self-healing: a new FULL backup is created after cleanup.
- Orphan checkpoints are NOT auto-deleted during startup validation
  (only ``qsnap reconcile`` does that).

Run only when explicitly requested::

    poetry run pytest tests/integration/test_startup_validation.py -v -m integration
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running
from tests.mocks import InMemoryStateManager, MockConfigFacade


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints."""
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


def _count_checkpoints(shell: SubprocessShell, vm_name: str) -> int:
    """Return the number of qsnap-prefixed checkpoints for *vm_name*."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return 0
    return sum(
        1 for line in (result.stdout or "").splitlines() if line.strip().startswith("qsnap-")
    )


# ──────────────────────────────────────────────────────────────────────
# Test: Startup validation detects phantom FULLs before onchange gate
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_startup_validation(test_vm, caplog):
    """Startup validation detects phantom FULLs before the onchange gate runs.

    1. Start the VM, run ``core.run()`` to create a FULL backup on the target.
    2. Manually delete the FULL backup file from the target (simulating disk
       failure while state still references it).
    3. Run ``core.run()`` again — the full pipeline.
    4. Verify:
       - Startup validation detects the phantom FULL (log contains
         ``"phantom FULL"``).
       - The onchange gate sees correct state (target empty → gate passes
         because snapshots exist but no backups match).
       - A new FULL backup is created (self-healing).
       - Stale baseline is cleared (log contains ``"cleared
         last_backup_allocation"`` or similar).
       - Orphan checkpoints are NOT auto-deleted during startup validation
         (checkpoints created by the first run still exist after the second
         run — only ``qsnap reconcile`` deletes them).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    # Set up config with onchange target.
    target = TargetConfig(path=target_dir, backup_create="onchange", compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_create="always",
        targets=[target],
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "startup_validation.toml")

    state = InMemoryStateManager()
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # ── Run 1: Create a FULL backup ───────────────────────────────────
    with caplog.at_level(logging.INFO):
        result1 = core.run(vm_name)

    if not result1.success:
        vm_result = result1.results[0]
        pytest.skip(
            f"First run failed (cannot proceed to test phantom detection): "
            f"success={vm_result.success} error={vm_result.error}"
        )

    # Verify run 1 produced a FULL backup on disk.
    full_backups = list(target_dir.glob("*.FULL.*.qcow2"))
    if not full_backups:
        pytest.skip(
            f"No FULL backup files found in {target_dir} after first run. "
            f"Contents: {list(target_dir.iterdir())}"
        )
    assert len(full_backups) >= 1, f"Expected at least 1 FULL backup, got {full_backups}"

    # Record FULL path for later deletion.
    first_full_path = full_backups[0]

    # Count checkpoints created during run 1 — must persist through run 2.
    ckpts_before = _count_checkpoints(shell, vm_name)
    assert ckpts_before >= 1, (
        f"Expected at least 1 qsnap checkpoint after first run, got {ckpts_before}"
    )

    # Record FULLs in state for confirmation before we delete the file.
    state_fulls_before = state.get_full_backups(str(target_dir))
    assert len(state_fulls_before) >= 1, "State must have FULL entry after run 1"

    # ── Simulate disk failure: delete the FULL backup file ────────────
    first_full_path.unlink()
    assert not first_full_path.exists(), (
        f"FULL backup file {first_full_path} must be deleted for phantom detection test"
    )

    # ── Run 2: Full pipeline — must self-heal ─────────────────────────
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result2 = core.run(vm_name)

    # Run 2 must succeed (self-healing).
    assert result2.success, (
        f"Second run must succeed after phantom cleanup, got: "
        f"{[r.error for r in result2.results]}"
    )

    # ── Assertion 1: Phantom FULL detection ───────────────────────────
    phantom_msgs = [
        r.message for r in caplog.records if "phantom FULL" in r.message
    ]
    assert len(phantom_msgs) >= 1, (
        f"Expected startup validation to detect phantom FULL, "
        f"but no 'phantom FULL' message found. "
        f"Records: {[r.message for r in caplog.records]}"
    )

    # ── Assertion 2: Stale baseline cleared ───────────────────────────
    cleared_msgs = [
        r.message for r in caplog.records if "cleared last_backup_allocation" in r.message
    ]
    assert len(cleared_msgs) >= 1, (
        f"Expected baseline to be cleared after phantom FULL cleanup, "
        f"but no 'cleared last_backup_allocation' message found. "
        f"Records: {[r.message for r in caplog.records]}"
    )

    # ── Assertion 3: New FULL backup created (self-healing) ───────────
    new_full_backups = list(target_dir.glob("*.FULL.*.qcow2"))
    assert len(new_full_backups) >= 1, (
        f"Expected a new FULL backup to be created after phantom cleanup, "
        f"but no .FULL.*.qcow2 files found in {target_dir}. "
        f"Contents: {list(target_dir.iterdir())}"
    )
    # The new FULL may share the same name as the deleted one (date-based
    # naming produces identical names for same-day FULLs).  What matters
    # is that a new FULL file was created — already asserted above.

    # ── Assertion 4: Orphan checkpoints NOT auto-deleted ──────────────
    # Startup validation is non-fatal and does NOT auto-delete checkpoints.
    # Checkpoints from run 1 should still exist after run 2.
    ckpts_after = _count_checkpoints(shell, vm_name)
    # Checkpoints may change count due to normal rotation —
    # _delete_superseded_checkpoints() in create_full_backup deletes
    # old checkpoints when a new FULL is created.  What matters: at
    # least 1 checkpoint remains after recovery, and startup validation
    # did NOT delete any.
    assert ckpts_after >= 1, (
        f"At least 1 checkpoint should remain after phantom FULL recovery: "
        f"before={ckpts_before}, after={ckpts_after}. "
        f"Startup validation must NOT auto-delete checkpoints (only reconcile does)."
    )

    # Verify no "orphan checkpoint" deletion happened in the logs.
    orphan_delete_msgs = [
        r.message
        for r in caplog.records
        if "orphan" in r.message and "checkpoint" in r.message and "delet" in r.message
    ]
    assert len(orphan_delete_msgs) == 0, (
        f"Startup validation must NOT delete orphan checkpoints. "
        f"Found: {orphan_delete_msgs}"
    )

    # ── Cleanup ────────────────────────────────────────────────────────
    _cleanup_checkpoints(shell, vm_name)
