"""Per-target ENOSPC isolation tests (fault-tolerance-hardening).

Covers design D2 (per-target suspension on space errors — retention and
cleanup still run, non-space failures still abort the VM), the
never-delete-on-ENOSPC invariant (design goal: ENOSPC never causes data
deletion), the auto-resume contract (D7: success-only advancement), and
the proactive free-space gate (D5: strict/warn/off).

All tests use MockShell + MockVMModuleFactory + InMemoryStateManager —
zero real I/O.  Space errors are simulated via ``is_space_error``-matching
error strings ("No space left on device" / "disk quota exceeded") or via
the free-space gate (``check_free_space`` patched in ``qsnap.core``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import BackupAbortError, Core
from qsnap.models.results import BackupResult, ShellResult, SnapshotInfo
from qsnap.utils.space import SpaceCheckResult
from tests.mocks import MockConfigFacade

pytestmark = pytest.mark.unit

_ENOSPC = "No space left on device"
_EDQUOT = "disk quota exceeded"


def _add_snapshot(
    state,
    name: str = "snap1",
    disk: str = "vda",
    timestamp: datetime | None = None,
    path: str | None = None,
) -> SnapshotInfo:
    """Pre-populate state with a snapshot record for ``testvm``."""
    info = SnapshotInfo(
        name=name,
        path=Path(path or f"/tmp/{name}.qcow2"),
        timestamp=timestamp or datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk=disk,
    )
    state.record_snapshot("testvm", info)
    return info


def _add_full_anchor(
    state,
    target,
    disk: str = "vda",
    name: str = "testvm.FULL.anchor.qcow2",
    timestamp: datetime | None = None,
) -> None:
    """Record + materialize a pre-existing FULL anchor on *target*.

    The file is touched so the phantom filter and startup validation see
    a real backup.  Names containing ``.FULL.`` are skipped by pre-flight
    cleanup's stale-partial scan.
    """
    target.path.mkdir(parents=True, exist_ok=True)
    (target.path / name).touch()
    state.record_full_backup(
        str(target.path),
        name,
        timestamp or datetime(2025, 7, 12, 10, 0),
        disk,
    )


def _failed_result(snap: SnapshotInfo, target, error: str = _ENOSPC) -> BackupResult:
    return BackupResult(
        success=False,
        snapshot_name=snap.name,
        source_path=snap.path,
        target_path=target.path / f"{snap.name}.qcow2",
        bytes_transferred=0,
        error=error,
        disk=snap.disk,
    )


def _ok_result(snap: SnapshotInfo, target) -> BackupResult:
    return BackupResult(
        success=True,
        snapshot_name=snap.name,
        source_path=snap.path,
        target_path=target.path / f"{snap.name}.qcow2",
        bytes_transferred=1048576,
        error=None,
        disk=snap.disk,
    )


# ── Per-target suspension on space errors (design D2) ────────────────────


def test_enospc_suspends_only_affected_target(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A space error on target A suspends ONLY A; target B still completes.

    No ``BackupAbortError`` is raised: the VM pipeline continues and the
    successful target B transfers are audited (enospc-fault-handling
    scenario 4; core-orchestrator scenario 6).
    """
    target_a = make_target(path=str(tmp_path / "a"))
    target_b = make_target(path=str(tmp_path / "b"))
    vm = make_vm_config(name="testvm", targets=[target_a, target_b])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _add_snapshot(mock_state)
    _add_full_anchor(mock_state, target_a)
    _add_full_anchor(mock_state, target_b)

    def transfer_side_effect(vm_config, target, disk, **kwargs):
        if target.path == target_a.path:
            return _failed_result(snap, target)
        return _ok_result(snap, target)

    with patch.object(
        mock_factory._backup_provider,
        "run_backup",
        side_effect=transfer_side_effect,
    ):
        result = core.run()

    # VM continues — no BackupAbortError, no backup_failed flag.
    assert result.results[0].success is True
    assert result.results[0].backup_failed is False
    assert result.space_limited is True

    # Target A's transfers failed → nothing audited for A.
    a_transfers = [
        a
        for a in result.actions
        if a.action == "backup_transfer" and a.path.parent == target_a.path
    ]
    assert a_transfers == []

    # Target B completed its transfers → audited.
    b_transfers = [
        a
        for a in result.actions
        if a.action == "backup_transfer" and a.path.parent == target_b.path
    ]
    assert len(b_transfers) >= 1


