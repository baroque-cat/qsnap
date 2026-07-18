"""Btrbk-style summary table formatter for pipeline results.

This module is a **pure function** — it accepts a :class:`PipelineResult`
and returns a formatted string.  It has no side effects, no I/O, and no
access to ``IStateManager``, ``IConfigFacade``, or any module.

The only import from the domain layer is the data types
(:class:`PipelineResult`, :class:`ActionRecord`) needed to read the
result structure.  No business logic is performed here.
"""

from __future__ import annotations

from datetime import datetime

from qsnap.core import PipelineResult
from qsnap.models.results import ActionRecord

# ── Symbol mapping ────────────────────────────────────────────────────────

_SYMBOLS: dict[str, str] = {
    "snapshot_create": "+++",
    "snapshot_delete": "---",
    "backup_transfer": ">>>",
    "backup_full": "***",
    "error": "!!!",
    "backup_delete": "---",
}

_LEGEND_LINES: list[tuple[str, str]] = [
    ("+++", "created snapshot"),
    ("---", "deleted snapshot (blockcommitted)"),
    (">>>", "transferred incremental backup"),
    ("***", "created FULL backup"),
    ("!!!", "ERROR"),
]


def _get_version() -> str:
    """Return the installed qsnap version, or ``"0.0.0"`` if unknown."""
    try:
        from importlib.metadata import version

        return version("qsnap")
    except Exception:  # noqa: BLE001 — importlib edge cases
        return "0.0.0"


def _format_size(size: int) -> str:
    """Format a byte count for human display."""
    if size <= 0:
        return "0 B"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    unit_idx = 0
    value = float(size)
    while value >= 1024 and unit_idx < len(units) - 1:
        value /= 1024
        unit_idx += 1
    if unit_idx == 0:
        return f"{int(value)} B"
    return f"{value:.1f} {units[unit_idx]}"


def _format_speed(bytes_transferred: int, duration: float) -> str:
    """Format transfer speed in MiB/s."""
    if duration <= 0 or bytes_transferred <= 0:
        return "0.0 MiB/s"
    speed_mib = bytes_transferred / (1024 * 1024) / duration
    return f"{speed_mib:.1f} MiB/s"


def _format_action(action: ActionRecord) -> str:
    """Format a single ActionRecord as one table row."""
    symbol = _SYMBOLS.get(action.action, "???")
    indent = "    "  # 4 spaces for table rows

    if action.action == "snapshot_create":
        return f"{indent}{symbol}  {action.name}  ({_format_size(action.size)})"
    if action.action == "snapshot_delete":
        return f"{indent}{symbol}  {action.name}"
    if action.action == "backup_transfer":
        target = str(action.path) if action.path else "-"
        return (
            f"{indent}{symbol}  {action.name}  → {target}  "
            f"({_format_size(action.size)} in {action.duration:.1f}s, "
            f"{_format_speed(action.size, action.duration)})"
        )
    if action.action == "backup_full":
        return f"{indent}{symbol}  {action.name}  ({_format_size(action.size)})"
    if action.action == "backup_delete":
        target = str(action.path.parent) if action.path else "-"
        return f"{indent}{symbol}  {action.name}  from {target}"
    if action.action == "error":
        return f"{indent}{symbol}  {action.name}  {action.error or 'unknown error'}"
    return f"{indent}{symbol}  {action.name}"


def _group_by_vm(actions: list[ActionRecord]) -> list[tuple[str, list[ActionRecord]]]:
    """Group actions by vm_name, preserving insertion (pipeline) order.

    VMs with no actions are omitted (spec: "VM with no actions is omitted").
    Within each VM, actions are already in pipeline execution order because
    they are appended sequentially during ``_run_pipeline()``.
    """
    groups: dict[str, list[ActionRecord]] = {}
    order: list[str] = []
    for a in actions:
        if a.vm_name not in groups:
            groups[a.vm_name] = []
            order.append(a.vm_name)
        groups[a.vm_name].append(a)
    return [(vm, groups[vm]) for vm in order]


def format_summary(result: PipelineResult) -> str:
    """Format a btrbk-style summary table from a PipelineResult.

    Pure function — no side effects, no I/O.  All data comes from
    ``result.actions`` and ``result.results``.
    """
    version = _get_version()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines: list[str] = []

    # ── Header ─────────────────────────────────────────────────────────
    header = f"qsnap Backup Summary (version {version}) - {now}"
    lines.append(header)
    if result.config_path:
        lines.append(f"Config: {result.config_path}")
    if result.dry_run:
        lines.append("Dryrun: YES")
    lines.append("")

    # ── Legend ─────────────────────────────────────────────────────────
    lines.append("Legend:")
    for symbol, desc in _LEGEND_LINES:
        lines.append(f"    {symbol}  {desc}")
    lines.append("")

    # ── Per-VM blocks ──────────────────────────────────────────────────
    vm_groups = _group_by_vm(result.actions)
    for vm_name, actions in vm_groups:
        lines.append(f"{vm_name}:")
        for action in actions:
            lines.append(_format_action(action))
        lines.append("")

    # ── Dry-run footer ─────────────────────────────────────────────────
    if result.dry_run:
        lines.append(
            "NOTE: Dryrun was active, none of the operations above were actually executed!"
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = ["format_summary"]
