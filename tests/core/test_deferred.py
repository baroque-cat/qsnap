"""Tests for deferred blockcommit integration in Core.

Covers:
- Deferred blockcommits executed when VM is shut off → queue cleared.
- Deferred blockcommits skipped when VM is running → INFO log.
- Deferred blockcommit fails on retry → stays in queue.
- Risk: deferred accumulation logs a warning.
- Risk: deferred count visible in list.
- Risk: deferred queue grows across multiple runs (MAC denials).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from qsnap.core import Core
from qsnap.models.results import (
    CommitResult,
    DeferredBlockcommit,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import MockConfigFacade

_OK = ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


def _add_snapshot(state, vm_name: str, name: str) -> None:
    """Pre-populate state with a snapshot record."""
    state.record_snapshot(
        vm_name,
        SnapshotInfo(
            name=name,
            path=Path(f"/tmp/{name}.qcow2"),
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=1000,
        ),
    )


def _set_vm_state(shell, state: str) -> None:
    """Configure MockShell to return *state* for ``virsh domstate``."""
    shell.expect("domstate").returns(
        ShellResult(
            success=True,
            stdout=state,
            stderr="",
            returncode=0,
            error=None,
        )
    )


def _add_deferred_with_since(
    state,
    vm_name: str,
    snapshots: list[str],
    reason: str,
    since: datetime,
) -> None:
    """Add a deferred blockcommit with a specific ``since`` timestamp.

    Unlike ``InMemoryStateManager.add_deferred_blockcommit`` which always
    uses ``datetime.now()``, this helper lets tests control the ``since``
    timestamp for age-based threshold tests.
    """
    if vm_name not in state._state:
        state._state[vm_name] = {}
    deferred = state._state[vm_name].setdefault("deferred_operations", [])
    deferred.append(
        DeferredBlockcommit(
            snapshots=list(snapshots),
            reason=reason,
            since=since,
        )
    )


# ── test_deferred_blockcommits_executed_on_shutoff_vm ─────────────────────


def test_deferred_blockcommits_executed_on_shutoff_vm(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Deferred ops exist + VM shut off → blockcommit executed, queue cleared."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with a deferred blockcommit and matching snapshot.
    _add_snapshot(mock_state, "testvm", "snap1")
    mock_state.add_deferred_blockcommit("testvm", ["snap1"], "apparmor")
    _set_vm_state(mock_shell, "shut off")

    lifecycle_manager = mock_factory._lifecycle_manager

    with patch.object(
        lifecycle_manager,
        "blockcommit",
        wraps=lifecycle_manager.blockcommit,
    ) as bc_spy:
        core.snapshot()

    # Blockcommit was called for the deferred snapshot.
    assert bc_spy.called

    # Deferred queue was cleared on success.
    assert mock_state.get_deferred_operations("testvm") == []


# ── test_deferred_blockcommits_skipped_on_running_vm ──────────────────────


def test_deferred_blockcommits_skipped_on_running_vm(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Deferred ops exist + VM running → skipped with INFO log."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with a deferred blockcommit.
    mock_state.add_deferred_blockcommit("testvm", ["snap1"], "apparmor")
    _set_vm_state(mock_shell, "running")

    lifecycle_manager = mock_factory._lifecycle_manager
    caplog.set_level(logging.INFO)

    with patch.object(
        lifecycle_manager,
        "blockcommit",
        wraps=lifecycle_manager.blockcommit,
    ) as bc_spy:
        core.snapshot()

    # Blockcommit was NOT called (VM is running).
    assert not bc_spy.called

    # Deferred operations remain in the queue.
    assert len(mock_state.get_deferred_operations("testvm")) == 1

    # INFO log about skipping.
    assert "VM is running" in caplog.text


# ── test_deferred_blockcommit_fails_on_retry_remains_queued ───────────────


def test_deferred_blockcommit_fails_on_retry_remains_queued(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Deferred blockcommit still fails on retry → stays in queue."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with a deferred blockcommit and matching snapshot.
    _add_snapshot(mock_state, "testvm", "snap1")
    mock_state.add_deferred_blockcommit("testvm", ["snap1"], "apparmor")
    _set_vm_state(mock_shell, "shut off")

    lifecycle_manager = mock_factory._lifecycle_manager
    caplog.set_level(logging.WARNING)

    # Patch blockcommit to fail.
    fail_result = CommitResult(
        success=False,
        committed_snapshot="",
        error="blockcommit still failing",
    )

    with patch.object(
        lifecycle_manager,
        "blockcommit",
        return_value=fail_result,
    ) as bc_spy:
        core.snapshot()

    # Blockcommit was called.
    assert bc_spy.called

    # Deferred operations were NOT cleared (still in queue).
    remaining = mock_state.get_deferred_operations("testvm")
    assert len(remaining) == 1

    # Warning logged about the failure.
    assert "still failing" in caplog.text


# ── test_risk_deferred_accumulation_logs_warning ──────────────────────────


def test_risk_deferred_accumulation_logs_warning(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Multiple deferred entries → warning logged.

    When multiple deferred blockcommits accumulate and the VM is running,
    the code logs an INFO message with the count.  When the VM is shut off
    and retries fail, WARNING-level messages are emitted for each failure.
    This test verifies that at least one warning is logged when deferred
    operations fail on retry.
    """
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with two deferred entries and matching snapshots.
    _add_snapshot(mock_state, "testvm", "snap1")
    _add_snapshot(mock_state, "testvm", "snap2")
    mock_state.add_deferred_blockcommit("testvm", ["snap1"], "apparmor")
    mock_state.add_deferred_blockcommit("testvm", ["snap2"], "selinux")
    _set_vm_state(mock_shell, "shut off")

    lifecycle_manager = mock_factory._lifecycle_manager
    caplog.set_level(logging.WARNING)

    fail_result = CommitResult(
        success=False,
        committed_snapshot="",
        error="blockcommit still failing",
    )

    with patch.object(
        lifecycle_manager,
        "blockcommit",
        return_value=fail_result,
    ):
        core.snapshot()

    # At least one warning logged about deferred failure.
    assert "still failing" in caplog.text

    # Both deferred entries remain (neither succeeded).
    remaining = mock_state.get_deferred_operations("testvm")
    assert len(remaining) == 2


