"""QemuImgCommitManager — backing chain lifecycle via qemu-img commit.

Implements ``ILifecycleManager``.  Does NOT inherit from Core (design D1).
Dependency: ``IShell`` only.

Uses ``qemu-img commit -b <base> -d <top>`` instead of ``virsh blockcommit``.
This is useful when libvirt is unavailable or when operating on offline images.
"""

from __future__ import annotations

import logging

from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.shell import IShell
from qsnap.models.config import VMConfig
from qsnap.models.results import CommitResult, SnapshotInfo

logger = logging.getLogger(__name__)


class QemuImgCommitManager(ILifecycleManager):
    """Manages backing chain lifecycle via ``qemu-img commit``.

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
    ) -> CommitResult:
        """Merge snapshots into the base image via ``qemu-img commit``.

        1. If the list is empty → no-op success.
        2. For each snapshot (oldest first): ``qemu-img commit -b <base>
           -d <top>``.  Short-circuit on first failure (design D4).
        """
        # Step 1: Empty list → no-op
        if not snapshots_to_merge:
            return CommitResult(success=True, committed_snapshot="", error=None)

        # Step 2: Merge each snapshot (oldest first)
        last_merged = ""
        for snapshot in snapshots_to_merge:
            cmd = [
                "qemu-img",
                "commit",
                "-b",
                str(vm_config.base_image),
                "-d",
                str(snapshot.path),
            ]
            result = self._shell.run(cmd, timeout=3600)
            if not result.success:
                # Short-circuit on first failure (design D4)
                return CommitResult(
                    success=False,
                    committed_snapshot=snapshot.name,
                    error=result.error,
                )
            last_merged = snapshot.name

        return CommitResult(
            success=True,
            committed_snapshot=last_merged,
            error=None,
        )
