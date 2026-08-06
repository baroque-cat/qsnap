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
    "blockcommit": "<<<",
}

_LEGEND_LINES: list[tuple[str, str]] = [
    ("+++", "created snapshot"),
    ("---", "deleted snapshot (blockcommitted)"),
    (">>>", "transferred incremental backup"),
    ("***", "created FULL backup"),
    ("<<<", "blockcommit (merged snapshots into base)"),
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
    """Format a single ActionRecord as one table row.

    When ``action.disk`` is not None, a disk prefix ``[<disk>]`` is
    rendered immediately after the action symbol (spec: backup-summary,
    "Summary lines carry disk prefix").  VM-level records (``disk`` is
    None) are rendered without the prefix, exactly as before.
    """
    symbol = _SYMBOLS.get(action.action, "???")
    indent = "    "  # 4 spaces for table rows
    lead = f"{symbol} [{action.disk}] " if action.disk is not None else f"{symbol}  "

    if action.action == "snapshot_create":
        return f"{indent}{lead}{action.name}  ({_format_size(action.size)})"
    if action.action == "snapshot_delete":
        return f"{indent}{lead}{action.name}"
    if action.action == "backup_transfer":
        target = str(action.path) if action.path else "-"
        return (
            f"{indent}{lead}{action.name}  → {target}  "
            f"({_format_size(action.size)} in {action.duration:.1f}s, "
            f"{_format_speed(action.size, action.duration)})"
        )
    if action.action == "backup_full":
        return f"{indent}{lead}{action.name}  ({_format_size(action.size)})"
    if action.action == "backup_delete":
        target = str(action.path.parent) if action.path else "-"
        return f"{indent}{lead}{action.name}  from {target}"
    if action.action == "error":
        return f"{indent}{lead}{action.name}  {action.error or 'unknown error'}"
    return f"{indent}{lead}{action.name}"


def _format_prediction(action: ActionRecord) -> str:
    """Format a dry-run prediction as one table row.

    Reuses the action-row convention (symbol + ``[disk]`` prefix) but
    marks sizes with ``~`` — predictions are upper-bound estimates
    (design D4 of fix-dry-run-predictions) — and omits duration/speed
    because nothing actually ran.  A size of 0 renders as
    ``size unknown`` (estimation failed or not applicable).
    """
    symbol = _SYMBOLS.get(action.action, "???")
    indent = "    "  # 4 spaces for table rows
    lead = f"{symbol} [{action.disk}] " if action.disk is not None else f"{symbol}  "
    size = f"~{_format_size(action.size)}" if action.size > 0 else "size unknown"

    if action.action == "snapshot_create":
        return f"{indent}{lead}{action.name}  ({size})"
    if action.action == "snapshot_delete":
        return f"{indent}{lead}{action.name}"
    if action.action == "backup_transfer":
        target = str(action.path) if action.path else "-"
        return f"{indent}{lead}{action.name}  → {target}  ({size})"
    if action.action == "backup_full":
        return f"{indent}{lead}{action.name}  ({size})"
    if action.action == "backup_delete":
        target = str(action.path.parent) if action.path else "-"
        return f"{indent}{lead}{action.name}  from {target}"
    if action.action == "blockcommit":
        return f"{indent}{lead}{action.name}"
    return f"{indent}{lead}{action.name}"


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

    # ── Space-limited targets ───────────────────────────────────────────
    # Report every target/blockcommit limited by a disk-full error so
    # operators see exactly what needs free space (spec: cli-interface
    # "Disk-full exit code").
    if result.space_limited and result.space_limited_targets:
        lines.append("Space-limited (disk-full):")
        for target in result.space_limited_targets:
            lines.append(f"  space-limited: {target}")
        lines.append("")

    # ── Per-VM blocks ──────────────────────────────────────────────────
    vm_groups = _group_by_vm(result.actions)
    for vm_name, actions in vm_groups:
        lines.append(f"{vm_name}:")
        for action in actions:
            lines.append(_format_action(action))
        lines.append("")

    # ── Planned actions (dry-run predictions) ──────────────────────────
    # Rendered from result.predictions (design D10 of
    # fix-dry-run-predictions).  Empty predictions → no section.
    if result.dry_run and result.predictions:
        lines.append("Planned actions (dry-run):")
        lines.append("")
        for vm_name, predictions in _group_by_vm(result.predictions):
            lines.append(f"{vm_name}:")
            for prediction in predictions:
                lines.append(_format_prediction(prediction))
            lines.append("")

    # ── Dry-run footer ─────────────────────────────────────────────────
    if result.dry_run:
        lines.append(
            "NOTE: Dryrun was active, none of the operations above were actually executed!"
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = ["format_summary"]
