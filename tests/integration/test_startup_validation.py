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
from collections.abc import Callable
from pathlib import Path

import pytest

try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.interfaces.shell import IShell
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import ShellResult
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running
from qsnap.utils.nbd_client import LibnbdClient
from tests.mocks import InMemoryStateManager, MockConfigFacade


class _RecordingShell(IShell):
    """IShell wrapper that delegates to SubprocessShell and records commands.

    Used to assert that a covered checkpoint's dirty bitmap is probed
    (read-only ``virsh qemu-monitor-command``) during startup validation
    (recover-lost-checkpoint-bitmaps, design D12).
    """

    def __init__(self, delegate: SubprocessShell) -> None:
        self._delegate = delegate
        self._commands: list[list[str]] = []

    @property
    def commands(self) -> list[list[str]]:
        """The recorded command lists, in execution order."""
        return list(self._commands)

    def run(self, cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        self._commands.append(list(cmd))
        return self._delegate.run(cmd, timeout, check)

    def run_with_stall_detection(
        self,
        cmd: list[str],
        output_file: Path | None = None,
        stall_timeout: int = 1800,
        check: bool = False,
    ) -> ShellResult:
        self._commands.append(list(cmd))
        return self._delegate.run_with_stall_detection(cmd, output_file, stall_timeout, check)

    def run_with_heartbeat(
        self,
        cmd: list[str],
        timeout: int,
        heartbeat_seconds: int,
        on_heartbeat: Callable[[int], None],
        check: bool = False,
    ) -> ShellResult:
        self._commands.append(list(cmd))
        return self._delegate.run_with_heartbeat(
            cmd, timeout, heartbeat_seconds, on_heartbeat, check
        )


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


def _checkpoint_names(shell: SubprocessShell, vm_name: str) -> list[str]:
    """Return the qsnap-prefixed checkpoint names of *vm_name*."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return []
    return [
        line.strip()
        for line in (result.stdout or "").splitlines()
        if line.strip().startswith("qsnap-")
    ]


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

    # ── D12: a covered checkpoint is probed at startup and kept on HEALTHY ──
    # The checkpoint from run 1 is covered by the FULL backup file; startup
    # validation must probe its dirty bitmap (read-only QMP) and KEEP the
    # checkpoint when the bitmap is healthy (keep-on-HEALTHY).
    rec_shell = _RecordingShell(shell)
    core_probe = Core(
        config=config,
        factory=DefaultFactory(shell=rec_shell, state=state),
        state=state,
        shell=rec_shell,
    )
    with caplog.at_level(logging.INFO):
        core_probe._validate_state_at_startup(vm_config)
    qmp_probes = [" ".join(cmd) for cmd in rec_shell.commands if "qemu-monitor-command" in cmd]
    assert len(qmp_probes) >= 1, (
        f"Covered checkpoint must be probed at startup (qemu-monitor-command). "
        f"Recorded commands: {[' '.join(c) for c in rec_shell.commands]}"
    )
    assert _count_checkpoints(shell, vm_name) == ckpts_before, (
        f"Healthy covered checkpoint must be KEPT at startup (keep-on-HEALTHY): "
        f"before={ckpts_before}, after={_count_checkpoints(shell, vm_name)}"
    )
    assert not any("dead-bitmap checkpoint" in r.message for r in caplog.records), (
        f"Healthy bitmap must not be flagged dead. Logs: {[r.message for r in caplog.records]}"
    )

    # Record FULLs in state for confirmation before we delete the file.
    state_fulls_before = state.get_full_backups(str(target_dir))
    assert len(state_fulls_before) >= 1, "State must have FULL entry after run 1"

    # The recorded FULL entry must carry the .qcow2 extension and its
    # path must resolve to the physical file on disk
    # (fix-full-backup-state-extension).
    assert state_fulls_before[0].name.endswith(".qcow2"), (
        f"FULL state entry must carry .qcow2 extension, got {state_fulls_before[0].name!r}"
    )
    assert state_fulls_before[0].path.exists(), (
        f"FULL state entry path must exist on disk: {state_fulls_before[0].path}"
    )

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
        f"Second run must succeed after phantom cleanup, got: {[r.error for r in result2.results]}"
    )

    # ── Assertion 1: Phantom FULL detection ───────────────────────────
    phantom_msgs = [r.message for r in caplog.records if "phantom FULL" in r.message]
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
        f"Startup validation must NOT delete orphan checkpoints. Found: {orphan_delete_msgs}"
    )

    # ── Cleanup ────────────────────────────────────────────────────────
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test: Covered checkpoint with a DEAD bitmap is removed at startup (D12)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_startup_dead_bitmap_covered_checkpoint_removed(test_vm, caplog):
    """A covered checkpoint with a DEAD bitmap is removed at startup.

    recover-lost-checkpoint-bitmaps design D12: when a covering backup
    file exists but the checkpoint's dirty bitmap is dead (lost after an
    unclean host shutdown), startup validation treats the checkpoint as
    an orphan and deletes it best-effort (delete-on-DEAD).  The covering
    FULL backup file itself must be preserved.

    1. Start VM; run a FULL backup (checkpoint + bitmap + covering file).
    2. Manufacture a REAL dead bitmap (mechanism c, test-plan §5.2):
       ``virsh destroy``, ``qemu-img bitmap --remove <active-layer>
       <checkpoint>``, ``virsh start``.
    3. Run ``core._validate_state_at_startup()``.
    4. Verify: "dead-bitmap checkpoint detected" WARNING; checkpoint
       removed; covering FULL file still present.
    """
    from datetime import datetime

    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)

    # Step 1: FULL backup → checkpoint + bitmap + covering file.
    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    full_result = provider.run_backup(vm_config, target, vm_config.disks[0])
    if not full_result.success:
        pytest.skip(f"FULL backup failed: {full_result.error}")
    cp_name = full_result.checkpoint
    assert cp_name is not None, "FULL backup must create a checkpoint"
    covering_file = full_result.target_path
    assert covering_file.exists(), "Covering FULL file must exist"

    state = InMemoryStateManager()
    state.record_full_backup(
        str(target_dir),
        f"{full_result.snapshot_name}.qcow2",
        datetime.now(),
        disk="vda",
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "startup_dead.toml")
    core = Core(
        config=config,
        factory=DefaultFactory(shell=shell, state=state),
        state=state,
        shell=shell,
    )

    # Step 2: Manufacture a REAL dead bitmap (mechanism c).
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(1)
    shell.run(
        ["qemu-img", "bitmap", "--remove", str(base_image), cp_name],
        timeout=60,
    )
    restart = shell.run(["virsh", "start", vm_name], timeout=60)
    if not restart.success:
        pytest.skip("VM did not restart after dead-bitmap manufacture")
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state after restart")

    # The checkpoint metadata must survive the restart (mechanism c
    # precondition); otherwise the delete-on-DEAD path is untestable.
    assert cp_name in _checkpoint_names(shell, vm_name), (
        f"Checkpoint {cp_name!r} must survive the VM restart: {_checkpoint_names(shell, vm_name)}"
    )

    # Self-validate the incident state: the probe must report DEAD.
    probe = provider._probe_checkpoint_bitmap(vm_name, cp_name, "vda", True, base_image)
    if probe != "dead":
        pytest.skip(
            f"Mechanism (c) did not produce a DEAD probe on this libvirt "
            f"(got {probe!r}) — bitmap survived the restart"
        )

    # Step 3: Startup validation — delete-on-DEAD.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        core._validate_state_at_startup(vm_config)

    # Step 4: dead-bitmap WARNING + checkpoint removed + file preserved.
    dead_logs = [r.message for r in caplog.records if "dead-bitmap checkpoint" in r.message]
    assert len(dead_logs) >= 1, (
        f"Startup must log the dead-bitmap WARNING. Logs: {[r.message for r in caplog.records]}"
    )
    assert cp_name not in _checkpoint_names(shell, vm_name), (
        f"Dead-bitmap covered checkpoint {cp_name!r} must be removed at startup "
        f"(delete-on-DEAD). Got: {_checkpoint_names(shell, vm_name)}"
    )
    assert covering_file.exists(), f"Covering FULL backup file must be preserved: {covering_file}"

    _cleanup_checkpoints(shell, vm_name)
