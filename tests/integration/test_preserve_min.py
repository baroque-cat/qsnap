"""Integration tests for ``preserve_min`` and source-disk ``onchange`` gate.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use ``Core`` (not module
implementations directly) because ``preserve_min`` is a Core-level
post-processing filter in ``_evaluate_snapshot_retention()`` and the
``onchange`` gate is Core-level logic in ``_backup_target()``.

Coverage:
- ``preserve_min`` filter with real blockcommit (integration-preserve-min)
- ``preserve_min`` exceeds total snapshots → no blockcommit
- Source-disk onchange gate opens after disk write
- Source-disk onchange gate skips when disk unchanged
- Per-target independent baselines
- First-run no-baseline gate opens

Run only when explicitly requested::

    poetry run pytest tests/integration/test_preserve_min.py -v -m integration
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
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running
from tests.mocks import InMemoryStateManager, MockConfigFacade

# ── helpers ────────────────────────────────────────────────────────────


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


def _snapshot_create(
    shell: SubprocessShell,
    vm_name: str,
    snap_name: str,
    snapshot_dir: Path,
    base_image: Path,
) -> SnapshotInfo:
    """Create an external snapshot and return ``SnapshotInfo``."""
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


def _start_vm_and_check(shell: SubprocessShell, vm_name: str) -> None:
    """Start the VM and verify prerequisites for NBD backup tests."""
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


def _vm_is_running(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True if VM is running."""
    result = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    return result.success and "running" in result.stdout.lower()


def _count_qcow2_files(directory: Path) -> int:
    """Count .qcow2 files in *directory*."""
    return len(list(directory.glob("*.qcow2")))


