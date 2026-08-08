"""Tests for deferred blockcommit integration in Core.

Covers:
- Deferred blockcommits executed when VM is shut off → queue cleared.
- Deferred blockcommits skipped when VM is running → INFO log.
- Deferred blockcommit fails on retry → stays in queue.
- Risk: deferred accumulation logs a warning.
- Risk: deferred count visible in list.
- Risk: deferred queue grows across multiple runs (MAC denials).
- Adaptive drain (design D6): per-entry executor, tip-preserving, partial drain.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.config import DiskConfig
from qsnap.models.results import (
    CommitResult,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
)
from tests.helpers import add_deferred_with_since
from tests.mocks import MockConfigFacade

_OK = ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


def _add_snapshot(state, vm_name: str, name: str, path: str | None = None) -> None:
    """Pre-populate state with a snapshot record."""
    state.record_snapshot(
        vm_name,
        SnapshotInfo(
            name=name,
            path=Path(path or f"/tmp/{name}.qcow2"),
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=1000,
            disk="vda",
        ),
    )


def _set_vm_state(shell, state: str) -> None:
    """Configure MockShell to return *state* for ``virsh domstate``."""
    shell.expect_first("domstate").returns(
        ShellResult(
            success=True,
            stdout=state,
            stderr="",
            returncode=0,
            error=None,
        )
    )


def _set_domblklist(shell, source_path: str) -> None:
    """Configure MockShell to return *source_path* for ``virsh domblklist``."""
    shell.expect_first("domblklist").returns(
        ShellResult(
            success=True,
            stdout=f"vda {source_path}\n",
            stderr="",
            returncode=0,
            error=None,
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
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with a deferred blockcommit and matching snapshot.
    _add_snapshot(mock_state, "testvm", "snap1")
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap1"], "apparmor")
    _set_vm_state(mock_shell, "shut off")
    # domblklist returns a DIFFERENT source — snap1 is NOT the tip → committable
    _set_domblklist(mock_shell, "/tmp/other.qcow2")

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
    """Deferred ops exist + VM running → skipped with INFO log.

    Uses default lifecycle_mode="virsh".  The deferred snapshot IS the
    current active layer (domblklist source == snapshot path), so it is
    not committable while running — the entry stays in the queue.
    """
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with a deferred blockcommit AND matching snapshot.
    _add_snapshot(mock_state, "testvm", "snap1")
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap1"], "apparmor")
    _set_vm_state(mock_shell, "running")
    # domblklist returns the same path → snap1 IS the active layer
    _set_domblklist(mock_shell, "/tmp/snap1.qcow2")

    lifecycle_manager = mock_factory._lifecycle_manager
    caplog.set_level(logging.INFO)

    with patch.object(
        lifecycle_manager,
        "blockcommit",
        wraps=lifecycle_manager.blockcommit,
    ) as bc_spy:
        core.snapshot()

    # Blockcommit was NOT called (active layer, not committable while running).
    assert not bc_spy.called

    # Deferred operations remain in the queue.
    assert len(mock_state.get_deferred_operations("testvm")) == 1

    # INFO log about skipping (D6: "not committable in current VM state").
    assert "not committable" in caplog.text


# ── test_deferred_blockcommit_fails_on_retry_remains_queued ───────────────


def test_deferred_blockcommit_fails_on_retry_remains_queued(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Deferred blockcommit still fails on retry → stays in queue."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with a deferred blockcommit and matching snapshot.
    _add_snapshot(mock_state, "testvm", "snap1")
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap1"], "apparmor")
    _set_vm_state(mock_shell, "shut off")
    # domblklist returns a DIFFERENT source — snap1 is NOT the tip → committable
    _set_domblklist(mock_shell, "/tmp/other.qcow2")

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
    vm = make_vm_config(name="testvm")
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
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap1"], "apparmor")
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap2"], "selinux")
    _set_vm_state(mock_shell, "shut off")
    # domblklist returns a DIFFERENT source — both snap1 and snap2 are committable
    _set_domblklist(mock_shell, "/tmp/other.qcow2")

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
    vm = make_vm_config(name="testvm")
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
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap1"], "apparmor")
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap2"], "selinux")

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
    """Deferred entries accumulate across runs when VM is running.

    Run 1: no deferred ops. Retention removes "snap1". VM is running →
    blockcommit deferred with reason "vm_running" (entry 1).
    Run 2: 1 deferred op exists, VM still running → skipped during
    _check_deferred_operations.  Retention removes "snap1" again →
    blockcommit deferred again (entry 2).
    Verify queue grew from 0 → 1 → 2.
    """
    vm = make_vm_config(
        name="testvm",
        snapshot_chain_length=24,
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
    # domblklist returns snap1's path → snap1 IS the active layer →
    # not committable in virsh mode → deferred (preserves original intent)
    _set_domblklist(mock_shell, "/tmp/snap1.qcow2")

    lifecycle_manager = mock_factory._lifecycle_manager

    # On Python 3.14 Path.exists() delegates to os.path.exists().  Use a
    # predicate that returns False for generated snapshot candidates
    # (names containing "_vda") so the collision loop in
    # _generate_snapshot_name terminates, while returning True for all
    # other paths the test needs (recorded snapshots like /tmp/snap1.qcow2,
    # state files).
    def _path_exists(p: object) -> bool:
        return "_vda" not in Path(str(p)).name

    with (
        patch("os.path.exists", side_effect=_path_exists),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=[], remove=["snap1"]),
        ),
        patch.object(
            lifecycle_manager,
            "blockcommit",
            wraps=lifecycle_manager.blockcommit,
        ) as bc_spy,
    ):
        # Run 1: no deferred ops yet.
        assert len(mock_state.get_deferred_operations("testvm")) == 0
        core.snapshot()

        # After run 1: 1 deferred entry (VM running → blockcommit deferred).
        after_run1 = mock_state.get_deferred_operations("testvm")
        assert len(after_run1) == 1
        assert after_run1[0].reason == "vm_running"

        # Run 2: deferred op exists, VM running → skipped.
        core.snapshot()

        # After run 2: 2 deferred entries (queue grew).
        after_run2 = mock_state.get_deferred_operations("testvm")
        assert len(after_run2) == 2

    # Blockcommit was never called — VM running → deferred before blockcommit.
    assert bc_spy.call_count == 0


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
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(4):
        mock_state.add_deferred_blockcommit("testvm", "vda", [f"snap{i}"], "apparmor")

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
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(5):
        mock_state.add_deferred_blockcommit("testvm", "vda", [f"snap{i}"], "apparmor")

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
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(10):
        mock_state.add_deferred_blockcommit("testvm", "vda", [f"snap{i}"], "apparmor")

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
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    frozen_dt = datetime(2025, 7, 13, 15, 31)
    since = frozen_dt - timedelta(days=7)
    add_deferred_with_since(mock_state, "testvm", "vda", ["snap1"], "apparmor", since)

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
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    frozen_dt = datetime(2025, 7, 13, 15, 31)
    since = frozen_dt - timedelta(days=14)
    add_deferred_with_since(mock_state, "testvm", "vda", ["snap1"], "apparmor", since)

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
    is still True (monitoring is non-fatal).

    Uses lifecycle_mode="qemu-img" so that on a running VM ALL deferred
    entries are skipped (nothing committable).  Each entry must have a
    matching state snapshot so they are not dropped as stale.
    """
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm", lifecycle_mode="qemu-img")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(10):
        # Add a matching snapshot so the deferred entry is not stale.
        _add_snapshot(mock_state, "testvm", f"snap{i}")
        mock_state.add_deferred_blockcommit("testvm", "vda", [f"snap{i}"], "apparmor")

    # VM is running → in qemu-img mode everything is skipped.
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
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(4):
        mock_state.add_deferred_blockcommit("testvm", "vda", [f"snap{i}"], "apparmor")

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
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(5):
        mock_state.add_deferred_blockcommit("testvm", "vda", [f"snap{i}"], "apparmor")

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
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    frozen_dt = datetime(2025, 7, 13, 15, 31)
    since = frozen_dt - timedelta(days=14)
    add_deferred_with_since(mock_state, "testvm", "vda", ["snap1"], "apparmor", since)

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
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(5):
        mock_state.add_deferred_blockcommit("testvm", "vda", [f"snap{i}"], "apparmor")

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
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(10):
        mock_state.add_deferred_blockcommit("testvm", "vda", [f"snap{i}"], "selinux")

    caplog.set_level(logging.CRITICAL)
    core._check_deferred_thresholds()

    critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical_records) > 0
    msg = critical_records[0].getMessage()
    assert "testvm" in msg
    assert "10 deferred blockcommit" in msg
    assert "selinux" in msg


