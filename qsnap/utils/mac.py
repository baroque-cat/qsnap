"""Shared MAC (AppArmor/SELinux) denial detection.

Used by lifecycle managers (``BlockCommitManager``, ``QemuImgCommitManager``)
to recognize Mandatory Access Control denials in command stderr so Core can
defer the operation instead of treating it as a hard failure.
"""

from __future__ import annotations


def detect_mac_denial(stderr: str) -> str | None:
    """Detect AppArmor/SELinux denial from command stderr.

    Returns ``"apparmor"``, ``"selinux"``, or ``None`` if the error is
    not MAC-related.
    """
    lower = stderr.lower()
    if "permission denied" in lower or "apparmor" in lower:
        return "apparmor"
    if "operation not permitted" in lower or "avc" in lower:
        return "selinux"
    return None
