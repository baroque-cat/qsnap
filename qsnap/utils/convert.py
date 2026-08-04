"""Stateless standalone-image conversion helpers.

Provides :func:`convert_to_standalone`, :func:`verify_standalone_image`,
and :func:`convert_with_retry` — shared by ``Core.fork()`` and
``Core.restore()`` so both meet the same reliability bar as the backup
pipeline (verify, retry, no partial litter — design D5).

These functions do not implement any ABC and are shared across module
boundaries, so they live in ``qsnap.utils`` rather than under a domain
module (same precedent as :func:`scan_backing_chain` in
``qsnap.utils.verification``).  They are **stateless**: they never read
or write ``IStateManager`` state, configuration files, domain XML, or
libvirt objects.  Every external command goes through the injected
:class:`IShell`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from pathlib import Path
from typing import cast

from qsnap.interfaces.shell import IShell
from qsnap.models.results import ShellResult
from qsnap.utils.retry import compute_backoff, is_retryable, parse_retry_duration

logger = logging.getLogger(__name__)

# Default fixed timeout for a single ``qemu-img convert`` run (seconds).
_DEFAULT_CONVERT_TIMEOUT = 7200  # 2 hours

# Timeout for the short ``qemu-img info`` metadata probes (seconds).
_INFO_TIMEOUT = 60

# Timeout for ``qemu-img check`` structural verification (seconds).
_CHECK_TIMEOUT = 7200


def _remove_partial(shell: IShell, output: Path) -> None:
    """Best-effort remove a partial *output* file via *shell*.

    Failures are swallowed (best-effort) — a leftover file is logged by
    the caller's error path, never raised.
    """
    with contextlib.suppress(Exception):  # noqa: BLE001 — best-effort cleanup
        shell.run(["rm", "-f", str(output)], timeout=10)


def convert_to_standalone(
    shell: IShell,
    source: Path,
    output: Path,
    timeout: int = _DEFAULT_CONVERT_TIMEOUT,
) -> ShellResult:
    """Flatten *source* (a qcow2 with a backing chain) into a standalone qcow2.

    Executes ``qemu-img convert --force-share -O qcow2 <source> <output>``
    through the injected *shell*.  ``--force-share`` is required because
    *source* may be the active layer of a running VM holding an exclusive
    write lock.

    On command failure the partial *output* file is removed best-effort
    and the failed :class:`ShellResult` is returned.  Expected failures
    (conversion errors, timeouts, missing binaries) are returned, never
    raised.

    Args:
        shell: :class:`IShell` instance for running qemu-img commands.
        source: Path to the source qcow2 (may have a backing chain).
        output: Path where the standalone qcow2 is written.
        timeout: Fixed timeout in seconds for the convert command.

    Returns:
        A :class:`ShellResult` — ``success`` is True iff the conversion
        completed; ``error`` describes the failure otherwise.
    """
    result = shell.run(
        [
            "qemu-img",
            "convert",
            "--force-share",
            "-O",
            "qcow2",
            str(source),
            str(output),
        ],
        timeout=timeout,
        check=True,
    )
    if not result.success:
        _remove_partial(shell, output)
    return result


def _read_virtual_size(shell: IShell, path: Path, force_share: bool) -> int | None:
    """Return the ``virtual-size`` of *path*, or None when unreadable."""
    cmd = ["qemu-img", "info"]
    if force_share:
        cmd.append("--force-share")
    cmd += ["--output=json", str(path)]
    result = shell.run(cmd, timeout=_INFO_TIMEOUT, check=True)
    if not result.success:
        return None
    try:
        info = cast(dict[str, object], json.loads(result.stdout))
    except json.JSONDecodeError:
        return None
    try:
        return int(cast(int, info.get("virtual-size", 0)))
    except (TypeError, ValueError):
        return None


def verify_standalone_image(
    shell: IShell,
    source: Path,
    output: Path,
) -> str | None:
    """Verify a freshly converted standalone image.

    Two verification tiers (mirroring the FULL-backup M1/M2 convention):

    - **M1** — virtual-size equality: the converted *output* must report
      the same ``virtual-size`` as the *source* chain
      (``qemu-img info --force-share --output=json``).  A mismatch
      indicates a truncated or wrong conversion.
    - **M2** — structural integrity: ``qemu-img check <output>`` must
      report zero errors and zero corruptions.  Leaks are tolerated.

    Args:
        shell: :class:`IShell` instance for running qemu-img commands.
        source: Path to the source qcow2 (chain head) the image was
            converted from — may be a live active layer (read with
            ``--force-share``).
        output: Path to the converted standalone qcow2 to verify.

    Returns:
        ``None`` when both tiers pass, or an error string naming the
        failed tier otherwise (same convention as
        :func:`verify_full_backup`).
    """
    # ── M1: virtual-size equality ────────────────────────────────────
    source_vsize = _read_virtual_size(shell, source, force_share=True)
    if source_vsize is None:
        return f"M1 failed: cannot read source virtual-size for {source}"
    output_vsize = _read_virtual_size(shell, output, force_share=True)
    if output_vsize is None:
        return f"M1 failed: cannot read output virtual-size for {output}"
    if source_vsize != output_vsize:
        return f"M1 failed: virtual-size mismatch (source={source_vsize}, output={output_vsize})"

    # ── M2: structural integrity via qemu-img check ──────────────────
    check_result = shell.run(
        ["qemu-img", "check", "--output=json", str(output)],
        timeout=_CHECK_TIMEOUT,
        check=True,
    )
    if not check_result.success:
        detail = check_result.stderr or check_result.error or "unknown"
        return f"M2 failed: qemu-img check returned {detail}"
    try:
        check_data = cast(dict[str, object], json.loads(check_result.stdout))
    except json.JSONDecodeError:
        return "M2 failed: cannot parse qemu-img check JSON output"

    errors = int(cast(int, check_data.get("errors", 0)))
    corruptions = int(cast(int, check_data.get("corruptions", 0)))
    if errors > 0 or corruptions > 0:
        return f"M2 failed: qemu-img check found {errors} errors and {corruptions} corruptions"

    return None


def convert_with_retry(
    shell: IShell,
    source: Path,
    output: Path,
    retry_max: int,
    retry_base: str,
) -> ShellResult:
    """Convert *source* to a standalone image, retrying retryable failures.

    Wraps :func:`convert_to_standalone` with the backup retry policy:
    only errors classified retryable by :func:`is_retryable` are retried,
    sleeping :func:`compute_backoff` between attempts, up to *retry_max*
    total attempts.  Any partial *output* file is removed before each
    retry attempt.  Callers pass ``GlobalConfig.backup_retry_max`` and
    ``GlobalConfig.backup_retry_base`` as the limits — no new
    configuration options are introduced (design D5).

    ``retry_max`` is the total number of attempts (matching
    ``Core._execute_with_retry``): ``retry_max <= 0`` executes exactly
    once.  A non-retryable failure returns immediately without further
    attempts.

    Args:
        shell: :class:`IShell` instance for running qemu-img commands.
        source: Path to the source qcow2 (may have a backing chain).
        output: Path where the standalone qcow2 is written.
        retry_max: Maximum total number of convert attempts.
        retry_base: Exponential-backoff base duration string (e.g. ``"2s"``).

    Returns:
        The successful :class:`ShellResult`, or the last failed one when
        attempts are exhausted or a non-retryable error occurs.
    """
    base_seconds = parse_retry_duration(retry_base)

    if retry_max <= 0:
        return convert_to_standalone(shell, source, output)

    result: ShellResult | None = None
    for attempt in range(1, retry_max + 1):
        if attempt > 1:
            # Remove any partial output, then back off before retrying.
            _remove_partial(shell, output)
            backoff = compute_backoff(base_seconds, attempt - 1)
            logger.info(
                "Retrying standalone conversion (attempt %d/%d, backoff %.1fs)",
                attempt,
                retry_max,
                backoff,
            )
            time.sleep(backoff)

        result = convert_to_standalone(shell, source, output)
        if result.success:
            if attempt > 1:
                logger.info(
                    "Standalone conversion succeeded on retry attempt %d/%d",
                    attempt,
                    retry_max,
                )
            return result

        error = result.error or ""
        if not is_retryable(error):
            return result

        if attempt >= retry_max:
            logger.warning(
                "Standalone conversion failed after %d attempts: %s",
                retry_max,
                error,
            )
            return result

    # Unreachable in practice — the loop always returns — but satisfies
    # the type checker.
    return result  # type: ignore[return-value]


__all__ = [
    "convert_to_standalone",
    "convert_with_retry",
    "verify_standalone_image",
]
