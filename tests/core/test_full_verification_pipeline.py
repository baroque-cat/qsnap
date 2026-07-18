"""Tests for FULL backup verification at M1/M2/M3 lifecycle points in Core orchestration.

Covers:
- Post-creation verification (``_backup_target``): M1 (metadata / corrupt-bit),
  M2 (qemu-img check), M3 (SHA-256 hash comparison).
- Pre-deletion verification (``_cleanup_backups``): M1 always enforced,
  M2 configurable, cascade-deletion blocking.
- Timing and state-recording guarantees.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qsnap.core import Core
from qsnap.models.results import (
    RetentionResult,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import MockBucketFullStrategy, MockConfigFacade

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
        content_hash="a" * 64,
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
        str(target_cfg.path), full_name, datetime(2025, 7, 13, 8, 0), "daily"
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
    """When full_verify_after_create="hash", verify_full_backup is called
    with expected_hash from the source snapshot's content_hash."""
    global_cfg = make_global_config(full_verify_after_create="hash")
    target = make_target(target_preserve="7d")
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
        content_hash="abcdef1234567890",
    )
    mock_state.record_snapshot("testvm", snap)

    # Configure strategy to trigger FULL creation
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "daily"))

    with patch("qsnap.core.verify_full_backup", return_value=None) as verify_spy:
        core._backup_target(vm, target, [snap])

    assert verify_spy.called, "verify_full_backup should be called"
    assert verify_spy.call_args[0][2] == "hash", "verify_mode should be 'hash'"
    assert "source_path" in verify_spy.call_args[1], "source_path should be passed for hash mode"
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
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # Configure strategy to trigger FULL creation
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "daily"))

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
    """M1 fails with corrupt bit, FULL file deleted, record_full_backup NOT called."""
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # Configure strategy to trigger FULL creation
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "daily"))

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
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # Configure strategy to trigger FULL creation
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "daily"))

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
    """M1 at pre-deletion passes, FULL and dependents cascade-deleted."""
    target = make_target(target_preserve="7d")
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
    )
    inc1_info = SI(
        name="inc1.qcow2",
        path=target.path / "inc1.qcow2",
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=0,
    )
    inc2_info = SI(
        name="inc2.qcow2",
        path=target.path / "inc2.qcow2",
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=0,
    )

    # Only FULL is in remove set; inc1/inc2 will be cascade-deleted
    # when the FULL is processed (they're dependents not in keep-set).
    retention = RetentionResult(
        keep=[],  # nothing kept — all should be removed
        remove=[full_name],
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


# ── test_cleanup_backups_m1_fails_cascade_blocked ────────────────────────


def test_cleanup_backups_m1_fails_cascade_blocked(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """M1 fails at pre-deletion, cascade completely blocked, CRITICAL logged."""
    target = make_target(target_preserve="7d")
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

    # delete() should NOT be called — cascade is blocked
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
    target = make_target(target_preserve="7d")
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
    target = make_target(target_preserve="7d")
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
    """Hash mode, hash matches, record_full_backup called."""
    global_cfg = make_global_config(full_verify_after_create="hash")
    target = make_target(target_preserve="7d")
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
        content_hash="match_me_hash_12345",
    )
    mock_state.record_snapshot("testvm", snap)

    # Configure strategy to trigger FULL creation
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "daily"))

    with patch("qsnap.core.verify_full_backup", return_value=None) as verify_spy:
        core._backup_target(vm, target, [snap])

    assert verify_spy.called
    assert verify_spy.call_args[0][2] == "hash", "verify_mode should be 'hash'"
    assert "source_path" in verify_spy.call_args[1], "source_path should be passed for hash mode"
    assert verify_spy.call_args[1]["source_path"] == snap.path, (
        "source_path should be the source snapshot's path"
    )
    # FULL should be recorded
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 1, "FULL should be recorded after hash verification passes"


# ── test_full_verify_hash_mismatch_fails ─────────────────────────────────


