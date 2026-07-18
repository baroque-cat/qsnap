"""Cross-cutting backup verification logic.

Provides stateless verification functions used by both
``FileCopyBackupProvider`` and ``BitmapBackupProvider`` to verify that
a transferred backup file is a valid qcow2 image matching the source.

These functions do not implement any ABC and are shared across module
boundaries, so they live in ``qsnap.utils`` rather than under
``qsnap.modules.backup``.

Verification levels (``TargetConfig.verify``):
- ``"off"``: no verification.
- ``"metadata"``: ``qemu-img info`` consistency check (format, virtual-size,
  actual-size tolerance).
- ``"hash"``: SHA-256 hash comparison (computed at snapshot creation time,
  stored in ``SnapshotInfo.content_hash``, validated on target after transfer).
- ``"full"``: metadata check + ``qemu-img compare -q`` byte-level comparison.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from qsnap.interfaces.shell import IShell
from qsnap.utils.hash import file_sha256

logger = logging.getLogger(__name__)

_VERIFY_COMPARE_TIMEOUT = 7200  # 2 hours


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
        info = json.loads(info_result.stdout)
    except json.JSONDecodeError as exc:
        return f"verification failed: cannot parse qemu-img info JSON: {exc}"

    # (a) format check
    target_format = info.get("format", "")
    if target_format != "qcow2":
        return f"verification failed: expected format qcow2, got {target_format}"

    # (b) corrupt-bit detection — check incompatible-features for "corrupt"
    format_specific = info.get("format-specific")
    if isinstance(format_specific, dict):
        data = format_specific.get("data", {})
        if isinstance(data, dict):
            incompatible = data.get("incompatible-features", [])
            if isinstance(incompatible, list):
                for feature in incompatible:
                    if isinstance(feature, dict) and feature.get("name") == "corrupt":
                        return (
                            "verification failed: FULL backup has corrupt bit set "
                            "— file is damaged"
                        )
                    if isinstance(feature, str) and feature == "corrupt":
                        return (
                            "verification failed: FULL backup has corrupt bit set "
                            "— file is damaged"
                        )

    # (c) optional virtual-size match
    if expected_virtual_size is not None:
        target_vsize = int(info.get("virtual-size", 0))
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
            return (
                "verification failed: content comparison mismatch: "
                f"{error_detail}"
            )

    return None


def verify_backup(
    shell: IShell,
    source_path: str,
    target_path: str,
    verify_mode: str,
    expected_hash: str | None = None,
) -> str | None:
    """Verify a backup file against its source.

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

    # (c) actual-size tolerance (±10% for metadata overhead)
    source_asize = int(source_info.get("actual-size", 0))
    target_asize = int(target_info.get("actual-size", 0))
    if source_asize > 0:
        tolerance = source_asize * 0.1
        if abs(target_asize - source_asize) > tolerance:
            return (
                f"verification failed: actual-size out of tolerance "
                f"(source={source_asize}, target={target_asize})"
            )

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
        # may fail with a lock error (compare is a data-copying operation
        # that does NOT use --force-share — design D5).
        logger.warning(
            "Full verification (qemu-img compare) on source %s — "
            "if this is a live VM active layer, the compare may fail "
            "with a lock error; consider verify='metadata' or 'hash' "
            "for running VMs",
            source_path,
        )
        compare_cmd = [
            "qemu-img",
            "compare",
            "-q",
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
                    "verification failed: data comparison failed "
                    "(source may be locked by running VM); "
                    "consider verify='metadata' or 'hash'"
                )
            return f"verification failed: data comparison mismatch: {error_detail}"

    return None
