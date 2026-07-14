"""Shared backup verification logic.

Used by both ``FileCopyBackupProvider`` and ``BitmapBackupProvider`` to
verify that a transferred backup file is a valid qcow2 image matching
the source.

Verification levels (``TargetConfig.verify``):
- ``"off"``: no verification.
- ``"metadata"``: ``qemu-img info`` consistency check (format, virtual-size,
  actual-size tolerance).
- ``"full"``: metadata check + ``qemu-img compare -q`` byte-level comparison.
"""

from __future__ import annotations

import json

from qsnap.interfaces.shell import IShell

_VERIFY_COMPARE_TIMEOUT = 7200  # 2 hours


def verify_backup(
    shell: IShell,
    source_path: str,
    target_path: str,
    verify_mode: str,
) -> str | None:
    """Verify a backup file against its source.

    Args:
        shell: IShell instance for running qemu-img commands.
        source_path: Path to the source qcow2 file.
        target_path: Path to the target (backup) qcow2 file.
        verify_mode: One of ``"off"``, ``"metadata"``, ``"full"``.

    Returns:
        ``None`` on success, or an error string starting with
        ``"verification failed: ..."`` on failure.
    """
    if verify_mode == "off":
        return None

    # ── Metadata verification ────────────────────────────────────────

    # Source info
    source_info_cmd = [
        "qemu-img", "info", "--output=json", str(source_path),
    ]
    source_result = shell.run(source_info_cmd, timeout=60)
    if not source_result.success:
        return (
            f"verification failed: cannot get source info: "
            f"{source_result.error}"
        )

    # Target info
    target_info_cmd = [
        "qemu-img", "info", "--output=json", str(target_path),
    ]
    target_result = shell.run(target_info_cmd, timeout=60)
    if not target_result.success:
        return (
            f"verification failed: cannot get target info: "
            f"{target_result.error}"
        )

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
        return (
            f"verification failed: expected format qcow2, "
            f"got {target_format}"
        )

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

    # ── Full verification ────────────────────────────────────────────

    if verify_mode == "full":
        compare_cmd = [
            "qemu-img", "compare", "-q",
            str(source_path), str(target_path),
        ]
        compare_result = shell.run(
            compare_cmd, timeout=_VERIFY_COMPARE_TIMEOUT,
        )
        if not compare_result.success:
            return "verification failed: data comparison mismatch"

    return None
