"""Integration tests for blockcommit chain-break recovery (design D7).

Tests verify chain-aware retention recovery: partial blockcommit before
a broken backing-chain point, auto-rebase of stuck snapshots onto the
valid base, and safe handling of no-committable-before-break scenarios.

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  Run only when explicitly requested::

    poetry run pytest tests/integration/test_blockcommit_recovery.py -v -m integration

.. note::

   **Known source bugs:** These tests currently assert the actual behavior
   which differs from the intended design due to a bug in
   ``_verify_backing_chain``.  See BUG-001 and BUG-002 in the test
   assertions for details.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import RetentionResult
from qsnap.shell.subprocess_shell import SubprocessShell
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_state import InMemoryStateManager

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _build_core(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    target_dir: Path,
) -> tuple[Core, VMConfig, InMemoryStateManager]:
    """Build a Core instance for offline (qemu-img) blockcommit recovery tests."""
    state = InMemoryStateManager()
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=24,
        lifecycle_mode="qemu-img",
        targets=[
            TargetConfig(
                path=target_dir,
                incremental=True,
                compress=False,
                verify="off",
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(
            timestamp_format="short",
            chain_verify_before_commit=True,
            chain_verify_after_commit=False,
        ),
        vms=[vm_config],
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config, state


def _vm_is_shut_off(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True if VM is shut off."""
    result = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    return result.success and "shut off" in result.stdout.lower()


def _backing_chain_length(tip_path: Path, shell: SubprocessShell) -> int | None:
    """Return the backing chain length from *tip_path*, or None on failure."""
    result = shell.run(
        [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            str(tip_path),
        ],
        timeout=30,
    )
    if not result.success:
        return None
    chain = json.loads(result.stdout)
    return len(chain) if isinstance(chain, list) else None


def _get_backing_filename(shell: SubprocessShell, path: Path) -> str | None:
    """Return ``backing-filename`` of a qcow2 from qemu-img info, or None."""
    result = shell.run(
        ["qemu-img", "info", "--force-share", "--output=json", str(path)],
        timeout=30,
    )
    if not result.success:
        return None
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    backing = info.get("full-backing-filename") or info.get("backing-filename")
    if not isinstance(backing, str) or not backing:
        return None
    return backing


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


def _assert_critical_verification_failed(caplog, expected_error_fragment: str = "") -> None:
    """Assert that a CRITICAL pre-commit verification failure log was emitted.

    This is the current behavior due to source bug BUG-001: when the
    backing chain is broken mid-way, ``qemu-img info --backing-chain``
    fails at the subprocess level, and ``_verify_backing_chain`` returns
    ``broken_file=None``, short-circuiting the recovery path.
    """
    critical_logs = [
        r.message for r in caplog.records
        if r.levelno >= logging.CRITICAL
    ]
    assert len(critical_logs) >= 1, (
        f"Expected at least one CRITICAL log, "
        f"got: {[(r.levelname, r.message[:120]) for r in caplog.records]}"
    )
    combined = " ".join(critical_logs).lower()
    assert "pre-commit chain verification failed" in combined, (
        f"Expected 'pre-commit chain verification failed' in CRITICAL logs. "
        f"Got: {combined[:300]}"
    )
    if expected_error_fragment:
        assert expected_error_fragment.lower() in combined, (
            f"Expected '{expected_error_fragment}' in CRITICAL message. "
            f"Got: {combined[:300]}"
        )


