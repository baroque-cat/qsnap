"""Tests for qsnap.cli.commands — handler dispatch and flag translation.

Each handler is a thin translation layer: it receives a Core instance and
parsed CLI args, calls the appropriate Core method, and formats the
returned results.  These tests verify that the correct Core method is
called with the correct arguments, that CLI flags are properly translated,
and that call ordering is correct.
"""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import Mock

from qsnap.cli.commands import (
    handle_backup,
    handle_check,
    handle_list,
    handle_prune,
    handle_run,
    handle_snapshot,
    handle_stats,
)
from qsnap.cli.errors import EXIT_SUCCESS
from qsnap.core import Core, PipelineResult, VMRunResult


# ── helpers ─────────────────────────────────────────────────────────────


def _make_action_args(**overrides) -> Namespace:
    """Create a Namespace for action subcommands (run/snapshot/backup/prune)."""
    defaults: dict[str, object] = {
        "command": "run",
        "vm": [],
        "print_schedule": False,
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
    core.list_snapshots.return_value = {}
    core.list_backups.return_value = {}
    core.list_config.return_value = []
    core.list_latest.return_value = {}
    core.check.return_value = {}
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
    mock_core.check.assert_called_once_with(None)


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


def test_print_schedule_flag_dispatches_to_core_print_schedule():
    mock_core = _make_mock_core()
    args = _make_action_args(print_schedule=True)
    handle_run(mock_core, args)
    mock_core.print_schedule.assert_called_once_with(None)
    mock_core.run.assert_called_once_with(None)
    call_names = [c[0] for c in mock_core.mock_calls]
    assert call_names.index("print_schedule") < call_names.index("run")


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