def test_enospc_retention_cleanup_still_run_for_suspended_target(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Retention evaluation + cleanup still run for the ENOSPC-suspended target.

    Deletion frees space — self-heal (design D2): the suspend path must not
    skip ``_evaluate_backup_retention`` / ``_cleanup_backups``.
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _add_snapshot(mock_state)
    _add_full_anchor(mock_state, target)

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            return_value=_failed_result(snap, target),
        ),
        patch.object(
            core, "_evaluate_backup_retention", wraps=core._evaluate_backup_retention
        ) as retention_spy,
        patch.object(core, "_cleanup_backups", wraps=core._cleanup_backups) as cleanup_spy,
    ):
        result = core.run()

    assert result.space_limited is True
    assert retention_spy.called, "retention should still run for the suspended target (self-heal)"
    assert cleanup_spy.called, "cleanup should still run for the suspended target (self-heal)"


def test_non_space_failure_raises_backup_abort(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A non-space transfer failure still aborts the VM (BackupAbortError).

    Verify-before-delete is not weakened: only space-classified errors use
    the suspension path (enospc-fault-handling scenario 6).
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _add_snapshot(mock_state)
    _add_full_anchor(mock_state, target)

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            return_value=_failed_result(snap, target, error="permission denied"),
        ),
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    # The non-space failure never entered the space-limited set.
    assert core._space_limited_targets == set()