def test_full_verify_hash_mismatch_fails(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Hash mode, hash mismatch, FULL deleted, NOT recorded."""
    global_cfg = make_global_config(full_verify_after_create="hash")
    target = make_target(target_preserve="7d")
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
        content_hash="expected_hash_value",
    )
    mock_state.record_snapshot("testvm", snap)

    # Configure strategy to trigger FULL creation
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "daily"))

    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with patch(
        "qsnap.core.verify_full_backup",
        return_value="verification failed: hash mismatch",
    ):
        core._backup_target(vm, target, [snap])

    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 0, "FULL should NOT be recorded after hash mismatch"


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
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # Configure strategy to trigger FULL creation
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "daily"))

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
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # Configure strategy to trigger FULL creation
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "daily"))

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
    ):
        result = core._backup_target(vm, target, [snap])

    assert not record_spy.called, "record_full_backup should NOT be called"
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 0
    assert result is True, "backup_failed should be True"


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
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    # Configure strategy to trigger FULL creation
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "daily"))

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
    target = make_target(target_preserve="4w 7d")
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
        "weekly",
    )

    # Snapshot in new weekly period (W28 — July 13, 2025)
    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    # Configure strategy to trigger FULL creation
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "weekly"))

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
    target = make_target(target_preserve="7d")
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
    target = make_target(target_preserve="7d")
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


# ── test_cascade_deletion_blocked_on_corrupt_full ─────────────────────────


def test_cascade_deletion_blocked_on_corrupt_full(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Cascade deletion blocked when FULL is corrupt — dependents preserved."""
    target = make_target(target_preserve="7d")
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
    )

    # All items in remove set — but cascade should be blocked
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

    # Nothing should be deleted — cascade is blocked
    assert not delete_spy.called, (
        "Cascade deletion should be completely blocked when FULL is corrupt"
    )
    # CRITICAL log should mention blocking deletion
    critical_logs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical_logs, "CRITICAL log expected"
    assert any("blocking" in r.message.lower() for r in critical_logs), (
        "CRITICAL log should mention blocking deletion"
    )


# ── test_orphaned_incrementals_cascade_deleted ────────────────────────────


def test_orphaned_incrementals_cascade_deleted(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Orphaned incrementals are cascade-deleted when M1 passes on FULL."""
    target = make_target(target_preserve="7d")
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
    )
    inc1_info = SI(
        name="inc1.qcow2",
        path=target.path / "inc1.qcow2",
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=0,
    )

    # Only FULL is explicitly in remove; incremental is implicitly orphaned
    retention = RetentionResult(keep=[], remove=[full_name])

    with (
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch.object(
            mock_factory._backup_provider,
            "delete",
            wraps=mock_factory._backup_provider.delete,
        ) as delete_spy,
    ):
        core._cleanup_backups(vm, target, [full_info, inc1_info], retention)

    # Both FULL and orphaned incremental should be deleted
    deleted_names = [call.args[0].name for call in delete_spy.call_args_list]
    assert full_name in deleted_names, f"FULL {full_name} should be deleted"
    assert "inc1.qcow2" in deleted_names, (
        "Orphaned incremental inc1.qcow2 should be cascade-deleted"
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
    """FULL in state but file doesn't exist on disk → remove_full_backup() called."""
    global_cfg = make_global_config(full_verify_after_create="check")
    target = make_target(target_preserve="7d")
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
        "monthly",
    )

    with (
        patch.object(
            mock_state,
            "remove_full_backup",
            wraps=mock_state.remove_full_backup,
        ) as remove_spy,
        patch("qsnap.core.os.path.exists", return_value=False),
        patch("qsnap.core.verify_full_backup", return_value=None),
    ):
        core._backup_target(vm, target, [snap])

    assert remove_spy.called, "remove_full_backup should be called for phantom FULL"
    assert remove_spy.call_args[0] == (str(target.path), phantom_full_name), (
        f"remove_full_backup called incorrectly: {remove_spy.call_args[0]}"
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
    target = make_target(target_preserve="7d")
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
        "daily",
    )

    with (
        patch.object(
            mock_state,
            "remove_full_backup",
            wraps=mock_state.remove_full_backup,
        ) as remove_spy,
        patch("qsnap.core.os.path.exists", return_value=True),
    ):
        core._backup_target(vm, target, [snap])

    assert not remove_spy.called, (
        "remove_full_backup should NOT be called when all FULLs exist on disk"
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
    target = make_target(target_preserve="7d")
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
    """Incremental cascade-deleted → remove_incremental_dependency() called."""
    target = make_target(target_preserve="7d")
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
    )
    inc1_info = SI(
        name="inc1.qcow2",
        path=target.path / "inc1.qcow2",
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=0,
    )

    # Only FULL is in remove set; inc1 is implicitly orphaned (cascade)
    retention = RetentionResult(keep=[], remove=[full_name])

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
        "remove_incremental_dependency should be called for cascade-deleted incremental"
    )
    assert remove_spy.call_args[0] == (str(target.path), "inc1.qcow2", full_name), (
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
    """Hash mode passes source_path=most_recent.path to verify_full_backup."""
    global_cfg = make_global_config(full_verify_after_create="hash")
    target = make_target(target_preserve="7d")
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
        content_hash="some_hash",
    )
    mock_state.record_snapshot("testvm", snap)

    # Configure strategy to trigger FULL creation
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "daily"))

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
# full_verify_before_rebase threading tests
# ═══════════════════════════════════════════════════════════════════════════


