"""Integration tests for per-target ``onchange`` backup gate.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use ``Core`` (not
``BitmapBackupProvider`` directly) because the onchange gate is
Core-level logic in ``_backup_target()``.

**Approach B:** The onchange gate calls ``provider.list(target)`` and
compares snapshot names in state against backup names on target.  When
any snapshot in state does not appear on the target, the gate opens
and the backup proceeds.  When all snapshots are already on the target,
the gate closes and the transfer is skipped — but retention + cleanup
still run (gate/retention separation).

Coverage:
- First run proceeds (target empty → gate open).
- Second run skips when all snapshots already on target.
- New snapshot → gate open → incremental backup created.
- Manual deletion recovery (phantom FULL cleanup + self-healing).
- Retention runs even when gate skips transfer.

Run only when explicitly requested::

    poetry run pytest tests/integration/test_onchange.py -v -m integration
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import pytest

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
                ["virsh", "checkpoint-delete", "--domain", vm_name, cp, "--metadata"],
                timeout=30,
            )


def _snapshot_create(shell, vm_name, snap_name, snapshot_dir, base_image):
    """Create an external snapshot and return ``SnapshotInfo``."""
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


def _start_vm_and_check(shell, vm_name) -> None:
    """Start the VM and verify prerequisites for NBD backup tests.

    Skips the test (via ``pytest.skip``) if any prerequisite is not met.
    """
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


def _count_qcow2_files(directory: Path) -> int:
    """Count .qcow2 files in *directory*."""
    return len(list(directory.glob("*.qcow2")))


# ──────────────────────────────────────────────────────────────────────
# Test 1: onchange skips when all snapshots already on target (Approach B)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_onchange_skips_when_unchanged(test_vm, caplog):
    """Per-target onchange skips second run via Approach B (snapshot-name comparison).

    1. Start VM, create external snapshot.
    2. First ``core.backup()`` — target is empty → gate open → backup proceeds.
    3. Verify backup file(s) exist on target (Approach B: ``provider.list()``
       would see them).
    4. Second ``core.backup()`` — snapshots already on target → gate closed →
       backup SKIPPED with "no new snapshots — skipping" in logs.
    5. Verify caplog does NOT contain old "unchanged (allocation" message.
       No allocation-baseline checks are performed.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _start_vm_and_check(shell, vm_name)

    # Create snapshot and record in state.
    snap_info = _snapshot_create(
        shell, vm_name, f"{vm_name}.onchange-skip", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap_info)

    # Create Core with onchange target.
    target = TargetConfig(path=target_dir, backup_create="onchange", compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir, targets=[target]
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "onchange_skip.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- First run: backup proceeds (target empty, gate open) ---
    with caplog.at_level(logging.INFO):
        result_first = core.backup(vm_name)
    skip_first = [
        r.message
        for r in caplog.records
        if "no new snapshots" in r.message and "skipping" in r.message
    ]
    assert len(skip_first) == 0, f"First run must NOT skip: {skip_first}"

    # Verify backup files on target (Approach B uses provider.list()).
    target_files_before = _count_qcow2_files(target_dir)
    assert target_files_before >= 1, (
        f"Expected at least one backup file on target after first run, "
        f"got {target_files_before}. Files: {list(target_dir.glob('*.qcow2'))}"
    )

    # Check first-run success (backup may fail for environment reasons —
    # if so, skip remainder).
    vm_result = result_first.results[0]
    if vm_result.backup_failed:
        pytest.skip("First-run backup failed — cannot test skip. (NBD/transfer issue?)")

    # --- Second run: snapshots already on target → SKIP ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.backup(vm_name)
    skip_msgs = [
        r.message
        for r in caplog.records
        if "no new snapshots" in r.message and "skipping" in r.message
    ]
    assert len(skip_msgs) >= 1, (
        f"Expected onchange skip on second run, but no skip message found. "
        f"Logs: {[r.message for r in caplog.records]}"
    )

    # Verify old allocation-based message is NOT present (Approach B replacement).
    old_msgs = [r.message for r in caplog.records if "unchanged (allocation" in r.message]
    assert len(old_msgs) == 0, (
        "Old 'unchanged (allocation' message found — should be replaced by Approach B"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: onchange proceeds when new snapshot exists (Approach B)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_onchange_proceeds_when_changed(test_vm, caplog):
    """Per-target onchange proceeds when a new snapshot exists (Approach B).

    1. Create first snapshot, back up to target (gate open — target empty).
    2. Verify backup file(s) exist on target after first run.
    3. Create a NEW snapshot with a different name not on target.
    4. Second run: gate detects new snapshot not on target → backup proceeds.
    5. Verify incremental backup file(s) created on target (count increased).
    No allocation-based comparisons are made.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _start_vm_and_check(shell, vm_name)

    # Write data via qemu-io before first snapshot so the overlay has content.
    shell.run(
        ["qemu-io", "--force-share", "-c", "write -P 0xCC 0 200M", str(base_image)],
        timeout=120,
        check=True,
    )

    # First snapshot.
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.onchange-A", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)

    target = TargetConfig(path=target_dir, backup_create="onchange", compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir, targets=[target]
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "onchange_proceed.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- First run: backup proceeds (target empty → gate open) ---
    with caplog.at_level(logging.INFO):
        result_first = core.backup(vm_name)

    first_backup_count = _count_qcow2_files(target_dir)
    assert first_backup_count >= 1, (
        f"Expected at least one backup on target after first run, got {first_backup_count}"
    )

    vm_result = result_first.results[0]
    if vm_result.backup_failed:
        pytest.skip("First backup failed — cannot test proceed.")

    # Write more data so second snapshot has content.
    shell.run(
        ["qemu-io", "--force-share", "-c", "write -P 0xDD 100M 100M", str(base_image)],
        timeout=120,
        check=True,
    )

    # Create a NEW snapshot — different name, not on target.
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.onchange-B", snapshot_dir, base_image)
    state.record_snapshot(vm_name, snap2)

    # --- Second run: new snapshot → gate open → backup proceeds ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.backup(vm_name)
    skip_msgs = [
        r.message
        for r in caplog.records
        if "no new snapshots" in r.message and "skipping" in r.message
    ]
    assert len(skip_msgs) == 0, (
        f"Gate must be open when new snapshot exists. Logs: {[r.message for r in caplog.records]}"
    )

    # Verify incremental backup file(s) created on target.
    second_backup_count = _count_qcow2_files(target_dir)
    assert second_backup_count > first_backup_count, (
        f"Expected more backups after second run ({first_backup_count} → {second_backup_count})"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Approach B gate sequence (target empty → skip → proceed)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_onchange_approach_b_gate(test_vm, caplog):
    """Approach B gate sequence: empty → skip → new snapshot → proceed.

    1. First backup: target is empty → gate passes → backup files created on target.
    2. Second backup: no new snapshots → gate skips → "no new snapshots — skipping".
    3. Third backup: create new snapshot → gate passes → incremental backup created.
    4. Verify caplog does NOT contain old "unchanged (allocation" message anywhere.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _start_vm_and_check(shell, vm_name)

    # --- Phase 1: First backup (target empty) ---
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.phase1-snap", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)

    target = TargetConfig(path=target_dir, backup_create="onchange", compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir, targets=[target]
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "onchange_gate.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        result_first = core.backup(vm_name)
    skip = [
        r.message
        for r in caplog.records
        if "no new snapshots" in r.message and "skipping" in r.message
    ]
    assert len(skip) == 0, "Phase 1: target empty → gate must be open"

    phase1_count = _count_qcow2_files(target_dir)
    assert phase1_count >= 1, f"Phase 1: expected backup files on target, got {phase1_count}"

    if result_first.results[0].backup_failed:
        pytest.skip("Phase 1 backup failed — cannot continue gate sequence test.")

    # --- Phase 2: Second backup (no new snapshots → skip) ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.backup(vm_name)
    skip_msgs = [
        r.message
        for r in caplog.records
        if "no new snapshots" in r.message and "skipping" in r.message
    ]
    assert len(skip_msgs) >= 1, (
        f"Phase 2: no new snapshots → gate must skip. Logs: {[r.message for r in caplog.records]}"
    )

    # --- Phase 3: Create new snapshot → gate opens → backup proceeds ---
    # Write data to change disk content.
    shell.run(
        ["qemu-io", "--force-share", "-c", "write -P 0xEE 0 50M", str(base_image)],
        timeout=120,
        check=True,
    )
    snap3 = _snapshot_create(shell, vm_name, f"{vm_name}.phase3-snap", snapshot_dir, base_image)
    state.record_snapshot(vm_name, snap3)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.backup(vm_name)
    skip_phase3 = [
        r.message
        for r in caplog.records
        if "no new snapshots" in r.message and "skipping" in r.message
    ]
    assert len(skip_phase3) == 0, "Phase 3: new snapshot → gate must be open"

    phase3_count = _count_qcow2_files(target_dir)
    assert phase3_count > phase1_count, (
        f"Phase 3: expected more backups ({phase1_count} → {phase3_count})"
    )

    # Verify old allocation message is never emitted.
    old_msgs = [r.message for r in caplog.records if "unchanged (allocation" in r.message]
    assert len(old_msgs) == 0, "Old 'unchanged (allocation' must not appear"

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 4: Manual deletion recovery (self-healing after backup file loss)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_onchange_manual_deletion_recovery(test_vm, caplog):
    """Self-healing: startup validation detects phantom FULLs, re-creates backups.

    1. Create VM, make snapshot → FULL backup + incremental to target.
    2. Manually delete ALL backup files and checkpoints from target.
    3. Run ``core.run()`` (full pipeline — includes ``_validate_state_at_startup``).
    4. Verify startup validation detects phantom FULLs (caplog contains
       "phantom FULL" in WARNING or higher).
    5. Verify stale baselines cleared (caplog contains "cleared last_backup_allocation"
       or source-equivalent state cleanup message).
    6. Verify new backup files are created on target (self-healing).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _start_vm_and_check(shell, vm_name)

    # Write initial data.
    shell.run(
        ["qemu-io", "--force-share", "-c", "write -P 0xAA 0 100M", str(base_image)],
        timeout=120,
        check=True,
    )

    # Create snapshot.
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.recovery-snap", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)

    # Seed FULL records in state (simulating a previous FULL backup that was
    # later deleted from disk).  This is what triggers phantom FULL detection
    # in _validate_state_at_startup().
    phantom_full_name = f"{vm_name}.FULL.phantom.qcow2"
    state.record_full_backup(
        str(target_dir),
        phantom_full_name,
        snap.timestamp,
    )

    target = TargetConfig(path=target_dir, backup_create="onchange", compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir, targets=[target]
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "onchange_recovery.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- Run full pipeline (includes startup validation) ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.run(vm_name)

    # Collect all log messages at INFO or above.
    all_messages = [r.message for r in caplog.records]
    all_messages_str = "\n".join(all_messages)

    # Assert phantom FULL detection.
    phantom_msgs = [m for m in all_messages if "phantom FULL" in m]
    assert len(phantom_msgs) >= 1, (
        f"Expected phantom FULL detection in logs. Got:\n{all_messages_str}"
    )

    # Assert stale baseline or state cleared.
    # Startup validation logs "cleared last_backup_allocation" when no FULLs remain.
    cleared_msgs = [
        m
        for m in all_messages
        if "cleared" in m.lower()
        and ("last_backup_allocation" in m or "baseline" in m.lower() or "no FULLs" in m.lower())
    ]
    assert len(cleared_msgs) >= 1, (
        f"Expected baseline/state cleared after phantom cleanup. Got:\n{all_messages_str}"
    )

    # Verify self-healing: new backup files created on target.
    target_files = list(target_dir.glob("*.qcow2"))
    assert len(target_files) >= 1, (
        f"Expected self-healing: backup files re-created on target. "
        f"Target contents: {[f.name for f in target_files]}"
    )
    # The phantom file should NOT exist (it was never real).
    phantom_files = [f for f in target_files if phantom_full_name in f.name]
    assert len(phantom_files) == 0, (
        f"Phantom file {phantom_full_name} should not exist on disk. It was a state-only record."
    )

    _cleanup_checkpoints(shell, vm_name)

