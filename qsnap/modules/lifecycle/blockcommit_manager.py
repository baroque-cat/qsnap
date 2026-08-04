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

    Snapshots are merged one at a time (design D4): safer to roll back
    on error, clearer logging.  On the first failure, processing
    short-circuits — remaining snapshots are NOT merged.
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
    ) -> CommitResult:
        """Merge snapshots of one disk into that disk's base image.

        Multi-disk (refactor): *disk* is the libvirt target device name and
        *base_image* is this disk's base qcow2 path.  The ``--path`` and
        ``--base`` arguments are taken from these parameters (not derived
        from a single VM-level base image).

        1. If the list is empty → no-op success.
        2. For each snapshot (oldest first): ``virsh blockcommit --delete
           --verbose --wait``.  Short-circuit on first failure (design D4).
        3. When *deep_verify* is True, run ``qemu-img check`` on
           *base_image* after a successful commit.  If corruptions are
           detected, return ``CommitResult(success=False)``.
        """
        # Step 1: Empty list → no-op
        if not snapshots_to_merge:
            return CommitResult(success=True, committed_snapshot="", error=None)

        # Step 2: Merge each snapshot (oldest first)
        last_merged = ""
        for snapshot in snapshots_to_merge:
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
                str(snapshot.path),
                "--delete",
                "--verbose",
                "--wait",
            ]
            result = self._shell.run(cmd, timeout=3600, check=True)
            if not result.success:
                # Check for MAC denial (AppArmor/SELinux)
                mac_reason = detect_mac_denial(result.stderr)
                if mac_reason is not None:
                    return CommitResult(
                        success=False,
                        committed_snapshot="",
                        error=f"blocked by {mac_reason}",
                    )
                # Short-circuit on first failure (design D4)
                return CommitResult(
                    success=False,
                    committed_snapshot=snapshot.name,
                    error=result.error,
                )
            last_merged = snapshot.name

        # Deep verify: run qemu-img check on the disk's base image after commit
        if deep_verify:
            fail = deep_verify_base_image(self._shell, base_image)
            if fail is not None:
                return fail

        return CommitResult(
            success=True,
            committed_snapshot=last_merged,
            error=None,
        )
