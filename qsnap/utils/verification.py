"""Cross-cutting backup verification logic.

Provides stateless verification functions used by both
``FileCopyBackupProvider`` and ``BitmapBackupProvider`` to verify that
a transferred backup file is a valid qcow2 image matching the source.

These functions do not implement any ABC and are shared across module
boundaries, so they live in ``qsnap.utils`` rather than under
``qsnap.modules.backup``.

Verification levels (``TargetConfig.verify``):
- ``"off"``: no verification.
- ``"metadata"``: ``qemu-img info`` consistency check (format,
  virtual-size).  ``actual-size`` is intentionally NOT checked — it is
  unreliable for live sources because the running VM writes data to the
  active snapshot layer between transfer and verification.
- ``"hash"``: SHA-256 hash comparison (computed at snapshot creation time,
  stored in ``SnapshotInfo.content_hash``, validated on target after transfer).
- ``"full"``: metadata check + ``qemu-img compare -q --force-share``
  byte-level comparison.  ``--force-share`` avoids lock errors on live
  sources; a WARNING is logged because results may be unreliable if the
  VM writes during the comparison.

Bitmap incrementals use :func:`verify_bitmap_incremental` instead of
:func:`verify_backup`: in addition to format and virtual-size, it checks
the delta's ``backing-filename`` against the resolved previous backup and
enforces the dirty-size regression barrier (``actual-size ≤ dirty_bytes ×
2 + 64 MiB``) that catches an engine regression to full-copy behavior.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

from qsnap.interfaces.shell import IShell
from qsnap.utils.hash import file_sha256

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
    """Verify a standalone FULL backup file (no source comparison).

    Unlike :func:`verify_backup` (which compares source and target),
    this function verifies a standalone FULL backup file's structural
    integrity.  Used at three FULL lifecycle points: post-creation
    (before state recording), pre-rebase (before linking incrementals),
    and pre-deletion (before cascade-deletion).

    Args:
        shell: :class:`IShell` instance for running qemu-img commands.
        target_path: Path to the FULL backup qcow2 file to verify.
        verify_mode: One of ``"off"``, ``"metadata"``, ``"check"``,
            ``"hash"``.
        source_path: Path to the source snapshot for ``"hash"`` mode
            (M3 — ``qemu-img compare`` content verification).  The
            compare traverses the backing chain automatically.  If
            ``None`` in ``"hash"`` mode, M3 is skipped.
        expected_virtual_size: When provided, verify the FULL's
            virtual-size matches this value (M1).

    Returns:
        ``None`` on success (all enabled checks pass), or an error
        string starting with ``"verification failed: ..."`` on failure.
    """
    if verify_mode == "off":
        return None

    # ── M1: Metadata verification (always runs for metadata/check/hash) ──

    info_cmd = [
        "qemu-img",
        "info",
        "--output=json",
        str(target_path),
    ]
    info_result = shell.run(info_cmd, timeout=60)
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

    # ── M2: Structural verification (check/hash modes only) ────────────

    if verify_mode in ("check", "hash"):
        check_cmd = [
            "qemu-img",
            "check",
            "--output=json",
            str(target_path),
        ]
        check_result = shell.run(check_cmd, timeout=7200)
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

    # ── M3: Content comparison via qemu-img compare (hash mode only) ──
    # NOTE: This replaces the previous SHA-256 hash comparison which was
    # broken — SHA-256 of a snapshot delta file (with backing chain)
    # never matches SHA-256 of a standalone NBD-converted FULL file.
    # qemu-img compare traverses the backing chain automatically and
    # compares virtual-disk content visible to the guest OS.

    if verify_mode == "hash" and source_path is not None:
        compare_cmd = [
            "qemu-img",
            "compare",
            "-q",
            "--force-share",
            str(source_path),
            str(target_path),
        ]
        compare_result = shell.run(compare_cmd, timeout=7200)
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

    For ``verify_mode`` ``"hash"`` or ``"full"``,
    ``qemu-img compare -q --force-share <source> <delta>`` additionally
    compares virtual disk content across both backing chains
    (chain-traversing; the live-source WARNING is logged as in
    :func:`verify_backup`).

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
            ``"hash"``, ``"full"``.

    Returns:
        ``None`` on success, or an error string starting with
        ``"verification failed: ..."`` on failure.
    """
    if verify_mode == "off":
        return None

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
    source_result = shell.run(source_info_cmd, timeout=60)
    if not source_result.success:
        return f"verification failed: cannot get source info: {source_result.error}"

    # ── Delta info ───────────────────────────────────────────────────
    delta_info_cmd = [
        "qemu-img",
        "info",
        "--output=json",
        str(delta_path),
    ]
    delta_result = shell.run(delta_info_cmd, timeout=60)
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

    # ── Content comparison across chains (hash/full tiers) ───────────
    if verify_mode in ("hash", "full"):
        # Same live-source caveat as verify_backup: the source may be a
        # running VM's active layer; --force-share avoids hard lock
        # errors, but writes during the comparison may produce false
        # mismatches (design D5/R6).
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
        )
        if not compare_result.success:
            error_detail = compare_result.error or compare_result.stderr or ""
            if "lock" in error_detail.lower() or "shared" in error_detail.lower():
                return (
                    "verification failed: lock conflict — "
                    "use verify='metadata' for live sources"
                )
            return f"verification failed: content comparison mismatch: {error_detail}"

    return None


