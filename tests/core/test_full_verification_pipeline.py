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

# ── test_full_backup_created_and_recorded ──────────────────────────────────


def test_full_backup_created_and_recorded(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A successful run_backup (verification passed inside the provider)
    leads to the FULL being recorded in state.

    Post-creation verification moved into ``IBackupProvider.run_backup``
    (orthogonal design D2) — Core no longer calls ``verify_full_backup``
    after creation.  A successful result means the provider already
    verified the FULL.
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
    with patch.object(
        mock_factory._backup_provider,
        "run_backup",
        wraps=mock_factory._backup_provider.run_backup,
    ) as run_spy:
        core._backup_target(vm, target, [snap])

    assert run_spy.called, "run_backup should be called"
    assert run_spy.call_args.kwargs["force_full"] is True, (
        "run_backup should be called with force_full=True on first backup"
    )
    # FULL should be recorded.
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 1, "FULL should be recorded after a successful run_backup"


# ── test_full_created_recorded_in_state ────────────────────────────────────


def test_full_created_recorded_in_state(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """run_backup succeeds → record_full_backup is called."""
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

    with patch.object(
        mock_state,
        "record_full_backup",
        wraps=mock_state.record_full_backup,
    ) as record_spy:
        core._backup_target(vm, target, [snap])

    assert record_spy.called, "record_full_backup should be called after a successful FULL"
    # Verify FULL was recorded
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 1, "One FULL backup should be recorded"


# ── test_full_created_m1_fails_corrupt_bit_not_recorded ───────────────────


def test_full_created_m1_fails_corrupt_bit_not_recorded(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """run_backup surfaces a corrupt-bit verification failure → FULL is NOT
    recorded and the VM pipeline aborts (VM-level isolation)."""
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

    failed = BackupResult(
        success=False,
        snapshot_name="",
        source_path=snap.path,
        target_path=target.path / "testvm.FULL.qcow2",
        bytes_transferred=0,
        error="verification failed: FULL backup has corrupt bit set — file is damaged",
        disk="vda",
        checkpoint="qsnap-ab12cd34-vda-20260807T020000-9f8e7d",
        kind="full",
    )

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            return_value=failed,
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


# ── test_full_created_m1_fails_not_qcow2_not_recorded ─────────────────────


def test_full_created_m1_fails_not_qcow2_not_recorded(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """run_backup surfaces a wrong-format verification failure → not recorded."""
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

    failed = BackupResult(
        success=False,
        snapshot_name="",
        source_path=snap.path,
        target_path=target.path / "testvm.FULL.qcow2",
        bytes_transferred=0,
        error="verification failed: expected format qcow2, got raw",
        disk="vda",
        kind="full",
    )

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            return_value=failed,
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
    """Compare mode, run_backup succeeds (provider verified content), FULL recorded."""
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

    with patch.object(
        mock_factory._backup_provider,
        "run_backup",
        wraps=mock_factory._backup_provider.run_backup,
    ) as run_spy:
        core._backup_target(vm, target, [snap])

    assert run_spy.called
    assert run_spy.call_args.kwargs["force_full"] is True, (
        "run_backup should be called with force_full=True"
    )
    # FULL should be recorded
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 1, "FULL should be recorded after run_backup succeeds"


# ── test_full_verify_content_comparison_mismatch_fails ──────────────────────


def test_full_verify_content_comparison_mismatch_fails(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Compare mode, content comparison mismatch surfaced by run_backup,
    FULL NOT recorded, VM pipeline aborts."""
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

    failed = BackupResult(
        success=False,
        snapshot_name="",
        source_path=snap.path,
        target_path=target.path / "testvm.FULL.qcow2",
        bytes_transferred=0,
        error="verification failed: content comparison mismatch",
        disk="vda",
        kind="full",
    )

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            return_value=failed,
        ),
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 0, "FULL should NOT be recorded after content comparison mismatch"


# ═══════════════════════════════════════════════════════════════════════════
# Timing / ordering guarantees
# ═══════════════════════════════════════════════════════════════════════════

# ── test_full_backup_recorded_after_run_backup_succeeds ────────────────────


def test_full_backup_recorded_after_run_backup_succeeds(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Timing check: record_full_backup happens only after run_backup succeeds."""
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

    def track_run(*args, **kwargs):
        call_order.append("run_backup")
        return BackupResult(
            success=True,
            snapshot_name="testvm.FULL.20250713T100000_vda_a1b2c3",
            source_path=snap.path,
            target_path=target.path / "testvm.FULL.20250713T100000_vda_a1b2c3.qcow2",
            bytes_transferred=1048576,
            error=None,
            disk="vda",
            kind="full",
        )

    orig_record = mock_state.record_full_backup

    def track_record(*args, **kwargs):
        call_order.append("record")
        return orig_record(*args, **kwargs)

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            side_effect=track_run,
        ),
        patch.object(mock_state, "record_full_backup", side_effect=track_record),
    ):
        core._backup_target(vm, target, [snap])

    assert call_order == ["run_backup", "record"], (
        f"run_backup must succeed BEFORE record_full_backup, got order: {call_order}"
    )


# ── test_full_backup_verify_fails_not_recorded ─────────────────────────────


def test_full_backup_verify_fails_not_recorded(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """run_backup fails verification, FULL not recorded."""
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

    failed = BackupResult(
        success=False,
        snapshot_name="",
        source_path=snap.path,
        target_path=target.path / "testvm.FULL.qcow2",
        bytes_transferred=0,
        error="verification failed: qemu-img check found 3 errors",
        disk="vda",
        kind="full",
    )

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            return_value=failed,
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
    """First backup to target creates FULL (via run_backup) and records it."""
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

    with patch.object(
        mock_factory._backup_provider,
        "run_backup",
        wraps=mock_factory._backup_provider.run_backup,
    ) as full_spy:
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "run_backup should be called on first backup"
    assert full_spy.call_args.kwargs["force_full"] is True, (
        "run_backup should be called with force_full=True on first backup"
    )
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
    """New weekly period triggers FULL (via run_backup) with recording."""
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
            "run_backup",
            wraps=mock_factory._backup_provider.run_backup,
        ) as full_spy,
        patch("qsnap.core.os.path.exists", return_value=True),
    ):
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "run_backup should be called for new weekly period"
    assert full_spy.call_args.kwargs["force_full"] is True, (
        "run_backup should be called with force_full=True when count exceeds chain_length"
    )
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
# FULL creation via run_backup in _backup_target()
# ═══════════════════════════════════════════════════════════════════════════

# ── test_compare_mode_creates_full_via_run_backup ──────────────────────────


def test_compare_mode_creates_full_via_run_backup(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Compare verify mode: Core still drives a per-disk run_backup FULL.

    Content verification itself happens inside the provider (which reads
    ``target.verify``); Core's job is to call ``run_backup`` with the
    FULL direction and record the result.
    """
    global_cfg = make_global_config(full_verify_after_create="compare")
    target = make_target(verify="compare")
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

    with patch.object(
        mock_factory._backup_provider,
        "run_backup",
        wraps=mock_factory._backup_provider.run_backup,
    ) as run_spy:
        core._backup_target(vm, target, [snap])

    assert run_spy.called, "run_backup should be called"
    assert run_spy.call_args.kwargs["force_full"] is True, (
        "run_backup should be called with force_full=True"
    )
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 1, "FULL should be recorded after run_backup succeeds"


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
    """Successful run_backup FULL — record_full_backup + retention + cleanup run.

    Count-based: no prior FULLs so first backup creates FULL.  The
    provider verified internally → state recording + backup retention
    evaluated.
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
    with patch.object(
        mock_state,
        "record_full_backup",
        wraps=mock_state.record_full_backup,
    ) as record_spy:
        core._backup_target(vm, target, [snap])

    # FULL was recorded after run_backup succeeded.
    assert record_spy.called, "record_full_backup should be called after a successful FULL"
    # At least one FULL is now recorded.
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) >= 1, "FULL should be recorded after successful run_backup"


# ── test_failed_full_verification_triggers_abort ──────────────────────────


def test_failed_full_verification_triggers_abort(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """A FULL verification failure surfaced by run_backup aborts the VM.

    The provider deletes the failed file and its successor checkpoint
    inside ``run_backup``; Core's rollback is to abort the VM pipeline
    (BackupAbortError) so old generations are preserved and nothing is
    recorded.
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

    failed = BackupResult(
        success=False,
        snapshot_name="",
        source_path=snap.path,
        target_path=target.path / "testvm.FULL.qcow2",
        bytes_transferred=0,
        error="verification failed: FULL backup has corrupt bit set — file is damaged",
        disk="vda",
        checkpoint="qsnap-ab12cd34-vda-20260807T020000-9f8e7d",
        kind="full",
    )

    caplog.set_level(logging.WARNING)

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            return_value=failed,
        ),
        patch.object(
            mock_state,
            "remove_full_backup",
            wraps=mock_state.remove_full_backup,
        ) as remove_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    # No FULL state was recorded, so no state rollback is needed.
    assert not remove_spy.called, (
        "remove_full_backup should NOT be called (the failed FULL was never recorded)"
    )
    # WARNING logged with target + disk attribution; CRITICAL logged
    # because this is a FULL backup failure (old generations preserved).
    assert "old generations preserved" in caplog.text
    assert "Backup to target" in caplog.text
    assert "vda" in caplog.text


# ── test_cleanup_failed_checkpoint_deletes_exact_checkpoint_name ──────────


@pytest.mark.unit
@pytest.mark.mock
def test_cleanup_failed_checkpoint_deletes_exact_checkpoint_name(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Rollback deletes exactly the failed FULL's checkpoint by exact name.

    Core-orchestrator scenario "Checkpoint cleaned up after failed FULL":
    ``_cleanup_failed_checkpoint`` issues exactly one
    ``virsh checkpoint-delete --metadata --domain testvm`` call for
    ``BackupResult.checkpoint`` — never a ``qsnap-{target_hash}-*`` bulk
    filter.
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_shell.expect("virsh checkpoint-delete").returns(_OK)

    full_result = BackupResult(
        success=False,
        snapshot_name="",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "testvm.FULL.qcow2",
        bytes_transferred=0,
        error="verification failed",
        disk="vda",
        checkpoint="qsnap-ab12cd34-vda-20260807T020000-9f8e7d",
    )

    core._cleanup_failed_checkpoint(vm, target, full_result)

    # Exactly ONE exact-name checkpoint-delete call — no bulk filter.
    expected_cmd = (
        "virsh checkpoint-delete --metadata --domain testvm "
        "qsnap-ab12cd34-vda-20260807T020000-9f8e7d"
    )
    delete_calls = [c for c in mock_shell.call_history if "checkpoint-delete" in c]
    assert len(delete_calls) == 1, (
        f"exactly one exact-name checkpoint-delete call expected, got: {delete_calls}"
    )
    assert delete_calls[0] == expected_cmd, (
        f"checkpoint-delete must target the exact checkpoint name, got: {delete_calls[0]}"
    )


# ── test_cleanup_failed_checkpoint_multi_disk_preserves_other_disks ───────


@pytest.mark.unit
@pytest.mark.mock
def test_cleanup_failed_checkpoint_multi_disk_preserves_other_disks(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Multi-disk rollback deletes only the failed disk's checkpoint.

    Core-orchestrator scenario "Multi-disk rollback leaves other disks
    untouched": ``_cleanup_failed_checkpoint`` targets the exact vda
    checkpoint name from ``BackupResult.checkpoint`` — never a
    ``qsnap-{target_hash}-*`` bulk filter that would wipe every disk's
    checkpoints for the target.
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target], disks=["vda", "vdb", "vdc"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_shell.expect("virsh checkpoint-delete").returns(_OK)

    # vdb/vdc checkpoints exist but must never be targeted.
    full_result = BackupResult(
        success=False,
        snapshot_name="",
        source_path=Path("/tmp/snap-vda.qcow2"),
        target_path=target.path / "testvm.FULL.qcow2",
        bytes_transferred=0,
        error="verification failed",
        disk="vda",
        checkpoint="qsnap-ab12cd34-vda-20260807T020000-9f8e7d",
    )

    core._cleanup_failed_checkpoint(vm, target, full_result)

    delete_calls = [c for c in mock_shell.call_history if "checkpoint-delete" in c]
    assert len(delete_calls) == 1, f"only the failed disk's checkpoint is targeted: {delete_calls}"
    # Only the vda checkpoint is targeted — no vdb/vdc, no bulk filter.
    assert "vda-20260807T020000-9f8e7d" in delete_calls[0]
    assert not any("vdb" in call or "vdc" in call for call in delete_calls), (
        f"vdb/vdc checkpoints must be left untouched, got: {delete_calls}"
    )
    # The checkpoint argument must be the exact name — no glob/bulk filter.
    assert "*" not in delete_calls[0], (
        f"no bulk-filter checkpoint-delete allowed, got: {delete_calls[0]}"
    )


# ── test_cleanup_failed_checkpoint_preserves_previous_baseline ────────────


@pytest.mark.unit
@pytest.mark.mock
def test_cleanup_failed_checkpoint_preserves_previous_baseline(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Rollback preserves the previous baseline checkpoint of the failed disk.

    Core-orchestrator scenario "Previous baseline of the failed disk is
    preserved": only the successor checkpoint (from
    ``BackupResult.checkpoint``) is deleted — the pre-existing baseline
    checkpoint is never targeted.
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Previous baseline of the failed disk.  Its checkpoint must survive.
    baseline_checkpoint = "qsnap-ab12cd34-vda-20260701T010000-a1b2c3"

    mock_shell.expect("virsh checkpoint-delete").returns(_OK)

    full_result = BackupResult(
        success=False,
        snapshot_name="",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "testvm.FULL.qcow2",
        bytes_transferred=0,
        error="verification failed",
        disk="vda",
        checkpoint="qsnap-ab12cd34-vda-20260807T020000-9f8e7d",
    )

    core._cleanup_failed_checkpoint(vm, target, full_result)

    delete_calls = [c for c in mock_shell.call_history if "checkpoint-delete" in c]
    # Only the successor checkpoint is targeted.
    assert delete_calls == [
        "virsh checkpoint-delete --metadata --domain testvm "
        "qsnap-ab12cd34-vda-20260807T020000-9f8e7d"
    ], f"only the successor checkpoint should be deleted, got: {delete_calls}"
    # The previous baseline is never mentioned in any checkpoint-delete call.
    assert not any(baseline_checkpoint in call for call in delete_calls), (
        f"previous baseline {baseline_checkpoint} must be preserved, got: {delete_calls}"
    )


# ── test_cleanup_failed_checkpoint_none_deletes_nothing ───────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_cleanup_failed_checkpoint_none_deletes_nothing(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Stopped-VM FULL failure (checkpoint=None) deletes no checkpoint.

    Core-orchestrator scenario "Stopped-VM FULL failure deletes nothing":
    when the failed FULL created no checkpoint, the rollback issues no
    ``virsh checkpoint-delete`` call.
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    full_result = BackupResult(
        success=False,
        snapshot_name="",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "testvm.FULL.qcow2",
        bytes_transferred=0,
        error="verification failed",
        disk="vda",
        checkpoint=None,
    )

    core._cleanup_failed_checkpoint(vm, target, full_result)

    delete_calls = [c for c in mock_shell.call_history if "checkpoint-delete" in c]
    assert delete_calls == [], (
        f"no checkpoint-delete call expected when checkpoint is None, got: {delete_calls}"
    )


# ── test_cleanup_failed_checkpoint_delete_failure_non_fatal ───────────────


@pytest.mark.unit
@pytest.mark.mock
def test_cleanup_failed_checkpoint_delete_failure_non_fatal(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Checkpoint deletion failure during rollback is non-fatal.

    Core-orchestrator scenario "Checkpoint deletion failure is
    non-fatal": a failing ``virsh checkpoint-delete`` logs a WARNING and
    the rollback continues (no exception raised from
    ``_cleanup_failed_checkpoint``).
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

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

    full_result = BackupResult(
        success=False,
        snapshot_name="",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "testvm.FULL.qcow2",
        bytes_transferred=0,
        error="verification failed",
        disk="vda",
        checkpoint="qsnap-deadc0de-vda-20260807T020000-111111",
    )

    # Must not raise — deletion failure is non-fatal.
    core._cleanup_failed_checkpoint(vm, target, full_result)

    # WARNING logged for the failed checkpoint deletion.
    assert "failed to delete checkpoint" in caplog.text, (
        "WARNING should be logged when checkpoint deletion fails"
    )


# ── test_retries_exhausted_keeps_old_generations ──────────────────────────


def test_retries_exhausted_keeps_old_generations(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """All FULL backup retries exhausted — old generations preserved,
    and the VM pipeline aborts (VM-level isolation).

    When every retry attempt fails, Core raises ``BackupAbortError``.
    The abort itself is the verify-before-delete gate: retention
    evaluation and cleanup are never reached, so old generations are
    never deleted.
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

    failed = BackupResult(
        success=False,
        snapshot_name="",
        source_path=snap.path,
        target_path=target.path / "testvm.FULL.qcow2",
        bytes_transferred=0,
        error="verification failed: content comparison mismatch",
        disk="vda",
        kind="full",
    )

    with (
        patch("qsnap.core.time.sleep"),
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            return_value=failed,
        ) as run_spy,
        patch.object(
            core,
            "_evaluate_backup_retention",
            wraps=core._evaluate_backup_retention,
        ) as retention_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    # run_backup was retried up to backup_retry_max times.
    assert run_spy.call_count == 2, (
        f"run_backup should be retried up to backup_retry_max, got {run_spy.call_count}"
    )
    # Retention was NOT evaluated (the abort precedes retention/cleanup).
    assert not retention_spy.called, (
        "Retention should NOT be evaluated when all FULL retries are exhausted"
    )


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
    retried via ``_execute_with_retry``, succeeds on attempt 2.  The FULL is
    recorded in state.

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
                snapshot_name="",
                source_path=Path("/tmp/snap1.qcow2"),
                target_path=target.path / "testvm.FULL.qcow2",
                bytes_transferred=0,
                error="Connection refused",
                disk="vda",
            )
        return BackupResult(
            success=True,
            snapshot_name="testvm.FULL.20250713T100000_vda_a1b2c3",
            source_path=Path("/tmp/snap1.qcow2"),
            target_path=target.path / "testvm.FULL.20250713T100000_vda_a1b2c3.qcow2",
            bytes_transferred=1048576,
            error=None,
            disk="vda",
            kind="full",
        )

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            side_effect=full_side_effect,
        ),
        patch("qsnap.core.time.sleep"),
        patch.object(
            mock_state,
            "record_full_backup",
            wraps=mock_state.record_full_backup,
        ) as record_spy,
    ):
        core._backup_target(vm, target, [snap])

    assert full_calls == 2, (
        f"run_backup should be called twice (retried after transient error), got {full_calls}"
    )
    # FULL was recorded after the successful retry.
    assert record_spy.called, "record_full_backup should be called after the retry succeeds"

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
            snapshot_name="",
            source_path=Path("/tmp/snap1.qcow2"),
            target_path=target.path / "testvm.FULL.qcow2",
            bytes_transferred=0,
            error="No space left on device",
            disk="vda",
        )

    caplog.set_level(logging.CRITICAL)

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            side_effect=full_side_effect,
        ),
        patch("qsnap.core.time.sleep"),
        patch.object(
            mock_state,
            "record_full_backup",
            wraps=mock_state.record_full_backup,
        ) as record_spy,
    ):
        # Space errors suspend the target — they must NOT raise.
        core._backup_target(vm, target, [snap])

    # run_backup called exactly once — no retry
    assert full_calls == 1, (
        f"run_backup should be called exactly once (non-retryable), got {full_calls}"
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
    """A successful run_backup FULL → retention evaluation + cleanup triggered.

    After run_backup succeeds (provider verified internally), Core must:
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
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            wraps=mock_factory._backup_provider.run_backup,
        ) as run_spy,
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

    # run_backup was called (provider-internal verification passed).
    assert run_spy.called, "run_backup should be called"
    assert run_spy.call_args.kwargs["force_full"] is True, (
        "run_backup should be called with force_full=True on first backup"
    )
    # FULL was recorded after run_backup succeeded.
    assert record_spy.called, "record_full_backup should be called after a successful FULL"
    # Retention was evaluated because no backup failure aborted the run.
    assert retention_spy.called, (
        "_evaluate_backup_retention should be called after a successful FULL"
    )
    # Cleanup was triggered.
    assert cleanup_spy.called, "_cleanup_backups should be called after retention evaluation"