def test_rebase_verify_metadata_mode(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core passes full_verify_before_rebase="metadata" from GlobalConfig to rebase path."""
    global_cfg = make_global_config(full_verify_before_rebase="metadata")
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    with patch.object(
        mock_factory._backup_provider,
        "transfer_missing",
        wraps=mock_factory._backup_provider.transfer_missing,
    ) as transfer_spy:
        core._backup_target(vm, target, [snap])

    assert transfer_spy.called, "transfer_missing should be called"
    assert transfer_spy.call_args.kwargs.get("full_verify_before_rebase") == "metadata", (
        f"full_verify_before_rebase should be 'metadata', "
        f"got {transfer_spy.call_args.kwargs.get('full_verify_before_rebase')}"
    )


def test_rebase_verify_off_mode(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core passes full_verify_before_rebase="off" from GlobalConfig to rebase path."""
    global_cfg = make_global_config(full_verify_before_rebase="off")
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    with patch.object(
        mock_factory._backup_provider,
        "transfer_missing",
        wraps=mock_factory._backup_provider.transfer_missing,
    ) as transfer_spy:
        core._backup_target(vm, target, [snap])

    assert transfer_spy.called, "transfer_missing should be called"
    assert transfer_spy.call_args.kwargs.get("full_verify_before_rebase") == "off", (
        f"full_verify_before_rebase should be 'off', "
        f"got {transfer_spy.call_args.kwargs.get('full_verify_before_rebase')}"
    )


def test_rebase_verify_check_mode(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core passes full_verify_before_rebase="check" from GlobalConfig to rebase path."""
    global_cfg = make_global_config(full_verify_before_rebase="check")
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    with patch.object(
        mock_factory._backup_provider,
        "transfer_missing",
        wraps=mock_factory._backup_provider.transfer_missing,
    ) as transfer_spy:
        core._backup_target(vm, target, [snap])

    assert transfer_spy.called, "transfer_missing should be called"
    assert transfer_spy.call_args.kwargs.get("full_verify_before_rebase") == "check", (
        f"full_verify_before_rebase should be 'check', "
        f"got {transfer_spy.call_args.kwargs.get('full_verify_before_rebase')}"
    )


def test_rebase_verify_mode_passed_as_parameter(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Verification mode is threaded through to the provider method call.

    Verifies that the ``full_verify_before_rebase`` value from global_config
    is passed as a keyword argument to the backup provider's ``transfer_missing()``.
    """
    global_cfg = make_global_config(full_verify_before_rebase="metadata")
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(target, vm, mock_state)

    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "daily"))

    with patch.object(
        mock_factory._backup_provider,
        "transfer_missing",
        wraps=mock_factory._backup_provider.transfer_missing,
    ) as transfer_spy:
        core._backup_target(vm, target, [snap])

    assert transfer_spy.called, "transfer_missing should be called"
    call_kwargs = transfer_spy.call_args.kwargs
    assert "full_verify_before_rebase" in call_kwargs, (
        "full_verify_before_rebase should be in transfer_missing kwargs, "
        f"got: {list(call_kwargs.keys())}"
    )
    assert call_kwargs["full_verify_before_rebase"] == "metadata", (
        f"full_verify_before_rebase should be 'metadata', "
        f"got: {call_kwargs['full_verify_before_rebase']}"
    )