# ── test_risk_deferred_count_visible_in_list ─────────────────────────────


def test_risk_deferred_count_visible_in_list(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Deferred count is accessible and list_snapshots works with deferred ops.

    The state manager exposes ``get_deferred_operations()`` which returns
    the list of pending deferred blockcommits.  ``list_snapshots`` must
    still return correct snapshot data even when deferred operations exist.
    """
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with snapshots and deferred operations.
    _add_snapshot(mock_state, "testvm", "snap1")
    _add_snapshot(mock_state, "testvm", "snap2")
    mock_state.add_deferred_blockcommit("testvm", ["snap1"], "apparmor")
    mock_state.add_deferred_blockcommit("testvm", ["snap2"], "selinux")

    # Deferred count is accessible via state manager.
    deferred = mock_state.get_deferred_operations("testvm")
    assert len(deferred) == 2
    assert deferred[0].reason == "apparmor"
    assert deferred[1].reason == "selinux"

    # list_snapshots still returns the recorded snapshots.
    snapshots = core.list_snapshots("testvm")
    assert "testvm" in snapshots
    assert len(snapshots["testvm"]) == 2
    names = {s.name for s in snapshots["testvm"]}
    assert names == {"snap1", "snap2"}


# ── test_risk_deferred_queue_grows_across_runs ────────────────────────────


def test_risk_deferred_queue_grows_across_runs(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Multiple MAC denials accumulate deferred entries across runs.

    Run 1: no deferred ops. Retention removes "snap1". Blockcommit fails
    with apparmor → deferred entry 1 added.
    Run 2: 1 deferred op (VM running → skipped). Retention removes "snap1"
    again. Blockcommit fails with apparmor → deferred entry 2 added.
    Verify queue grew from 0 → 1 → 2.
    """
    vm = make_vm_config(
        name="testvm",
        disks=["vda"],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with a snapshot that retention will remove.
    _add_snapshot(mock_state, "testvm", "snap1")

    # Set VM state to "running" so deferred ops are skipped, not retried.
    _set_vm_state(mock_shell, "running")

    lifecycle_manager = mock_factory._lifecycle_manager

    # Patch retention to remove "snap1" and blockcommit to fail with apparmor.
    mac_fail = CommitResult(
        success=False,
        committed_snapshot="",
        error="apparmor denies blockcommit",
    )

    with (
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=[], remove=["snap1"]),
        ),
        patch.object(
            lifecycle_manager,
            "blockcommit",
            return_value=mac_fail,
        ) as bc_spy,
    ):
        # Run 1: no deferred ops yet.
        assert len(mock_state.get_deferred_operations("testvm")) == 0
        core.snapshot()

        # After run 1: 1 deferred entry.
        after_run1 = mock_state.get_deferred_operations("testvm")
        assert len(after_run1) == 1
        assert after_run1[0].reason == "apparmor"

        # Run 2: deferred op exists, VM running → skipped.
        core.snapshot()

        # After run 2: 2 deferred entries (queue grew).
        after_run2 = mock_state.get_deferred_operations("testvm")
        assert len(after_run2) == 2

    # Blockcommit was called at least twice (once per run).
    assert bc_spy.call_count >= 2


# ── test_deferred_count_below_warn_silent ─────────────────────────────────