def test_verification_failure_not_treated_as_space_error(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A FULL verification failure raises BackupAbortError — never suspends.

    The verify-before-delete gate is not weakened by the isolation path
    (enospc-fault-handling scenario 7).  Verification failures abort before
    cleanup, so old generations stay untouched.
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # No FULL in state → the FULL creation path runs.
    snap = _add_snapshot(mock_state)
    failed_full = BackupResult(
        success=False,
        snapshot_name="",
        source_path=snap.path,
        target_path=target.path / "testvm.FULL.verify.qcow2",
        bytes_transferred=0,
        error="verification failed: qemu-img info returned No mock configured",
        disk=snap.disk,
    )

    with (
        patch.object(mock_factory._backup_provider, "run_backup", return_value=failed_full),
        patch.object(
            mock_factory._backup_provider, "delete", wraps=mock_factory._backup_provider.delete
        ) as del_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    # Verification failure is never space-classified.
    assert core._space_limited_targets == set()
    assert not del_spy.called, "abort precedes cleanup — old generations preserved"


# ── Never-delete-on-ENOSPC invariant ─────────────────────────────────────


def test_enospc_leaves_only_tmp_no_deletion(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """An interrupted FULL leaves only a .tmp file; nothing is deleted.

    The transfer artifact stays (next-run pre-flight cleans it), no FULL is
    recorded in state, no snapshot is removed, and no provider.delete runs
    (enospc-fault-handling scenario 8).
    """
    target = make_target(path=str(tmp_path / "backup"))
    target.path.mkdir(parents=True, exist_ok=True)
    tmp_leftover = target.path / "testvm.FULL.interrupted.qcow2.tmp"
    tmp_leftover.write_bytes(b"partial-transfer")

    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _add_snapshot(mock_state)

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            return_value=_failed_result(snap, target, error=_ENOSPC),
        ),
        patch.object(
            mock_factory._backup_provider, "delete", wraps=mock_factory._backup_provider.delete
        ) as del_spy,
    ):
        result = core.run()

    # The interrupted transfer's .tmp file is NOT deleted.
    assert tmp_leftover.exists(), ".tmp leftover must survive an ENOSPC run"

    # No deletion, no state record for the failed FULL, snapshot preserved.
    del_spy.assert_not_called()
    assert mock_state.get_full_backups(str(target.path)) == []
    assert len(mock_state.get_snapshots("testvm")) >= 1
    assert result.space_limited is True


def test_space_pressure_never_triggers_deletion(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Space pressure never triggers deletion of existing backups.

    With retention configured to keep everything, an ENOSPC-suspended run
    deletes nothing: the old FULL stays on disk and in state
    (enospc-fault-handling scenario 9).
    """
    target = make_target(path=str(tmp_path / "backup"), target_keep_generations=5)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _add_snapshot(mock_state)
    _add_full_anchor(mock_state, target, name="testvm.FULL.old.qcow2")

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            return_value=_failed_result(snap, target, error=_EDQUOT),
        ),
        patch.object(
            mock_factory._backup_provider, "delete", wraps=mock_factory._backup_provider.delete
        ) as del_spy,
    ):
        result = core.run()

    assert result.space_limited is True
    del_spy.assert_not_called()
    assert (target.path / "testvm.FULL.old.qcow2").exists()
    assert len(mock_state.get_full_backups(str(target.path))) == 1
    assert len(mock_state.get_snapshots("testvm")) >= 1


# ── Auto-resume contract (design D7: success-only advancement) ───────────


def test_next_run_resumes_interrupted_incremental(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run N+1 resumes the incremental interrupted by ENOSPC in run N.

    The failed transfer advances nothing: the snapshot is still pending and
    is re-transferred on the next run (enospc-fault-handling scenario 10).
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    snap = _add_snapshot(mock_state)
    _add_full_anchor(mock_state, target)

    # Run 1: ENOSPC — target suspended, snapshot NOT consumed.
    core1 = Core(
        config=MockConfigFacade(vms=[vm]),
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    with patch.object(
        mock_factory._backup_provider,
        "run_backup",
        return_value=_failed_result(snap, target),
    ):
        result1 = core1.run()

    assert result1.space_limited is True
    assert result1.results[0].success is True

    # Run 2: space freed — the same snapshot is transferred again.
    core2 = Core(
        config=MockConfigFacade(vms=[vm]),
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    with patch.object(
        mock_factory._backup_provider,
        "run_backup",
        wraps=mock_factory._backup_provider.run_backup,
    ) as transfer_spy:
        result2 = core2.run()

    assert result2.space_limited is False
    assert result2.results[0].success is True
    transferred = {a.name for a in result2.actions if a.action == "backup_transfer"}
    assert len(transferred) >= 1, (
        "the interrupted incremental must be re-transferred by the next run"
    )
    assert all("_vda_" in name for name in transferred), (
        f"transfer action names should be freeze-ts backup names, got: {transferred}"
    )


def test_next_run_retries_gate_skipped_full(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A FULL that never started (strict-gate skipped) is retried next run.

    Run 1: the strict gate blocks the doomed FULL — nothing is created.
    Run 2: space freed — the gate passes and the FULL is created from the
    same disk (enospc-fault-handling scenario 11).
    """
    target = make_target(path=str(tmp_path / "backup"))
    target.path.mkdir(parents=True, exist_ok=True)
    vm = make_vm_config(name="testvm", targets=[target])
    snap = _add_snapshot(mock_state)

    insufficient = SpaceCheckResult(sufficient=False, free_bytes=0, estimate=5000, required=10000)
    sufficient = SpaceCheckResult(sufficient=True, free_bytes=10**12, estimate=5000, required=10000)

    # Run 1: gate blocks the FULL.
    core1 = Core(
        config=MockConfigFacade(vms=[vm]),
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    with (
        patch("qsnap.core.estimate_full_size", return_value=5000),
        patch("qsnap.core.check_free_space", return_value=insufficient),
        patch.object(mock_factory._backup_provider, "run_backup") as run_spy,
    ):
        result1 = core1.run()

    assert result1.space_limited is True
    run_spy.assert_not_called()
    assert mock_state.get_full_backups(str(target.path)) == []
    assert len(mock_state.get_snapshots("testvm")) >= 1

    # Run 2: gate passes → FULL created from the preserved disk.
    core2 = Core(
        config=MockConfigFacade(vms=[vm]),
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    with (
        patch("qsnap.core.estimate_full_size", return_value=5000),
        patch("qsnap.core.check_free_space", return_value=sufficient),
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            wraps=mock_factory._backup_provider.run_backup,
        ) as run_spy2,
    ):
        result2 = core2.run()

    assert result2.space_limited is False
    assert run_spy2.called
    assert run_spy2.call_args.kwargs["force_full"] is True, (
        "the retried FULL must be created with force_full=True"
    )
    assert len(mock_state.get_full_backups(str(target.path))) == 1


# ── Proactive free-space gate (design D5) ────────────────────────────────


def test_strict_gate_blocks_doomed_full(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Strict gate blocks a doomed FULL: no transfer, target suspended.

    A CRITICAL log names the target and the gate blocks before any
    checkpoint/export is created (enospc-fault-handling scenario 12).
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshot(mock_state)

    caplog.set_level(logging.CRITICAL)
    with (
        patch("qsnap.core.estimate_full_size", return_value=5000),
        patch(
            "qsnap.core.check_free_space",
            return_value=SpaceCheckResult(
                sufficient=False, free_bytes=100, estimate=5000, required=10000
            ),
        ),
        patch.object(mock_factory._backup_provider, "run_backup") as run_spy,
    ):
        result = core.run()

    assert result.space_limited is True
    run_spy.assert_not_called()
    assert any("suspending target" in r.getMessage() for r in caplog.records), (
        "strict gate must log CRITICAL naming the suspension"
    )


def test_warn_mode_proceeds(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Warn mode logs a WARNING but proceeds with the transfer.

    The gate never blocks in warn mode and the run is not space-limited
    (enospc-fault-handling scenario 13).
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(
        name="testvm",
        targets=[target],
        free_space_check="warn",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _add_snapshot(mock_state)
    _add_full_anchor(mock_state, target)

    caplog.set_level(logging.WARNING)
    with (
        patch(
            "qsnap.core.check_free_space",
            return_value=SpaceCheckResult(
                sufficient=False, free_bytes=100, estimate=5000, required=10000
            ),
        ),
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            wraps=mock_factory._backup_provider.run_backup,
        ) as transfer_spy,
    ):
        result = core.run()

    assert result.space_limited is False
    assert transfer_spy.called, "warn mode must proceed with the transfer"
    assert any("proceeding anyway" in r.getMessage() for r in caplog.records), (
        "warn mode must log a WARNING"
    )


def test_off_mode_skips_gate(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Off mode skips the gate entirely: check_free_space is never called."""
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(
        name="testvm",
        targets=[target],
        free_space_check="off",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _add_snapshot(mock_state)
    _add_full_anchor(mock_state, target)

    with (
        patch(
            "qsnap.core.check_free_space",
            side_effect=AssertionError("gate must not run in off mode"),
        ),
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            wraps=mock_factory._backup_provider.run_backup,
        ) as transfer_spy,
    ):
        result = core.run()

    assert result.space_limited is False
    assert transfer_spy.called, "off mode must not block the transfer"


def test_suspended_target_still_runs_retention_cleanup(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A strict-gate-suspended target still runs retention and cleanup.

    core-orchestrator scenario "suspended target still runs retention and
    cleanup": the gate skips the transfer but retention + cleanup execute.
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _add_snapshot(mock_state)
    _add_full_anchor(mock_state, target)

    with (
        patch("qsnap.core.estimate_full_size", return_value=5000),
        patch(
            "qsnap.core.check_free_space",
            return_value=SpaceCheckResult(
                sufficient=False, free_bytes=100, estimate=5000, required=10000
            ),
        ),
        patch.object(mock_factory._backup_provider, "run_backup") as run_spy,
        patch.object(
            core, "_evaluate_backup_retention", wraps=core._evaluate_backup_retention
        ) as retention_spy,
        patch.object(core, "_cleanup_backups", wraps=core._cleanup_backups) as cleanup_spy,
    ):
        result = core.run()

    assert result.space_limited is True
    run_spy.assert_not_called(), ("no transfer may be attempted for a gate-suspended target")
    assert retention_spy.called
    assert cleanup_spy.called


def test_strict_gate_no_transfer_attempted(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Strict incremental gate rejects a doomed transfer — no transfer runs.

    core-orchestrator scenario "strict gate rejection suspends target
    without transfer".
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _add_snapshot(mock_state)
    _add_full_anchor(mock_state, target)

    with (
        patch("qsnap.core.estimate_full_size", return_value=5000),
        patch(
            "qsnap.core.check_free_space",
            return_value=SpaceCheckResult(
                sufficient=False, free_bytes=100, estimate=5000, required=10000
            ),
        ),
        patch.object(mock_factory._backup_provider, "run_backup") as run_spy,
    ):
        result = core.run()

    assert result.space_limited is True
    run_spy.assert_not_called(), ("no transfer may be attempted for a gate-suspended target")


# ── VM-level isolation ───────────────────────────────────────────────────


def test_space_error_suspends_target_vm_continues(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A space error suspends the target; the VM keeps running.

    The VMRunResult is success=True with no error and no backup_failed
    flag; the run is flagged space_limited (core-orchestrator scenario 10).
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _add_snapshot(mock_state)
    _add_full_anchor(mock_state, target)

    with patch.object(
        mock_factory._backup_provider,
        "run_backup",
        return_value=_failed_result(snap, target),
    ):
        result = core.run()

    assert result.results[0].success is True
    assert result.results[0].error is None
    assert result.results[0].backup_failed is False
    assert result.space_limited is True


def test_space_failure_no_backup_abort_error(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A FULL-creation space failure suspends without BackupAbortError.

    core-orchestrator scenario "space failure does not raise
    BackupAbortError": the FULL source snapshot is preserved and no FULL is
    recorded (nothing advances on failure).
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _add_snapshot(mock_state)

    with patch.object(
        mock_factory._backup_provider,
        "run_backup",
        return_value=_failed_result(snap, target, error=_ENOSPC),
    ):
        # Must NOT raise BackupAbortError.
        core._backup_target(vm, target, [snap])

    assert str(target.path) in core._space_limited_targets
    assert mock_state.get_full_backups(str(target.path)) == []
    assert len(mock_state.get_snapshots("testvm")) == 1
