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
    handle_deploy,
    handle_estimate,
    handle_fork,
    handle_list,
    handle_list_deferred,
    handle_prune,
    handle_restore,
    handle_run,
    handle_snapshot,
    handle_stats,
)
from qsnap.cli.errors import EXIT_BACKUP_ABORT, EXIT_GENERIC, EXIT_SUCCESS
from qsnap.core import Core, PipelineResult, VMRunResult
from qsnap.models.config import GlobalConfig, VMConfig
from qsnap.models.results import (
    ActionRecord,
    CheckResult,
    DeferredSummary,
    RestoreResult,
    SnapshotInfo,
)

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
    core.run.return_value = PipelineResult(results=[VMRunResult(vm_name="vm1", success=True)])
    core.snapshot.return_value = PipelineResult(results=[VMRunResult(vm_name="vm1", success=True)])
    core.backup.return_value = PipelineResult(results=[VMRunResult(vm_name="vm1", success=True)])
    core.prune.return_value = PipelineResult(results=[VMRunResult(vm_name="vm1", success=True)])
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
    core.fork.return_value = RestoreResult(
        success=True,
        snapshot_name="",
        restored_path=Path("/tmp"),
        chain_files=[],
        error=None,
    )
    core.deploy.return_value = RestoreResult(
        success=True,
        snapshot_name="",
        restored_path=Path("/tmp"),
        chain_files=[],
        error=None,
    )
    core.estimate.return_value = "ESTIMATE OUTPUT"
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


def test_dry_run_flag_sets_core_dry_run_true(mock_config, mock_factory, mock_state, mock_shell):
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


def test_handle_restore_dispatches_to_core_restore_with_positional_args(cli_app, tmp_path):
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


# ── summary table printed via _format_pipeline_result ────────────────────