def test_deferred_count_below_warn_silent(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """4 deferred ops (below warn=5) → no WARNING/CRITICAL logged."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(4):
        mock_state.add_deferred_blockcommit("testvm", [f"snap{i}"], "apparmor")

    caplog.set_level(logging.WARNING)
    core._check_deferred_thresholds()

    # No WARNING or CRITICAL logged (4 < warn threshold of 5).
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 0


# ── test_deferred_count_meets_warn_threshold ──────────────────────────────


def test_deferred_count_meets_warn_threshold(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """5 deferred ops (==warn=5) → WARNING logged."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(5):
        mock_state.add_deferred_blockcommit("testvm", [f"snap{i}"], "apparmor")

    caplog.set_level(logging.WARNING)
    core._check_deferred_thresholds()

    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ── test_deferred_count_meets_crit_threshold ──────────────────────────────


def test_deferred_count_meets_crit_threshold(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """10 deferred ops (==crit=10) → CRITICAL logged."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(10):
        mock_state.add_deferred_blockcommit("testvm", [f"snap{i}"], "apparmor")

    caplog.set_level(logging.CRITICAL)
    core._check_deferred_thresholds()

    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


# ── test_deferred_age_meets_warn_threshold ────────────────────────────────


def test_deferred_age_meets_warn_threshold(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
    caplog,
):
    """1 deferred op aged 7d (==warn_age=7d) → WARNING logged."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    frozen_dt = datetime(2025, 7, 13, 15, 31)
    since = frozen_dt - timedelta(days=7)
    _add_deferred_with_since(mock_state, "testvm", ["snap1"], "apparmor", since)

    caplog.set_level(logging.WARNING)
    with frozen_clock(frozen_dt):
        core._check_deferred_thresholds()

    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ── test_deferred_age_meets_crit_threshold ─────────────────────────────────


def test_deferred_age_meets_crit_threshold(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
    caplog,
):
    """1 deferred op aged 14d (==crit_age=14d) → CRITICAL logged."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    frozen_dt = datetime(2025, 7, 13, 15, 31)
    since = frozen_dt - timedelta(days=14)
    _add_deferred_with_since(mock_state, "testvm", ["snap1"], "apparmor", since)

    caplog.set_level(logging.CRITICAL)
    with frozen_clock(frozen_dt):
        core._check_deferred_thresholds()

    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


# ── test_threshold_check_exit_code_unchanged ──────────────────────────────


def test_threshold_check_exit_code_unchanged(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """CRITICAL threshold breached during core.run() → PipelineResult.success
    is still True (monitoring is non-fatal)."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(10):
        mock_state.add_deferred_blockcommit("testvm", [f"snap{i}"], "apparmor")

    # VM is running → deferred ops skipped (remain in queue for threshold check).
    _set_vm_state(mock_shell, "running")

    caplog.set_level(logging.CRITICAL)
    result = core.run()

    # CRITICAL was logged (threshold breached).
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)

    # But pipeline success is unchanged (monitoring is non-fatal).
    assert result.success is True


# ── test_deferred_status_ok_below_thresholds ───────────────────────────────


def test_deferred_status_ok_below_thresholds(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Below all thresholds → check() reports deferred_severity 'ok'."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(4):
        mock_state.add_deferred_blockcommit("testvm", [f"snap{i}"], "apparmor")

    result = core.check()

    assert result["testvm"].deferred_count == 4
    assert result["testvm"].deferred_severity == "ok"


# ── test_deferred_status_warning_count ────────────────────────────────────


def test_deferred_status_warning_count(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Count at warn threshold → check() reports deferred_severity 'warning'."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(5):
        mock_state.add_deferred_blockcommit("testvm", [f"snap{i}"], "apparmor")

    result = core.check()

    assert result["testvm"].deferred_count == 5
    assert result["testvm"].deferred_severity == "warning"


# ── test_deferred_status_critical_age ──────────────────────────────────────


def test_deferred_status_critical_age(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
):
    """Age at crit threshold → check() reports deferred_severity 'critical'."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    frozen_dt = datetime(2025, 7, 13, 15, 31)
    since = frozen_dt - timedelta(days=14)
    _add_deferred_with_since(mock_state, "testvm", ["snap1"], "apparmor", since)

    with frozen_clock(frozen_dt):
        result = core.check()

    assert result["testvm"].deferred_count == 1
    assert result["testvm"].deferred_severity == "critical"


# ── test_deferred_threshold_warning_logged ─────────────────────────────────


def test_deferred_threshold_warning_logged(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Verify WARNING log message format for deferred threshold breach."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(5):
        mock_state.add_deferred_blockcommit("testvm", [f"snap{i}"], "apparmor")

    caplog.set_level(logging.WARNING)
    core._check_deferred_thresholds()

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) > 0
    msg = warning_records[0].getMessage()
    assert "testvm" in msg
    assert "5 deferred blockcommit" in msg
    assert "apparmor" in msg


# ── test_deferred_threshold_critical_logged ───────────────────────────────


def test_deferred_threshold_critical_logged(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Verify CRITICAL log message format for deferred threshold breach."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(10):
        mock_state.add_deferred_blockcommit("testvm", [f"snap{i}"], "selinux")

    caplog.set_level(logging.CRITICAL)
    core._check_deferred_thresholds()

    critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical_records) > 0
    msg = critical_records[0].getMessage()
    assert "testvm" in msg
    assert "10 deferred blockcommit" in msg
    assert "selinux" in msg