def verify_backup(
    shell: IShell,
    source_path: str,
    target_path: str,
    verify_mode: str,
    expected_hash: str | None = None,
) -> str | None:
    """Verify a backup file against its source.

    Metadata verification (always runs unless ``verify_mode == "off"``)
    checks: (a) target format is ``"qcow2"``, (b) target ``virtual-size``
    matches the source exactly.  ``actual-size`` is intentionally NOT
    checked — it is unreliable for live sources because the running VM
    writes data to the active snapshot layer between transfer completion
    and verification (design D1).

    Args:
        shell: IShell instance for running qemu-img commands.
        source_path: Path to the source qcow2 file.
        target_path: Path to the target (backup) qcow2 file.
        verify_mode: One of ``"off"``, ``"metadata"``, ``"hash"``, ``"full"``.
        expected_hash: SHA-256 hex digest of the source file, required
            for ``"hash"`` mode.  If ``None`` in ``"hash"`` mode, hash
            verification is skipped.

    Returns:
        ``None`` on success, or an error string starting with
        ``"verification failed: ..."`` on failure.
    """
    if verify_mode == "off":
        return None

    # ── Metadata verification ────────────────────────────────────────

    # Source info
    # --force-share: the source may be the active layer of a running
    # VM, which has an exclusive write lock.  --force-share requests a
    # shared lock for this metadata-only read (design D5).
    source_info_cmd = [
        "qemu-img",
        "info",
        "--force-share",
        "--output=json",
        str(source_path),
    ]
    source_result = shell.run(source_info_cmd, timeout=60)
    if not source_result.success:
        return f"verification failed: cannot get source info: {source_result.error}"

    # Target info
    target_info_cmd = [
        "qemu-img",
        "info",
        "--output=json",
        str(target_path),
    ]
    target_result = shell.run(target_info_cmd, timeout=60)
    if not target_result.success:
        return f"verification failed: cannot get target info: {target_result.error}"

    try:
        source_info = json.loads(source_result.stdout)
    except json.JSONDecodeError as exc:
        return f"verification failed: cannot parse source info JSON: {exc}"

    try:
        target_info = json.loads(target_result.stdout)
    except json.JSONDecodeError as exc:
        return f"verification failed: cannot parse target info JSON: {exc}"

    # (a) format check
    target_format = target_info.get("format", "")
    if target_format != "qcow2":
        return f"verification failed: expected format qcow2, got {target_format}"

    # (b) virtual-size match (exact)
    source_vsize = int(source_info.get("virtual-size", 0))
    target_vsize = int(target_info.get("virtual-size", 0))
    if source_vsize != target_vsize:
        return "verification failed: virtual-size mismatch"

    # NOTE: actual-size is intentionally NOT checked.  It is unreliable
    # for live sources because the running VM writes data to the active
    # snapshot layer between transfer completion and verification,
    # causing the source's actual-size to grow beyond any reasonable
    # tolerance (design D1).  Format + virtual-size are sufficient for
    # metadata-level verification.

    # ── Hash verification (mid-level between metadata and full) ──────

    if verify_mode == "hash" and expected_hash is not None:
        try:
            actual_hash = file_sha256(Path(target_path))
        except OSError as exc:
            return f"verification failed: cannot read target file for hashing: {exc}"
        if actual_hash != expected_hash:
            return "verification failed: hash mismatch"

    # ── Full verification ────────────────────────────────────────────

    if verify_mode == "full":
        # Warn: if the source is a live VM active layer, qemu-img compare
        # with --force-share opens the image in shared mode — the
        # comparison may produce false mismatches if the VM writes
        # during the comparison.  --force-share is used to avoid hard
        # lock errors (design D5); a potential false mismatch is better
        # than no verification at all.
        logger.warning(
            "verify=full on running VM active layer %s — "
            "results may be unreliable, consider verify='metadata' "
            "or verify='hash'",
            source_path,
        )
        compare_cmd = [
            "qemu-img",
            "compare",
            "-q",
            "--force-share",
            str(source_path),
            str(target_path),
        ]
        compare_result = shell.run(
            compare_cmd,
            timeout=_VERIFY_COMPARE_TIMEOUT,
        )
        if not compare_result.success:
            error_detail = compare_result.error or compare_result.stderr or ""
            if "lock" in error_detail.lower() or "shared" in error_detail.lower():
                return (
                    "verification failed: lock conflict — "
                    "use verify='metadata' or verify='hash' for live sources"
                )
            return f"verification failed: data comparison mismatch: {error_detail}"

    return None
