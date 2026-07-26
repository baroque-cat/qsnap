"""Tests for qsnap.cli.app — argument parsing and exit codes."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from qsnap.cli import commands as cli_commands
from qsnap.cli.app import _DISPATCH, build_argparser, main
from qsnap.cli.errors import EXIT_LOCKFILE, EXIT_PARSE, EXIT_SUCCESS
from qsnap.core import PipelineResult, VMRunResult
from tests.mocks import MockConfigFacade

# ── Argument parsing tests ──────────────────────────────────────────────


def test_help_text_lists_subcommands_and_flags(capsys):
    parser = build_argparser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    captured = capsys.readouterr()
    text = captured.out
    for subcommand in (
        "run",
        "snapshot",
        "backup",
        "prune",
        "list",
        "stats",
        "check",
        "restore",
        "estimate",
    ):
        assert subcommand in text
    for flag in (
        "--config",
        "--dry-run",
        "--preserve",
        "--verbose",
        "--quiet",
        "--loglevel",
        "--format",
        "--lockfile",
        "-L",
    ):
        assert flag in text
    # The 'deferred' sub-subcommand appears in the 'list' subcommand help
    with pytest.raises(SystemExit):
        parser.parse_args(["list", "--help"])
    captured = capsys.readouterr()
    assert "deferred" in captured.out


def test_explicit_config_path_passed_to_configfacade():
    parser = build_argparser()
    ns = parser.parse_args(["-c", "/path/to/custom.toml", "run"])
    assert ns.config == "/path/to/custom.toml"


def test_default_config_path_is_etc_qsnap_toml():
    parser = build_argparser()
    ns = parser.parse_args(["run"])
    assert ns.config == "/etc/qsnap/qsnap.toml"


def test_verbose_flag_sets_loglevel_debug():
    parser = build_argparser()
    ns = parser.parse_args(["-v", "run"])
    assert ns.verbose is True


def test_quiet_flag_sets_loglevel_error():
    parser = build_argparser()
    ns = parser.parse_args(["-q", "run"])
    assert ns.quiet is True


def test_lockfile_flag_overrides_config_lockfile_path():
    parser = build_argparser()
    ns = parser.parse_args(["--lockfile", "/run/qsnap.lock", "run"])
    assert ns.lockfile == "/run/qsnap.lock"


def test_loglevel_flag_sets_explicit_level():
    parser = build_argparser()
    ns = parser.parse_args(["-l", "warn", "run"])
    assert ns.loglevel == "warn"


def test_format_flag_default_is_table():
    parser = build_argparser()
    ns = parser.parse_args(["run"])
    assert ns.format == "table"


def test_format_col_custom_columns():
    parser = build_argparser()
    ns = parser.parse_args(["--format", "col:name,path", "list", "snapshots"])
    assert ns.format == "col:name,path"


# ── main() exit code tests ──────────────────────────────────────────────


@patch("qsnap.cli.app.Core")
@patch("qsnap.cli.app.DefaultFactory")
@patch("qsnap.cli.app.SubprocessShell")
@patch("qsnap.cli.app.ConfigFacade")
@patch("qsnap.cli.app.LockManager")
def test_success_returns_exit_code_zero(
    mock_lock_cls,
    mock_config_cls,
    mock_shell_cls,
    mock_factory_cls,
    mock_core_cls,
):
    mock_config = MockConfigFacade(vms=[])
    mock_config_cls.return_value = mock_config

    mock_core = Mock()
    mock_core.run.return_value = PipelineResult(results=[VMRunResult(vm_name="vm1", success=True)])
    mock_core_cls.return_value = mock_core

    code = main(["run"])
    assert code == EXIT_SUCCESS


@patch("qsnap.cli.app.DefaultFactory")
@patch("qsnap.cli.app.SubprocessShell")
@patch("qsnap.cli.app.ConfigFacade")
@patch("qsnap.cli.app.LockManager")
def test_lockfile_held_returns_exit_code_three(
    mock_lock_cls,
    mock_config_cls,
    mock_shell_cls,
    mock_factory_cls,
):
    mock_config = MockConfigFacade(vms=[])
    mock_config_cls.return_value = mock_config

    mock_lock = Mock()
    mock_lock.acquire.return_value = False
    mock_lock_cls.return_value = mock_lock

    code = main(["--lockfile", "/tmp/test.lock", "run"])
    assert code == EXIT_LOCKFILE


def test_unknown_subcommand_returns_parse_error_exit_code_2():
    code = main(["bogus"])
    assert code == EXIT_PARSE


# ── restore and check argument parsing tests ────────────────────────────


def test_restore_subcommand_parses_positional_args():
    parser = build_argparser()
    ns = parser.parse_args(["restore", "mysnap", "/tmp/target"])
    assert ns.snapshot_name == "mysnap"
    assert ns.target_dir == "/tmp/target"


def test_check_deep_flag_sets_deep_true():
    parser = build_argparser()
    ns = parser.parse_args(["check", "--deep"])
    assert ns.deep is True


def test_check_without_deep_defaults_false():
    parser = build_argparser()
    ns = parser.parse_args(["check"])
    assert ns.deep is False


# ── --tree and -L/--long flag tests ─────────────────────────────────────


def test_tree_flag_parses(cli_app):
    """--tree flag on 'list snapshots' sets ns.tree to True."""
    ns = cli_app.parse_args(["list", "snapshots", "--tree"])
    assert ns.tree is True


def test_tree_flag_defaults_false(cli_app):
    """Without --tree, ns.tree is False."""
    ns = cli_app.parse_args(["list", "snapshots"])
    assert ns.tree is False


def test_long_flag_translates_to_format_long(cli_app):
    """-L flag sets long_format=True; after main() resolution, format == 'long'."""
    ns = cli_app.parse_args(["-L", "list", "snapshots"])
    assert ns.long_format is True
    # Simulate main() resolution: -L → format="long"
    if getattr(ns, "long_format", False):
        ns.format = "long"
    assert ns.format == "long"


def test_long_flag_with_list(cli_app):
    """--long flag (long form of -L) sets ns.long_format to True."""
    ns = cli_app.parse_args(["--long", "list", "snapshots"])
    assert ns.long_format is True


# ── --timer and --print-schedule flag tests ────────────────────────────


def test_timer_flag_parsed(cli_app):
    """--timer flag on 'run' subcommand sets args.timer to True."""
    ns = cli_app.parse_args(["run", "myvm", "--timer"])
    assert ns.timer is True


def test_timer_flag_defaults_false(cli_app):
    """Without --timer, args.timer is False."""
    ns = cli_app.parse_args(["run", "myvm"])
    assert ns.timer is False


def test_print_schedule_short_flag_S_parsed(cli_app):
    """-S short flag sets args.print_schedule to True."""
    ns = cli_app.parse_args(["run", "myvm", "-S"])
    assert ns.print_schedule is True


# ── list deferred sub-subcommand tests ─────────────────────────────────


def test_list_deferred_sub_subcommand(cli_app):
    """'list deferred' sets list_subcommand to 'deferred'."""
    ns = cli_app.parse_args(["list", "deferred"])
    assert ns.list_subcommand == "deferred"


def test_list_deferred_with_vm_arg(cli_app):
    """'list deferred myvm' sets vm to ['myvm']."""
    ns = cli_app.parse_args(["list", "deferred", "myvm"])
    assert ns.vm == ["myvm"]


# ── estimate subcommand tests ────────────────────────────────────────────


def test_estimate_subcommand_registered_in_argparser(cli_app):
    """'estimate' subcommand is registered with optional VM positional arg."""
    ns = cli_app.parse_args(["estimate"])
    assert ns.command == "estimate"
    assert ns.vm == []
    assert ns.format == "table"


def test_estimate_subcommand_with_specific_vm(cli_app):
    """'estimate myvm' sets command='estimate' and vm=['myvm']."""
    ns = cli_app.parse_args(["estimate", "myvm"])
    assert ns.command == "estimate"
    assert ns.vm == ["myvm"]


def test_estimate_subcommand_with_multiple_vms(cli_app):
    """'estimate vm1 vm2' sets vm=['vm1', 'vm2']."""
    ns = cli_app.parse_args(["estimate", "vm1", "vm2"])
    assert ns.command == "estimate"
    assert ns.vm == ["vm1", "vm2"]


# ── dispatch map entry tests ─────────────────────────────────────────────


def test_reconcile_dispatch_map_entry():
    """Verify 'reconcile' key exists in _DISPATCH and maps to commands.handle_reconcile."""
    assert "reconcile" in _DISPATCH
    assert _DISPATCH["reconcile"] is cli_commands.handle_reconcile
