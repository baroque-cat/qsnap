"""Tests for FULL backup verification at M1/M2/M3 lifecycle points in Core orchestration.

Covers:
- Post-creation verification (``_backup_target``): M1 (metadata / corrupt-bit),
  M2 (qemu-img check), M3 (SHA-256 hash comparison).
- Pre-deletion verification (``_cleanup_backups``): M1 always enforced,
  M2 configurable, cascade-deletion blocking.
- Timing and state-recording guarantees.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import BackupAbortError, Core
from qsnap.models.results import (
    BackupResult,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import MockConfigFacade

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

_OK = ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


def _record_snap(target, vm, mock_state):
    """Record a snapshot in state and return it."""
    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot(vm.name, snap)
    return snap


def _setup_cleanup_backups_context(
    mock_state,
    target,
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_shell,
    *,
    full_verify_before_delete="check",
    full_name="full.FULL.daily.qcow2",
    dep_names=None,
    keep_set=None,
    remove_set=None,
):
    """Set up a Core instance with state pre-populated for _cleanup_backups testing.

    Returns (core, vm, target).
    """
    global_cfg = make_global_config(
        full_verify_before_delete=full_verify_before_delete,
    )
    target_cfg = target
    vm = make_vm_config(name="testvm", targets=[target_cfg])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Record a FULL backup entry in state
    mock_state.record_full_backup(
        str(target_cfg.path),
        full_name,
        datetime(2025, 7, 13, 8, 0),
        "vda",
    )

    # Record incremental dependencies
    for dep in dep_names or []:
        mock_state.record_incremental_dependency(str(target_cfg.path), dep, full_name)

    return core, vm, target_cfg


# ═══════════════════════════════════════════════════════════════════════════
# Post-creation FULL backup verification (M1/M2/M3)
# ═══════════════════════════════════════════════════════════════════════════

# ── test_full_verify_after_create_hash_uses_snapshot_hash ────────────────


def test_full_verify_after_create_hash_uses_snapshot_hash(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When full_verify_after_create="compare", verify_full_backup is called
    with source_path for comparison.

    Uses count-based FULL trigger: no prior FULLs causes first backup to
    create a FULL unconditionally.
    """
    global_cfg = make_global_config(full_verify_after_create="compare")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # No prior FULLs → first backup triggers FULL creation (count-based).
    with patch("qsnap.core.verify_full_backup", return_value=None) as verify_spy:
        core._backup_target(vm, target, [snap])

    assert verify_spy.called, "verify_full_backup should be called"
    assert verify_spy.call_args[0][2] == "compare", "verify_mode should be 'compare'"
    assert "source_path" in verify_spy.call_args[1], "source_path should be passed for compare mode"
    assert verify_spy.call_args[1]["source_path"] == snap.path, (
        "source_path should be the source snapshot's path"
    )


# ── test_full_created_m1_passes_recorded_in_state ────────────────────────


def test_full_created_m1_passes_recorded_in_state(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """create_full_backup succeeds, M1 passes, record_full_backup is called."""
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # No prior FULLs → first backup triggers FULL creation (count-based).

    with (
        patch("qsnap.core.verify_full_backup", return_value=None) as verify_spy,
        patch.object(
            mock_state,
            "record_full_backup",
            wraps=mock_state.record_full_backup,
        ) as record_spy,
    ):
        core._backup_target(vm, target, [snap])

    assert verify_spy.called, "verify_full_backup should be called"
    assert record_spy.called, "record_full_backup should be called after verification passes"
    # Verify FULL was recorded
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 1, "One FULL backup should be recorded"


# ── test_full_created_m1_fails_corrupt_bit_deleted ───────────────────────


def test_full_created_m1_fails_corrupt_bit_deleted(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """M1 fails with corrupt bit, FULL file deleted, record_full_backup NOT called,
    and the VM pipeline is aborted (VM-level isolation)."""
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # No prior FULLs → first backup triggers FULL creation (count-based).

    # Configure rm -f expectation so the file-deletion shell command succeeds
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: FULL backup has corrupt bit set — file is damaged",
        ),
        patch.object(
            mock_state,
            "record_full_backup",
            wraps=mock_state.record_full_backup,
        ) as record_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    assert not record_spy.called, "record_full_backup should NOT be called when verification fails"
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 0, "No FULL should be recorded when verification fails"


# ── test_full_created_m1_fails_not_qcow2_deleted ─────────────────────────


def test_full_created_m1_fails_not_qcow2_deleted(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """M1 fails with wrong format (not qcow2), FULL file deleted."""
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # No prior FULLs → first backup triggers FULL creation (count-based).

    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: expected format qcow2, got raw",
        ),
        patch.object(
            mock_state,
            "record_full_backup",
            wraps=mock_state.record_full_backup,
        ) as record_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    assert not record_spy.called, "record_full_backup should NOT be called on verify failure"
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Pre-deletion FULL backup verification (M1/M2)
# ═══════════════════════════════════════════════════════════════════════════

# ── test_cleanup_backups_m1_passes_full_deleted ──────────────────────────


def test_cleanup_backups_m1_passes_full_deleted(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """M1 at pre-deletion passes, FULL deleted via per-chain cleanup."""
    target = make_target()
    full_name = "full.FULL.daily.qcow2"
    dep_names = ["inc1.qcow2", "inc2.qcow2"]

    core, vm, target = _setup_cleanup_backups_context(
        mock_state,
        target,
        make_vm_config,
        make_global_config,
        mock_factory,
        mock_shell,
        full_verify_before_delete="check",
        full_name=full_name,
        dep_names=dep_names,
    )

    # Mock backup provider's list() to return the FULL and its dependents
    from qsnap.models.results import SnapshotInfo as SI

    full_info = SI(
        name=full_name,
        path=target.path / full_name,
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=0,
        disk="vda",
    )
    inc1_info = SI(
        name="inc1.qcow2",
        path=target.path / "inc1.qcow2",
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=0,
        disk="vda",
    )
    inc2_info = SI(
        name="inc2.qcow2",
        path=target.path / "inc2.qcow2",
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=0,
        disk="vda",
    )

    # FULL, inc1, inc2 all in remove list via per-chain retention
    retention = RetentionResult(
        keep=[],  # nothing kept — all should be removed
        remove=[full_name] + dep_names,
    )

    # Mock _resolve_chain_full_anchor for inc1 and inc2
    inc_full_path = str(target.path / full_name)
    for dn in dep_names:
        mock_shell.expect(rf"qemu-img info.*--output=json.*{dn}").returns(
            ShellResult(
                success=True,
                stdout=json.dumps(
                    {
                        "format": "qcow2",
                        "backing-filename": inc_full_path,
                    }
                ),
                stderr="",
                returncode=0,
                error=None,
            )
        )

    with (
        patch("qsnap.core.verify_full_backup", return_value=None) as verify_spy,
        patch.object(
            mock_factory._backup_provider,
            "delete",
            wraps=mock_factory._backup_provider.delete,
        ) as delete_spy,
    ):
        core._cleanup_backups(vm, target, [full_info, inc1_info, inc2_info], retention)

    # M1 verification was called for the FULL
    assert verify_spy.called, "M1 verification should be called"
    # delete() was called for FULL + 2 cascade-deleted dependents (3 calls)
    deleted_names = [call.args[0].name for call in delete_spy.call_args_list]
    assert delete_spy.call_count == 3, (
        f"FULL + 2 cascade-deleted dependents = 3 deletions, got {delete_spy.call_count}: {deleted_names}"
    )
    assert full_name in deleted_names, f"FULL {full_name} should be deleted"
    assert "inc1.qcow2" in deleted_names, "inc1.qcow2 should be cascade-deleted"
    assert "inc2.qcow2" in deleted_names, "inc2.qcow2 should be cascade-deleted"


# ── test_cleanup_backups_m1_fails_deletion_blocked ────────────────────────


def test_cleanup_backups_m1_fails_deletion_blocked(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """M1 fails at pre-deletion — per-chain deletion blocked, CRITICAL logged."""
    target = make_target()
    full_name = "full.FULL.daily.qcow2"
    dep_names = ["inc1.qcow2"]

    core, vm, target = _setup_cleanup_backups_context(
        mock_state,
        target,
        make_vm_config,
        make_global_config,
        mock_factory,
        mock_shell,
        full_verify_before_delete="check",
        full_name=full_name,
        dep_names=dep_names,
    )

    from qsnap.models.results import SnapshotInfo as SI

    full_info = SI(
        name=full_name,
        path=target.path / full_name,
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=0,
        disk="vda",
    )

    retention = RetentionResult(keep=[], remove=[full_name])

    caplog.set_level(logging.CRITICAL)

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: FULL backup has corrupt bit set — file is damaged",
        ),
        patch.object(
            mock_factory._backup_provider,
            "delete",
            wraps=mock_factory._backup_provider.delete,
        ) as delete_spy,
    ):
        core._cleanup_backups(vm, target, [full_info], retention)

    # delete() should NOT be called — per-chain deletion blocked
    assert not delete_spy.called, "delete should NOT be called when M1 fails"
    # CRITICAL log should be emitted
    critical_logs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical_logs, "CRITICAL log should be emitted when M1 fails"
    assert "corrupt" in critical_logs[0].message.lower()


# ── test_cleanup_backups_m1_fails_no_dependents_still_blocked ────────────


def test_cleanup_backups_m1_fails_no_dependents_still_blocked(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """M1 fails on FULL with no dependents, deletion still blocked."""
    target = make_target()
    full_name = "full.FULL.daily.qcow2"

    core, vm, target = _setup_cleanup_backups_context(
        mock_state,
        target,
        make_vm_config,
        make_global_config,
        mock_factory,
        mock_shell,
        full_verify_before_delete="check",
        full_name=full_name,
        dep_names=[],  # no dependents
    )

    from qsnap.models.results import SnapshotInfo as SI

    full_info = SI(
        name=full_name,
        path=target.path / full_name,
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=0,
        disk="vda",
    )

    retention = RetentionResult(keep=[], remove=[full_name])

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: expected format qcow2, got raw",
        ),
        patch.object(
            mock_factory._backup_provider,
            "delete",
            wraps=mock_factory._backup_provider.delete,
        ) as delete_spy,
    ):
        core._cleanup_backups(vm, target, [full_info], retention)

    assert not delete_spy.called, (
        "delete should NOT be called even when FULL has no dependents but M1 fails"
    )


