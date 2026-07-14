"""Tests for qsnap.cli.commands — handler dispatch and flag translation.

Each handler is a thin translation layer: it receives a Core instance and
parsed CLI args, calls the appropriate Core method, and formats the
returned results.  These tests verify that the correct Core method is
called with the correct arguments, that CLI flags are properly translated,
and that call ordering is correct.
"""

from __future__ import annotations

import logging
from argparse import Namespace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from qsnap.cli.commands import (
    handle_backup,
    handle_check,
    handle_list,
    handle_list_deferred,
    handle_prune,
    handle_restore,
    handle_run,
    handle_snapshot,
    handle_stats,
)
from qsnap.cli.errors import EXIT_GENERIC, EXIT_SUCCESS
from qsnap.core import Core, PipelineResult, VMRunResult
from qsnap.models.config import VMConfig
from qsnap.models.results import DeferredSummary, RestoreResult, SnapshotInfo

# ── helpers ─────────────────────────────────────────────────────────────


def _make_action_args(**overrides) -> Namespace:
    """Create a Namespace for action subcommands (run/snapshot/backup/prune)."""
    defaults: dict[str, object] = {
        "command": "run",
        "vm": [],
        "print_schedule": False,
        "timer": False,
        "dry_run": False,
        "preserve": False,
        "preserve_snapshots": False,
        "preserve_backups": False,
        "format": "table",
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def _make_list_args(**overrides) -> Namespace:
    """Create a Namespace for the list subcommand."""
    defaults: dict[str, object] = {
        "command": "list",
        "list_subcommand": "snapshots",
        "vm": [],
        "format": "table",
        "tree": False,
        "dry_run": False,
        "preserve": False,
        "preserve_snapshots": False,
        "preserve_backups": False,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def _make_mock_core() -> Mock:
    """Create a Mock core with sensible return values for all methods."""
    core = Mock()
    core.run.return_value = PipelineResult(
        results=[VMRunResult(vm_name="vm1", success=True)]
    )
    core.snapshot.return_value = PipelineResult(
        results=[VMRunResult(vm_name="vm1", success=True)]
    )
    core.backup.return_value = PipelineResult(
        results=[VMRunResult(vm_name="vm1", success=True)]
    )
    core.prune.return_value = PipelineResult(
        results=[VMRunResult(vm_name="vm1", success=True)]
    )
    core.print_schedule.return_value = {}
    core.schedule_summary.return_value = ""
    core.list_snapshots.return_value = {}
    core.list_backups.return_value = {}
    core.list_config.return_value = []
    core.list_latest.return_value = {}
    core.check.return_value = {}
    core.restore.return_value = RestoreResult(
        success=True,
        snapshot_name="",
        restored_path=Path("/tmp"),
        chain_files=[],
        error=None,
    )
    return core


# ── action subcommand dispatch tests ───────────────────────────────────


def test_run_subcommand_dispatches_to_core_run():
    mock_core = _make_mock_core()
    args = _make_action_args(command="run")
    handle_run(mock_core, args)
    mock_core.run.assert_called_once_with(None)


def test_snapshot_subcommand_dispatches_to_core_snapshot():
    mock_core = _make_mock_core()
    args = _make_action_args(command="snapshot")
    handle_snapshot(mock_core, args)
    mock_core.snapshot.assert_called_once_with(None)


def test_backup_subcommand_dispatches_to_core_backup():
    mock_core = _make_mock_core()
    args = _make_action_args(command="backup")
    handle_backup(mock_core, args)
    mock_core.backup.assert_called_once_with(None)


def test_prune_subcommand_dispatches_to_core_prune():
    mock_core = _make_mock_core()
    args = _make_action_args(command="prune")
    handle_prune(mock_core, args)
    mock_core.prune.assert_called_once_with(None)


# ── list subcommand dispatch tests ──────────────────────────────────────


def test_list_snapshots_subcommand_dispatches_to_core_list_snapshots():
    mock_core = _make_mock_core()
    args = _make_list_args(list_subcommand="snapshots")
    handle_list(mock_core, args)
    mock_core.list_snapshots.assert_called_once_with(None)


def test_list_backups_subcommand_dispatches_to_core_list_backups():
    mock_core = _make_mock_core()
    args = _make_list_args(list_subcommand="backups")
    handle_list(mock_core, args)
    mock_core.list_backups.assert_called_once_with(None)


def test_list_config_subcommand_dispatches_to_core_list_config():
    mock_core = _make_mock_core()
    args = _make_list_args(list_subcommand="config")
    handle_list(mock_core, args)
    mock_core.list_config.assert_called_once_with()


def test_list_latest_subcommand_dispatches_to_core_list_latest():
    mock_core = _make_mock_core()
    args = _make_list_args(list_subcommand="latest")
    handle_list(mock_core, args)
    mock_core.list_latest.assert_called_once_with(None)


# ── stats and check dispatch tests ─────────────────────────────────────


def test_stats_subcommand_dispatches_to_core_list_snapshots_and_backups():
    mock_core = _make_mock_core()
    args = _make_action_args(command="stats")
    handle_stats(mock_core, args)
    mock_core.list_snapshots.assert_called_once_with(None)
    mock_core.list_backups.assert_called_once_with(None)


def test_check_subcommand_dispatches_to_core_check():
    mock_core = _make_mock_core()
    args = _make_action_args(command="check")
    handle_check(mock_core, args)
    mock_core.check.assert_called_once_with(None, deep=False)


# ── flag translation tests (real Core) ─────────────────────────────────


def test_dry_run_flag_sets_core_dry_run_true(
    mock_config, mock_factory, mock_state, mock_shell
):
    core = Core(mock_config, mock_factory, mock_state, mock_shell)
    args = _make_action_args(dry_run=True)
    core.dry_run = args.dry_run
    handle_run(core, args)
    assert core.dry_run is True


def test_preserve_flag_sets_both_preserve_properties(
    mock_config, mock_factory, mock_state, mock_shell
):
    core = Core(mock_config, mock_factory, mock_state, mock_shell)
    args = _make_action_args(preserve=True)
    if args.preserve:
        core.preserve_snapshots = True
        core.preserve_backups = True
    handle_run(core, args)
    assert core.preserve_snapshots is True
    assert core.preserve_backups is True


def test_preserve_snapshots_flag_sets_only_preserve_snapshots(
    mock_config, mock_factory, mock_state, mock_shell
):
    core = Core(mock_config, mock_factory, mock_state, mock_shell)
    args = _make_action_args(preserve_snapshots=True)
    if args.preserve_snapshots:
        core.preserve_snapshots = True
    handle_run(core, args)
    assert core.preserve_snapshots is True
    assert core.preserve_backups is False


# ── print-schedule and vm-filter tests ─────────────────────────────────


def test_print_schedule_flag_dispatches_to_core_schedule_summary():
    mock_core = _make_mock_core()
    args = _make_action_args(print_schedule=True, dry_run=True)
    handle_run(mock_core, args)
    mock_core.schedule_summary.assert_called_once_with(None)
    mock_core.run.assert_called_once_with(None)
    call_names = [c[0] for c in mock_core.mock_calls]
    assert call_names.index("schedule_summary") < call_names.index("run")


def test_vm_filter_positional_passed_to_core_method():
    mock_core = _make_mock_core()
    args = _make_action_args(vm=["debiantest"])
    handle_run(mock_core, args)
    mock_core.run.assert_called_once_with("debiantest")


def test_no_vm_filter_passes_none_to_core_method():
    mock_core = _make_mock_core()
    args = _make_action_args(vm=[])
    handle_run(mock_core, args)
    mock_core.run.assert_called_once_with(None)


# ── restore subcommand dispatch tests ────────────────────────────────────


def test_handle_restore_dispatches_to_core_restore_with_positional_args(
    cli_app, tmp_path
):
    """Parse 'restore SNAP TARGET' args, verify core.restore is called."""
    mock_core = _make_mock_core()
    args = cli_app.parse_args(["restore", "SNAP", str(tmp_path)])
    handle_restore(mock_core, args)
    mock_core.restore.assert_called_once_with("SNAP", tmp_path, None)


def test_handle_restore_nonexistent_backup_returns_exit_1(cli_app, tmp_path):
    """When core.restore() returns RestoreResult(success=False), returns EXIT_GENERIC."""
    mock_core = _make_mock_core()
    mock_core.restore.return_value = RestoreResult(
        success=False,
        snapshot_name="SNAP",
        restored_path=tmp_path,
        chain_files=[],
        error="Snapshot 'SNAP' not found",
    )
    args = cli_app.parse_args(["restore", "SNAP", str(tmp_path)])
    result = handle_restore(mock_core, args)
    assert result == EXIT_GENERIC


def test_handle_restore_missing_target_dir_returns_exit_1(cli_app, tmp_path):
    """When target_dir does not exist, returns EXIT_GENERIC without calling core.restore()."""
    mock_core = _make_mock_core()
    nonexistent = tmp_path / "does_not_exist"
    args = cli_app.parse_args(["restore", "SNAP", str(nonexistent)])
    result = handle_restore(mock_core, args)
    assert result == EXIT_GENERIC
    mock_core.restore.assert_not_called()


# ── check --deep flag tests ──────────────────────────────────────────────


def test_handle_check_deep_passes_deep_true_to_core(cli_app):
    """Parse 'check --deep' args, verify core.check(vm_filter=None, deep=True)."""
    mock_core = _make_mock_core()
    args = cli_app.parse_args(["check", "--deep"])
    handle_check(mock_core, args)
    mock_core.check.assert_called_once_with(None, deep=True)


def test_handle_check_without_deep_passes_deep_false_to_core(cli_app):
    """Parse 'check' args (no --deep), verify core.check(vm_filter=None, deep=False)."""
    mock_core = _make_mock_core()
    args = cli_app.parse_args(["check"])
    handle_check(mock_core, args)
    mock_core.check.assert_called_once_with(None, deep=False)


# ── --tree flag dispatch tests ───────────────────────────────────────────


def test_list_snapshots_tree_dispatches_to_core_list_snapshots(capsys):
    """handle_list with tree=True calls core.list_snapshots() and _print_tree."""
    mock_core = _make_mock_core()
    mock_core.list_snapshots.return_value = {
        "testvm": [
            SnapshotInfo(
                name="snap1",
                path=Path("/var/lib/libvirt/snapshots/testvm/testvm.snap1.qcow2"),
                timestamp=datetime(2025, 7, 14, 10, 0),
                allocation=1024,
            ),
        ]
    }
    mock_core.list_config.return_value = [
        VMConfig(
            name="testvm",
            base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
            snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
        )
    ]
    args = _make_list_args(tree=True)
    result = handle_list(mock_core, args)

    assert result == EXIT_SUCCESS
    mock_core.list_snapshots.assert_called_once_with(None)
    mock_core.list_config.assert_called_once_with()
    captured = capsys.readouterr()
    assert "=== testvm ===" in captured.out
    assert "testvm.qcow2" in captured.out
    assert "  testvm.snap1.qcow2" in captured.out


# ── --print-schedule and --timer behavior tests ─────────────────────────


def test_print_schedule_with_run_prints_before_pipeline(capsys):
    """When --print-schedule is set with --dry-run, schedule_summary is called
    and printed BEFORE the pipeline executes in dry-run mode.
    """
    mock_core = _make_mock_core()
    mock_core.schedule_summary.return_value = "SCHEDULE SUMMARY"
    args = _make_action_args(command="run", print_schedule=True, dry_run=True)

    handle_run(mock_core, args)

    # schedule_summary was called
    mock_core.schedule_summary.assert_called_once_with(None)

    # The summary was printed to stdout
    captured = capsys.readouterr()
    assert "SCHEDULE SUMMARY" in captured.out

    # The pipeline DID execute (not skipped, because --dry-run was set)
    mock_core.run.assert_called_once_with(None)

    # schedule_summary was called BEFORE run
    call_names = [c[0] for c in mock_core.mock_calls]
    assert call_names.index("schedule_summary") < call_names.index("run")


def test_standalone_print_schedule_exits_without_snapshots(capsys):
    """When --print-schedule is set WITHOUT --dry-run, the handler prints
    the schedule and exits without creating snapshots.

    Per the test-plan, --print-schedule should act as a standalone preview:
    print the schedule and return without invoking the pipeline.
    """
    mock_core = _make_mock_core()
    mock_core.schedule_summary.return_value = "SCHEDULE OUTPUT"
    args = _make_action_args(command="run", print_schedule=True, dry_run=False)

    handle_run(mock_core, args)

    # Schedule should be printed to stdout
    captured = capsys.readouterr()
    assert "SCHEDULE OUTPUT" in captured.out

    # Pipeline should NOT run — no snapshots created
    mock_core.run.assert_not_called()


def test_timer_invocation_logs_schedule_at_info(caplog):
    """When --timer is set, _handle_schedule_and_timer logs the schedule
    summary at INFO level via logger.info."""
    mock_core = _make_mock_core()
    mock_core.schedule_summary.return_value = "TIMER SCHEDULE"
    args = _make_action_args(command="run", timer=True)

    with caplog.at_level(logging.INFO, logger="qsnap.cli.commands"):
        handle_run(mock_core, args)

    # schedule_summary was called for the timer
    mock_core.schedule_summary.assert_called_once_with(None)

    # The schedule was logged at INFO level
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("TIMER SCHEDULE" in r.getMessage() for r in info_records)


# ── list deferred subcommand dispatch tests ─────────────────────────────


def _make_deferred_summary(
    vm_name: str = "vm-home",
    snapshot_count: int = 3,
    reason: str = "apparmor",
    age_hours: int = 2,
    since: datetime | None = None,
) -> DeferredSummary:
    """Create a DeferredSummary for tests."""
    if since is None:
        since = datetime(2025, 7, 14, 10, 0)
    return DeferredSummary(
        vm_name=vm_name,
        snapshot_count=snapshot_count,
        reason=reason,
        age=timedelta(hours=age_hours),
        since=since,
    )


def test_list_deferred_dispatches_to_core():
    """handle_list with subcommand='deferred' calls core.list_deferred(None)."""
    mock_core = _make_mock_core()
    mock_core.list_deferred.return_value = []
    args = _make_list_args(list_subcommand="deferred")
    handle_list(mock_core, args)
    mock_core.list_deferred.assert_called_once_with(None)


def test_list_deferred_with_vm_filter_dispatches():
    """handle_list with subcommand='deferred' and a VM filter calls core.list_deferred(vm)."""
    mock_core = _make_mock_core()
    mock_core.list_deferred.return_value = []
    args = _make_list_args(list_subcommand="deferred", vm=["vm-home"])
    handle_list(mock_core, args)
    mock_core.list_deferred.assert_called_once_with("vm-home")


def test_list_deferred_format_raw(capsys):
    """handle_list_deferred with --format raw produces raw key=value output."""
    since_dt = datetime(2025, 7, 14, 10, 0)
    mock_core = _make_mock_core()
    mock_core.list_deferred.return_value = [
        _make_deferred_summary(
            vm_name="vm-home",
            snapshot_count=3,
            reason="apparmor",
            since=since_dt,
        )
    ]
    args = _make_list_args(list_subcommand="deferred", format="raw")
    result = handle_list_deferred(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "vm_name=vm-home" in captured.out
    assert "snapshots=3" in captured.out
    assert "reason=apparmor" in captured.out
    assert f"since={since_dt.isoformat()}" in captured.out


def test_list_deferred_all_operations(capsys):
    """handle_list_deferred with multiple VMs produces a table with all columns."""
    mock_core = _make_mock_core()
    mock_core.list_deferred.return_value = [
        _make_deferred_summary(
            vm_name="vm-home",
            snapshot_count=3,
            reason="apparmor",
            age_hours=2,
        ),
        _make_deferred_summary(
            vm_name="vm-work",
            snapshot_count=1,
            reason="selinux",
            age_hours=5,
        ),
    ]
    args = _make_list_args(list_subcommand="deferred")
    result = handle_list_deferred(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    output = captured.out
    # Table headers present
    assert "VM" in output
    assert "SNAPSHOTS" in output
    assert "REASON" in output
    assert "AGE" in output
    # Both VMs present
    assert "vm-home" in output
    assert "vm-work" in output


def test_list_deferred_filtered_by_vm(capsys):
    """handle_list_deferred with a VM filter shows only the matching VM."""
    mock_core = _make_mock_core()
    # Core.list_deferred is expected to already filter; return only the filtered VM
    mock_core.list_deferred.return_value = [
        _make_deferred_summary(
            vm_name="vm-home",
            snapshot_count=3,
            reason="apparmor",
            age_hours=2,
        ),
    ]
    args = _make_list_args(list_subcommand="deferred", vm=["vm-home"])
    result = handle_list_deferred(mock_core, args)

    assert result == EXIT_SUCCESS
    mock_core.list_deferred.assert_called_once_with("vm-home")
    captured = capsys.readouterr()
    assert "vm-home" in captured.out
    # The other VM is not present (Core filtered it out)
    assert "vm-work" not in captured.out


def test_list_deferred_no_operations(capsys):
    """handle_list_deferred with no deferred ops prints the empty message."""
    mock_core = _make_mock_core()
    mock_core.list_deferred.return_value = []
    args = _make_list_args(list_subcommand="deferred")
    result = handle_list_deferred(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "No deferred blockcommit operations" in captured.out
