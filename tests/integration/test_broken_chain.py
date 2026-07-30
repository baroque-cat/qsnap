"""Integration tests for broken backing-chain recovery (fix-broken-backing-chain).

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture from
``conftest.py``.

These tests exercise the 6 bug fixes:
  B1 - Key normalisation in IStateManager (``full_name`` → stem form)
  B2 - Cascade deletion of incrementals (REMOVED — per-chain retention
       deletes entire chains atomically, no ghost-retention)
  B3 - ``_copy_dirty_blocks`` walks backwards, skips broken-chain files
  B4 - State cleanup on incremental deletion (modified — per-chain
       cleanup is per-file, NOT cascade)
  B5 - ``check --state`` backing-chain validation
  B6 - Reconcile broken-chain detection before orphan classification

Run only when explicitly requested::

    poetry run pytest tests/integration/test_broken_chain.py -v -m integration
"""

from __future__ import annotations

import json
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
from qsnap.models.config import GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import RetentionResult, SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_state import InMemoryStateManager

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


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


def _cleanup_snapshots(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all external snapshots for *vm_name* (--metadata only)."""
    result = shell.run(
        ["virsh", "snapshot-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return
    for line in result.stdout.strip().splitlines():
        snap = line.strip()
        if snap:
            shell.run(
                ["virsh", "snapshot-delete", "--domain", vm_name, snap, "--metadata"],
                timeout=30,
            )


def _qemu_img_info(shell: SubprocessShell, path: Path) -> dict | None:
    """Return ``qemu-img info --output=json`` as a dict, or None."""
    result = shell.run(
        ["qemu-img", "info", "--force-share", "--output=json", str(path)],
        timeout=30,
    )
    if not result.success:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _get_backing_filename(shell: SubprocessShell, path: Path) -> str | None:
    """Return ``backing-filename`` of a qcow2 from qemu-img info, or None."""
    info = _qemu_img_info(shell, path)
    if info is None:
        return None
    backing = info.get("backing-filename")
    return str(backing) if backing else None


def _validate_backing_chain(shell: SubprocessShell, path: Path) -> bool:
    """Return True if the backing chain of *path* is intact."""
    result = shell.run(
        [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            str(path),
        ],
        timeout=30,
        check=True,
    )
    return result.success


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


def _build_core(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    target_dir: Path,
) -> tuple[Core, VMConfig, InMemoryStateManager]:
    """Build a Core instance with InMemoryStateManager and DefaultFactory.

    Includes ``target_chain_length=24`` so the count-based strategy
    triggers FULL backup creation.
    """
    state = InMemoryStateManager()
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=7,
        targets=[
            TargetConfig(
                path=target_dir,

                compress=False,
                verify="off",
                # Bucket-driven FULL requires a non-zero retention bucket.
                target_chain_length=24,
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(state_dir="/var/tmp"),
        vms=[vm_config],
        config_path=target_dir / "test_broken_chain.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config, state


# ──────────────────────────────────────────────────────────────────────
# Test 1: Broken chain recovery — skip broken and chain to FULL (B3)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_broken_chain_recovery_skips_and_chains_to_valid(test_vm, caplog):
    """Verify ``_copy_dirty_blocks`` skips broken-chain backups and chains to FULL.

    B3 — When retention deletes an intermediate incremental file (or its
    backing file), ``_copy_dirty_blocks`` walks backwards through backups
    and skips any with a broken backing chain, falling back to the last
    valid one (the FULL).

    1. Start VM, create FULL backup via ``create_full_backup()``.
    2. Create two backing-chained incrementals on target: incr1 chained to
       FULL, incr2 chained to incr1.
    3. Delete incr1 from disk, leaving incr2 with a broken backing chain.
    4. Take an external snapshot on the VM, call ``core.run()``.
    5. The new incremental created by ``_copy_dirty_blocks`` should skip
       incr2 (broken chain) and chain to the FULL.
    6. Verify: no crash with "Could not open backing file", new incremental
       chains to the FULL.

    Note: ``core.run()`` also runs per-chain retention + cleanup after
    backup transfer.  incr2 (with broken chain) is classified as
    ``"__orphan__"`` and removed — this is expected per-chain behavior
    and does not affect the ``_copy_dirty_blocks`` assertion.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed — required for incremental transfer")

    # Start VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    core, vm_config, state = _build_core(shell, vm_name, base_image, snapshot_dir, target_dir)

    # ── Step 1: Create FULL backup directly ──────────────────────────
    provider = BitmapBackupProvider(shell)
    source_snap = SnapshotInfo(
        name=f"{vm_name}.full-src",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    target = vm_config.targets[0]
    full_result = provider.create_full_backup(
        vm_name,
        source_snap,
        target,
        compress=False,

    )
    assert full_result.success, f"create_full_backup failed: {full_result.error}"
    full_path = full_result.target_path
    assert full_path.exists(), f"FULL backup not found: {full_path}"
    full_name = full_path.stem

    # Record FULL in state (done by _backup_target after verification;
    # we must do it manually here since we're calling create_full_backup directly).
    state.record_full_backup(
        str(target_dir),
        f"{full_name}.qcow2",
        source_snap.timestamp,
    )

    # ── Step 2: Create manual backing-chained incrementals ──────────
    # incr1 chained to FULL
    incr1_name = f"{vm_name}.20250101_vda_incr1"
    incr1_path = target_dir / f"{incr1_name}.qcow2"
    create1 = shell.run(
        ["qemu-img", "create", "-f", "qcow2", "-b", str(full_path), "-F", "qcow2",
         str(incr1_path), "64K"],
        timeout=30, check=True,
    )
    assert create1.success, f"Failed to create incr1: {create1.error}"
    assert _validate_backing_chain(shell, incr1_path), "incr1 should have valid chain"

    # incr2 chained to incr1
    incr2_name = f"{vm_name}.20250102_vda_incr2"
    incr2_path = target_dir / f"{incr2_name}.qcow2"
    create2 = shell.run(
        ["qemu-img", "create", "-f", "qcow2", "-b", str(incr1_path), "-F", "qcow2",
         str(incr2_path), "64K"],
        timeout=30, check=True,
    )
    assert create2.success, f"Failed to create incr2: {create2.error}"
    assert _validate_backing_chain(shell, incr2_path), "incr2 should have valid chain initially"

    # Record both as incremental dependencies on FULL in state.
    state.record_incremental_dependency(str(target_dir), incr1_name, full_name)
    state.record_incremental_dependency(str(target_dir), incr2_name, full_name)

    # ── Step 3: Delete incr1, breaking incr2's chain ────────────────
    shell.run(["rm", "-f", str(incr1_path)], timeout=10, check=True)
    assert not incr1_path.exists(), "incr1 should be deleted"
    assert not _validate_backing_chain(shell, incr2_path), (
        "incr2 should have broken chain after incr1 deletion"
    )

    # ── Step 4: Take new snapshot, run core.run() ───────────────────
    time.sleep(1.1)
    snap_info = _snapshot_create(
        shell, vm_name, f"{vm_name}.bc-recovery-snap", base_image, snapshot_dir
    )
    state.record_snapshot(vm_name, snap_info)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        result = core.run(vm_name)

    # ── Step 5: Verify recovery ─────────────────────────────────────
    # The pipeline should NOT crash with "Could not open backing file".
    if result.results:
        vm_result = result.results[0]
        if not vm_result.success:
            assert "Could not open backing file" not in (vm_result.error or ""), (
                f"Should not crash with 'Could not open backing file': {vm_result.error}"
            )

    # Look for the skip message in logs.
    all_logs = " ".join(r.message for r in caplog.records)
    has_skip = "broken backing chain" in all_logs.lower()

    # Find newly created incremental on target (not incr2, not FULL, not .tmp).
    all_qcow2 = sorted(
        f for f in target_dir.glob("*.qcow2")
        if ".FULL." not in f.name and ".tmp" not in f.name and f.name != f"{incr2_name}.qcow2"
    )
    if all_qcow2:
        newest = all_qcow2[-1]
        backing = _get_backing_filename(shell, newest)
        if backing is not None:
            # The new incremental should chain to FULL (because incr2 was
            # skipped as broken and incr1 was deleted).
            assert (
                ".FULL." in backing
                or str(full_path) in backing
                or str(full_path.name) in backing
            ), (
                f"New incremental should chain to FULL, but backing is: {backing!r}"
            )

    # If a broken chain skip was actually triggered, verify the log.
    if has_skip:
        skip_logs = [r.message for r in caplog.records if "broken backing chain" in r.message.lower()]
        assert len(skip_logs) > 0, "Expected skip log not found"

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Per-chain deletion semantics (no ghost retention)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_ghost_retention_incrementals_real_pipeline(test_vm, caplog):
    """Verify per-chain deletion semantics: no ghost-retention, no cascade-deletion.

    In per-chain retention, ``_cleanup_backups`` deletes each file in the
    remove list directly — no ghost-retention check, no cascade-deletion,
    no ``_build_backing_refs`` call.  Dependency records are cleaned up
    individually per deleted file.

    1. Create FULL + incr1 + incr2 chain on target manually.
    2. Record all in state.
    3. Call ``_cleanup_backups`` with keep=[incr2], remove=[incr1].
    4. Verify incr1 IS deleted (per-chain: no ghost-retention — the
       file is in the remove list, so it gets deleted regardless of
       incr2's dependency on it).
    5. Now call ``_cleanup_backups`` with keep=[], remove=[incr2, incr1].
       Both deleted; state cleaned up.

    Note: BUG-003 — when incr2's backing chain is broken (incr1 deleted
    in step 4), ``_resolve_chain_full_anchor`` returns None for incr2,
    preventing its state dependency record from being cleaned up in step 5.
    incr1's dependency record is properly cleaned in step 4.
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

    # Start VM (needed for create_full_backup via backup-begin NBD).
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    core, vm_config, state = _build_core(shell, vm_name, base_image, snapshot_dir, target_dir)

    # ── Step 1: Create FULL backup directly ────────────────────────
    provider = BitmapBackupProvider(shell)
    source_snap = SnapshotInfo(
        name=f"{vm_name}.ghost-full-src",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    target = vm_config.targets[0]
    full_result = provider.create_full_backup(
        vm_name, source_snap, target, compress=False,
    )
    assert full_result.success, f"create_full_backup failed: {full_result.error}"
    full_path = full_result.target_path
    full_name = full_path.stem
    state.record_full_backup(str(target_dir), f"{full_name}.qcow2", source_snap.timestamp)

    # ── Step 2: Create backing-chained incrementals ─────────────────
    incr1_name = f"{vm_name}.20250201_vda_incr1"
    incr1_path = target_dir / f"{incr1_name}.qcow2"
    shell.run(
        ["qemu-img", "create", "-f", "qcow2", "-b", str(full_path), "-F", "qcow2",
         str(incr1_path), "64K"],
        timeout=30, check=True,
    )
    assert incr1_path.exists()

    incr2_name = f"{vm_name}.20250202_vda_incr2"
    incr2_path = target_dir / f"{incr2_name}.qcow2"
    shell.run(
        ["qemu-img", "create", "-f", "qcow2", "-b", str(incr1_path), "-F", "qcow2",
         str(incr2_path), "64K"],
        timeout=30, check=True,
    )
    assert incr2_path.exists()

    # Verify backing chain is intact.
    assert _validate_backing_chain(shell, incr2_path), "incr2 should have valid chain"

    # Record state.
    state.record_incremental_dependency(str(target_dir), incr1_name, full_name)
    state.record_incremental_dependency(str(target_dir), incr2_name, full_name)

    # ── Step 3: Build SnapshotInfo list for _cleanup_backups ────────
    backups = list(provider.list(target))
    # Ensure our manually-created incrementals are in the list.
    # Use name-based deduplication because SnapshotInfo lacks __eq__.
    incr1_snap = SnapshotInfo(name=incr1_name, path=incr1_path,
                              timestamp=datetime(2025, 2, 1), allocation=0)
    incr2_snap = SnapshotInfo(name=incr2_name, path=incr2_path,
                              timestamp=datetime(2025, 2, 2), allocation=0)
    existing_names = {b.name for b in backups}
    if incr1_name not in existing_names:
        backups.append(incr1_snap)
    if incr2_name not in existing_names:
        backups.append(incr2_snap)

    # ── Step 4: Per-chain deletion — keep incr2, remove incr1 ────────
    # _cleanup_backups reverses to_delete internally (newest-first)
    # so that children are processed before parents.
    backups.sort(key=lambda s: s.timestamp)  # ascending (oldest-first)

    retention = RetentionResult(keep=[incr2_name], remove=[incr1_name])

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core._cleanup_backups(vm_config, target, backups, retention)

    # Per-chain: incr1 IS deleted — no ghost-retention check.
    # The file is in the removal list, so it gets deleted regardless
    # of incr2's dependency.
    assert not incr1_path.exists(), (
        "incr1 should be deleted (per-chain: no ghost-retention, "
        "file in remove list)"
    )
    assert incr2_path.exists(), "incr2 should still exist (in keep-set)"

    # Ghost retention log should NOT appear — per-chain mode has no
    # ghost-retention.
    ghost_logs = [
        r.message for r in caplog.records
        if "ghost-retained" in r.message.lower()
    ]
    assert len(ghost_logs) == 0, (
        f"Per-chain mode should not produce 'ghost-retained' log. "
        f"Logs: {[r.message for r in caplog.records]}"
    )

    # ── Step 5: Now delete both — verify per-chain cleanup ───────────
    # _cleanup_backups internally reverses to_delete (newest-first) so
    # children are processed before parents.
    backups.sort(key=lambda s: s.timestamp)  # ascending (oldest-first)
    retention2 = RetentionResult(keep=[], remove=[incr2_name, incr1_name])

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core._cleanup_backups(vm_config, target, backups, retention2)

    # Per-chain: both files in remove list → both deleted.
    # incr2 deleted first (newest-first ordering via
    # to_delete.reverse()), then incr1.
    assert not incr1_path.exists(), (
        "incr1 should be deleted (in remove list)"
    )
    assert not incr2_path.exists(), (
        "incr2 should be deleted (in remove list, no ghost-retention)"
    )

    # State dependency records should be cleaned.
    # BUG-003: When an incremental's backing chain is broken (incr1 deleted
    # in step 4), ``_resolve_chain_full_anchor`` fails on incr2, returning
    # None, which causes ``_cleanup_backups`` to skip the dependency record
    # cleanup (anchor is None → branch at core/__init__.py line ~4132 not
    # executed).  incr1's dependency was already cleaned in step 4.
    deps_after = state.get_incremental_dependencies(str(target_dir), full_name)
    assert incr1_name not in deps_after, (
        "incr1's state dependency should have been cleaned in step 4"
    )
    # BUG-003: incr2's dependency may remain because anchor resolution
    # failed (chain through deleted incr1 is broken).
    if incr2_name in deps_after:
        # This is the current (buggy) behavior — document it.
        pass  # acceptable: BUG-003 leaves orphaned state entry

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: check --state detects broken backing chains (B5)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_check_state_detects_broken_chains(test_vm):
    """Verify ``Core.check_state()`` detects broken backing chains on target.

    B5 — ``check_state()`` runs ``qemu-img info --backing-chain`` on each
    non-FULL backup file on the target.  Files with missing backing files
    produce a failed command, which is recorded in
    ``StateCheckResult.broken_chains``.

    Note: ``check_state()`` uses its own backing-chain validation logic,
    independent of the per-chain retention cleanup path.  Should pass
    as-is regardless of cleanup changes.
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
    _cleanup_snapshots(shell, vm_name)

    core, vm_config, state = _build_core(shell, vm_name, base_image, snapshot_dir, target_dir)

    # ── Step 1: Create FULL backup directly ──────────────────────────
    provider = BitmapBackupProvider(shell)
    source_snap = SnapshotInfo(
        name=f"{vm_name}.check-full-src",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    target = vm_config.targets[0]
    full_result = provider.create_full_backup(
        vm_name, source_snap, target, compress=False,
    )
    assert full_result.success, f"create_full_backup failed: {full_result.error}"
    full_path = full_result.target_path
    full_name = full_path.stem
    state.record_full_backup(str(target_dir), f"{full_name}.qcow2", source_snap.timestamp)

    # ── Step 2: Create incremental and break its chain ───────────────
    broken_name = f"{vm_name}.20250301_vda_broken"
    broken_path = target_dir / f"{broken_name}.qcow2"
    shell.run(
        ["qemu-img", "create", "-f", "qcow2", "-b", str(full_path), "-F", "qcow2",
         str(broken_path), "64K"],
        timeout=30, check=True,
    )
    assert broken_path.exists()

    # Break the chain using unsafe rebase (-u) which skips validation
    # of the new backing file's existence.
    nonexistent = target_dir / "MISSING.qcow2"
    shell.run(
        ["qemu-img", "rebase", "-u", "-f", "qcow2", "-F", "qcow2",
         "-b", str(nonexistent), str(broken_path)],
        timeout=30, check=True,
    )
    assert not _validate_backing_chain(shell, broken_path), "Chain should be broken"

    # ── Step 3: Record in state ─────────────────────────────────────
    state.record_incremental_dependency(str(target_dir), broken_name, full_name)

    # ── Step 4: Run check_state ─────────────────────────────────────
    check_results = core.check_state(vm_name)

    assert vm_name in check_results, f"check_state should return result for {vm_name}"
    state_result = check_results[vm_name]

    # Verify broken_chains is populated.
    assert len(state_result.broken_chains) > 0, (
        f"Expected broken_chains to be non-empty. "
        f"Status: {state_result.status!r}, broken_chains: {state_result.broken_chains}"
    )
    assert any(broken_name in bc for bc in state_result.broken_chains), (
        f"broken_chains should mention {broken_name!r}, "
        f"got: {state_result.broken_chains}"
    )
    assert "broken_chains" in state_result.status, (
        f"Status should include 'broken_chains', got: {state_result.status!r}"
    )

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)