# ── test_full_verify_metadata_mode_skips_m2 ──────────────────────────────


def test_full_verify_metadata_mode_skips_m2(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When full_verify_before_delete="metadata", M2 (qemu-img check) is NOT called."""
    target = make_target()
    full_name = "full.FULL.daily.qcow2"

    core, vm, target = _setup_cleanup_backups_context(
        mock_state,
        target,
        make_vm_config,
        make_global_config,
        mock_factory,
        mock_shell,
        full_verify_before_delete="metadata",
        full_name=full_name,
        dep_names=[],
    )

    from qsnap.models.results import SnapshotInfo as SI

    full_info = SI(
        name=full_name,
        path=target.path / full_name,
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=0,
        disk="vda",
    )

    retention = RetentionResult(keep=[], remove=[full_name])

    # Track all verify_full_backup calls
    verify_calls = []

    def track_verify(shell, target_path, verify_mode, **kwargs):
        verify_calls.append(verify_mode)
        return None

    with patch("qsnap.core.verify_full_backup", side_effect=track_verify):
        core._cleanup_backups(vm, target, [full_info], retention)

    # M1 ("metadata") should always be called
    assert "metadata" in verify_calls, "M1 should always be called"
    # M2 ("check") should NOT appear since mode is "metadata"
    assert "check" not in verify_calls, (
        "M2 ('check') should NOT be called when full_verify_before_delete='metadata'"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Hash verification (M3)
# ═══════════════════════════════════════════════════════════════════════════

# ── test_full_verify_hash_match_success ───────────────────────────────────


def test_full_verify_hash_match_success(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Compare mode, compare matches, record_full_backup called."""
    global_cfg = make_global_config(full_verify_after_create="compare")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # No prior FULLs → first backup triggers FULL creation (count-based).

    with patch("qsnap.core.verify_full_backup", return_value=None) as verify_spy:
        core._backup_target(vm, target, [snap])

    assert verify_spy.called
    assert verify_spy.call_args[0][2] == "compare", "verify_mode should be 'compare'"
    assert "source_path" in verify_spy.call_args[1], "source_path should be passed for compare mode"
    assert verify_spy.call_args[1]["source_path"] == snap.path, (
        "source_path should be the source snapshot's path"
    )
    # FULL should be recorded
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 1, "FULL should be recorded after hash verification passes"


# ── test_full_verify_content_comparison_mismatch_fails ──────────────────────


def test_full_verify_content_comparison_mismatch_fails(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Compare mode, content comparison mismatch, FULL deleted, NOT recorded."""
    global_cfg = make_global_config(full_verify_after_create="compare")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # No prior FULLs → first backup triggers FULL creation (count-based).

    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: content comparison mismatch",
        ),
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 0, "FULL should NOT be recorded after content comparison mismatch"


# ═══════════════════════════════════════════════════════════════════════════
# Timing / ordering guarantees
# ═══════════════════════════════════════════════════════════════════════════

# ── test_full_backup_verified_before_state_recording ──────────────────────


def test_full_backup_verified_before_state_recording(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Timing check: verify_full_backup called BEFORE record_full_backup."""
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # No prior FULLs → first backup triggers FULL creation (count-based).

    call_order = []

    def track_verify(*args, **kwargs):
        call_order.append("verify")
        return None

    orig_record = mock_state.record_full_backup

    def track_record(*args, **kwargs):
        call_order.append("record")
        return orig_record(*args, **kwargs)

    with (
        patch("qsnap.core.verify_full_backup", side_effect=track_verify),
        patch.object(mock_state, "record_full_backup", side_effect=track_record),
    ):
        core._backup_target(vm, target, [snap])

    assert call_order == ["verify", "record"], (
        f"verify_full_backup must be called BEFORE record_full_backup, got order: {call_order}"
    )


# ── test_full_backup_verify_fails_file_deleted_not_recorded ───────────────


def test_full_backup_verify_fails_file_deleted_not_recorded(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """verify_full_backup fails, file deleted, not recorded."""
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # No prior FULLs → first backup triggers FULL creation (count-based).

    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: qemu-img check found 3 errors",
        ),
        patch.object(
            mock_state,
            "record_full_backup",
            wraps=mock_state.record_full_backup,
        ) as record_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    assert not record_spy.called, "record_full_backup should NOT be called"
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 0


# ═══════════════════════════════════════════════════════════════════════════
# First backup / new period creates FULL with verification
# ═══════════════════════════════════════════════════════════════════════════

# ── test_first_backup_creates_full_with_verification ──────────────────────


def test_first_backup_creates_full_with_verification(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """First backup to target creates FULL and verifies it."""
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # No prior FULLs → first backup triggers FULL creation (count-based).

    with (
        patch.object(
            mock_factory._backup_provider,
            "create_full_backup",
            wraps=mock_factory._backup_provider.create_full_backup,
        ) as full_spy,
        patch("qsnap.core.verify_full_backup", return_value=None) as verify_spy,
    ):
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "create_full_backup should be called on first backup"
    assert verify_spy.called, "verify_full_backup should be called after first FULL"
    # FULL should be recorded
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 1


# ── test_new_weekly_creates_full_with_verification ────────────────────────


def test_new_weekly_creates_full_with_verification(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """New weekly period triggers FULL with verification."""
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(target_chain_length=0)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Prior FULL in old weekly period (W26 — June 23, 2025)
    mock_state.record_full_backup(
        str(target.path),
        "old_full.FULL.weekly.qcow2",
        datetime(2025, 6, 23),
        "vda",
    )

    # Snapshot in new weekly period (W28 — July 13, 2025)
    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Count-based trigger: old_full has no incrementals, chain_length=0,
    # so incremental_count (0) > chain_length (0) = False.
    # Record an incremental to push it over the threshold.
    mock_state.record_incremental_dependency(
        str(target.path), "inc_dep.qcow2", "old_full.FULL.weekly.qcow2"
    )

    with (
        patch.object(
            mock_factory._backup_provider,
            "create_full_backup",
            wraps=mock_factory._backup_provider.create_full_backup,
        ) as full_spy,
        patch("qsnap.core.verify_full_backup", return_value=None) as verify_spy,
        patch("qsnap.core.os.path.exists", return_value=True),
    ):
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "create_full_backup should be called for new weekly period"
    assert verify_spy.called, "verify_full_backup should be called"
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 2, "Both old and new FULL should be recorded"


# ═══════════════════════════════════════════════════════════════════════════
# Cleanup behaviour (proceed vs blocked)
# ═══════════════════════════════════════════════════════════════════════════

# ── test_cleanup_proceeds_on_m1_pass ──────────────────────────────────────


def test_cleanup_proceeds_on_m1_pass(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Cleanup proceeds after M1 passes on FULL."""
    target = make_target()
    full_name = "full.FULL.daily.qcow2"

    core, vm, target = _setup_cleanup_backups_context(
        mock_state,
        target,
        make_vm_config,
        make_global_config,
        mock_factory,
        mock_shell,
        full_verify_before_delete="check",
        full_name=full_name,
        dep_names=[],
    )

    from qsnap.models.results import SnapshotInfo as SI

    full_info = SI(
        name=full_name,
        path=target.path / full_name,
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=0,
        disk="vda",
    )

    retention = RetentionResult(keep=[], remove=[full_name])

    with (
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch.object(
            mock_factory._backup_provider,
            "delete",
            wraps=mock_factory._backup_provider.delete,
        ) as delete_spy,
    ):
        core._cleanup_backups(vm, target, [full_info], retention)

    assert delete_spy.called, "delete should be called when M1 passes"
    # FULL should have been deleted
    deleted_names = [call.args[0].name for call in delete_spy.call_args_list]
    assert full_name in deleted_names, f"FULL {full_name} should be deleted, got: {deleted_names}"


# ── test_cleanup_blocked_on_m1_fail ───────────────────────────────────────


def test_cleanup_blocked_on_m1_fail(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Cleanup blocked when M1 fails on FULL."""
    target = make_target()
    full_name = "full.FULL.daily.qcow2"

    core, vm, target = _setup_cleanup_backups_context(
        mock_state,
        target,
        make_vm_config,
        make_global_config,
        mock_factory,
        mock_shell,
        full_verify_before_delete="check",
        full_name=full_name,
        dep_names=[],
    )

    from qsnap.models.results import SnapshotInfo as SI

    full_info = SI(
        name=full_name,
        path=target.path / full_name,
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=0,
        disk="vda",
    )

    retention = RetentionResult(keep=[], remove=[full_name])

    with (
        patch(
            "qsnap.core.verify_full_backup",
            side_effect=lambda shell, path, mode, **kw: (
                "verification failed: qemu-img info returned error" if mode == "metadata" else None
            ),
        ),
        patch.object(
            mock_factory._backup_provider,
            "delete",
            wraps=mock_factory._backup_provider.delete,
        ) as delete_spy,
    ):
        core._cleanup_backups(vm, target, [full_info], retention)

    assert not delete_spy.called, "delete should be blocked when M1 fails"


# ── test_per_chain_deletion_blocked_on_corrupt_full ───────────────────────


def test_per_chain_deletion_blocked_on_corrupt_full(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Per-chain deletion blocked when FULL is corrupt — M1 failure prevents deletion."""
    target = make_target()
    full_name = "full.FULL.daily.qcow2"
    dep_names = ["inc1.qcow2", "inc2.qcow2"]

    core, vm, target = _setup_cleanup_backups_context(
        mock_state,
        target,
        make_vm_config,
        make_global_config,
        mock_factory,
        mock_shell,
        full_verify_before_delete="check",
        full_name=full_name,
        dep_names=dep_names,
    )

    from qsnap.models.results import SnapshotInfo as SI

    full_info = SI(
        name=full_name,
        path=target.path / full_name,
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=0,
        disk="vda",
    )

    # All items in remove set — but M1 failure blocks deletion
    retention = RetentionResult(
        keep=[],
        remove=[full_name] + dep_names,
    )

    caplog.set_level(logging.CRITICAL)

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: FULL backup has corrupt bit set — file is damaged",
        ),
        patch.object(
            mock_factory._backup_provider,
            "delete",
            wraps=mock_factory._backup_provider.delete,
        ) as delete_spy,
    ):
        core._cleanup_backups(vm, target, [full_info], retention)

    # Nothing should be deleted — per-chain deletion blocked by M1 failure
    assert not delete_spy.called, (
        "Per-chain deletion should be completely blocked when FULL is corrupt"
    )
    # CRITICAL log should mention blocking deletion
    critical_logs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical_logs, "CRITICAL log expected"
    assert any("blocking" in r.message.lower() for r in critical_logs), (
        "CRITICAL log should mention blocking deletion"
    )


# ── test_per_chain_orphaned_incrementals_deleted ──────────────────────────


def test_per_chain_orphaned_incrementals_deleted(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Incrementals deleted via per-chain cleanup when M1 passes."""
    target = make_target()
    full_name = "full.FULL.daily.qcow2"
    dep_names = ["inc1.qcow2"]

    core, vm, target = _setup_cleanup_backups_context(
        mock_state,
        target,
        make_vm_config,
        make_global_config,
        mock_factory,
        mock_shell,
        full_verify_before_delete="check",
        full_name=full_name,
        dep_names=dep_names,
    )

    from qsnap.models.results import SnapshotInfo as SI

    full_info = SI(
        name=full_name,
        path=target.path / full_name,
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=0,
        disk="vda",
    )
    inc1_info = SI(
        name="inc1.qcow2",
        path=target.path / "inc1.qcow2",
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=0,
        disk="vda",
    )

    # FULL + inc both in remove via per-chain evaluation
    retention = RetentionResult(keep=[], remove=[full_name, "inc1.qcow2"])

    # Mock _resolve_chain_full_anchor for inc1
    mock_shell.expect(r"qemu-img info.*--output=json.*inc1\.qcow2").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "backing-filename": str(target.path / full_name),
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch.object(
            mock_factory._backup_provider,
            "delete",
            wraps=mock_factory._backup_provider.delete,
        ) as delete_spy,
    ):
        core._cleanup_backups(vm, target, [full_info, inc1_info], retention)

    # FULL should be deleted (per-chain, M1 passed)
    deleted_names = [call.args[0].name for call in delete_spy.call_args_list]
    assert full_name in deleted_names, f"FULL {full_name} should be deleted"
    # Incremental also deleted (in remove list via per-chain retention)
    assert "inc1.qcow2" in deleted_names, (
        "Incremental inc1.qcow2 should be deleted via per-chain cleanup"
    )
    assert delete_spy.call_count == 2, (
        f"FULL + 1 dependent = 2 deletions, got {delete_spy.call_count}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Phantom FULL detection in _backup_target()
# ═══════════════════════════════════════════════════════════════════════════

# ── test_phantom_full_detected_removed_from_state ─────────────────────────


def test_phantom_full_detected_removed_from_state(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """FULL in state but file doesn't exist on disk → cascaded cleanup (design D2)."""
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # Record a phantom FULL in state — file does not exist on disk
    phantom_full_name = "testvm.FULL.monthly.qcow2"
    mock_state.record_full_backup(
        str(target.path),
        phantom_full_name,
        datetime(2025, 7, 1),
        "vda",
    )

    # Record incremental dependencies for cascade cleanup verification
    mock_state.record_incremental_dependency(str(target.path), "inc1.qcow2", phantom_full_name)
    mock_state.record_incremental_dependency(str(target.path), "inc2.qcow2", phantom_full_name)

    # Set a last_backup_allocation to verify it gets cleared when no FULLs remain
    mock_state.set_last_backup_allocation(str(target.path), "vda", 1048576)

    with (
        patch.object(
            mock_state,
            "remove_full_backup",
            wraps=mock_state.remove_full_backup,
        ) as remove_spy,
        patch.object(
            mock_state,
            "remove_all_incremental_dependencies",
            wraps=mock_state.remove_all_incremental_dependencies,
        ) as cascade_spy,
        patch.object(
            mock_state,
            "clear_last_backup_allocation",
            wraps=mock_state.clear_last_backup_allocation,
        ) as clear_baseline_spy,
        patch("qsnap.core.os.path.exists", return_value=False),
        patch("qsnap.core.verify_full_backup", return_value=None),
    ):
        core._backup_target(vm, target, [snap])

    assert remove_spy.called, "remove_full_backup should be called for phantom FULL"
    assert remove_spy.call_args[0] == (str(target.path), phantom_full_name), (
        f"remove_full_backup called incorrectly: {remove_spy.call_args[0]}"
    )

    # Cascade cleanup: remove_all_incremental_dependencies called for the phantom FULL
    assert cascade_spy.called, (
        "remove_all_incremental_dependencies should be called (cascade cleanup)"
    )
    assert cascade_spy.call_args[0] == (str(target.path), phantom_full_name), (
        f"remove_all_incremental_dependencies called for wrong FULL: {cascade_spy.call_args[0]}"
    )

    # When the last/only FULL is removed, clear_last_backup_allocation is called
    assert clear_baseline_spy.called, (
        "clear_last_backup_allocation should be called when no FULLs remain"
    )
    assert clear_baseline_spy.call_args[0] == (str(target.path), "vda"), (
        f"clear_last_backup_allocation called with wrong target: {clear_baseline_spy.call_args[0]}"
    )


# ── test_all_fulls_exist_no_phantom_cleanup ───────────────────────────────


def test_all_fulls_exist_no_phantom_cleanup(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """All FULLs exist on disk → no phantom cleanup, remove_full_backup() NOT called."""
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(target_chain_length=5)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # Record a FULL in the same daily period as the snapshot (2025-07-13)
    # so _should_create_bucket_full returns False — no new FULL created.
    full_name = "testvm.FULL.daily.qcow2"
    mock_state.record_full_backup(
        str(target.path),
        full_name,
        datetime(2025, 7, 13, 8, 0),
        "vda",
    )

    # Record incremental dependencies to verify they are NOT cascade-cleaned
    mock_state.record_incremental_dependency(str(target.path), "inc1.qcow2", full_name)

    with (
        patch.object(
            mock_state,
            "remove_full_backup",
            wraps=mock_state.remove_full_backup,
        ) as remove_spy,
        patch.object(
            mock_state,
            "remove_all_incremental_dependencies",
            wraps=mock_state.remove_all_incremental_dependencies,
        ) as cascade_spy,
        patch("qsnap.core.os.path.exists", return_value=True),
    ):
        core._backup_target(vm, target, [snap])

    assert not remove_spy.called, (
        "remove_full_backup should NOT be called when all FULLs exist on disk"
    )
    assert not cascade_spy.called, (
        "remove_all_incremental_dependencies should NOT be called when all FULLs exist on disk"
    )


# ═══════════════════════════════════════════════════════════════════════════
# State cleanup after deletion in _cleanup_backups()
# ═══════════════════════════════════════════════════════════════════════════

# ── test_full_deleted_fullbackupinfo_removed_from_state ────────────────────


def test_full_deleted_fullbackupinfo_removed_from_state(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """FULL deleted → remove_full_backup() called with correct args."""
    target = make_target()
    full_name = "full.FULL.daily.qcow2"

    core, vm, target = _setup_cleanup_backups_context(
        mock_state,
        target,
        make_vm_config,
        make_global_config,
        mock_factory,
        mock_shell,
        full_verify_before_delete="metadata",  # M2 skipped — only M1 runs
        full_name=full_name,
        dep_names=[],
    )

    from qsnap.models.results import SnapshotInfo as SI

    full_info = SI(
        name=full_name,
        path=target.path / full_name,
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=0,
        disk="vda",
    )

    retention = RetentionResult(keep=[], remove=[full_name])

    with (
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch.object(
            mock_state,
            "remove_full_backup",
            wraps=mock_state.remove_full_backup,
        ) as remove_spy,
    ):
        core._cleanup_backups(vm, target, [full_info], retention)

    assert remove_spy.called, "remove_full_backup should be called after FULL is deleted"
    assert remove_spy.call_args[0] == (str(target.path), full_name), (
        f"remove_full_backup called with wrong args: {remove_spy.call_args[0]}"
    )


# ── test_incremental_deleted_dependency_removed_from_state ─────────────────


def test_incremental_deleted_dependency_removed_from_state(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Incremental deleted via per-chain cleanup → remove_incremental_dependency() called."""
    target = make_target()
    full_name = "full.FULL.daily.qcow2"
    dep_names = ["inc1.qcow2"]

    core, vm, target = _setup_cleanup_backups_context(
        mock_state,
        target,
        make_vm_config,
        make_global_config,
        mock_factory,
        mock_shell,
        full_verify_before_delete="metadata",  # M2 skipped
        full_name=full_name,
        dep_names=dep_names,
    )

    from qsnap.models.results import SnapshotInfo as SI

    full_info = SI(
        name=full_name,
        path=target.path / full_name,
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=0,
        disk="vda",
    )
    inc1_info = SI(
        name="inc1.qcow2",
        path=target.path / "inc1.qcow2",
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=0,
        disk="vda",
    )

    # FULL + inc in remove set via per-chain evaluation
    retention = RetentionResult(keep=[], remove=[full_name, "inc1.qcow2"])

    # Mock _resolve_chain_full_anchor: qemu-img info on inc1 returns FULL backing.
    mock_shell.expect_first(r"qemu-img info.*--output=json.*inc1\.qcow2").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1000,
                    "backing-filename": str(target.path / full_name),
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch.object(
            mock_state,
            "remove_incremental_dependency",
            wraps=mock_state.remove_incremental_dependency,
        ) as remove_spy,
    ):
        core._cleanup_backups(vm, target, [full_info, inc1_info], retention)

    assert remove_spy.called, (
        "remove_incremental_dependency should be called for per-chain-deleted incremental"
    )
    assert remove_spy.call_args[0] == (str(target.path), "inc1.qcow2", Path(full_name).stem), (
        f"remove_incremental_dependency called with wrong args: {remove_spy.call_args[0]}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# source_path passed to verify_full_backup in _backup_target()
# ═══════════════════════════════════════════════════════════════════════════

# ── test_hash_mode_passes_source_path_to_verify ────────────────────────────


def test_hash_mode_passes_source_path_to_verify(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Compare mode passes source_path=most_recent.path to verify_full_backup."""
    global_cfg = make_global_config(full_verify_after_create="compare")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # No prior FULLs → first backup triggers FULL creation (count-based).

    with patch("qsnap.core.verify_full_backup", return_value=None) as verify_spy:
        core._backup_target(vm, target, [snap])

    assert verify_spy.called, "verify_full_backup should be called"
    kwargs = verify_spy.call_args[1]
    assert "source_path" in kwargs, (
        "source_path keyword argument should be passed to verify_full_backup"
    )
    assert kwargs["source_path"] == snap.path, (
        f"source_path should be {snap.path}, got {kwargs['source_path']}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Verify-before-delete gate — count-based FULL pipeline
# ═══════════════════════════════════════════════════════════════════════════

# ── test_verified_full_triggers_retention_and_cleanup ─────────────────────


def test_verified_full_triggers_retention_and_cleanup(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """FULL passes M1/M2 verification — record_full_backup + retention + cleanup run.

    Count-based: no prior FULLs so first backup creates FULL.  Verification
    passes → state recording + backup retention evaluated.
    """
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # No prior FULLs → first backup triggers FULL creation (count-based).
    with (
        patch("qsnap.core.verify_full_backup", return_value=None) as verify_spy,
        patch.object(
            mock_state,
            "record_full_backup",
            wraps=mock_state.record_full_backup,
        ) as record_spy,
    ):
        core._backup_target(vm, target, [snap])

    # Verification was called.
    assert verify_spy.called, "verify_full_backup should be called"
    # FULL was recorded after verification passed.
    assert record_spy.called, "record_full_backup should be called after verification passes"
    # At least one FULL is now recorded.
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) >= 1, "FULL should be recorded after successful verification"


# ── test_failed_full_verification_triggers_rollback ───────────────────────


def test_failed_full_verification_triggers_rollback(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """FULL fails M1/M2 verification — rollback: delete file + checkpoint + state.

    When verify_full_backup returns an error, Core must:
    1. Remove the FULL file via rm -f
    2. Call _cleanup_failed_checkpoint (exact-name checkpoint deletion)
    3. Remove the FULL from state
    4. Log a WARNING and retry
    """
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(backup_retry_max=1)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # Pre-configure rm -f to succeed (rollback file deletion).
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Pre-configure checkpoint deletion to succeed (rollback checkpoint cleanup).
    mock_shell.expect("virsh checkpoint-delete").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.WARNING)

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: FULL backup has corrupt bit set — file is damaged",
        ),
        patch.object(
            mock_factory._bitmap_backup_provider,
            "create_full_backup",
            return_value=BackupResult(
                success=True,
                snapshot_name="snap1",
                source_path=Path("/tmp/snap1.qcow2"),
                target_path=target.path / "testvm.FULL.qcow2",
                bytes_transferred=1048576,
                error=None,
                disk="vda",
                checkpoint="qsnap-ab12cd34-vda-20260807T020000-9f8e7d",
            ),
        ),
        patch.object(
            mock_state,
            "remove_full_backup",
            wraps=mock_state.remove_full_backup,
        ) as remove_spy,
        patch.object(
            core,
            "_cleanup_failed_checkpoint",
            wraps=core._cleanup_failed_checkpoint,
        ) as checkpoint_spy,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    # Rollback: FULL removed from state.
    assert remove_spy.called, "remove_full_backup should be called on verification failure"
    # Checkpoint cleanup was called.
    assert checkpoint_spy.called, "_cleanup_failed_checkpoint should be called on rollback"
    # Exactly one exact-name checkpoint-delete call (design D1 — no bulk filter).
    expected_cmd = (
        "virsh checkpoint-delete --metadata --domain testvm "
        "qsnap-ab12cd34-vda-20260807T020000-9f8e7d"
    )
    checkpoint_delete_calls = [
        c
        for c in shell_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and " ".join(c.args[0]) == expected_cmd
    ]
    assert len(checkpoint_delete_calls) == 1, (
        "exactly one exact-name virsh checkpoint-delete call expected, got "
        f"{len(checkpoint_delete_calls)}: "
        f"{[c for c in shell_spy.call_args_list if c.args and isinstance(c.args[0], list) and 'checkpoint-delete' in ' '.join(c.args[0])]}"
    )
    # WARNING logged.
    assert "rolled back" in caplog.text or "FULL backup verification failed" in caplog.text


# ── test_cleanup_failed_checkpoint_deletes_exact_checkpoint_name ──────────


@pytest.mark.unit
@pytest.mark.mock
def test_cleanup_failed_checkpoint_deletes_exact_checkpoint_name(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Rollback deletes exactly the failed FULL's checkpoint by exact name.

    Core-orchestrator scenario "Checkpoint cleaned up after failed FULL":
    when FULL verification fails, ``_cleanup_failed_checkpoint`` issues
    exactly one ``virsh checkpoint-delete --metadata --domain testvm``
    call for ``BackupResult.checkpoint`` — never a
    ``qsnap-{target_hash}-*`` bulk filter.
    """
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(backup_retry_max=1)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # Pre-configure rollback shell commands to succeed.
    mock_shell.expect("rm -f").returns(_OK)
    mock_shell.expect("virsh checkpoint-delete").returns(_OK)

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: FULL backup has corrupt bit set — file is damaged",
        ),
        patch.object(
            mock_factory._bitmap_backup_provider,
            "create_full_backup",
            return_value=BackupResult(
                success=True,
                snapshot_name="snap1",
                source_path=Path("/tmp/snap1.qcow2"),
                target_path=target.path / "testvm.FULL.qcow2",
                bytes_transferred=1048576,
                error=None,
                disk="vda",
                checkpoint="qsnap-ab12cd34-vda-20260807T020000-9f8e7d",
            ),
        ),
        patch.object(
            mock_state,
            "remove_full_backup",
            wraps=mock_state.remove_full_backup,
        ) as remove_spy,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    # Exactly ONE exact-name checkpoint-delete call — no bulk filter.
    expected_cmd = (
        "virsh checkpoint-delete --metadata --domain testvm "
        "qsnap-ab12cd34-vda-20260807T020000-9f8e7d"
    )
    checkpoint_delete_calls = [
        c
        for c in shell_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and " ".join(c.args[0]) == expected_cmd
    ]
    assert len(checkpoint_delete_calls) == 1, (
        "exactly one exact-name checkpoint-delete call expected, got "
        f"{len(checkpoint_delete_calls)}: "
        f"{[c for c in shell_spy.call_args_list if c.args and isinstance(c.args[0], list) and 'checkpoint-delete' in ' '.join(c.args[0])]}"
    )
    # FULL file cleanup from state is separate and still happens.
    assert remove_spy.called, "remove_full_backup should be called during rollback"


# ── test_cleanup_failed_checkpoint_multi_disk_preserves_other_disks ───────


@pytest.mark.unit
@pytest.mark.mock
def test_cleanup_failed_checkpoint_multi_disk_preserves_other_disks(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Multi-disk rollback deletes only the failed disk's checkpoint.

    Core-orchestrator scenario "Multi-disk rollback leaves other disks
    untouched": a vda FULL verification failure must delete only the vda
    checkpoint — never the vdb/vdc checkpoints (regression against the
    old ``qsnap-{target_hash}-*`` bulk filter that wiped every disk's
    checkpoints for the target).
    """
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(backup_retry_max=1)
    vm = make_vm_config(name="testvm", targets=[target], disks=["vda", "vdb", "vdc"])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Every disk has a snapshot so each would need a FULL of its own.
    for disk in ["vda", "vdb", "vdc"]:
        snap = SnapshotInfo(
            name=f"snap-{disk}",
            path=Path(f"/tmp/snap-{disk}.qcow2"),
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=1000,
            disk=disk,
        )
        mock_state.record_snapshot("testvm", snap)

    def full_side_effect(vm_name, source_snapshot, target_cfg, **kwargs):
        """Per-disk checkpoint naming so vdb/vdc names are distinguishable."""
        return BackupResult(
            success=True,
            snapshot_name=source_snapshot.name,
            source_path=source_snapshot.path,
            target_path=target_cfg.path / f"{vm_name}.FULL.{source_snapshot.disk}.qcow2",
            bytes_transferred=1048576,
            error=None,
            disk=source_snapshot.disk,
            checkpoint=f"qsnap-ab12cd34-{source_snapshot.disk}-20260807T020000-9f8e7d",
        )

    def verify_side_effect(shell, target_path, verify_mode, **kwargs):
        """FULL verification fails for vda only — the other disks pass."""
        source = kwargs.get("source_path")
        if source is not None and source.name == "snap-vda.qcow2":
            return "verification failed: FULL backup has corrupt bit set — file is damaged"
        return None

    mock_shell.expect("rm -f").returns(_OK)
    mock_shell.expect("virsh checkpoint-delete").returns(_OK)

    with (
        patch.object(
            mock_factory._bitmap_backup_provider,
            "create_full_backup",
            side_effect=full_side_effect,
        ),
        patch("qsnap.core.verify_full_backup", side_effect=verify_side_effect),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, mock_state.get_snapshots("testvm"))

    checkpoint_delete_calls = [
        " ".join(c.args[0])
        for c in shell_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and "checkpoint-delete" in " ".join(c.args[0])
    ]
    # Only the failed vda checkpoint is targeted — exactly one call.
    assert checkpoint_delete_calls == [
        "virsh checkpoint-delete --metadata --domain testvm "
        "qsnap-ab12cd34-vda-20260807T020000-9f8e7d"
    ], f"only the vda checkpoint should be deleted, got: {checkpoint_delete_calls}"
    # No vdb/vdc checkpoint-delete call may exist.
    assert not any("vdb" in call or "vdc" in call for call in checkpoint_delete_calls), (
        f"vdb/vdc checkpoints must be left untouched, got: {checkpoint_delete_calls}"
    )


# ── test_cleanup_failed_checkpoint_preserves_previous_baseline ────────────


@pytest.mark.unit
@pytest.mark.mock
def test_cleanup_failed_checkpoint_preserves_previous_baseline(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Rollback preserves the previous baseline checkpoint of the failed disk.

    Core-orchestrator scenario "Previous baseline of the failed disk is
    preserved": a new FULL attempt for vda fails verification, and only
    the successor checkpoint (from ``BackupResult.checkpoint``) is
    deleted — the pre-existing baseline checkpoint is never targeted.
    """
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(backup_retry_max=1)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # Previous baseline of the failed disk, recorded from a prior
    # successful transfer.  Its checkpoint must survive the rollback.
    baseline_checkpoint = "qsnap-ab12cd34-vda-20260701T010000-a1b2c3"
    mock_state.record_full_backup(
        str(target.path),
        f"{baseline_checkpoint}.qcow2",
        datetime(2026, 7, 1),
        "vda",
    )
    # Force a new FULL attempt despite the existing baseline.
    core._force_full_targets.add(str(target.path))

    mock_shell.expect("rm -f").returns(_OK)
    mock_shell.expect("virsh checkpoint-delete").returns(_OK)

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: FULL backup has corrupt bit set — file is damaged",
        ),
        patch.object(
            mock_factory._bitmap_backup_provider,
            "create_full_backup",
            return_value=BackupResult(
                success=True,
                snapshot_name="snap1",
                source_path=Path("/tmp/snap1.qcow2"),
                target_path=target.path / "testvm.FULL.qcow2",
                bytes_transferred=1048576,
                error=None,
                disk="vda",
                checkpoint="qsnap-ab12cd34-vda-20260807T020000-9f8e7d",
            ),
        ),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    checkpoint_delete_calls = [
        " ".join(c.args[0])
        for c in shell_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and "checkpoint-delete" in " ".join(c.args[0])
    ]
    # Only the successor checkpoint is targeted.
    assert checkpoint_delete_calls == [
        "virsh checkpoint-delete --metadata --domain testvm "
        "qsnap-ab12cd34-vda-20260807T020000-9f8e7d"
    ], f"only the successor checkpoint should be deleted, got: {checkpoint_delete_calls}"
    # The previous baseline is never mentioned in any checkpoint-delete call.
    assert not any(baseline_checkpoint in call for call in checkpoint_delete_calls), (
        f"previous baseline {baseline_checkpoint} must be preserved, got: {checkpoint_delete_calls}"
    )


# ── test_cleanup_failed_checkpoint_none_deletes_nothing ───────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_cleanup_failed_checkpoint_none_deletes_nothing(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Stopped-VM FULL failure (checkpoint=None) deletes no checkpoint.

    Core-orchestrator scenario "Stopped-VM FULL failure deletes nothing":
    when the failed FULL created no checkpoint, the rollback issues no
    ``virsh checkpoint-delete`` call — but FULL state cleanup via
    ``remove_full_backup`` still happens (file cleanup is separate from
    checkpoint cleanup).
    """
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(backup_retry_max=1)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # No "virsh checkpoint-delete" expectation on purpose: the mock shell
    # would still record the call (as a failure) if Core wrongly issued one.
    mock_shell.expect("rm -f").returns(_OK)

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: FULL backup has corrupt bit set — file is damaged",
        ),
        patch.object(
            mock_factory._bitmap_backup_provider,
            "create_full_backup",
            return_value=BackupResult(
                success=True,
                snapshot_name="snap1",
                source_path=Path("/tmp/snap1.qcow2"),
                target_path=target.path / "testvm.FULL.qcow2",
                bytes_transferred=1048576,
                error=None,
                disk="vda",
                checkpoint=None,
            ),
        ),
        patch.object(
            mock_state,
            "remove_full_backup",
            wraps=mock_state.remove_full_backup,
        ) as remove_spy,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    checkpoint_delete_calls = [
        " ".join(c.args[0])
        for c in shell_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and "checkpoint-delete" in " ".join(c.args[0])
    ]
    assert checkpoint_delete_calls == [], (
        f"no checkpoint-delete call expected when checkpoint is None, got: "
        f"{checkpoint_delete_calls}"
    )
    assert remove_spy.called, (
        "remove_full_backup should still be called (FULL file cleanup is separate)"
    )


# ── test_cleanup_failed_checkpoint_delete_failure_non_fatal ───────────────


@pytest.mark.unit
@pytest.mark.mock
def test_cleanup_failed_checkpoint_delete_failure_non_fatal(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Checkpoint deletion failure during rollback is non-fatal.

    Core-orchestrator scenario "Checkpoint deletion failure is
    non-fatal": a failing ``virsh checkpoint-delete`` logs a WARNING and
    the rollback continues — FULL file removal and state cleanup still
    complete (no exception raised from ``_cleanup_failed_checkpoint``).
    """
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(backup_retry_max=1)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    mock_shell.expect("rm -f").returns(_OK)
    mock_shell.expect("virsh checkpoint-delete").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="",
            returncode=1,
            error="checkpoint not found",
        )
    )

    caplog.set_level(logging.WARNING)

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: FULL backup has corrupt bit set — file is damaged",
        ),
        patch.object(
            mock_factory._bitmap_backup_provider,
            "create_full_backup",
            return_value=BackupResult(
                success=True,
                snapshot_name="snap1",
                source_path=Path("/tmp/snap1.qcow2"),
                target_path=target.path / "testvm.FULL.qcow2",
                bytes_transferred=1048576,
                error=None,
                disk="vda",
                checkpoint="qsnap-deadc0de-vda-20260807T020000-111111",
            ),
        ),
        patch.object(
            mock_state,
            "remove_full_backup",
            wraps=mock_state.remove_full_backup,
        ) as remove_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    # WARNING logged for the failed checkpoint deletion.
    assert "failed to delete checkpoint" in caplog.text, (
        "WARNING should be logged when checkpoint deletion fails"
    )
    # Rollback continued past the checkpoint-deletion failure.
    assert remove_spy.called, (
        "remove_full_backup should still be called after checkpoint-delete failure"
    )


# ── test_retries_exhausted_keeps_old_generations ──────────────────────────


def test_retries_exhausted_keeps_old_generations(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """All FULL backup retries exhausted — old generations preserved, CRITICAL log,
    and the VM pipeline aborts (VM-level isolation).

    When every retry attempt fails to create+verify a FULL, Core emits a
    CRITICAL log stating old generations are preserved and raises
    ``BackupAbortError``.  The abort itself is the verify-before-delete
    gate: retention evaluation and cleanup are never reached, so old
    generations are never deleted.
    """
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(
        backup_retry_max=2,
        backup_retry_base="0s",
    )
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # Pre-configure rm -f to succeed (rollback file deletion for every attempt).
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.CRITICAL)

    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: content comparison mismatch",
        ),
        patch.object(
            core,
            "_evaluate_backup_retention",
            wraps=core._evaluate_backup_retention,
        ) as retention_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    # Retention was NOT evaluated (the abort precedes retention/cleanup).
    assert not retention_spy.called, (
        "Retention should NOT be evaluated when all FULL retries are exhausted"
    )
    # CRITICAL log emitted about preserving old generations.
    assert "old generations preserved" in caplog.text.lower()


# ═══════════════════════════════════════════════════════════════════════════
# FULL backup creation retry via _execute_with_retry
# ═══════════════════════════════════════════════════════════════════════════


# ── test_full_backup_creation_retried_transient ──────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_full_backup_creation_retried_transient(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """FULL creation fails with transient ``"Connection refused"`` on attempt 1,
    retried via ``_execute_with_retry``, succeeds on attempt 2.  Verification
    passes and the FULL is recorded in state.

    This test verifies that ``_backup_target()`` delegates FULL creation
    retry to ``_execute_with_retry()`` with ``backup_retry_max=3``.
    """
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(backup_retry_max=3, backup_retry_base="0s")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # No prior FULLs → first backup triggers FULL creation (count-based).
    full_calls = 0

    def full_side_effect(*args, **kwargs):
        nonlocal full_calls
        full_calls += 1
        if full_calls == 1:
            return BackupResult(
                success=False,
                snapshot_name="snap1",
                source_path=Path("/tmp/snap1.qcow2"),
                target_path=target.path / "testvm.FULL.qcow2",
                bytes_transferred=0,
                error="Connection refused",
            )
        return BackupResult(
            success=True,
            snapshot_name="snap1",
            source_path=Path("/tmp/snap1.qcow2"),
            target_path=target.path / "testvm.FULL.qcow2",
            bytes_transferred=1048576,
            error=None,
        )

    with (
        patch.object(
            mock_factory._backup_provider,
            "create_full_backup",
            side_effect=full_side_effect,
        ),
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch("time.sleep"),
        patch.object(
            mock_state,
            "record_full_backup",
            wraps=mock_state.record_full_backup,
        ) as record_spy,
    ):
        core._backup_target(vm, target, [snap])

    assert full_calls == 2, (
        f"create_full_backup should be called twice (retried after transient error), "
        f"got {full_calls}"
    )
    # FULL was recorded after successful retry + verification.
    assert record_spy.called, "record_full_backup should be called after verify passes"

    # Verify FULL was recorded
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 1, "One FULL should be recorded after successful retry"


# ── test_full_backup_creation_not_retried_no_space ───────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_full_backup_creation_not_retried_no_space(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """FULL creation fails with ``"No space left on device"`` → NOT retried.

    ``is_retryable("No space left on device")`` returns ``False``, so
    ``_execute_with_retry`` returns the failure immediately without entering
    the retry loop.  Under the ENOSPC hardening, a space-classified failure
    SUSPENDS the target (design D2) instead of raising ``BackupAbortError``:
    no FULL is recorded and the target is flagged space-limited.
    """
    import logging

    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(backup_retry_max=3, backup_retry_base="0s")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # No prior FULLs → first backup triggers FULL creation (count-based).
    full_calls = 0

    def full_side_effect(*args, **kwargs):
        nonlocal full_calls
        full_calls += 1
        return BackupResult(
            success=False,
            snapshot_name="snap1",
            source_path=Path("/tmp/snap1.qcow2"),
            target_path=target.path / "testvm.FULL.qcow2",
            bytes_transferred=0,
            error="No space left on device",
        )

    caplog.set_level(logging.CRITICAL)

    with (
        patch.object(
            mock_factory._backup_provider,
            "create_full_backup",
            side_effect=full_side_effect,
        ),
        patch("time.sleep"),
        patch.object(
            mock_state,
            "record_full_backup",
            wraps=mock_state.record_full_backup,
        ) as record_spy,
    ):
        # Space errors suspend the target — they must NOT raise.
        core._backup_target(vm, target, [snap])

    # create_full_backup called exactly once — no retry
    assert full_calls == 1, (
        f"create_full_backup should be called exactly once (non-retryable), got {full_calls}"
    )
    # FULL was NOT recorded in state
    assert not record_spy.called, "record_full_backup should NOT be called when FULL fails"
    # Target suspended and flagged space-limited (drives EXIT_DISKFULL).
    assert str(target.path) in core._space_limited_targets
    # CRITICAL log about suspending the target
    critical_logs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical_logs, "CRITICAL log should be emitted"
    assert "suspending target" in critical_logs[0].message.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Verified FULL triggers retention + cleanup (verify-before-delete gate)
# ═══════════════════════════════════════════════════════════════════════════


def test_verified_full_triggers_retention(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """FULL passes M1/M2 verification → retention evaluation + cleanup triggered.

    After verification succeeds, Core must:
    1. Record the FULL in state via record_full_backup()
    2. Evaluate backup retention via _evaluate_backup_retention()
    3. Trigger cleanup of old generations via _cleanup_backups()
    """
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # No prior FULLs → first backup triggers FULL creation (count-based).
    with (
        patch("qsnap.core.verify_full_backup", return_value=None) as verify_spy,
        patch.object(
            mock_state,
            "record_full_backup",
            wraps=mock_state.record_full_backup,
        ) as record_spy,
        patch.object(
            core,
            "_evaluate_backup_retention",
            wraps=core._evaluate_backup_retention,
        ) as retention_spy,
        patch.object(
            core,
            "_cleanup_backups",
            wraps=core._cleanup_backups,
        ) as cleanup_spy,
    ):
        core._backup_target(vm, target, [snap])

    # Verification was called.
    assert verify_spy.called, "verify_full_backup should be called"
    # FULL was recorded after verification passed.
    assert record_spy.called, "record_full_backup should be called after verification passes"
    # Retention was evaluated because full_verification_failed is False.
    assert retention_spy.called, (
        "_evaluate_backup_retention should be called after successful FULL verification"
    )
    # Cleanup was triggered.
    assert cleanup_spy.called, "_cleanup_backups should be called after retention evaluation"
