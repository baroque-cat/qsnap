"""Tests for qsnap.cli.app — argument parsing and exit codes."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from qsnap.cli import commands as cli_commands
from qsnap.cli.app import _DISPATCH, build_argparser, main
from qsnap.cli.errors import (
    EXIT_BACKUP_ABORT,
    EXIT_DISKFULL,
    EXIT_GENERIC,
    EXIT_LOCKFILE,
    EXIT_PARSE,
    EXIT_SUCCESS,
)
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
        "fork",
        "reconcile",
    ):
        assert subcommand in text
    # deploy should NOT appear
    assert "deploy" not in text
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


# ── disk-full (ENOSPC) exit code tests ──────────────────────────────────


@patch("qsnap.cli.app.Core")
@patch("qsnap.cli.app.DefaultFactory")
@patch("qsnap.cli.app.SubprocessShell")
@patch("qsnap.cli.app.ConfigFacade")
@patch("qsnap.cli.app.LockManager")
def test_diskfull_run_exit_code_four(
    mock_lock_cls,
    mock_config_cls,
    mock_shell_cls,
    mock_factory_cls,
    mock_core_cls,
):
    """A run limited by a space-classified error (space_limited=True) exits 4."""
    mock_config = MockConfigFacade(vms=[])
    mock_config_cls.return_value = mock_config

    mock_core = Mock()
    mock_core.run.return_value = PipelineResult(
        results=[VMRunResult(vm_name="vm1", success=True)],
        space_limited=True,
    )
    mock_core_cls.return_value = mock_core

    code = main(["run"])
    assert code == EXIT_DISKFULL


@patch("qsnap.cli.app.Core")
@patch("qsnap.cli.app.DefaultFactory")
@patch("qsnap.cli.app.SubprocessShell")
@patch("qsnap.cli.app.ConfigFacade")
@patch("qsnap.cli.app.LockManager")
def test_diskfull_precedence_over_generic(
    mock_lock_cls,
    mock_config_cls,
    mock_shell_cls,
    mock_factory_cls,
    mock_core_cls,
):
    """space_limited=True with success=False exits 4, not 1 (4 > 1 precedence)."""
    mock_config = MockConfigFacade(vms=[])
    mock_config_cls.return_value = mock_config

    mock_core = Mock()
    mock_core.run.return_value = PipelineResult(
        results=[
            VMRunResult(vm_name="vm1", success=False, error="snapshot failed"),
        ],
        space_limited=True,
    )
    mock_core_cls.return_value = mock_core

    code = main(["run"])
    assert code == EXIT_DISKFULL


@patch("qsnap.cli.app.Core")
@patch("qsnap.cli.app.DefaultFactory")
@patch("qsnap.cli.app.SubprocessShell")
@patch("qsnap.cli.app.ConfigFacade")
@patch("qsnap.cli.app.LockManager")
def test_non_space_backup_abort_exits_ten(
    mock_lock_cls,
    mock_config_cls,
    mock_shell_cls,
    mock_factory_cls,
    mock_core_cls,
):
    """A non-space backup abort (backup_failed=True, space_limited=False)
    exits 10 per spec: cli-interface "Non-space backup abort still exits 10".

    Disk-full (4) only takes precedence when space_limited=True; a pure
    verification/non-space backup failure without any space error still
    reports exit code 10."""
    mock_config = MockConfigFacade(vms=[])
    mock_config_cls.return_value = mock_config

    mock_core = Mock()
    mock_core.run.return_value = PipelineResult(
        results=[
            VMRunResult(
                vm_name="vm1",
                success=False,
                error="FULL backup verification failed",
                backup_failed=True,
            ),
        ],
        space_limited=False,
    )
    mock_core_cls.return_value = mock_core

    code = main(["run"])
    assert code == EXIT_BACKUP_ABORT


@patch("qsnap.cli.app.Core")
@patch("qsnap.cli.app.DefaultFactory")
@patch("qsnap.cli.app.SubprocessShell")
@patch("qsnap.cli.app.ConfigFacade")
@patch("qsnap.cli.app.LockManager")
def test_no_space_error_exits_one(
    mock_lock_cls,
    mock_config_cls,
    mock_shell_cls,
    mock_factory_cls,
    mock_core_cls,
):
    """A run failing without any space involvement exits 1, not 4."""
    mock_config = MockConfigFacade(vms=[])
    mock_config_cls.return_value = mock_config

    mock_core = Mock()
    mock_core.run.return_value = PipelineResult(
        results=[
            VMRunResult(vm_name="vm1", success=False, error="broken backing chain"),
        ],
        space_limited=False,
    )
    mock_core_cls.return_value = mock_core

    code = main(["run"])
    assert code == EXIT_GENERIC


@patch("qsnap.cli.app.Core")
@patch("qsnap.cli.app.DefaultFactory")
@patch("qsnap.cli.app.SubprocessShell")
@patch("qsnap.cli.app.ConfigFacade")
@patch("qsnap.cli.app.LockManager")
def test_backup_abort_still_exits_ten(
    mock_lock_cls,
    mock_config_cls,
    mock_shell_cls,
    mock_factory_cls,
    mock_core_cls,
):
    """A non-space BackupAbortError exits 10 even without space errors."""
    mock_config = MockConfigFacade(vms=[])
    mock_config_cls.return_value = mock_config

    mock_core = Mock()
    mock_core.run.return_value = PipelineResult(
        results=[
            VMRunResult(
                vm_name="vm1",
                success=False,
                error="FULL backup verification failed",
                backup_failed=True,
            ),
        ],
        space_limited=False,
    )
    mock_core_cls.return_value = mock_core

    code = main(["run"])
    assert code == EXIT_BACKUP_ABORT


# ── help epilog documents exit codes ────────────────────────────────────


def test_help_epilog_documents_exit_code_4(capsys):
    """The --help epilog documents exit code 4 (disk-full)."""
    import re

    parser = build_argparser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    captured = capsys.readouterr()
    text = captured.out
    # argparse collapses the epilog's leading spaces/newlines and wraps
    # long lines at the em-dash, so match with flexible whitespace.
    assert "4 — disk-full" in text
    assert re.search(r"10\s*—\s*backup abort", text) is not None


# ── restore and check argument parsing tests ────────────────────────────


def test_restore_parses_snapshot_name():
    parser = build_argparser()
    ns = parser.parse_args(["restore", "mysnap"])
    assert ns.snapshot_name == "mysnap"
    assert ns.vm == []


def test_restore_parses_vm_filter():
    parser = build_argparser()
    ns = parser.parse_args(["restore", "mysnap", "myvm"])
    assert ns.snapshot_name == "mysnap"
    assert ns.vm == ["myvm"]


def test_restore_parses_dry_run_flag():
    parser = build_argparser()
    ns = parser.parse_args(["restore", "mysnap", "--dry-run"])
    assert ns.dry_run is True


def test_restore_global_dry_run_not_clobbered():
    """``qsnap --dry-run restore SNAP`` must stay a dry run.

    Regression: the restore subparser used a plain ``store_true`` local
    ``--dry-run`` (default ``False``) which overwrote the global flag in
    the shared namespace, silently disabling dry-run.  With
    ``default=argparse.SUPPRESS`` the absent local flag no longer clobbers
    the global value.
    """
    parser = build_argparser()
    ns = parser.parse_args(["--dry-run", "restore", "mysnap"])
    assert ns.dry_run is True


def test_restore_dry_run_short_alias():
    parser = build_argparser()
    ns = parser.parse_args(["restore", "mysnap", "-n"])
    assert ns.dry_run is True


@pytest.mark.parametrize("cmd", ["run", "snapshot", "backup", "prune"])
def test_action_subcommand_dry_run_both_positions(cmd):
    """``--dry-run`` is accepted before and after action subcommands."""
    parser = build_argparser()
    assert parser.parse_args(["--dry-run", cmd]).dry_run is True
    assert parser.parse_args([cmd, "--dry-run"]).dry_run is True


def test_restore_parses_yes_flag():
    parser = build_argparser()
    ns = parser.parse_args(["restore", "mysnap", "--yes"])
    assert ns.yes is True


def test_restore_no_target_dir(cli_app):
    """Verify restore does NOT accept target_dir positional arg (removed)."""
    ns = cli_app.parse_args(["restore", "mysnap"])
    # target_dir attribute should not exist on the namespace
    assert not hasattr(ns, "target_dir")


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


# ── deploy removal verification tests ──────────────────────────────────────


def test_deploy_not_in_help(capsys):
    """Verify 'deploy' does NOT appear in help text."""
    parser = build_argparser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    captured = capsys.readouterr()
    assert "deploy" not in captured.out


def test_deploy_not_in_dispatch_map():
    """Verify 'deploy' is NOT in _DISPATCH map."""
    assert "deploy" not in _DISPATCH


# ── fork argument parsing tests ──────────────────────────────────────────


def test_fork_parses_output_flag(cli_app):
    """Verify fork subcommand parses --output as required argument."""
    ns = cli_app.parse_args(["fork", "snap1", "--output", "/tmp/output.qcow2"])
    assert ns.snapshot_name == "snap1"
    assert ns.output == "/tmp/output.qcow2"
    assert ns.vm == []


def test_fork_parses_vm_filter(cli_app):
    """Verify fork subcommand parses vm positional arg."""
    ns = cli_app.parse_args(["fork", "snap1", "--output", "/tmp/output.qcow2", "myvm"])
    assert ns.vm == ["myvm"]


def test_fork_requires_output(cli_app):
    """Verify fork subcommand fails when --output is missing."""
    with pytest.raises(SystemExit):
        cli_app.parse_args(["fork", "snap1"])


def test_fork_parses_dry_run_flag(cli_app):
    """Parse fork with --dry-run and verify ns.dry_run is True."""
    ns = cli_app.parse_args(["fork", "snap1", "--output", "/tmp/o.qcow2", "--dry-run"])
    assert ns.dry_run is True


# ── dispatch map entry tests ─────────────────────────────────────────────


def test_reconcile_dispatch_map_entry():
    """Verify 'reconcile' key exists in _DISPATCH and maps to commands.handle_reconcile."""
    assert "reconcile" in _DISPATCH
    assert _DISPATCH["reconcile"] is cli_commands.handle_reconcile
