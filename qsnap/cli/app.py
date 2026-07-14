"""CLI entry point — argparse-based argument parser and main() dispatcher.

Builds the argument parser with subcommands and global flags, creates
infrastructure objects (ConfigFacade, SubprocessShell, JsonStateManager,
DefaultFactory, Core), sets Core properties from CLI flags, resolves
the lockfile, and dispatches to the appropriate handler in
``qsnap.cli.commands``.
"""

from __future__ import annotations

import argparse
import logging
import sys

from qsnap.cli import commands
from qsnap.cli.errors import (
    EXIT_GENERIC,
    EXIT_LOCKFILE,
    EXIT_PARSE,
    EXIT_SUCCESS,
)
from qsnap.config.facade import ConfigError, ConfigFacade
from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.locking import LockManager, resolve_lockfile_path
from qsnap.shell.subprocess_shell import SubprocessShell

_DISPATCH: dict[str, object] = {
    "run": commands.handle_run,
    "snapshot": commands.handle_snapshot,
    "backup": commands.handle_backup,
    "prune": commands.handle_prune,
    "list": commands.handle_list,
    "stats": commands.handle_stats,
    "check": commands.handle_check,
    "restore": commands.handle_restore,
}


def build_argparser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with subcommands and global flags."""
    parser = argparse.ArgumentParser(
        prog="qsnap",
        description="QEMU/KVM snapshot and backup orchestration for qcow2 images",
    )
    # Global flags
    parser.add_argument(
        "--config", "-c",
        default="/etc/qsnap/qsnap.toml",
        help="Path to TOML configuration file (default: /etc/qsnap/qsnap.toml)",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Print planned actions without executing them",
    )
    parser.add_argument(
        "--preserve",
        action="store_true",
        help="Skip all deletion (snapshots and backups)",
    )
    parser.add_argument(
        "--preserve-snapshots",
        action="store_true",
        help="Skip snapshot deletion (blockcommit) only",
    )
    parser.add_argument(
        "--preserve-backups",
        action="store_true",
        help="Skip backup deletion only",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Enable ERROR-level logging only",
    )
    parser.add_argument(
        "--loglevel", "-l",
        choices=["error", "warn", "info", "debug"],
        help="Set log level explicitly",
    )
    parser.add_argument(
        "--format",
        default="table",
        help="Output format: table (default), long, raw, col:<columns>",
    )
    parser.add_argument(
        "--long", "-L", dest="long_format",
        action="store_true",
        help="Shortcut for --format long",
    )
    parser.add_argument(
        "--lockfile",
        default=None,
        help="Override lockfile path (overrides config file value)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Action subcommands: run, snapshot, backup, prune
    for cmd in ("run", "snapshot", "backup", "prune"):
        sub = subparsers.add_parser(cmd)
        sub.add_argument("vm", nargs="*", help="VM name(s) to filter")
        sub.add_argument(
            "--print-schedule", "-S",
            action="store_true",
            help="Print retention schedule before executing",
        )
        sub.add_argument(
            "--timer",
            action="store_true",
            help="Indicate timer invocation (logs schedule summary at INFO)",
        )

    # list subcommand with sub-subcommands
    list_parser = subparsers.add_parser("list", help="List snapshots, backups, config, or latest")
    list_subparsers = list_parser.add_subparsers(dest="list_subcommand", required=True)
    snap_sub = list_subparsers.add_parser("snapshots")
    snap_sub.add_argument("vm", nargs="*", help="VM name(s) to filter")
    snap_sub.add_argument(
        "--tree",
        action="store_true",
        help="Display backing chain as an indented tree",
    )
    for list_cmd in ("backups", "latest"):
        list_sub = list_subparsers.add_parser(list_cmd)
        list_sub.add_argument("vm", nargs="*", help="VM name(s) to filter")
    list_subparsers.add_parser("config")

    # stats subcommand
    stats_parser = subparsers.add_parser("stats", help="Show snapshot/backup counts and sizes")
    stats_parser.add_argument("vm", nargs="*", help="VM name(s) to filter")

    # check subcommand
    check_parser = subparsers.add_parser("check", help="Verify backing-chain integrity")
    check_parser.add_argument("vm", nargs="*", help="VM name(s) to filter")
    check_parser.add_argument(
        "--deep",
        action="store_true",
        help="Run qemu-img check for corruption detection",
    )

    # restore subcommand
    restore_parser = subparsers.add_parser("restore", help="Restore a backup chain to a target directory")
    restore_parser.add_argument("snapshot_name", help="Snapshot name to restore")
    restore_parser.add_argument("target_dir", help="Target directory for restored files")
    restore_parser.add_argument("vm", nargs="*", default=[], help="VM name filter (optional)")

    return parser


def _setup_logging(args: argparse.Namespace) -> None:
    """Configure logging level from CLI flags."""
    if args.verbose:
        level = logging.DEBUG
    elif args.quiet:
        level = logging.ERROR
    elif args.loglevel:
        level_map = {
            "error": logging.ERROR,
            "warn": logging.WARNING,
            "info": logging.INFO,
            "debug": logging.DEBUG,
        }
        level = level_map[args.loglevel]
    else:
        level = logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Parse args, create infrastructure, dispatch."""
    parser = build_argparser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else EXIT_GENERIC
        return EXIT_SUCCESS if code == 0 else EXIT_PARSE

    _setup_logging(args)

    # Resolve --format long shortcut
    if getattr(args, "long_format", False):
        args.format = "long"

    # Create infrastructure
    try:
        config = ConfigFacade(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return EXIT_PARSE
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_GENERIC

    shell = SubprocessShell()
    state = DefaultFactory.create_state_manager(config.get_global().state_dir)
    factory = DefaultFactory(shell, state)
    core = Core(config, factory, state, shell)

    # Set Core properties from CLI flags
    core.dry_run = args.dry_run
    if args.preserve:
        core.preserve_snapshots = True
        core.preserve_backups = True
    if args.preserve_snapshots:
        core.preserve_snapshots = True
    if args.preserve_backups:
        core.preserve_backups = True

    # Resolve lockfile: --lockfile → GlobalConfig.lockfile → None (no locking)
    lockfile = resolve_lockfile_path(args.lockfile, config.get_global().lockfile)
    lock_manager: LockManager | None = None
    if lockfile:
        lock_manager = LockManager(lockfile)
        if not lock_manager.acquire():
            print(
                f"Error: another qsnap instance is running (lockfile: {lockfile})",
                file=sys.stderr,
            )
            return EXIT_LOCKFILE

    # Dispatch to handler
    try:
        handler = _DISPATCH.get(args.command)
        if handler is None:
            print(f"Unknown command: {args.command}", file=sys.stderr)
            return EXIT_GENERIC
        return handler(core, args)  # type: ignore[operator]
    finally:
        if lock_manager:
            lock_manager.release()
