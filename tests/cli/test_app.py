"""Tests for qsnap.cli.app — argument parsing and exit codes."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from qsnap.cli.app import build_argparser, main
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
    for subcommand in ("run", "snapshot", "backup", "prune", "list", "stats", "check"):
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
    ):
        assert flag in text


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
    mock_core.run.return_value = PipelineResult(
        results=[VMRunResult(vm_name="vm1", success=True)]
    )
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