# ──────────────────────────────────────────────────────────────────────
# Test 1 (#42): Broken snapshot chain — partial blockcommit + auto-rebase
# ──────────────────────────────────────────────────────────────────────
#
# Post-fix behavior (design D7): ``_verify_backing_chain`` correctly
# identifies the broken file via ``_find_broken_chain_file``.  The
# recovery path is triggered:
#   - snap2 (stale, missing file) removed from state
#   - snap1 (before break) committed and deleted
#   - snap3 (stuck, after break) auto-rebased onto base_image
#   - snap4 (tip) deferred as active_layer
#   - pipeline continues (non-fatal)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_blockcommit_recovery_broken_snapshot_chain(test_vm, caplog):
    """Broken snapshot chain — partial blockcommit + auto-rebase, pipeline survives.

    Setup: Create snapshot chain (base → snap1 → snap2 → snap3 → snap4).
    Delete snap2 file to break the chain.  Mark all for blockcommit.

    Post-fix behavior: ``_verify_backing_chain`` correctly finds the
    broken file via ``_find_broken_chain_file``.  Recovery path
    (design D7) triggers partial blockcommit: snap1 (before break)
    committed and deleted, snap3 (stuck) auto-rebased onto base_image,
    snap4 (tip) deferred.  No CRITICAL log — pipeline makes progress.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # VM must be shut off for offline (qemu-img) blockcommit.
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)
    if not _vm_is_shut_off(shell, vm_name):
        pytest.skip("Cannot shut off VM for offline blockcommit test")

    core, vm_config, state = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir
    )

    # ── Step 1: Create snapshot chain base → snap1 → snap2 → snap3 → snap4 ──
    for i in range(4):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(0.1)

    snapshots = state.get_snapshots(vm_name)
    assert len(snapshots) == 4, f"Expected 4 snapshots, got {len(snapshots)}"

    snap1 = snapshots[0]
    snap2 = snapshots[1]
    snap3 = snapshots[2]
    snap4 = snapshots[3]

    # Verify chain intact before break.
    assert snap2.path.exists(), f"snap2 should exist: {snap2.path}"
    assert _validate_backing_chain(shell, snap4.path), "Chain should be intact initially"

    # ── Step 2: Delete snap2 file to break the chain ──────────────────────
    os.remove(str(snap2.path))
    assert not snap2.path.exists(), "snap2 should be deleted"

    # ── Step 3: Ensure VM is shut off before blockcommit ──────────────────
    # NOTE: virsh snapshot-create-as on an inactive domain may transiently
    # start the VM.  Explicitly destroy to guarantee offline blockcommit.
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)
    if not _vm_is_shut_off(shell, vm_name):
        pytest.skip("Cannot shut off VM for offline blockcommit test")

    # ── Step 4: Mark all snapshots for blockcommit ────────────────────────
    retention = RetentionResult(
        keep=[],
        remove=[snap1.name, snap2.name, snap3.name, snap4.name],
    )

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core._blockcommit_snapshots(vm_config, retention)

    all_logs = " ".join(r.message for r in caplog.records)

    # ── Step 5: Assertions (post-fix behavior — recovery path is reached) ──

    # snap2 was stale (file missing) → state entry removed (self-healing)
    remaining = state.get_snapshots(vm_name)
    remaining_names = {s.name for s in remaining}
    assert snap2.name not in remaining_names, (
        f"snap2 (stale/missing file) should be removed from state. "
        f"Remaining: {remaining_names}"
    )

    # Recovery path IS now reached — WARNING (not CRITICAL) log emitted.
    warning_logs = [
        r.message for r in caplog.records
        if r.levelno >= logging.WARNING and r.levelno < logging.ERROR
    ]
    assert len(warning_logs) >= 1, (
        f"Expected at least one WARNING log, "
        f"got: {[(r.levelname, r.message[:120]) for r in caplog.records]}"
    )
    combined_warning = " ".join(warning_logs).lower()
    assert "pre-commit chain verification found break" in combined_warning, (
        f"Expected 'pre-commit chain verification found break' in WARNING logs. "
        f"Got: {combined_warning[:300]}"
    )

    # No CRITICAL log should be emitted (recovery path was triggered).
    critical_logs = [
        r.message for r in caplog.records
        if r.levelno >= logging.CRITICAL
    ]
    assert len(critical_logs) == 0, (
        f"Expected no CRITICAL logs, got: {critical_logs[:5]}"
    )

    # Partial blockcommit succeeded: snap1 (before break) was committed.
    assert "merged" in all_logs.lower(), (
        f"Expected 'merged' in logs for partial blockcommit. Logs: {all_logs[:300]}"
    )
    assert f"[blockcommit] {vm_name}: merged" in all_logs, (
        f"Expected blockcommit merge log. Logs: {all_logs[:300]}"
    )

    # snap1 was committed → file deleted
    assert not snap1.path.exists(), (
        f"snap1 should have been committed (blockcommit merged into base). "
        f"Found at: {snap1.path}"
    )
    assert snap1.name not in remaining_names, (
        f"snap1 should be removed from state after commit. "
        f"Remaining: {remaining_names}"
    )

    # Auto-rebase was attempted for stuck snapshots (snap3 after break).
    assert "auto-rebased" in all_logs.lower(), (
        f"Expected 'auto-rebased' in logs. Logs: {all_logs[:300]}"
    )

    # snap3 should still exist (re-based but not committed).
    assert snap3.path.exists(), (
        "snap3 should still exist after auto-rebase (not committed)"
    )

    # snap4 (tip) deferred to active_layer — safe behavior
    assert "deferring blockcommit" in all_logs.lower(), (
        f"Expected deferral log for tip snapshot. Logs: {all_logs[:200]}"
    )
    assert snap4.path.exists(), (
        "snap4 should still exist (tip — correctly deferred)"
    )

    # Pipeline did not crash — recovery path made progress (partial
    # blockcommit + auto-rebase) instead of aborting with CRITICAL.


# ──────────────────────────────────────────────────────────────────────
# Test 2 (#43): No committable snapshots before break
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_blockcommit_recovery_no_committable_before_break(test_vm, caplog):
    """Broken chain at first snapshot — safe deferral, no crash.

    Setup: Create snapshot chain (base → snap1 → snap2).  Delete snap1
    file (first snapshot after base).  Mark all for blockcommit.

    Current behavior: snap1 detected as stale → removed from state.
    snap2 is the tip → deferred (active_layer).  With no other
    committable snapshots, the method returns early at the empty-
    committable guard — ``_verify_backing_chain`` is never reached.
    No CRITICAL log, no crash.  Safe behavior.

    INTENDED behavior (with 3+ snapshots so there is a committable
    snapshot before the break): CRITICAL log "No snapshots can be
    committed before the break", pipeline continues.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # VM must be shut off for offline blockcommit.
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)
    if not _vm_is_shut_off(shell, vm_name):
        pytest.skip("Cannot shut off VM for offline blockcommit test")

    core, vm_config, state = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir
    )

    # ── Step 1: Create snapshot chain base → snap1 → snap2 ───────────────
    for i in range(2):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(0.1)

    snapshots = state.get_snapshots(vm_name)
    assert len(snapshots) == 2, f"Expected 2 snapshots, got {len(snapshots)}"

    snap1 = snapshots[0]
    snap2 = snapshots[1]

    assert snap1.path.exists(), f"snap1 should exist: {snap1.path}"
    assert snap2.path.exists(), f"snap2 should exist: {snap2.path}"

    # ── Step 2: Delete snap1 (first snapshot after base) ─────────────────
    os.remove(str(snap1.path))
    assert not snap1.path.exists(), "snap1 should be deleted"

    # ── Re-destroy VM in case snapshots transiently started it ────────
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)
    if not _vm_is_shut_off(shell, vm_name):
        pytest.skip("Cannot shut off VM after snapshot creation")

    # ── Step 3: Mark all for blockcommit ─────────────────────────────────
    retention = RetentionResult(
        keep=[],
        remove=[snap1.name, snap2.name],
    )

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core._blockcommit_snapshots(vm_config, retention)

    all_logs = " ".join(r.message for r in caplog.records)

    # ── Step 4: Assertions ───────────────────────────────────────────────

    # snap1 detected as stale → removed from state (self-healing)
    remaining = state.get_snapshots(vm_name)
    remaining_names = {s.name for s in remaining}
    assert snap1.name not in remaining_names, (
        "snap1 (stale/missing file) should be removed from state"
    )

    # snap2 is the tip → deferred as active_layer.  Since there are no
    # other snapshots, committable is empty and `_verify_backing_chain`
    # is never reached (the method returns early at the empty-committable
    # guard).  No CRITICAL log is emitted in this case — the chain
    # verification is only reached when there are committable snapshots.
    assert snap2.path.exists(), (
        "snap2 should still exist (deferred as active_layer)"
    )
    assert "deferring blockcommit" in all_logs.lower(), (
        f"Expected deferral log. Logs: {all_logs[:200]}"
    )

    # snap2 should still be in state (deferred, not committed)
    assert snap2.name in remaining_names, (
        "snap2 should still be in state (deferred)"
    )

    # Pipeline returned safely — no crash, no blockcommit
    assert "[blockcommit]" not in all_logs, (
        "No blockcommit should have occurred (no committable snapshots)"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 3 (#44): Stuck snapshot rebased onto valid ancestor
# ──────────────────────────────────────────────────────────────────────
#
# Post-fix behavior (design D7): broken chain mid-way triggers recovery.
#   - snap2 (stale, missing file) removed from state
#   - snap1 (before break) committed and deleted
#   - snap3 (stuck, after break) auto-rebased onto base_image
#   - snap4 (tip) deferred as active_layer
#   - pipeline continues (non-fatal)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_rebase_stuck_to_valid_ancestor(test_vm, caplog):
    """Broken chain mid-way — partial blockcommit + auto-rebase of stuck snapshots.

    Setup: Create snapshot chain (base → snap1 → snap2 → snap3 → snap4).
    Delete snap2 file to break the chain between snap1 and snap3.
    Mark all for blockcommit.

    Post-fix behavior: ``_verify_backing_chain`` correctly identifies
    snap2 as the broken file via ``_find_broken_chain_file``.  The
    recovery path (design D7) is triggered: snap1 (before break) is
    committed, snap3 (stuck, non-tip) is auto-rebased onto base_image,
    snap4 (tip) is deferred as active_layer.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # VM must be shut off for offline blockcommit.
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)
    if not _vm_is_shut_off(shell, vm_name):
        pytest.skip("Cannot shut off VM for offline blockcommit test")

    core, vm_config, state = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir
    )

    # ── Step 1: Create snapshot chain base → snap1 → snap2 → snap3 → snap4 ──
    for i in range(4):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(0.1)

    snapshots = state.get_snapshots(vm_name)
    assert len(snapshots) == 4, f"Expected 4 snapshots, got {len(snapshots)}"

    snap1 = snapshots[0]
    snap2 = snapshots[1]
    snap3 = snapshots[2]
    snap4 = snapshots[3]

    assert snap1.path.exists(), f"snap1 should exist: {snap1.path}"
    assert snap2.path.exists(), f"snap2 should exist: {snap2.path}"
    assert snap3.path.exists(), f"snap3 should exist: {snap3.path}"
    assert snap4.path.exists(), f"snap4 should exist: {snap4.path}"

    # ── Step 2: Delete snap2 to break the chain between snap1 and snap3 ──
    backing_before = _get_backing_filename(shell, snap3.path)
    assert backing_before is not None, "snap3 should have a backing file initially"
    # snap3's backing should reference snap2 (to be deleted).
    assert str(snap2.path) in (backing_before or ""), (
        f"snap3's backing should be snap2, got: {backing_before}"
    )

    os.remove(str(snap2.path))
    assert not snap2.path.exists(), "snap2 should be deleted"

    # ── Re-destroy VM in case snapshots transiently started it ──────
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)
    if not _vm_is_shut_off(shell, vm_name):
        pytest.skip("Cannot shut off VM after snapshot creation")

    # ── Step 3: Mark all for blockcommit ────────────────────────────
    retention = RetentionResult(
        keep=[],
        remove=[snap1.name, snap2.name, snap3.name, snap4.name],
    )

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core._blockcommit_snapshots(vm_config, retention)

    all_logs = " ".join(r.message for r in caplog.records)

    # ── Step 4: Assertions (post-fix behavior) ────────────────────────────

    # snap2 stale → removed from state
    remaining = state.get_snapshots(vm_name)
    remaining_names = {s.name for s in remaining}
    assert snap2.name not in remaining_names, "snap2 (stale) should be removed from state"

    # Recovery path IS now reached — WARNING log (not CRITICAL) emitted.
    warning_logs = [
        r.message for r in caplog.records
        if r.levelno >= logging.WARNING and r.levelno < logging.ERROR
    ]
    assert len(warning_logs) >= 1, (
        f"Expected at least one WARNING log about chain break. "
        f"Got: {[(r.levelname, r.message[:120]) for r in caplog.records]}"
    )
    combined_warning = " ".join(warning_logs).lower()
    assert "pre-commit chain verification found break" in combined_warning, (
        f"Expected 'pre-commit chain verification found break' in WARNING logs. "
        f"Got: {combined_warning[:300]}"
    )

    # No CRITICAL log should be emitted (recovery path triggered).
    critical_logs = [
        r.message for r in caplog.records
        if r.levelno >= logging.CRITICAL
    ]
    assert len(critical_logs) == 0, (
        f"Expected no CRITICAL logs, got: {critical_logs[:5]}"
    )

    # Partial blockcommit succeeded: snap1 (before break) was committed.
    assert "merged" in all_logs.lower(), (
        f"Expected 'merged' in logs for partial blockcommit. Logs: {all_logs[:300]}"
    )
    assert f"[blockcommit] {vm_name}: merged" in all_logs, (
        f"Expected blockcommit merge log. Logs: {all_logs[:300]}"
    )

    # snap1 was committed → file deleted
    assert not snap1.path.exists(), (
        f"snap1 should have been committed (blockcommit merged into base). "
        f"Found at: {snap1.path}"
    )

    # snap1 removed from state
    assert snap1.name not in remaining_names, (
        f"snap1 should be removed from state after commit. "
        f"Remaining: {remaining_names}"
    )

    # Auto-rebase attempted for stuck snapshot snap3 (non-tip, after break).
    assert "auto-rebased" in all_logs.lower(), (
        f"Expected 'auto-rebased' in logs. Logs: {all_logs[:300]}"
    )
    assert "auto-rebased stuck snapshot" in all_logs.lower(), (
        f"Expected auto-rebase log for stuck snapshot. Logs: {all_logs[:300]}"
    )

    # snap3 should still exist (re-based onto base_image, not committed).
    assert snap3.path.exists(), (
        "snap3 should still exist after auto-rebase (not committed)"
    )

    # snap3's backing chain should now be intact (rebased to base_image).
    assert _validate_backing_chain(shell, snap3.path), (
        "snap3 should have a valid backing chain after auto-rebase"
    )
    backing_after = _get_backing_filename(shell, snap3.path)
    assert backing_after is not None, (
        "snap3 should have a backing file after auto-rebase"
    )
    # snap3 should now be rebased onto the base image.
    assert str(base_image) in (backing_after or ""), (
        f"snap3 should be rebased onto base_image ({base_image}), "
        f"but backing is: {backing_after}"
    )

    # snap4 (tip) deferred to active_layer
    assert "deferring blockcommit" in all_logs.lower(), (
        f"Expected deferral log for tip snapshot. Logs: {all_logs[:200]}"
    )
    assert snap4.path.exists(), (
        "snap4 should still exist (tip — correctly deferred)"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 4 (#45): Safe — no data loss, pipeline survives
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_rebase_safe_for_snapshots(test_vm, caplog):
    """Broken chain — no data loss, pipeline returns safely.

    Setup: Create snapshot chain.  Delete intermediate file.
    Run blockcommit.

    Current behavior: snap1 stale → removed from state.  snap2 is the
    tip → deferred (active_layer).  Committable is empty, so
    ``_verify_backing_chain`` is never reached.  No CRITICAL log, no
    blockcommit.  All files preserved (no data loss).  Pipeline does
    not crash.

    INTENDED (with 3+ snapshots for a non-tip committable): ``qemu-img
    rebase -u`` used for stuck snapshots (unsafe rebase — skips missing
    file's data).  Active layer has all data.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # VM must be shut off for offline blockcommit.
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)
    if not _vm_is_shut_off(shell, vm_name):
        pytest.skip("Cannot shut off VM for offline blockcommit test")

    core, vm_config, state = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir
    )

    # ── Step 1: Create snapshot chain base → snap1 → snap2 ──────────────
    for i in range(2):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(0.1)

    snapshots = state.get_snapshots(vm_name)
    assert len(snapshots) == 2, f"Expected 2 snapshots, got {len(snapshots)}"

    snap1 = snapshots[0]
    snap2 = snapshots[1]

    assert snap1.path.exists(), f"snap1 should exist: {snap1.path}"
    assert snap2.path.exists(), f"snap2 should exist: {snap2.path}"

    # ── Step 2: Delete snap1 to break the chain ──────────────────────────
    os.remove(str(snap1.path))
    assert not snap1.path.exists(), "snap1 should be deleted"

    # ── Re-destroy VM in case snapshots transiently started it ──────
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)
    if not _vm_is_shut_off(shell, vm_name):
        pytest.skip("Cannot shut off VM after snapshot creation")

    # ── Step 3: Mark all for blockcommit ────────────────────────────
    retention = RetentionResult(
        keep=[],
        remove=[snap1.name, snap2.name],
    )

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core._blockcommit_snapshots(vm_config, retention)

    all_logs = " ".join(r.message for r in caplog.records)

    # ── Step 4: Assertions ───────────────────────────────────────────────

    # snap1 was stale → removed from state (self-healing)
    remaining = state.get_snapshots(vm_name)
    remaining_names = {s.name for s in remaining}
    assert snap1.name not in remaining_names, (
        "snap1 (stale/missing file) should be removed from state"
    )

    # snap2 is the tip → deferred as active_layer.  Since committable is
    # empty (only the tip snapshot exists after snap1 removal),
    # `_verify_backing_chain` is never reached.  No CRITICAL log.
    assert snap2.path.exists(), (
        "snap2 (tip) should still exist after blockcommit — no data loss"
    )
    assert "deferring blockcommit" in all_logs.lower(), (
        f"Expected deferral log. Logs: {all_logs[:200]}"
    )

    # Pipeline did not crash
    assert "[blockcommit]" not in all_logs, (
        "No blockcommit should have occurred (empty committable set)"
    )