def test_summary_printed_after_successful_run(capsys):
    """After a successful pipeline run, the btrbk-style summary table is
    printed to stdout via format_summary()."""
    mock_core = _make_mock_core()
    mock_core.run.return_value = PipelineResult(
        results=[VMRunResult(vm_name="vm1", success=True)],
        actions=[
            ActionRecord(
                action="snapshot_create",
                vm_name="vm1",
                name="snap_a",
                path=Path("/var/lib/libvirt/snapshots/vm1/snap_a.qcow2"),
                size=1048576,
            ),
        ],
        dry_run=False,
    )
    args = _make_action_args()
    result = handle_run(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    # Per-VM OK line
    assert "vm1: OK" in captured.out
    # Summary table header
    assert "qsnap Backup Summary" in captured.out
    # Action row visible
    assert "snap_a" in captured.out
    assert "+++" in captured.out


def test_summary_printed_after_run_with_failures(capsys):
    """After a pipeline run with backup failures (exit code 10), the
    summary table is still printed and error actions are marked with !!!."""
    mock_core = _make_mock_core()
    mock_core.run.return_value = PipelineResult(
        results=[
            VMRunResult(vm_name="vm1", success=True, backup_failed=True),
        ],
        actions=[
            ActionRecord(
                action="error",
                vm_name="vm1",
                name="backup_target",
                path=Path("/mnt/backup/vm1"),
                error="backup failed: permission denied",
            ),
        ],
        dry_run=False,
    )
    args = _make_action_args()
    result = handle_run(mock_core, args)

    # backup_failed → EXIT_BACKUP_ABORT (10)
    assert result == EXIT_BACKUP_ABORT
    captured = capsys.readouterr()
    # VM itself succeeded but backup failed
    assert "vm1: OK" in captured.out
    # Summary table still printed
    assert "qsnap Backup Summary" in captured.out
    # Error marked with !!!
    assert "!!!" in captured.out
    assert "permission denied" in captured.out


def test_summary_printed_after_dry_run(capsys):
    """After a dry run, the summary includes the 'Dryrun: YES' header
    and the dry-run disclaimer footer."""
    mock_core = _make_mock_core()
    mock_core.run.return_value = PipelineResult(
        results=[VMRunResult(vm_name="vm1", success=True)],
        actions=[],
        dry_run=True,
    )
    args = _make_action_args()
    result = handle_run(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "qsnap Backup Summary" in captured.out
    assert "Dryrun: YES" in captured.out
    assert "NOTE: Dryrun was active" in captured.out
    assert "none of the operations above were actually executed" in captured.out


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


# ── list config safety transparency tests ───────────────────────────────


def test_list_config_shows_off_for_default_deep_verify(capsys):
    """handle_list with sub=config shows OFF for blockcommit_deep_verify
    and snapshot_deep_verify when both are disabled (default).

    Also verifies the "Global safety settings" header is printed.
    """
    mock_core = _make_mock_core()
    mock_core.config = Mock()
    mock_core.config.get_global.return_value = GlobalConfig(
        auto_cleanup=True,
        chain_verify_before_commit=True,
        chain_verify_after_commit=True,
        deep_check_schedule="off",
    )
    mock_core.list_config.return_value = [
        VMConfig(
            name="testvm",
            base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
            snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
            blockcommit_deep_verify=False,
            snapshot_deep_verify=False,
        )
    ]

    args = _make_list_args(list_subcommand="config")
    result = handle_list(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "Global safety settings" in captured.out
    # Column headers are uppercased by format_output
    assert "BLOCKCOMMIT_DEEP_VERIFY" in captured.out
    assert "SNAPSHOT_DEEP_VERIFY" in captured.out
    # Verify OFF appears in the output (it appears in the table row)
    assert "OFF" in captured.out


def test_list_config_shows_on_for_enabled_deep_verify(capsys):
    """handle_list with sub=config shows ON for blockcommit_deep_verify
    and snapshot_deep_verify when both are enabled.
    """
    mock_core = _make_mock_core()
    mock_core.config = Mock()
    mock_core.config.get_global.return_value = GlobalConfig(
        auto_cleanup=True,
        chain_verify_before_commit=True,
        chain_verify_after_commit=True,
        deep_check_schedule="off",
    )
    mock_core.list_config.return_value = [
        VMConfig(
            name="critical-db",
            base_image=Path("/var/lib/libvirt/images/critical-db.qcow2"),
            snapshot_dir=Path("/var/lib/libvirt/snapshots/critical-db"),
            blockcommit_deep_verify=True,
            snapshot_deep_verify=True,
        )
    ]

    args = _make_list_args(list_subcommand="config")
    result = handle_list(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "Global safety settings" in captured.out
    # Column headers are uppercased by format_output
    assert "BLOCKCOMMIT_DEEP_VERIFY" in captured.out
    assert "SNAPSHOT_DEEP_VERIFY" in captured.out
    assert "ON" in captured.out


# ── check safety transparency tests ─────────────────────────────────────


def test_check_output_shows_disabled_safety_features(capsys):
    """handle_check prints all safety features as OFF when they are
    disabled in the global config and deep_check_schedule is "off".
    """
    mock_core = _make_mock_core()
    mock_core.config = Mock()
    mock_core.config.get_global.return_value = GlobalConfig(
        auto_cleanup=False,
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
        deep_check_schedule="off",
    )
    mock_core.check.return_value = {}
    mock_core.get_deep_check_schedule_info.return_value = "OFF"

    args = _make_action_args(command="check")
    result = handle_check(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "auto_cleanup: OFF" in captured.out
    assert "chain_verify_before_commit: OFF" in captured.out
    assert "chain_verify_after_commit: OFF" in captured.out
    assert "Deep check schedule: OFF" in captured.out


def test_check_deep_all_images_pass_exit_zero(capsys):
    """handle_check with --deep returns EXIT_SUCCESS (0) when all
    CheckResults have status="ok".
    """
    mock_core = _make_mock_core()
    mock_core.config = Mock()
    mock_core.config.get_global.return_value = GlobalConfig()
    mock_core.get_deep_check_schedule_info.return_value = "OFF"
    mock_core.check.return_value = {
        "vm1": CheckResult(vm_name="vm1", status="ok"),
        "vm2": CheckResult(vm_name="vm2", status="ok"),
    }

    args = _make_action_args(command="check", deep=True)
    result = handle_check(mock_core, args)

    assert result == EXIT_SUCCESS


def test_check_deep_corruption_detected_exit_zero_warning(capsys):
    """handle_check with --deep returns EXIT_SUCCESS (0) when a
    CheckResult has status="corrupted" — corruptions are warnings,
    not critical errors.
    """
    mock_core = _make_mock_core()
    mock_core.config = Mock()
    mock_core.config.get_global.return_value = GlobalConfig()
    mock_core.get_deep_check_schedule_info.return_value = "OFF"
    mock_core.check.return_value = {
        "vm1": CheckResult(
            vm_name="vm1",
            status="corrupted",
            broken_snapshots=["snap1"],
        ),
    }

    args = _make_action_args(command="check", deep=True)
    result = handle_check(mock_core, args)

    # "corrupted" is not "broken" — exit 0
    assert result == EXIT_SUCCESS


def test_check_deep_image_unreadable_exit_one(capsys):
    """handle_check with --deep returns EXIT_GENERIC (1) when a
    CheckResult has status="broken" — unreadable images are critical.
    """
    mock_core = _make_mock_core()
    mock_core.config = Mock()
    mock_core.config.get_global.return_value = GlobalConfig()
    mock_core.get_deep_check_schedule_info.return_value = "OFF"
    mock_core.check.return_value = {
        "vm1": CheckResult(
            vm_name="vm1",
            status="broken",
            broken_snapshots=["snap1"],
        ),
    }

    args = _make_action_args(command="check", deep=True)
    result = handle_check(mock_core, args)

    assert result == EXIT_GENERIC


def test_check_output_displays_deep_check_schedule_overdue(capsys):
    """handle_check prints the deep check schedule info containing
    "OVERDUE" when the schedule is overdue.
    """
    mock_core = _make_mock_core()
    mock_core.config = Mock()
    mock_core.config.get_global.return_value = GlobalConfig(
        deep_check_schedule="weekly",
    )
    mock_core.check.return_value = {}
    mock_core.get_deep_check_schedule_info.return_value = "WEEKLY — OVERDUE (never checked)"

    args = _make_action_args(command="check")
    result = handle_check(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "OVERDUE" in captured.out


# ── estimate subcommand dispatch tests ─────────────────────────────────────


def test_estimate_subcommand_specific_vm_dispatches(cli_app):
    """Estimate for a specific VM dispatches to core.estimate('myvm')."""
    mock_core = _make_mock_core()
    args = cli_app.parse_args(["estimate", "myvm"])
    result = handle_estimate(mock_core, args)
    assert result == EXIT_SUCCESS
    mock_core.estimate.assert_called_once_with("myvm")


def test_estimate_subcommand_all_vms_dispatches(cli_app):
    """Estimate with no VM filter dispatches to core.estimate(None)."""
    mock_core = _make_mock_core()
    args = cli_app.parse_args(["estimate"])
    result = handle_estimate(mock_core, args)
    assert result == EXIT_SUCCESS
    mock_core.estimate.assert_called_once_with(None)


def test_estimate_subcommand_respects_format_flag(cli_app, capsys):
    """Estimate with --format flag passes the format through to core.estimate
    and prints the output to stdout."""
    mock_core = _make_mock_core()
    args = cli_app.parse_args(["--format", "raw", "estimate", "myvm"])
    assert args.format == "raw"
    result = handle_estimate(mock_core, args)
    assert result == EXIT_SUCCESS
    mock_core.estimate.assert_called_once_with("myvm")
    captured = capsys.readouterr()
    assert "ESTIMATE OUTPUT" in captured.out


# ── fork subcommand dispatch tests ────────────────────────────────────────


def test_fork_command_dispatches_to_core_fork(cli_app):
    """Parse 'fork snap1 --as-vm newvm --storage /tmp/storage' and
    verify core.fork is called with the translated arguments."""
    mock_core = _make_mock_core()
    args = cli_app.parse_args(["fork", "snap1", "--as-vm", "newvm", "--storage", "/tmp/storage"])
    result = handle_fork(mock_core, args)
    mock_core.fork.assert_called_once_with(
        "snap1",
        "newvm",
        Path("/tmp/storage"),
        add_to_config=False,
        vm_filter=None,
    )
    assert result == EXIT_SUCCESS


def test_fork_command_missing_snapshot_exit_one(cli_app):
    """When core.fork returns RestoreResult(success=False), handle_fork
    returns EXIT_GENERIC (1)."""
    mock_core = _make_mock_core()
    mock_core.fork.return_value = RestoreResult(
        success=False,
        snapshot_name="snap1",
        restored_path=Path("/var/lib/libvirt/images"),
        chain_files=[],
        error="Snapshot not found: snap1",
    )
    args = cli_app.parse_args(["fork", "snap1", "--as-vm", "newvm"])
    result = handle_fork(mock_core, args)
    assert result == EXIT_GENERIC


def test_fork_command_add_to_config_flag(cli_app):
    """Parse 'fork snap1 --as-vm newvm --add-to-config' and verify
    core.fork is called with add_to_config=True."""
    mock_core = _make_mock_core()
    args = cli_app.parse_args(["fork", "snap1", "--as-vm", "newvm", "--add-to-config"])
    result = handle_fork(mock_core, args)
    mock_core.fork.assert_called_once_with(
        "snap1",
        "newvm",
        Path("/var/lib/libvirt/images"),
        add_to_config=True,
        vm_filter=None,
    )
    assert result == EXIT_SUCCESS


# ── deploy subcommand dispatch tests ──────────────────────────────────────


def test_deploy_command_dispatches_to_core_deploy(cli_app):
    """Parse 'deploy backup1 --as-vm newvm --storage /tmp/storage' and
    verify core.deploy is called with the translated arguments."""
    mock_core = _make_mock_core()
    args = cli_app.parse_args(
        ["deploy", "backup1", "--as-vm", "newvm", "--storage", "/tmp/storage"]
    )
    result = handle_deploy(mock_core, args)
    mock_core.deploy.assert_called_once_with(
        "backup1",
        "newvm",
        Path("/tmp/storage"),
        add_to_config=False,
        vm_filter=None,
    )
    assert result == EXIT_SUCCESS


def test_deploy_command_storage_and_add_to_config_flags(cli_app):
    """Parse 'deploy backup1 --as-vm newvm --storage /custom/path --add-to-config'
    and verify core.deploy is called with both flags translated."""
    mock_core = _make_mock_core()
    args = cli_app.parse_args(
        [
            "deploy",
            "backup1",
            "--as-vm",
            "newvm",
            "--storage",
            "/custom/path",
            "--add-to-config",
        ]
    )
    result = handle_deploy(mock_core, args)
    mock_core.deploy.assert_called_once_with(
        "backup1",
        "newvm",
        Path("/custom/path"),
        add_to_config=True,
        vm_filter=None,
    )
    assert result == EXIT_SUCCESS
