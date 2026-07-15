"""Handler functions for each CLI subcommand.

This module is a **thin translation layer**: it receives a ``Core``
instance and parsed CLI args, calls the appropriate Core method, and
formats the returned results.  It contains NO business logic and NO
imports from ``qsnap.modules``, ``qsnap.config``, ``qsnap.retention``,
or ``qsnap.state``.
"""

from __future__ import annotations

import logging
import sys
from argparse import Namespace
from pathlib import Path

from qsnap.cli.errors import EXIT_BACKUP_ABORT, EXIT_GENERIC, EXIT_SUCCESS
from qsnap.cli.format import (
    format_deferred_raw,
    format_deferred_table,
    format_output,
)
from qsnap.core import Core, PipelineResult
from qsnap.models.config import VMConfig
from qsnap.models.results import CheckResult, ScheduleResult, SnapshotInfo

logger = logging.getLogger(__name__)

# ── helpers ───────────────────────────────────────────────────────────────


def _get_vm_filter(args: Namespace) -> str | None:
    """Extract the first VM name from positional args, or ``None``."""
    vm: list[str] = getattr(args, "vm", [])
    return vm[0] if vm else None


def _snapshots_to_rows(data: dict[str, list[SnapshotInfo]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for vm_name, snapshots in data.items():
        for snap in snapshots:
            rows.append(
                {
                    "vm": vm_name,
                    "name": snap.name,
                    "path": str(snap.path),
                    "timestamp": snap.timestamp.isoformat(),
                    "allocation": str(snap.allocation),
                }
            )
    return rows


def _config_to_rows(vms: list[VMConfig]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for vm in vms:
        rows.append(
            {
                "name": vm.name,
                "base_image": str(vm.base_image),
                "snapshot_dir": str(vm.snapshot_dir),
                "snapshot_create": vm.snapshot_create,
                "targets": str(len(vm.targets)),
                "blockcommit_deep_verify": "ON" if vm.blockcommit_deep_verify else "OFF",
                "snapshot_deep_verify": "ON" if vm.snapshot_deep_verify else "OFF",
            }
        )
    return rows


def _latest_to_rows(
    data: dict[str, SnapshotInfo | None],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for vm_name, snap in data.items():
        if snap is None:
            rows.append(
                {"vm": vm_name, "name": "-", "timestamp": "-", "allocation": "-"}
            )
        else:
            rows.append(
                {
                    "vm": vm_name,
                    "name": snap.name,
                    "timestamp": snap.timestamp.isoformat(),
                    "allocation": str(snap.allocation),
                }
            )
    return rows


def _print_tree(
    data: dict[str, list[SnapshotInfo]],
    vm_configs: list[VMConfig],
) -> None:
    """Print snapshots as an indented backing-chain tree.

    The backing chain is: base_image ← snap1 ← snap2 ← ...
    Each level is indented one step deeper than its parent.
    """
    base_map = {vm.name: vm.base_image for vm in vm_configs}
    for vm_name, snapshots in data.items():
        print(f"=== {vm_name} ===")
        base = base_map.get(vm_name)
        if base is not None:
            print(f"{base.name}")
        else:
            print("(unknown base image)")
        for i, snap in enumerate(snapshots):
            indent = "  " * (i + 1)
            print(f"{indent}{snap.path.name}")


def _check_to_rows(data: dict[str, CheckResult]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for vm_name, result in data.items():
        rows.append(
            {
                "vm": vm_name,
                "status": result.status,
                "broken_snapshots": (
                    ", ".join(result.broken_snapshots)
                    if result.broken_snapshots
                    else "-"
                ),
            }
        )
    return rows


def _stats_to_rows(
    snapshots: dict[str, list[SnapshotInfo]],
    backups: dict[str, list[SnapshotInfo]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    all_vms = set(snapshots.keys()) | set(backups.keys())
    for vm_name in sorted(all_vms):
        snaps = snapshots.get(vm_name, [])
        bcks = backups.get(vm_name, [])
        rows.append(
            {
                "vm": vm_name,
                "snapshots": str(len(snaps)),
                "snapshot_size": str(sum(s.allocation for s in snaps)),
                "backups": str(len(bcks)),
                "backup_size": str(sum(b.allocation for b in bcks)),
            }
        )
    return rows


def _print_schedule(schedule: dict[str, ScheduleResult]) -> None:
    """Print retention schedule (keep/remove) per VM to stdout."""
    for vm_name, result in schedule.items():
        print(f"=== {vm_name} ===")
        keep_str = ", ".join(result.snapshots.keep) if result.snapshots.keep else "(none)"
        remove_str = ", ".join(result.snapshots.remove) if result.snapshots.remove else "(none)"
        print("  Snapshots:")
        print(f"    Keep:   {keep_str}")
        print(f"    Remove: {remove_str}")
        for target_path, backup_ret in result.backups.items():
            print(f"  Backups [{target_path}]:")
            b_keep = ", ".join(backup_ret.keep) if backup_ret.keep else "(none)"
            b_remove = ", ".join(backup_ret.remove) if backup_ret.remove else "(none)"
            print(f"    Keep:   {b_keep}")
            print(f"    Remove: {b_remove}")


def _format_pipeline_result(result: PipelineResult) -> int:
    """Print pipeline results and return exit code.

    Returns ``EXIT_BACKUP_ABORT`` (10) if any VM had a backup failure,
    even if the pipeline itself succeeded.
    """
    for r in result.results:
        if r.success:
            print(f"  {r.vm_name}: OK")
        else:
            print(f"  {r.vm_name}: FAILED - {r.error or 'unknown error'}")
    if not result.success:
        return EXIT_GENERIC
    if any(r.backup_failed for r in result.results):
        return EXIT_BACKUP_ABORT
    return EXIT_SUCCESS


# ── action subcommands ───────────────────────────────────────────────────


def _handle_schedule_and_timer(core: Core, args: Namespace) -> bool:
    """Print schedule summary if --print-schedule, log if --timer.

    Returns ``True`` if the pipeline should be skipped (i.e. when
    ``--print-schedule`` was set without ``--dry-run``).
    """
    vm_filter = _get_vm_filter(args)
    if getattr(args, "timer", False):
        summary = core.schedule_summary(vm_filter)
        logger.info("Schedule summary:\n%s", summary)
    if getattr(args, "print_schedule", False):
        summary = core.schedule_summary(vm_filter)
        print(summary)
        if not getattr(args, "dry_run", False):
            return True
    return False


def handle_run(core: Core, args: Namespace) -> int:
    if _handle_schedule_and_timer(core, args):
        return EXIT_SUCCESS
    vm_filter = _get_vm_filter(args)
    result = core.run(vm_filter)
    return _format_pipeline_result(result)


def handle_snapshot(core: Core, args: Namespace) -> int:
    if _handle_schedule_and_timer(core, args):
        return EXIT_SUCCESS
    vm_filter = _get_vm_filter(args)
    result = core.snapshot(vm_filter)
    return _format_pipeline_result(result)


def handle_backup(core: Core, args: Namespace) -> int:
    if _handle_schedule_and_timer(core, args):
        return EXIT_SUCCESS
    vm_filter = _get_vm_filter(args)
    result = core.backup(vm_filter)
    return _format_pipeline_result(result)


def handle_prune(core: Core, args: Namespace) -> int:
    if _handle_schedule_and_timer(core, args):
        return EXIT_SUCCESS
    vm_filter = _get_vm_filter(args)
    result = core.prune(vm_filter)
    return _format_pipeline_result(result)


# ── informational subcommands ─────────────────────────────────────────────


def handle_list(core: Core, args: Namespace) -> int:
    sub: str = args.list_subcommand
    vm_filter = _get_vm_filter(args)
    fmt: str = getattr(args, "format", "table")

    if sub == "snapshots":
        data = core.list_snapshots(vm_filter)
        if getattr(args, "tree", False):
            _print_tree(data, core.list_config())
            return EXIT_SUCCESS
        rows = _snapshots_to_rows(data)
        columns = ["vm", "name", "path", "timestamp", "allocation"]
    elif sub == "backups":
        data = core.list_backups(vm_filter)
        rows = _snapshots_to_rows(data)
        columns = ["vm", "name", "path", "timestamp", "allocation"]
    elif sub == "config":
        vms = core.list_config()
        rows = _config_to_rows(vms)
        columns = [
            "name", "base_image", "snapshot_dir", "snapshot_create",
            "targets", "blockcommit_deep_verify", "snapshot_deep_verify",
        ]
        # Print global safety settings header
        global_cfg = core._config.get_global()
        print("Global safety settings:")
        print(f"  auto_cleanup: {'ON' if global_cfg.auto_cleanup else 'OFF'}")
        print(f"  chain_verify_before_commit: {'ON' if global_cfg.chain_verify_before_commit else 'OFF'}")
        print(f"  chain_verify_after_commit: {'ON' if global_cfg.chain_verify_after_commit else 'OFF'}")
        print(f"  deep_check_schedule: {global_cfg.deep_check_schedule}")
        print()
    elif sub == "latest":
        data = core.list_latest(vm_filter)
        rows = _latest_to_rows(data)
        columns = ["vm", "name", "timestamp", "allocation"]
    elif sub == "deferred":
        return handle_list_deferred(core, args)
    else:
        return EXIT_GENERIC

    output = format_output(rows, columns, fmt)
    if output:
        print(output)
    return EXIT_SUCCESS


def handle_stats(core: Core, args: Namespace) -> int:
    vm_filter = _get_vm_filter(args)
    fmt: str = getattr(args, "format", "table")
    snapshots = core.list_snapshots(vm_filter)
    backups = core.list_backups(vm_filter)
    rows = _stats_to_rows(snapshots, backups)
    columns = ["vm", "snapshots", "snapshot_size", "backups", "backup_size"]
    output = format_output(rows, columns, fmt)
    if output:
        print(output)
    return EXIT_SUCCESS


def handle_check(core: Core, args: Namespace) -> int:
    vm_filter = _get_vm_filter(args)
    fmt: str = getattr(args, "format", "table")
    deep: bool = getattr(args, "deep", False)
    data = core.check(vm_filter, deep=deep)

    # Print safety configuration summary
    global_cfg = core._config.get_global()
    print("Safety configuration:")
    print(f"  auto_cleanup: {'ON' if global_cfg.auto_cleanup else 'OFF'}")
    print(f"  chain_verify_before_commit: {'ON' if global_cfg.chain_verify_before_commit else 'OFF'}")
    print(f"  chain_verify_after_commit: {'ON' if global_cfg.chain_verify_after_commit else 'OFF'}")
    print(f"  Deep check schedule: {core.get_deep_check_schedule_info()}")
    print()

    rows = _check_to_rows(data)
    columns = ["vm", "status", "broken_snapshots"]

    # Determine exit code: 1 if any VM has unreadable images, 0 otherwise
    has_critical = any(r.status == "broken" for r in data.values())

    output = format_output(rows, columns, fmt)
    if output:
        print(output)

    if deep and has_critical:
        return EXIT_GENERIC
    return EXIT_SUCCESS


def handle_restore(core: Core, args: Namespace) -> int:
    snapshot_name: str = args.snapshot_name
    target_dir = Path(args.target_dir)
    vm_filter = _get_vm_filter(args)

    # Validate target_dir exists
    if not target_dir.is_dir():
        print(
            f"Error: target directory does not exist: {target_dir}",
            file=sys.stderr,
        )
        return EXIT_GENERIC

    result = core.restore(snapshot_name, target_dir, vm_filter)

    if result.success:
        print(f"Restored '{snapshot_name}' to {target_dir}")
        print("Chain files:")
        for f in result.chain_files:
            print(f"  {f}")
        return EXIT_SUCCESS
    else:
        print(f"Error: {result.error}", file=sys.stderr)
        return EXIT_GENERIC


def handle_list_deferred(core: Core, args: Namespace) -> int:
    """List deferred blockcommit operations per VM.

    Calls ``Core.list_deferred()`` and formats the output as a table
    (default) or raw key=value pairs.
    """
    vm_filter = _get_vm_filter(args)
    fmt: str = getattr(args, "format", "table")
    summaries = core.list_deferred(vm_filter)

    if fmt == "raw":
        output = format_deferred_raw(summaries)
    else:
        output = format_deferred_table(summaries)

    if output:
        print(output)
    return EXIT_SUCCESS
