"""Cross-cutting backup verification logic.

Provides stateless verification functions used by
``BitmapBackupProvider`` and ``Core`` to verify that a transferred
backup file is a valid qcow2 image.

These functions do not implement any ABC and are shared across module
boundaries, so they live in ``qsnap.utils`` rather than under
``qsnap.modules.backup``.

:func:`verify_full_backup` verifies standalone FULL backup files
(structural integrity via ``qemu-img info``/``qemu-img check``, plus
optional content comparison).

:func:`verify_bitmap_incremental` verifies backing-chained bitmap
delta files: in addition to format and virtual-size, it checks the
delta's ``backing-filename`` against the resolved previous backup and
enforces the dirty-size regression barrier (``actual-size ≤ dirty_bytes
× 2 + 64 MiB``) that catches an engine regression to full-copy
behavior.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

from qsnap.interfaces.shell import IShell
from qsnap.models.results import ChainScanResult, CommitResult

logger = logging.getLogger(__name__)

_VERIFY_COMPARE_TIMEOUT = 7200  # 2 hours

_DIRTY_BARRIER_FACTOR = 2
"""Multiplier on dirty bytes for the bitmap incremental regression barrier."""

_DIRTY_BARRIER_SLACK = 64 * 1024 * 1024
"""Slack (64 MiB) added to the barrier to absorb qcow2 metadata overhead."""


def verify_full_backup(
    shell: IShell,
    target_path: Path,
    verify_mode: str,
    source_path: Path | None = None,
    expected_virtual_size: int | None = None,
) -> str | None:
    """Verify a standalone FULL backup file.

    Verifies a standalone FULL backup file's structural integrity.
    Used at three FULL lifecycle points: post-creation
    (before state recording), pre-rebase (before linking incrementals),
    and pre-deletion (before cascade-deletion).

    Args:
        shell: :class:`IShell` instance for running qemu-img commands.
        target_path: Path to the FULL backup qcow2 file to verify.
        verify_mode: One of ``"off"``, ``"metadata"``, ``"check"``,
            ``"compare"``.
        source_path: Path to the source snapshot for ``"compare"`` mode
            (M3 — ``qemu-img compare`` content verification).  The
            compare traverses the backing chain automatically.  If
            ``None`` in ``"compare"`` mode, M3 is skipped.
        expected_virtual_size: When provided, verify the FULL's
            virtual-size matches this value (M1).

    Returns:
        ``None`` on success (all enabled checks pass), or an error
        string starting with ``"verification failed: ..."`` on failure.
    """
    if verify_mode == "off":
        return None

    # Deprecation: "hash" was replaced by "compare" (unify-nbd-transfer).
    if verify_mode == "hash":
        logger.warning(
            "verify_mode=%r is deprecated — treating as 'compare'",
            verify_mode,
        )
        verify_mode = "compare"

    # ── M1: Metadata verification (always runs for metadata/check/compare) ──

    info_cmd = [
        "qemu-img",
        "info",
        "--output=json",
        str(target_path),
    ]
    info_result = shell.run(info_cmd, timeout=60, check=True)
    if not info_result.success:
        error_detail = info_result.stderr or info_result.error or "unknown"
        return f"verification failed: qemu-img info returned {error_detail}"

    try:
        info = cast(dict[str, object], json.loads(info_result.stdout))
    except json.JSONDecodeError as exc:
        return f"verification failed: cannot parse qemu-img info JSON: {exc}"

    # (a) format check
    target_format = info.get("format", "")
    if target_format != "qcow2":
        return f"verification failed: expected format qcow2, got {target_format}"

    # (b) corrupt-bit detection — check incompatible-features for "corrupt"
    format_specific_raw = info.get("format-specific")
    if isinstance(format_specific_raw, dict):
        format_specific = cast(dict[str, object], format_specific_raw)
        data = format_specific.get("data", {})
        if isinstance(data, dict):
            incompatible = data.get("incompatible-features", [])  # type: ignore[reportUnknownVariableType]
            if isinstance(incompatible, list):
                for feature in incompatible:  # type: ignore[reportUnknownVariableType]
                    if isinstance(feature, dict) and feature.get("name") == "corrupt":  # type: ignore[reportUnknownMemberType]
                        return (
                            "verification failed: FULL backup has corrupt bit set — file is damaged"
                        )
                    if isinstance(feature, str) and feature == "corrupt":
                        return (
                            "verification failed: FULL backup has corrupt bit set — file is damaged"
                        )

    # (c) optional virtual-size match
    if expected_virtual_size is not None:
        target_vsize = int(cast(int, info.get("virtual-size", 0)))
        if target_vsize != expected_virtual_size:
            return (
                f"verification failed: virtual-size mismatch "
                f"(expected={expected_virtual_size}, got={target_vsize})"
            )

    # ── M2: Structural verification (check/compare modes only) ────────

    if verify_mode in ("check", "compare"):
        check_cmd = [
            "qemu-img",
            "check",
            "--output=json",
            str(target_path),
        ]
        check_result = shell.run(check_cmd, timeout=7200, check=True)
        if not check_result.success:
            error_detail = check_result.stderr or check_result.error or "unknown"
            return f"verification failed: qemu-img check returned {error_detail}"

        try:
            check_data = json.loads(check_result.stdout)
        except json.JSONDecodeError as exc:
            return f"verification failed: cannot parse qemu-img check JSON: {exc}"

        errors = int(check_data.get("errors", 0))
        if errors > 0:
            return f"verification failed: qemu-img check found {errors} errors"

        leaks = int(check_data.get("leaks", 0))
        if leaks > 0:
            return f"verification failed: qemu-img check found {leaks} leaks"

        corruptions = int(check_data.get("corruptions", 0))
        if corruptions > 0:
            return f"verification failed: qemu-img check found {corruptions} corruptions"

    # ── M3: Content comparison via qemu-img compare (compare mode only)

    if verify_mode == "compare" and source_path is not None:
        compare_cmd = [
            "qemu-img",
            "compare",
            "-q",
            "--force-share",
            str(source_path),
            str(target_path),
        ]
        compare_result = shell.run(compare_cmd, timeout=7200, check=True)
        if not compare_result.success:
            error_detail = compare_result.stderr or compare_result.error or "unknown"
            if "lock" in error_detail.lower() or "shared" in error_detail.lower():
                return (
                    "verification failed: content comparison failed "
                    "(source may be locked by running VM); "
                    "consider verify='metadata' or 'check'"
                )
            return f"verification failed: content comparison mismatch: {error_detail}"

    return None


def verify_bitmap_incremental(
    shell: IShell,
    source_path: str,
    delta_path: str,
    expected_backing: str,
    dirty_bytes: int,
    verify_mode: str,
) -> str | None:
    """Verify a bitmap-mode incremental delta (backing-chained qcow2).

    Checks (all tiers except ``"off"``):

    a. ``qemu-img info`` reports format ``qcow2``.
    b. ``virtual-size`` matches the source disk exactly.
    c. ``backing-filename`` equals *expected_backing* (the resolved
       previous backup path the delta was chained to).
    d. Regression barrier: the file's ``actual-size`` does not exceed
       ``dirty_bytes × 2 + 64 MiB`` — *dirty_bytes* is the sum of dirty
       extent lengths measured by the copy loop before transfer.  A
       breach means the engine regressed to copying the full disk.

    For ``verify_mode`` ``"compare"``,
    ``qemu-img compare -q --force-share <source> <delta>`` additionally
    compares virtual disk content across both backing chains
    (chain-traversing; a live-source WARNING is logged).

    Args:
        shell: :class:`IShell` instance for running qemu-img commands.
        source_path: Path to the source snapshot qcow2 (may be the live
            active layer — read with ``--force-share``).
        delta_path: Path to the transferred incremental delta.
        expected_backing: Absolute path of the previous backup the delta
            must chain to (``backing-filename`` check).
        dirty_bytes: Sum of dirty extent lengths measured by the copy
            loop before transfer (regression barrier input).
        verify_mode: One of ``"off"``, ``"metadata"``, ``"check"``,
            ``"compare"``.

    Returns:
        ``None`` on success, or an error string starting with
        ``"verification failed: ..."`` on failure.
    """
    if verify_mode == "off":
        return None

    # Deprecation: "hash"/"full" were replaced by "compare"
    # (unify-nbd-transfer).
    if verify_mode in ("hash", "full"):
        logger.warning(
            "verify_mode=%r is deprecated — treating as 'compare'",
            verify_mode,
        )
        verify_mode = "compare"

    # ── Source info (for virtual-size match) ─────────────────────────
    # --force-share: the source may be the active layer of a running
    # VM, which has an exclusive write lock (design D5).
    source_info_cmd = [
        "qemu-img",
        "info",
        "--force-share",
        "--output=json",
        str(source_path),
    ]
    source_result = shell.run(source_info_cmd, timeout=60, check=True)
    if not source_result.success:
        return f"verification failed: cannot get source info: {source_result.error}"

    # ── Delta info ───────────────────────────────────────────────────
    delta_info_cmd = [
        "qemu-img",
        "info",
        "--output=json",
        str(delta_path),
    ]
    delta_result = shell.run(delta_info_cmd, timeout=60, check=True)
    if not delta_result.success:
        return f"verification failed: cannot get delta info: {delta_result.error}"

    try:
        source_info = json.loads(source_result.stdout)
    except json.JSONDecodeError as exc:
        return f"verification failed: cannot parse source info JSON: {exc}"

    try:
        delta_info = json.loads(delta_result.stdout)
    except json.JSONDecodeError as exc:
        return f"verification failed: cannot parse delta info JSON: {exc}"

    # (a) format check
    delta_format = delta_info.get("format", "")
    if delta_format != "qcow2":
        return f"verification failed: expected format qcow2, got {delta_format}"

    # (b) virtual-size match (exact)
    source_vsize = int(source_info.get("virtual-size", 0))
    delta_vsize = int(delta_info.get("virtual-size", 0))
    if source_vsize != delta_vsize:
        return (
            f"verification failed: virtual-size mismatch "
            f"(expected={source_vsize}, got={delta_vsize})"
        )

    # (c) backing-filename check — the delta must chain to the resolved
    # previous backup (design D3).  Checked before any content
    # comparison.
    backing = delta_info.get("backing-filename")
    if backing != expected_backing:
        return (
            f"verification failed: backing-filename mismatch "
            f"(expected={expected_backing}, got={backing})"
        )

    # (d) dirty-size regression barrier: actual-size must stay within
    # dirty_bytes × 2 + 64 MiB slack (K=2 absorbs qcow2 metadata; a
    # breach means the engine regressed to full-copy — design R5).
    actual_size = int(delta_info.get("actual-size", 0))
    barrier = dirty_bytes * _DIRTY_BARRIER_FACTOR + _DIRTY_BARRIER_SLACK
    if actual_size > barrier:
        return (
            f"verification failed: delta actual-size {actual_size} exceeds "
            f"dirty-data barrier (dirty_bytes={dirty_bytes} × "
            f"{_DIRTY_BARRIER_FACTOR} + 64 MiB slack = {barrier}) — "
            "engine regressed to full copy"
        )

    # ── Content comparison across chains (compare tier) ──────────────
    if verify_mode == "compare":
        # Live-source caveat: the source may be a running VM's active
        # layer; --force-share avoids hard lock errors, but writes
        # during the comparison may produce false mismatches (design
        # D5/R6).
        logger.warning(
            "verify=%s on running VM active layer %s — "
            "results may be unreliable, consider verify='metadata'",
            verify_mode,
            source_path,
        )
        compare_cmd = [
            "qemu-img",
            "compare",
            "-q",
            "--force-share",
            str(source_path),
            str(delta_path),
        ]
        compare_result = shell.run(
            compare_cmd,
            timeout=_VERIFY_COMPARE_TIMEOUT,
            check=True,
        )
        if not compare_result.success:
            error_detail = compare_result.error or compare_result.stderr or ""
            if "lock" in error_detail.lower() or "shared" in error_detail.lower():
                return "verification failed: lock conflict — use verify='metadata' for live sources"
            return f"verification failed: content comparison mismatch: {error_detail}"

    return None


# ── Shared lifecycle verification helpers ────────────────────────────────


def deep_verify_base_image(
    shell: IShell,
    base_image: Path,
) -> CommitResult | None:
    """Run ``qemu-img check`` on *base_image* after a blockcommit.

    Consolidates the ~44-line identical deep-verify block from
    ``BlockCommitManager`` and ``QemuImgCommitManager``.  The
    ``shell.run()`` call does NOT pass ``check=True`` — the function
    inspects ``chk.success`` and ``chk.error`` directly, consistent with
    the shell abstraction contract (result objects, not exceptions for
    expected failures).

    Args:
        shell: :class:`IShell` instance for running qemu-img commands.
        base_image: Path to the base qcow2 image to verify.

    Returns:
        ``None`` on success (zero corruptions/errors/leaks), or a
        :class:`CommitResult` with ``success=False`` on failure.
    """
    chk = shell.run(
        ["qemu-img", "check", "--output=json", str(base_image)],
        timeout=3600,
    )
    if not chk.success:
        return CommitResult(
            success=False,
            committed_snapshot="",
            error=f"deep verify: qemu-img check failed: {chk.error}",
        )
    try:
        data = json.loads(chk.stdout)
    except json.JSONDecodeError:
        return CommitResult(
            success=False,
            committed_snapshot="",
            error="deep verify: failed to parse qemu-img check output",
        )
    for field_name in ("corruptions", "errors", "leaks"):
        count = int(data.get(field_name, 0))
        if count > 0:
            return CommitResult(
                success=False,
                committed_snapshot="",
                error=f"deep verify: {count} {field_name} in base image",
            )
    return None


def scan_backing_chain(
    shell: IShell,
    entry_path: Path,
) -> ChainScanResult:
    """Scan a qcow2 backing chain starting from *entry_path*.

    Runs ``qemu-img info --force-share --backing-chain --output=json``
    and verifies: (a) every file referenced in the chain exists on the
    filesystem, (b) every file has format ``"qcow2"``, (c) the
    ``backing-filename`` reference in each image matches the actual next
    file in the chain, (d) no file appears twice (no cycles).

    The JSON parsing accepts both ``"image"`` (legacy QEMU) and
    ``"filename"`` (QEMU 11.0+) as the key for the disk image file path.
    The ``"children"`` nested array is ignored.

    Args:
        shell: :class:`IShell` instance for running qemu-img commands.
        entry_path: Path to the entry point of the backing chain
            (typically the active layer or the last incremental).

    Returns:
        :class:`ChainScanResult` with ``paths`` containing all file
        paths found in the chain, ``broken_files`` listing files with
        issues, ``success`` indicating whether the scan command itself
        succeeded, and ``error`` describing any command/parse failure.
    """
    result = shell.run(
        [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            str(entry_path),
        ],
        timeout=30,
    )
    if not result.success:
        return ChainScanResult(
            paths=set(),
            broken_files=[],
            success=False,
            error=f"qemu-img info failed: {result.error}",
        )

    try:
        raw_chain = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ChainScanResult(
            paths=set(),
            broken_files=[],
            success=False,
            error="failed to parse qemu-img info JSON output",
        )

    # qemu-img info --backing-chain returns a flat JSON array of
    # objects, each describing one image in the chain (entry → base).
    if not isinstance(raw_chain, list):
        return ChainScanResult(
            paths=set(),
            broken_files=[],
            success=False,
            error="unexpected qemu-img info JSON structure (expected array)",
        )

    paths: set[str] = set()
    broken_files: list[str] = []
    seen: set[str] = set()

    for i, item in enumerate(raw_chain):
        if not isinstance(item, dict):
            continue

        # Accept both "image" (legacy) and "filename" (QEMU 11.0+) keys.
        file_path = item.get("filename") or item.get("image") or ""

        # Format check.
        fmt = item.get("format", "")
        if fmt != "qcow2":
            broken_files.append(file_path)
            paths.add(file_path)
            continue

        # Existence check (via IShell for mockability).
        existence = shell.run(
            ["test", "-f", file_path],
            timeout=10,
            check=True,
        )
        if not existence.success:
            broken_files.append(file_path)
            paths.add(file_path)
            continue

        # Cycle detection.
        if file_path in seen:
            broken_files.append(f"cycle detected at {file_path}")
            paths.add(file_path)
            continue

        seen.add(file_path)
        paths.add(file_path)

        # Backing-filename consistency (spec point c): the
        # backing-filename should match the next entry's filename in
        # the chain array.  Also verify the referenced file exists.
        backing = item.get("backing-filename")
        if backing:
            backing_existence = shell.run(
                ["test", "-f", backing],
                timeout=10,
                check=True,
            )
            if not backing_existence.success:
                broken_files.append(file_path)
            if i + 1 < len(raw_chain):
                next_item = raw_chain[i + 1]
                if isinstance(next_item, dict):
                    next_path = next_item.get("filename") or next_item.get("image") or ""
                    if next_path and backing != next_path:
                        broken_files.append(f"backing-filename mismatch in {Path(file_path).name}")

    return ChainScanResult(
        paths=paths,
        broken_files=broken_files,
        success=True,
        error=None,
    )
