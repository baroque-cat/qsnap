"""Free-space estimation and gate helpers for proactive ENOSPC prevention.

These helpers estimate the file-system space required for backup
transfers by querying the qcow2 backing chain via ``IShell`` and
checking the target filesystem via ``shutil.disk_usage``.  They return
result objects or ``None`` for undecidable estimates — never raise
exceptions for expected failures (design D5).
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from qsnap.interfaces.shell import IShell

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpaceCheckResult:
    """Outcome of a free-space gate check.

    ``sufficient`` is True when the target filesystem has enough free
    space for the estimated transfer size plus reserve.  ``free_bytes``
    is the raw ``shutil.disk_usage`` free value.  ``estimate`` is the
    size estimate used for the comparison (may be None when
    undecidable).
    """

    sufficient: bool
    free_bytes: int
    estimate: int | None = None
    required: int | None = None
    error: str | None = None


def estimate_full_size(shell: IShell, source_path: Path) -> int | None:
    """Estimate the disk size for a FULL backup of *source_path*.

    Returns the sum of ``actual-size`` over every image in the backing
    chain of *source_path* — a worst-case upper bound for a standalone
    copy.  Returns ``None`` when the chain is undecidable (e.g.
    ``qemu-img info`` fails or the JSON output is unparseable).
    """
    result = shell.run(
        [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            str(source_path),
        ],
        timeout=30,
    )
    if not result.success:
        logger.warning(
            "Cannot estimate FULL size for %s: qemu-img info failed: %s",
            source_path,
            result.error or result.stderr,
        )
        return None

    try:
        chain = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Cannot estimate FULL size for %s: unparseable JSON: %s",
            source_path,
            exc,
        )
        return None

    # Accept both "image" (legacy QEMU) and "filename" (QEMU 11.0+) as
    # the key for each chain element.  Walk the flat list; ignore
    # nested "children" arrays (same semantics as ChainScanResult).
    total: int = 0
    for element in chain:
        actual_size = element.get("actual-size")
        if actual_size is None:
            continue
        try:
            total += int(actual_size)
        except (ValueError, TypeError):
            continue

    return total if total > 0 else None


def estimate_incremental_size(shell: IShell, source_path: Path) -> int | None:
    """Estimate the maximum size of an incremental backup of *source_path*.

    Returns the ``actual-size`` of the active layer (the top-level image
    at *source_path*) — an upper bound for a dirty-block delta.
    Returns ``None`` when undecidable.
    """
    result = shell.run(
        [
            "qemu-img",
            "info",
            "--force-share",
            "--output=json",
            str(source_path),
        ],
        timeout=30,
    )
    if not result.success:
        logger.warning(
            "Cannot estimate incremental size for %s: qemu-img info failed: %s",
            source_path,
            result.error or result.stderr,
        )
        return None

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Cannot estimate incremental size for %s: unparseable JSON: %s",
            source_path,
            exc,
        )
        return None

    actual_size = info.get("actual-size")
    if actual_size is None:
        return None
    try:
        size = int(actual_size)
    except (ValueError, TypeError):
        return None
    return size if size > 0 else None


def check_free_space(
    target_dir: Path,
    estimate: int | None,
    reserve: int = 0,
    factor: float = 1.0,
) -> SpaceCheckResult:
    """Check whether *target_dir* has enough free space for a transfer.

    Compares ``shutil.disk_usage(target_dir).free >= estimate * factor +
    reserve``.  When *estimate* is ``None`` (undecidable), returns a
    result with ``sufficient=True`` and ``error`` set — the caller
    should treat this as "proceed with warning" (never block on an
    undecidable estimate, design D5).

    Args:
        target_dir: The directory on the target filesystem.
        estimate: Estimated transfer size in bytes, or None.
        reserve: Extra safety margin in bytes (``free_space_reserve``).
        factor: Multiplier for the estimate (``free_space_factor``).

    Returns:
        :class:`SpaceCheckResult` with the comparison outcome.
    """
    try:
        usage = shutil.disk_usage(str(target_dir))
    except OSError as exc:
        return SpaceCheckResult(
            sufficient=True,
            free_bytes=0,
            estimate=estimate,
            error=f"disk_usage failed for {target_dir}: {exc} — gate not applied",
        )

    free = usage.free

    if estimate is None:
        return SpaceCheckResult(
            sufficient=True,
            free_bytes=free,
            estimate=None,
            error="estimate undecidable — gate not applied",
        )

    required = int(estimate * factor) + reserve
    return SpaceCheckResult(
        sufficient=free >= required,
        free_bytes=free,
        estimate=estimate,
        required=required,
    )
