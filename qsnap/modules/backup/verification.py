"""Shared backup verification logic.

Used by both ``FileCopyBackupProvider`` and ``BitmapBackupProvider`` to
verify that a transferred backup file is a valid qcow2 image matching
the source.

Verification levels (``TargetConfig.verify``):
- ``"off"``: no verification.
- ``"metadata"``: ``qemu-img info`` consistency check (format, virtual-size,
  actual-size tolerance).
- ``"hash"``: SHA-256 hash comparison (computed at snapshot creation time,
  stored in ``SnapshotInfo.content_hash``, validated on target after transfer).
- ``"full"``: metadata check + ``qemu-img compare -q`` byte-level comparison.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from qsnap.interfaces.shell import IShell

logger = logging.getLogger(__name__)

_VERIFY_COMPARE_TIMEOUT = 7200  # 2 hours

_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB


def _file_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file, reading 8 MB chunks.

    Returns the hex digest string.
    """
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


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
            actual_hash = _file_sha256(Path(target_path))
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
