"""BlockCommitManager — backing chain lifecycle management via virsh blockcommit.

Implements ``ILifecycleManager``.  Does NOT inherit from Core (design D1).
Dependency: ``IShell`` only.
"""

from __future__ import annotations

import logging

from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.shell import IShell
from qsnap.models.config import VMConfig
from qsnap.models.results import CommitResult, SnapshotInfo
from qsnap.utils.mac import detect_mac_denial
from qsnap.utils.parsing import parse_domblklist_target

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
        deep_verify: bool = False,
    ) -> CommitResult:
        """Merge snapshots into the base image via ``virsh blockcommit``.

        1. If the list is empty → no-op success.
        2. Resolve the disk target via ``virsh domblklist``.
        3. For each snapshot (oldest first): ``virsh blockcommit --delete
           --verbose --wait``.  Short-circuit on first failure (design D4).
        4. When *deep_verify* is True, run ``qemu-img check`` on the base
           image after a successful commit.  If corruptions are detected,
           return ``CommitResult(success=False)``.
        """
        # Step 1: Empty list → no-op
        if not snapshots_to_merge:
            return CommitResult(success=True, committed_snapshot="", error=None)

        # Step 2: Get disk target via domblklist
        domblklist_cmd = ["virsh", "domblklist", "--domain", vm_config.name]
        domblklist_result = self._shell.run(domblklist_cmd, timeout=30)
        if not domblklist_result.success:
            return CommitResult(
                success=False,
                committed_snapshot="",
                error=domblklist_result.error,
            )

        try:
            target = parse_domblklist_target(domblklist_result.stdout)
        except ValueError:
            return CommitResult(
                success=False,
                committed_snapshot="",
                error="Failed to parse disk target from domblklist output",
            )

        # Step 3: Merge each snapshot (oldest first)
        last_merged = ""
        for snapshot in snapshots_to_merge:
            cmd = [
                "virsh",
                "blockcommit",
                "--domain",
                vm_config.name,
                "--path",
                target,
                "--base",
                str(vm_config.base_image),
                "--top",
                str(snapshot.path),
                "--delete",
                "--verbose",
                "--wait",
            ]
            result = self._shell.run(cmd, timeout=3600)
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

        # Deep verify: run qemu-img check on base image after commit
        if deep_verify:
            import json

            chk = self._shell.run(
                ["qemu-img", "check", "--output=json", str(vm_config.base_image)],
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
                corruptions = data.get("corruptions", 0)
                if corruptions > 0:
                    return CommitResult(
                        success=False,
                        committed_snapshot="",
                        error=f"deep verify: {corruptions} corruptions in base image",
                    )
            except json.JSONDecodeError:
                return CommitResult(
                    success=False,
                    committed_snapshot="",
                    error="deep verify: failed to parse qemu-img check output",
                )

        return CommitResult(
            success=True,
            committed_snapshot=last_merged,
            error=None,
        )