# ════════════════════════════════════════════════════════════════════════════
# NEW TESTS — adaptive drain (design D6)
# ════════════════════════════════════════════════════════════════════════════

# ── A1: test_drain_shutoff_uses_qemu_img_executor ────────────────────────


def test_drain_shutoff_uses_qemu_img_executor(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VM shut off, snapshots all non-tip → factory receives mode="qemu-img".

    After a successful drain the queue is empty.
    """
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshot(mock_state, "testvm", "s1")
    mock_state.add_deferred_blockcommit("testvm", "vda", ["s1"], "apparmor")
    _set_vm_state(mock_shell, "shut off")
    # domblklist points to a DIFFERENT path → s1 is NOT the tip
    _set_domblklist(mock_shell, "/tmp/other.qcow2")

    with patch.object(
        mock_factory,
        "create_lifecycle_manager",
        wraps=mock_factory.create_lifecycle_manager,
    ) as factory_spy:
        core.snapshot()

    # Factory was called at least once with mode="qemu-img" (shut-off path).
    calls_with_qemu_img = [
        c for c in factory_spy.call_args_list if c.kwargs.get("mode") == "qemu-img"
    ]
    assert len(calls_with_qemu_img) >= 1

    # Queue is empty after successful drain.
    assert mock_state.get_deferred_operations("testvm") == []


# ── A2: test_drain_shutoff_tip_remainder_requeued ────────────────────────


def test_drain_shutoff_tip_remainder_requeued(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VM shut off, entry ["s1","s2"] where s2 is the tip → partial drain.

    s1 committed (mode qemu-img) and removed from state.  Queue has ONE
    entry with snapshots==["s2"] and the ORIGINAL reason ("apparmor").
    """
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshot(mock_state, "testvm", "s1")
    _add_snapshot(mock_state, "testvm", "s2")
    mock_state.add_deferred_blockcommit("testvm", "vda", ["s1", "s2"], "apparmor")
    _set_vm_state(mock_shell, "shut off")
    # domblklist returns s2's path → s2 IS the tip, s1 is below it
    _set_domblklist(mock_shell, "/tmp/s2.qcow2")

    core.snapshot()

    remaining = mock_state.get_deferred_operations("testvm")
    assert len(remaining) == 1
    assert remaining[0].snapshots == ["s2"]
    assert remaining[0].reason == "apparmor"

    # s1 has been removed from state (committed).
    snap_names = {s.name for s in mock_state.get_snapshots("testvm")}
    assert "s1" not in snap_names


# ── A3: test_drain_running_virsh_mode_commits_non_active ─────────────────


def test_drain_running_virsh_mode_commits_non_active(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VM running, lifecycle_mode="virsh", snapshots all below active layer.

    Formerly-active snapshots become committable; entry removed from queue.
    """
    vm = make_vm_config(name="testvm", lifecycle_mode="virsh")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshot(mock_state, "testvm", "snap1")
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap1"], "apparmor")
    _set_vm_state(mock_shell, "running")
    # domblklist returns a DIFFERENT (newer) file → snap1 is below active layer
    _set_domblklist(mock_shell, "/tmp/newer_active.qcow2")

    lifecycle_manager = mock_factory._lifecycle_manager
    with (
        patch.object(
            lifecycle_manager,
            "blockcommit",
            wraps=lifecycle_manager.blockcommit,
        ) as bc_spy,
        patch.object(
            mock_factory,
            "create_lifecycle_manager",
            wraps=mock_factory.create_lifecycle_manager,
        ) as factory_spy,
    ):
        core.snapshot()

    # Blockcommit was called (snap1 was committable below the active layer).
    assert bc_spy.called

    # Factory received mode="virsh" for the live blockcommit.
    calls_with_virsh = [c for c in factory_spy.call_args_list if c.kwargs.get("mode") == "virsh"]
    assert len(calls_with_virsh) >= 1

    # Entry was removed from the queue.
    assert mock_state.get_deferred_operations("testvm") == []


# ── A4: test_drain_running_qemu_img_mode_skips ───────────────────────────


def test_drain_running_qemu_img_mode_skips(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VM running + lifecycle_mode="qemu-img" → blockcommit NOT called.

    Queue is unchanged (entry kept).
    """
    vm = make_vm_config(name="testvm", lifecycle_mode="qemu-img")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshot(mock_state, "testvm", "snap1")
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap1"], "apparmor")
    _set_vm_state(mock_shell, "running")

    lifecycle_manager = mock_factory._lifecycle_manager
    with patch.object(
        lifecycle_manager,
        "blockcommit",
        wraps=lifecycle_manager.blockcommit,
    ) as bc_spy:
        core.snapshot()

    # Blockcommit was NOT called (qemu-img mode → nothing committable on running VM).
    assert not bc_spy.called

    # Deferred operations remain unchanged.
    remaining = mock_state.get_deferred_operations("testvm")
    assert len(remaining) == 1
    assert remaining[0].snapshots == ["snap1"]


# ── A5: test_drain_paused_skips ──────────────────────────────────────────


def test_drain_paused_skips(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """VM paused → blockcommit NOT called, queue unchanged.

    Tested with both lifecycle_mode="virsh" (default); paused skips in all modes.
    """
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshot(mock_state, "testvm", "snap1")
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap1"], "apparmor")
    _set_vm_state(mock_shell, "paused")

    caplog.set_level(logging.INFO)
    lifecycle_manager = mock_factory._lifecycle_manager
    with patch.object(
        lifecycle_manager,
        "blockcommit",
        wraps=lifecycle_manager.blockcommit,
    ) as bc_spy:
        core.snapshot()

    # Blockcommit was NOT called (paused → nothing committable).
    assert not bc_spy.called

    # Deferred operations remain unchanged.
    remaining = mock_state.get_deferred_operations("testvm")
    assert len(remaining) == 1

    # INFO log about skipping (D6: "not committable in current VM state").
    assert "not committable" in caplog.text


# ── A6: test_drain_removes_committed_from_state ──────────────────────────


def test_drain_removes_committed_from_state(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """After a successful drain (shut off), committed names gone from state."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshot(mock_state, "testvm", "snap_a")
    _add_snapshot(mock_state, "testvm", "snap_b")
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap_a"], "apparmor")
    _set_vm_state(mock_shell, "shut off")
    # domblklist points elsewhere → both are committable (only snap_a is in the entry)
    _set_domblklist(mock_shell, "/tmp/other.qcow2")

    core.snapshot()

    # snap_a committed → gone from state.
    remaining_snaps = mock_state.get_snapshots("testvm")
    remaining_names = {s.name for s in remaining_snaps}
    assert "snap_a" not in remaining_names
    # snap_b was not in the deferred entry → still in state.
    assert "snap_b" in remaining_names

    # Queue is empty.
    assert mock_state.get_deferred_operations("testvm") == []


# ════════════════════════════════════════════════════════════════════════════
# MULTI-DISK — per-disk deferred drain independence
# ════════════════════════════════════════════════════════════════════════════


def test_multidisk_deferred_drain_per_disk_independence(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
):
    """Both disks have deferred entries → drain processes each independently.

    Each entry gets the correct disk's base_image.  Both succeed and the
    queue is empty afterwards.
    """
    vda_base = Path("/var/lib/libvirt/images/testvm_vda.qcow2")
    vdb_base = Path("/var/lib/libvirt/images/testvm_vdb.qcow2")
    disks = [
        DiskConfig(target="vda", base_image=vda_base),
        DiskConfig(target="vdb", base_image=vdb_base),
    ]
    vm = make_vm_config(name="testvm", disks=disks)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with snapshots for both disks
    for disk, name, path in [
        ("vda", "vda_s1", "/tmp/vda_s1.qcow2"),
        ("vdb", "vdb_s1", "/tmp/vdb_s1.qcow2"),
    ]:
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=name,
                path=Path(path),
                timestamp=datetime(2025, 7, 13, 10, 0),
                allocation=1000,
                disk=disk,
            ),
        )

    frozen_dt = datetime(2025, 7, 13, 15, 31)
    since = frozen_dt - timedelta(days=1)

    # Add deferred entries for both disks
    add_deferred_with_since(mock_state, "testvm", "vda", ["vda_s1"], "apparmor", since)
    add_deferred_with_since(mock_state, "testvm", "vdb", ["vdb_s1"], "selinux", since)

    # VM shut off → committable via qemu-img
    _set_vm_state(mock_shell, "shut off")
    # domblklist returns paths DIFFERENT from the deferred snapshots
    # (so all are committable)
    mock_shell.expect_first("domblklist").returns(
        ShellResult(
            success=True,
            stdout=(
                "Target   Source\n"
                "--------------------------------\n"
                "vda   /tmp/vda_tip.qcow2\n"
                "vdb   /tmp/vdb_tip.qcow2\n"
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    lifecycle_manager = mock_factory._lifecycle_manager

    with (
        frozen_clock(frozen_dt),
        patch.object(
            lifecycle_manager,
            "blockcommit",
            wraps=lifecycle_manager.blockcommit,
        ) as bc_spy,
    ):
        core.snapshot()

    # Both entries were drained (blockcommit called twice)
    assert bc_spy.call_count == 2, f"Expected 2 blockcommit calls, got {bc_spy.call_count}"

    # Collect per-disk call details
    calls_by_disk: dict[str, dict] = {}
    for call in bc_spy.call_args_list:
        disk = call.kwargs["disk"]
        calls_by_disk[disk] = {
            "snapshots": call[0][1],  # positional arg: snapshots_to_merge
            "base_image": call.kwargs["base_image"],
        }

    assert "vda" in calls_by_disk
    assert "vdb" in calls_by_disk

    # vda: correct base_image, only vda snapshots
    vda_snap_names = {s.name for s in calls_by_disk["vda"]["snapshots"]}
    assert vda_snap_names == {"vda_s1"}
    assert calls_by_disk["vda"]["base_image"] == vda_base

    # vdb: correct base_image, only vdb snapshots
    vdb_snap_names = {s.name for s in calls_by_disk["vdb"]["snapshots"]}
    assert vdb_snap_names == {"vdb_s1"}
    assert calls_by_disk["vdb"]["base_image"] == vdb_base

    # Both entries cleared
    assert mock_state.get_deferred_operations("testvm") == []


def test_multidisk_deferred_drain_one_failure_independent(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
):
    """vda deferred fails, vdb deferred succeeds → vda re-queued, vdb cleared.

    One disk's failure/re-queue does NOT disturb the other disk's drain.
    """
    vda_base = Path("/var/lib/libvirt/images/testvm_vda.qcow2")
    vdb_base = Path("/var/lib/libvirt/images/testvm_vdb.qcow2")
    disks = [
        DiskConfig(target="vda", base_image=vda_base),
        DiskConfig(target="vdb", base_image=vdb_base),
    ]
    vm = make_vm_config(name="testvm", disks=disks)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with snapshots for both disks
    for disk, name, path in [
        ("vda", "vda_s1", "/tmp/vda_s1.qcow2"),
        ("vdb", "vdb_s1", "/tmp/vdb_s1.qcow2"),
    ]:
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=name,
                path=Path(path),
                timestamp=datetime(2025, 7, 13, 10, 0),
                allocation=1000,
                disk=disk,
            ),
        )

    frozen_dt = datetime(2025, 7, 13, 15, 31)
    since = frozen_dt - timedelta(days=1)

    add_deferred_with_since(mock_state, "testvm", "vda", ["vda_s1"], "apparmor", since)
    add_deferred_with_since(mock_state, "testvm", "vdb", ["vdb_s1"], "selinux", since)

    _set_vm_state(mock_shell, "shut off")
    mock_shell.expect_first("domblklist").returns(
        ShellResult(
            success=True,
            stdout=(
                "Target   Source\n"
                "--------------------------------\n"
                "vda   /tmp/vda_tip.qcow2\n"
                "vdb   /tmp/vdb_tip.qcow2\n"
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    lifecycle_manager = mock_factory._lifecycle_manager

    # Make blockcommit fail only for vda
    original_bc = lifecycle_manager.blockcommit

    def _fail_vda(vm_config, snapshots_to_merge, *, disk, base_image, deep_verify=False):
        if disk == "vda":
            return CommitResult(
                success=False,
                committed_snapshot="",
                error="vda commit failed",
            )
        return original_bc(
            vm_config,
            snapshots_to_merge,
            disk=disk,
            base_image=base_image,
            deep_verify=deep_verify,
        )

    with (
        frozen_clock(frozen_dt),
        patch.object(
            lifecycle_manager,
            "blockcommit",
            side_effect=_fail_vda,
        ) as bc_spy,
    ):
        core.snapshot()

    # Blockcommit was called twice (once per disk entry)
    assert bc_spy.call_count == 2

    # vdb entry drained (cleared from queue); only vda remains
    remaining = mock_state.get_deferred_operations("testvm")
    assert len(remaining) == 1
    assert remaining[0].disk == "vda"
    assert remaining[0].snapshots == ["vda_s1"]
    assert remaining[0].reason == "apparmor"


# ── test_deferred_threshold_warning_dry_run_no_state_write ────────────────


def test_deferred_threshold_warning_dry_run_no_state_write(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """L2: dry-run logs the threshold WARNING but never writes state."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.dry_run = True

    for i in range(5):
        mock_state.add_deferred_blockcommit("testvm", "vda", [f"snap{i}"], "apparmor")
    deferred_before = mock_state.get_deferred_operations("testvm")

    caplog.set_level(logging.WARNING)
    with patch.object(
        mock_state, "update_deferred_warning", wraps=mock_state.update_deferred_warning
    ) as warn_spy:
        core._check_deferred_thresholds()

    # WARNING still emitted in dry-run...
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    # ...but no state write happens.
    warn_spy.assert_not_called()
    deferred_after = mock_state.get_deferred_operations("testvm")
    assert [e.last_warned_at for e in deferred_before] == [e.last_warned_at for e in deferred_after]

    # Control: the same setup WITHOUT dry-run updates last_warned_at.
    core.dry_run = False
    with patch.object(
        mock_state, "update_deferred_warning", wraps=mock_state.update_deferred_warning
    ) as warn_spy_real:
        core._check_deferred_thresholds()
    assert warn_spy_real.call_count == 1


# ════════════════════════════════════════════════════════════════════════════
# ENOSPC deferral (design D4 — blockcommit space errors defer, not abort)
# ════════════════════════════════════════════════════════════════════════════


def test_offline_commit_enospc_defers_no_runtime_error(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """An offline (qemu-img) commit hitting ENOSPC defers — no RuntimeError.

    The commit failure is queued with reason ``"enospc"``, the snapshot
    state record is preserved (removal only happens after a successful
    commit), and the run is flagged space-limited (design D4/D6).
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshot(mock_state, "testvm", "snap1")
    snap_info = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    _set_vm_state(mock_shell, "shut off")
    # domblklist points elsewhere → snap1 is NOT the tip → committable
    _set_domblklist(mock_shell, "/tmp/other.qcow2")

    enospc = CommitResult(
        success=False,
        committed_snapshot="",
        error="qemu-img commit failed: No space left on device",
    )

    with patch.object(mock_factory._lifecycle_manager, "blockcommit", return_value=enospc):
        # Must NOT raise RuntimeError.
        core._blockcommit_one_disk(vm, "vda", [snap_info])

    remaining = mock_state.get_deferred_operations("testvm")
    assert len(remaining) == 1
    assert remaining[0].reason == "enospc"
    assert remaining[0].snapshots == ["snap1"]

    # Snapshot state record preserved — the merge is retried intact.
    assert len(mock_state.get_snapshots("testvm")) == 1

    # Exit-code tracking: blockcommit space errors set the disk-full flag.
    assert "blockcommit:testvm:vda" in core._space_limited_targets


def test_live_commit_enospc_defers(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A live (virsh) commit hitting ENOSPC also defers with reason "enospc"."""
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(name="testvm", lifecycle_mode="virsh")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshot(mock_state, "testvm", "snap1")
    snap_info = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    _set_vm_state(mock_shell, "running")
    # domblklist points to a NEWER file → snap1 is below the active layer
    _set_domblklist(mock_shell, "/tmp/newer_active.qcow2")

    enospc = CommitResult(
        success=False,
        committed_snapshot="",
        error="virsh blockcommit failed: disk quota exceeded",
    )

    with patch.object(mock_factory._lifecycle_manager, "blockcommit", return_value=enospc):
        core._blockcommit_one_disk(vm, "vda", [snap_info])

    remaining = mock_state.get_deferred_operations("testvm")
    assert len(remaining) == 1
    assert remaining[0].reason == "enospc"
    assert len(mock_state.get_snapshots("testvm")) == 1


def test_deferred_enospc_drained_later(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A deferred enospc entry is drained by the next run after space frees.

    Run 1: offline commit hits ENOSPC → deferred, no exception, snapshot
    record intact.  Run 2: the deferred drain commits and removes the
    snapshot; the queue is cleared.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshot(mock_state, "testvm", "snap1")
    snap_info = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    _set_vm_state(mock_shell, "shut off")
    _set_domblklist(mock_shell, "/tmp/other.qcow2")

    # Run 1: ENOSPC → deferred.
    enospc = CommitResult(
        success=False,
        committed_snapshot="",
        error="qemu-img commit failed: No space left on device",
    )
    with patch.object(mock_factory._lifecycle_manager, "blockcommit", return_value=enospc):
        core._blockcommit_one_disk(vm, "vda", [snap_info])

    deferred = mock_state.get_deferred_operations("testvm")
    assert len(deferred) == 1
    assert deferred[0].reason == "enospc"

    # Run 2: space freed → the next run drains the entry.
    lifecycle_manager = mock_factory._lifecycle_manager
    with patch.object(
        lifecycle_manager,
        "blockcommit",
        wraps=lifecycle_manager.blockcommit,
    ) as bc_spy:
        core._check_deferred_operations(vm)

    assert bc_spy.called, "the deferred enospc entry must be drained on the next run"
    assert mock_state.get_deferred_operations("testvm") == []
    assert mock_state.get_snapshots("testvm") == [], (
        "the committed snapshot is removed from state after a successful drain"
    )


def test_enospc_deferred_threshold_monitoring(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Deferred enospc entries appear in threshold monitoring and listing."""
    global_config = make_global_config(
        deferred_warn_count="5",
        deferred_crit_count="10",
        deferred_warn_age="7d",
        deferred_crit_age="14d",
    )
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_config, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(5):
        mock_state.add_deferred_blockcommit("testvm", "vda", [f"snap{i}"], "enospc")

    # Threshold monitoring logs the warning naming the enospc reason.
    caplog.set_level(logging.WARNING)
    core._check_deferred_thresholds()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) >= 1
    assert "enospc" in warnings[0].getMessage()

    # list_deferred summarizes the enospc entries per disk.
    summaries = core.list_deferred()
    assert len(summaries) == 1
    assert summaries[0].reason == "enospc"
    assert summaries[0].snapshot_count == 5


def test_non_space_commit_failure_aborts(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A non-space commit failure still aborts the VM (RuntimeError).

    Only space-classified failures defer; everything else keeps the
    VM-level isolation abort (verify-before-delete is not weakened).
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshot(mock_state, "testvm", "snap1")
    snap_info = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    _set_vm_state(mock_shell, "shut off")
    _set_domblklist(mock_shell, "/tmp/other.qcow2")

    failure = CommitResult(
        success=False,
        committed_snapshot="",
        error="qemu-img commit failed: input/output error",
    )

    with (
        patch.object(mock_factory._lifecycle_manager, "blockcommit", return_value=failure),
        pytest.raises(RuntimeError, match="Blockcommit failed"),
    ):
        core._blockcommit_one_disk(vm, "vda", [snap_info])

    # Not deferred, not space-limited — a definitive non-space abort.
    assert mock_state.get_deferred_operations("testvm") == []
    assert core._space_limited_targets == set()
