"""BlockCommitManager — backing chain lifecycle management via virsh blockcommit.

Implements ``ILifecycleManager``.  Does NOT inherit from Core (design D1).
Dependency: ``IShell`` only.
"""

from __future__ import annotations

import logging
from pathlib import Path

from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.shell import IShell
from qsnap.models.config import VMConfig
from qsnap.models.results import CommitResult, SnapshotInfo
from qsnap.utils.mac import detect_mac_denial
from qsnap.utils.verification import deep_verify_base_image

logger = logging.getLogger(__name__)


class BlockCommitManager(ILifecycleManager):
    """Manages backing chain lifecycle via ``virsh blockcommit``.

    Single-segment bulk commit (design D1): the ENTIRE merge set is
    committed with ONE ``virsh blockcommit --base … --top …`` segment
    command, copying each cluster at most once.  The commit is
    all-or-nothing for the live path.
    """

    def __init__(self, shell: IShell) -> None:
        self._shell = shell

    # ── ILifecycleManager implementation ──────────────────────────────

    def blockcommit(
        self,
        vm_config: VMConfig,
        snapshots_to_merge: list[SnapshotInfo],
        *,
        disk: str,
        base_image: Path,
        deep_verify: bool = False,
        timeout: int = 1800,
    ) -> CommitResult:
        """Merge snapshots of one disk into that disk's base image.

        Multi-disk (refactor): *disk* is the libvirt target device name and
        *base_image* is this disk's base qcow2 path.  The ``--path`` and
        ``--base`` arguments are taken from these parameters (not derived
        from a single VM-level base image).

        *timeout* is the maximum wall-clock time in seconds (default 1800).
        The commit command is executed via ``run_with_heartbeat`` so that
        a hung ``virsh blockcommit --wait`` is eventually killed and
        classified as ``outcome="unknown"`` instead of a definitive failure.

        Ordering contract (design D2): *snapshots_to_merge* MUST be ordered
        oldest-first (the retention engine output), so the NEWEST removable
        snapshot is ``snapshots_to_merge[-1]``.  The manager asserts only
        non-emptiness; an ordering violation cannot be detected from paths
        alone.

        1. If the list is empty → no-op success.
        2. Otherwise issue ONE segment command with ``--top`` = the newest
           removable snapshot (``snapshots_to_merge[-1].path``) and
           ``--delete --verbose --wait`` — no per-snapshot loop.
        3. When *deep_verify* is True, run ``qemu-img check`` on
           *base_image* after a successful commit.  If corruptions are
           detected, return ``CommitResult(success=False)``.
        """
        # Step 1: Empty list → no-op success (lifecycle-manager spec:
        # outcome="success" — success=True SHALL imply outcome="success").
        if not snapshots_to_merge:
            return CommitResult(success=True, committed_snapshot="", error=None, outcome="success")

        # Step 2: Single segment command — top is the newest removable
        # snapshot (oldest-first ordering contract, design D2).
        newest = snapshots_to_merge[-1]
        n = len(snapshots_to_merge)
        cmd = [
            "virsh",
            "blockcommit",
            "--domain",
            vm_config.name,
            "--path",
            disk,
            "--base",
            str(base_image),
            "--top",
            str(newest.path),
            "--delete",
            "--verbose",
            "--wait",
        ]

        def _heartbeat(elapsed: int, _count: int = n) -> None:
            logger.info(
                "[blockcommit] %s/%s: still collapsing %d %s into base (%ds elapsed)",
                vm_config.name,
                disk,
                _count,
                "layer" if _count == 1 else "layers",
                elapsed,
            )

        result = self._shell.run_with_heartbeat(
            cmd,
            timeout=timeout,
            heartbeat_seconds=60,
            on_heartbeat=_heartbeat,
            check=True,
        )
        if not result.success:
            # Check for MAC denial (AppArmor/SELinux) — still a definitive
            # failure (the commit never started).
            mac_reason = detect_mac_denial(result.stderr)
            if mac_reason is not None:
                return CommitResult(
                    success=False,
                    committed_snapshot="",
                    error=f"blocked by {mac_reason}",
                    outcome="failure",
                )
            # Timeout / kill → unknown outcome (job may have completed).
            if result.error and "timed out" in result.error:
                return CommitResult(
                    success=False,
                    committed_snapshot="",
                    error=result.error,
                    outcome="unknown",
                )
            return CommitResult(
                success=False,
                committed_snapshot=newest.name,
                error=result.error,
                outcome="failure",
            )

        # Deep verify: run qemu-img check on the disk's base image after commit
        if deep_verify:
            fail = deep_verify_base_image(self._shell, base_image, timeout=timeout)
            if fail is not None:
                return fail

        return CommitResult(
            success=True,
            committed_snapshot=newest.name,
            error=None,
            outcome="success",
        )