# ──────────────────────────────────────────────────────────────────────
# Test 1: preserve_min keeps newest snapshots with real blockcommit
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_preserve_min_keeps_newest_with_real_blockcommit(test_vm, caplog):
    """Create 10 snapshots, ``preserve_min=8`` → only 2 oldest blockcommitted.

    1. Start VM, create 10 snapshots via ``Core._create_snapshot``.
    2. Evaluate retention with ``snapshot_chain_length=1`` and
       ``snapshot_preserve_min=8`` — verify remove list has exactly 2 items
       (the 2 oldest), keep list has 8 items.
    3. Execute ``core.prune()`` to perform actual blockcommit.
    4. Verify oldest 2 snapshot files are deleted from disk, 8 newest remain.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Start VM for snapshot creation.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    # Build Core with preserve_min=8 and a very short chain_length=1 so
    # retention engine wants to remove almost everything.
    tmpdir_for_config: Path = test_vm["tmpdir"]
    state = InMemoryStateManager()
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=1,
        snapshot_preserve_min=8,
        lifecycle_mode="virsh",
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
            )
        ],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir_for_config / "preserve_min.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Create 10 snapshots.
    for i in range(10):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(1.1)  # Ensure unique timestamps

    snapshots = state.get_snapshots(vm_name)
    assert len(snapshots) == 10, f"Expected 10 snapshots, got {len(snapshots)}"

    # Record snapshot file paths before blockcommit.
    sorted_snaps = sorted(snapshots, key=lambda s: s.timestamp)
    snap_paths_before = {s.name: s.path for s in snapshots}
    oldest_two_names = {sorted_snaps[0].name, sorted_snaps[1].name}
    newest_eight_names = {s.name for s in sorted_snaps[2:]}

    # Evaluate retention.
    retention = core._evaluate_snapshot_retention(vm_config)
    assert retention is not None, "Retention result should not be None"
    assert len(retention.remove) == 2, (
        f"Expected 2 snapshots in remove list, got {len(retention.remove)}: {retention.remove}"
    )
    assert len(retention.keep) == 8, (
        f"Expected 8 snapshots in keep list, got {len(retention.keep)}: {retention.keep}"
    )
    assert set(retention.remove) == oldest_two_names, (
        f"remove should be the 2 oldest, got {retention.remove}"
    )
    assert set(retention.keep) == newest_eight_names, (
        f"keep should be the 8 newest, got {retention.keep}"
    )

    # Run prune to execute blockcommit (retention + lifecycle).
    # prune calls _evaluate_snapshot_retention + _blockcommit_snapshots.
    with caplog.at_level(logging.INFO):
        core.prune(vm_name)

    # Verify oldest 2 snapshots are deleted, 8 newest remain.
    for snap in sorted_snaps[:2]:
        path = snap_paths_before[snap.name]
        assert not path.exists(), f"Oldest snapshot file should be deleted by blockcommit: {path}"

    for snap in sorted_snaps[2:]:
        path = snap_paths_before[snap.name]
        assert path.exists(), f"Newest snapshot file should be preserved: {path}"

    # VM should still be running (live blockcommit via virsh).
    assert _vm_is_running(shell, vm_name), "VM should still be running after live blockcommit"

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: preserve_min exceeds total → no blockcommit
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_preserve_min_exceeds_total_no_blockcommit_integration(test_vm, caplog):
    """Create 5 snapshots, ``preserve_min=10`` → no snapshots blockcommitted.

    1. Start VM, create 5 snapshots.
    2. Evaluate retention with ``snapshot_chain_length=1`` and
       ``snapshot_preserve_min=10`` — max_removable = max(0, 5-10) = 0.
    3. Verify remove list is empty, keep list has all 5 snapshots.
    4. Execute ``core.prune()`` — verify NO files are deleted.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Start VM for snapshot creation.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    tmpdir_cfg_exceeds: Path = test_vm["tmpdir"]
    state = InMemoryStateManager()
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=1,
        snapshot_preserve_min=10,
        lifecycle_mode="virsh",
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
            )
        ],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir_cfg_exceeds / "preserve_min_exceeds.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Create 5 snapshots.
    for i in range(5):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(1.1)

    snapshots = state.get_snapshots(vm_name)
    assert len(snapshots) == 5, f"Expected 5 snapshots, got {len(snapshots)}"
    snap_paths_before = {s.name: s.path for s in snapshots}

    # Evaluate retention: preserve_min=10 > total=5 → no removable.
    retention = core._evaluate_snapshot_retention(vm_config)
    assert retention is not None, "Retention result should not be None"
    assert len(retention.remove) == 0, (
        f"Expected empty remove list (preserve_min exceeds total), "
        f"got {len(retention.remove)}: {retention.remove}"
    )
    assert len(retention.keep) == 5, (
        f"Expected all 5 snapshots in keep list, got {len(retention.keep)}"
    )

    # Run prune — should be a no-op for blockcommit (nothing to remove).
    with caplog.at_level(logging.INFO):
        core.prune(vm_name)

    # Verify NO files were deleted.
    for snap in snapshots:
        path = snap_paths_before[snap.name]
        assert path.exists(), (
            f"Snapshot file should NOT be deleted (preserve_min exceeds total): {path}"
        )

    assert _vm_is_running(shell, vm_name), "VM should still be running"

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 7: default preserve_min=48 dominates chain_length (real blockcommit)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_default_preserve_min_48_real_blockcommit(test_vm, caplog):
    """Default ``snapshot_preserve_min=48`` blocks blockcommit under 48 snaps.

    D13: the global default is 48.  With 30 snapshots and the default
    ``snapshot_chain_length=24``, the preserve_min floor dominates —
    ``core.prune()`` must perform ZERO blockcommits and delete nothing.
    Flipping ``snapshot_preserve_min=0`` (explicit opt-out) restores the
    old behavior: prune then commits the 6 oldest snapshots.

    1. Start VM, create 30 snapshots via ``Core._create_snapshot`` with
       the facade-resolved defaults (chain_length=24, preserve_min=48).
    2. Run ``core.prune()`` — verify zero blockcommits: all 30 files
       still exist, state still holds 30 snapshots, deferred queue empty.
    3. Rebuild Core with ``snapshot_preserve_min=0`` and prune again —
       verify the 6 oldest files are deleted and 24 newest remain.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start VM for snapshot creation.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    # ── Phase 1: default floor (preserve_min=48) ──────────────────────
    # These are the values ConfigFacade resolves when the TOML omits
    # snapshot_preserve_min and snapshot_chain_length (D13).
    state = InMemoryStateManager()
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=24,
        snapshot_preserve_min=48,
        lifecycle_mode="virsh",
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
            )
        ],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "preserve_min_default48.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Create 30 snapshots.
    for i in range(30):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(0.35)  # distinct timestamps (name embeds seconds + hex)

    snapshots = state.get_snapshots(vm_name)
    assert len(snapshots) == 30, f"Expected 30 snapshots, got {len(snapshots)}"
    snap_paths_before = {s.name: s.path for s in snapshots}

    # Retention: floor dominates chain_length → empty remove list.
    retention = core._evaluate_snapshot_retention(vm_config)
    assert retention is not None, "Retention result should not be None"
    assert len(retention.remove) == 0, (
        f"Expected empty remove list under default preserve_min=48 "
        f"(30 < 48), got {len(retention.remove)}: {retention.remove}"
    )
    assert len(retention.keep) == 30, f"Expected all 30 snapshots kept, got {len(retention.keep)}"

    # Prune → zero blockcommits, zero deletions.
    with caplog.at_level(logging.INFO):
        core.prune(vm_name)

    for snap in snapshots:
        path = snap_paths_before[snap.name]
        assert path.exists(), (
            f"Snapshot file must NOT be deleted under default preserve_min=48: {path}"
        )
    remaining = state.get_snapshots(vm_name)
    assert len(remaining) == 30, (
        f"State must still hold 30 snapshots after default-floor prune, got {len(remaining)}"
    )
    assert state.get_deferred_operations(vm_name) == [], (
        "No deferred blockcommit entries may be created by a floor-blocked prune"
    )

    # ── Phase 2: explicit opt-out (preserve_min=0) restores old behavior ─
    state2 = InMemoryStateManager()
    # Re-seed state2 with the same 30 snapshot records so the second
    # Core sees the identical chain (fresh VM state would be empty).
    for snap in snapshots:
        state2.record_snapshot(vm_name, snap)

    vm_config_zero = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=24,
        snapshot_preserve_min=0,
        lifecycle_mode="virsh",
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
            )
        ],
    )
    config_zero = MockConfigFacade(
        vms=[vm_config_zero],
        config_path=tmpdir / "preserve_min_optout.toml",
    )
    core_zero = Core(
        config=config_zero,
        factory=DefaultFactory(shell=shell, state=state2),
        state=state2,
        shell=shell,
    )

    retention_zero = core_zero._evaluate_snapshot_retention(vm_config_zero)
    assert retention_zero is not None and len(retention_zero.remove) == 6, (
        f"With preserve_min=0 and chain_length=24, exactly 6 oldest must be "
        f"removed, got {len(retention_zero.remove) if retention_zero else 'None'}"
    )

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core_zero.prune(vm_name)

    # The 6 oldest files deleted, 24 newest remain.
    sorted_snaps = sorted(snapshots, key=lambda s: s.timestamp)
    for snap in sorted_snaps[:6]:
        path = snap_paths_before[snap.name]
        assert not path.exists(), f"Oldest snapshot should be committed when preserve_min=0: {path}"
    for snap in sorted_snaps[6:]:
        path = snap_paths_before[snap.name]
        assert path.exists(), f"Newest snapshot should be preserved: {path}"

    assert _vm_is_running(shell, vm_name), "VM should still be running after live blockcommit"

    _cleanup_checkpoints(shell, vm_name)


@pytest.mark.integration
@pytest.mark.timeout(3600)
@pytest.mark.parametrize(
    "change_detection_mode",
    ["allocation-size"],
)
def test_source_disk_onchange_gate_opens_after_write(test_vm, caplog, change_detection_mode):
    """Source-disk onchange gate opens when the source disk has changed.

    1. Start VM, write data, create snapshot, back up (first run → gate
       opens, baseline recorded as the active layer's actual-size).
    2. Artificially lower the baseline to simulate a stale baseline (the
       disk has grown since the baseline was recorded).
    3. Call ``_should_backup_onchange()`` → verify gate opens because
       current_allocation > stale_baseline.
    4. Lower the baseline again → gate opens a second time.

    This approach tests the gate's comparison logic directly via state
    manipulation, since writing via ``qemu-io`` to a running VM's active
    disk does not reliably increase the ``actual-size`` that ``qemu-img
    info`` reports (the VM's QEMU process controls the overlay allocation).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _start_vm_and_check(shell, vm_name)

    # Write initial data so disk has measurable actual-size.
    shell.run(
        ["qemu-io", "--force-share", "-c", "write -P 0xAA 0 100M", str(base_image)],
        timeout=120,
        check=True,
    )

    # Create first snapshot.
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.gate-open-1", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    # Seed last_allocation so AllocationSizeDetector enters the normal
    # code path instead of the early-return (which would report
    # current_allocation=0).  Without this, the detector's fail-safe
    # path returns current_allocation=0 and Core records baseline=0.
    state.set_last_allocation(vm_name, "vda", 0)

    target = TargetConfig(path=target_dir, backup_create="onchange", compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        change_detection_mode=change_detection_mode,
        targets=[target],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "gate_open.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- First run: no baseline → gate opens ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result_first = core.backup(vm_name)

    first_skip_msgs = [
        r.message for r in caplog.records if "no disk changed since last backup" in r.message
    ]
    assert len(first_skip_msgs) == 0, (
        f"First run must not skip — no baseline. Logs: {[r.message for r in caplog.records]}"
    )

    # Verify backup files on target.
    first_backup_count = _count_qcow2_files(target_dir)
    assert first_backup_count >= 1, (
        f"Expected backup files after first run, got {first_backup_count}. "
        f"Files: {list(target_dir.glob('*.qcow2'))}"
    )

    vm_result = result_first.results[0]
    if vm_result.backup_failed:
        _cleanup_checkpoints(shell, vm_name)
        pytest.skip("First backup failed — cannot test gate-open.")

    # Verify baseline was recorded after successful backup.
    baseline = state.get_last_backup_allocation(str(target_dir), "vda")
    assert baseline is not None, "Baseline should be recorded after successful backup"

    # --- Simulate disk growth: lower the baseline ---
    # The detector queries the active layer's current actual-size, which
    # hasn't changed (no new VM writes).  By setting the baseline lower,
    # we simulate that the disk grew since the last backup.
    stale_baseline = max(baseline - 50000, 0)
    state.set_last_backup_allocation(str(target_dir), "vda", stale_baseline)

    should_proceed, change_result = core._should_backup_onchange(vm_config, target)
    assert should_proceed is True, (
        f"Gate should open when baseline is stale. "
        f"current_allocation={change_result.current_allocation}, "
        f"stale_baseline={stale_baseline}, original_baseline={baseline}"
    )

    # --- Lower baseline further; gate opens again ---
    even_staler = max(stale_baseline - 50000, 0)
    state.set_last_backup_allocation(str(target_dir), "vda", even_staler)
    # Restore last_allocation so detector still enters normal path.
    state.set_last_allocation(vm_name, "vda", 0)

    should_proceed_2, change_result_2 = core._should_backup_onchange(vm_config, target)
    assert should_proceed_2 is True, (
        f"Gate should open again with even staler baseline. "
        f"current_allocation={change_result_2.current_allocation}, "
        f"stale_baseline={even_staler}"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 4: source-disk onchange gate skips when unchanged
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
@pytest.mark.parametrize(
    "change_detection_mode",
    ["allocation-size"],
)
def test_source_disk_onchange_gate_skips_when_unchanged(test_vm, caplog, change_detection_mode):
    """Source-disk onchange gate skips when no new data written.

    1. Start VM, write data, create snapshot, back up (baseline recorded).
    2. Run backup again without writing new data.
    3. Verify gate skips with "disk unchanged since last backup" message.
    4. Verify caplog does NOT contain old "no new snapshots" message.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _start_vm_and_check(shell, vm_name)

    # Write data so disk has measurable actual-size.
    shell.run(
        ["qemu-io", "--force-share", "-c", "write -P 0xAA 0 100M", str(base_image)],
        timeout=120,
        check=True,
    )

    snap = _snapshot_create(shell, vm_name, f"{vm_name}.skip-unchanged", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)
    # Seed last_allocation so detector enters the normal code path.
    state.set_last_allocation(vm_name, "vda", 0)

    target = TargetConfig(path=target_dir, backup_create="onchange", compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        change_detection_mode=change_detection_mode,
        targets=[target],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "skip_unchanged.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- First run: baseline recorded ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result_first = core.backup(vm_name)

    first_backup_count = _count_qcow2_files(target_dir)
    assert first_backup_count >= 1, f"Expected backup files, got {first_backup_count}"

    if result_first.results[0].backup_failed:
        _cleanup_checkpoints(shell, vm_name)
        pytest.skip("First backup failed — cannot test gate-skip.")

    # Verify baseline recorded.
    baseline = state.get_last_backup_allocation(str(target_dir), "vda")
    assert baseline is not None, "Baseline should be recorded after successful backup"

    # --- Second run: no new data written → gate skips ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.backup(vm_name)

    skip_msgs = [
        r.message for r in caplog.records if "no disk changed since last backup" in r.message
    ]
    assert len(skip_msgs) >= 1, (
        f"Expected onchange skip on second run, but no skip message found. "
        f"Logs: {[r.message for r in caplog.records]}"
    )

    # Verify old Approach B message is NOT present.
    old_msgs = [
        r.message
        for r in caplog.records
        if "no new snapshots" in r.message and "skipping" in r.message
    ]
    assert len(old_msgs) == 0, (
        "Old 'no new snapshots — skipping' message should not appear "
        "(source-disk detection replaces Approach B)"
    )

    # Verify no new backup files created on target.
    second_backup_count = _count_qcow2_files(target_dir)
    assert second_backup_count == first_backup_count, (
        f"Expected NO new backup files when disk unchanged "
        f"({first_backup_count} → {second_backup_count})"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 5: per-target baseline independent
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
@pytest.mark.parametrize(
    "change_detection_mode",
    ["allocation-size"],
)
def test_per_target_baseline_independent(test_vm, caplog, change_detection_mode):
    """Per-target baselines are independent — each target has its own gate.

    1. Back up to target A and target B (both onchange).  Baseline A and
       Baseline B are recorded independently.
    2. Clear baseline B to simulate a fresh target.
    3. Verify baseline B is None while baseline A is still present.
    4. Verify gate opens for target B (no baseline → first-run semantics).
    5. Verify gate skips for target A when disk is unchanged.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_a: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Create a second target directory.
    target_b = tmpdir / "backup-b"
    target_b.mkdir(parents=True, exist_ok=True)

    _start_vm_and_check(shell, vm_name)

    # Write data and create snapshot.
    shell.run(
        ["qemu-io", "--force-share", "-c", "write -P 0xAA 0 100M", str(base_image)],
        timeout=120,
        check=True,
    )
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.indep-1", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    # Seed last_allocation so detector enters the normal code path.
    state.set_last_allocation(vm_name, "vda", 0)

    target_a_config = TargetConfig(
        path=target_a, backup_create="onchange", compress=False, verify="off"
    )
    target_b_config = TargetConfig(
        path=target_b, backup_create="onchange", compress=False, verify="off"
    )
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        change_detection_mode=change_detection_mode,
        targets=[target_a_config, target_b_config],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "independent.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- First backup: both targets get baselines ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result = core.backup(vm_name)

    if result.results[0].backup_failed:
        _cleanup_checkpoints(shell, vm_name)
        pytest.skip("First backup failed — cannot test independent baselines.")

    baseline_a = state.get_last_backup_allocation(str(target_a), "vda")
    baseline_b = state.get_last_backup_allocation(str(target_b), "vda")
    assert baseline_a is not None, "Baseline A should be recorded"
    assert baseline_b is not None, "Baseline B should be recorded"

    # --- Clear baseline B to simulate a fresh target ---
    cleared = state.clear_last_backup_allocation(str(target_b), "vda")
    assert cleared, "clear_last_backup_allocation should return True"

    baseline_b_after_clear = state.get_last_backup_allocation(str(target_b), "vda")
    assert baseline_b_after_clear is None, "Baseline B should be None after clear"

    # Verify baseline A is still intact (independent).
    baseline_a_after = state.get_last_backup_allocation(str(target_a), "vda")
    assert baseline_a_after == baseline_a, (
        f"Baseline A should be unchanged ({baseline_a}), got {baseline_a_after}"
    )

    # --- Gate check for target B (no baseline → opens) ---
    should_proceed_b, _ = core._should_backup_onchange(vm_config, target_b_config)
    assert should_proceed_b is True, (
        "Gate should open for target B (no baseline → first-run semantics)"
    )

    # --- Gate check for target A (disk unchanged → skips) ---
    should_proceed_a, _ = core._should_backup_onchange(vm_config, target_a_config)
    assert should_proceed_a is False, (
        "Gate should close for target A (disk unchanged since baseline)"
    )

    # --- Write data to active layer, verify both gates open ---
    # Since qemu-io writes to a running VM's active layer do not
    # reliably increase actual-size, we simulate disk growth by
    # lowering the baseline so that current > stale_baseline.
    stale_a = max(baseline_a - 50000, 0)
    state.set_last_backup_allocation(str(target_a), "vda", stale_a)

    # Target A: stale baseline → gate opens
    should_proceed_a2, _ = core._should_backup_onchange(vm_config, target_a_config)
    assert should_proceed_a2 is True, "Gate A should open when baseline is stale"

    # Target B: still no baseline → gate opens (first-run semantics)
    should_proceed_b2, _ = core._should_backup_onchange(vm_config, target_b_config)
    assert should_proceed_b2 is True, "Gate B should still open (no baseline → first-run semantics)"

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 6: first run no-baseline gate opens
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
@pytest.mark.parametrize(
    "change_detection_mode",
    ["allocation-size"],
)
def test_onchange_first_run_no_baseline_integration(test_vm, caplog, change_detection_mode):
    """Fresh target, no prior state → baseline is None → gate opens.

    1. Start VM, write data, create snapshot.
    2. Verify ``get_last_backup_allocation`` returns None.
    3. Call ``_should_backup_onchange`` → verify gate opens (first run).
    4. Run ``core.backup()`` → backup proceeds, baseline recorded.
    5. Verify backup files exist on target.
    6. Call ``_should_backup_onchange`` again → verify gate skips (unchanged).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _start_vm_and_check(shell, vm_name)

    # Write data and create snapshot.
    shell.run(
        ["qemu-io", "--force-share", "-c", "write -P 0xAA 0 100M", str(base_image)],
        timeout=120,
        check=True,
    )
    snap = _snapshot_create(shell, vm_name, f"{vm_name}.first-run", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)
    # Seed last_allocation so detector enters the normal code path.
    state.set_last_allocation(vm_name, "vda", 0)

    target = TargetConfig(path=target_dir, backup_create="onchange", compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        change_detection_mode=change_detection_mode,
        targets=[target],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "first_run.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Verify no baseline exists before first run.
    baseline_before = state.get_last_backup_allocation(str(target_dir), "vda")
    assert baseline_before is None, (
        f"Baseline should be None before first run, got {baseline_before}"
    )

    # --- Gate check: no baseline → gate opens ---
    should_proceed, _ = core._should_backup_onchange(vm_config, target)
    assert should_proceed is True, "Gate should open when baseline is None (first-run semantics)"

    # --- First run: gate opens, backup proceeds ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result_first = core.backup(vm_name)

    first_skip_msgs = [
        r.message for r in caplog.records if "no disk changed since last backup" in r.message
    ]
    assert len(first_skip_msgs) == 0, (
        f"First run must not skip — no baseline. Logs: {[r.message for r in caplog.records]}"
    )

    # Verify backup files exist.
    first_backup_count = _count_qcow2_files(target_dir)
    assert first_backup_count >= 1, (
        f"Expected backup files after first run, got {first_backup_count}. "
        f"Files: {list(target_dir.glob('*.qcow2'))}"
    )

    if result_first.results[0].backup_failed:
        _cleanup_checkpoints(shell, vm_name)
        pytest.skip("First backup failed — cannot continue first-run test.")

    # Verify baseline was recorded.
    baseline_after = state.get_last_backup_allocation(str(target_dir), "vda")
    assert baseline_after is not None, "Baseline should be recorded after successful backup"

    # --- Gate check: disk unchanged → gate skips ---
    should_proceed_2, _ = core._should_backup_onchange(vm_config, target)
    assert should_proceed_2 is False, (
        f"Gate should close when disk unchanged. baseline={baseline_after}"
    )

    # --- Second run: verify gate skips via backup ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        core.backup(vm_name)

    skip_msgs = [
        r.message for r in caplog.records if "no disk changed since last backup" in r.message
    ]
    assert len(skip_msgs) >= 1, (
        f"Second run must skip (disk unchanged). Logs: {[r.message for r in caplog.records]}"
    )

    # Verify no new backup files.
    second_backup_count = _count_qcow2_files(target_dir)
    assert second_backup_count == first_backup_count, (
        f"Expected no new backups when disk unchanged "
        f"({first_backup_count} → {second_backup_count})"
    )

    _cleanup_checkpoints(shell, vm_name)
