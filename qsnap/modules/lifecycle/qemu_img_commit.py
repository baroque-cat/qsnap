"""QemuImgCommitManager — backing chain lifecycle via qemu-img commit.

Implements ``ILifecycleManager``.  Does NOT inherit from Core (design D1).
Dependency: ``IShell`` only.

Offline executor: Core invokes it only when the VM is shut off and only
with snapshots that exclude the XML-referenced tip overlay.  Per snapshot
(oldest first): ``qemu-img commit -b <base> <snap>`` → child discovery →
``qemu-img rebase -u -F qcow2 -b <base> <child>`` → ``rm -f <snap>``.
Does NOT rely on ``qemu-img commit -d`` (a no-op on QEMU 11.0.2).
"""

from __future__ import annotations

import json
import logging
import os

from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.shell import IShell
from qsnap.models.config import VMConfig
from qsnap.models.results import CommitResult, SnapshotInfo
from qsnap.utils.mac import detect_mac_denial

logger = logging.getLogger(__name__)


class QemuImgCommitManager(ILifecycleManager):
    """Manages backing chain lifecycle via ``qemu-img commit``.

    Snapshots are merged one at a time (design D4): safer to roll back
    on error, clearer logging.  On the first failure, processing
    short-circuits — the failing file is NOT deleted and remaining
    snapshots are NOT merged.  The chain is left consistent, so the
    next run can safely retry.
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
        """Merge snapshots into the base image via ``qemu-img commit``.

        1. If the list is empty → no-op success.
        2. For each snapshot (oldest first): ``qemu-img commit
           -b <base> <snap>``, then pivot the child overlay onto the
           base via ``qemu-img rebase -u``, then delete the committed
           file explicitly.  Short-circuit on first failure (design D4).
        3. When *deep_verify* is True, run ``qemu-img check`` on the
           base image after a successful commit.
        """
        # Step 1: Empty list → no-op
        if not snapshots_to_merge:
            return CommitResult(success=True, committed_snapshot="", error=None)

        # Step 2: Merge each snapshot (oldest first)
        last_merged = ""
        for snapshot in snapshots_to_merge:
            # 2a: Merge the snapshot into the base image.  Never commits
            # INTO a kept overlay — the target is always the base.
            cmd = [
                "qemu-img",
                "commit",
                "-b",
                str(vm_config.base_image),
                str(snapshot.path),
            ]
            result = self._shell.run(cmd, timeout=3600)
            if not result.success:
                mac_result = self._mac_failure(result.stderr, result.error)
                if mac_result is not None:
                    return mac_result
                return CommitResult(
                    success=False,
                    committed_snapshot=snapshot.name,
                    error=result.error,
                )

            # 2b: Child discovery — find the overlay whose backing file
            # is the just-committed snapshot (linear chain ⇒ at most one).
            child_result = self._find_child(vm_config, snapshot)
            if isinstance(child_result, CommitResult):
                return child_result
            child = child_result

            # 2c: Pivot the child onto the base image (metadata-only).
            # Safe because the snapshot's data is now contained in the
            # base image, so the child's view is unchanged.
            if child is not None:
                rebase_cmd = [
                    "qemu-img",
                    "rebase",
                    "-u",
                    "-F",
                    "qcow2",
                    "-b",
                    str(vm_config.base_image),
                    child,
                ]
                rebase_result = self._shell.run(rebase_cmd, timeout=300)
                if not rebase_result.success:
                    mac_result = self._mac_failure(rebase_result.stderr, rebase_result.error)
                    if mac_result is not None:
                        return mac_result
                    # Rebase failed — the child still points at the
                    # committed file; do NOT delete it.
                    return CommitResult(
                        success=False,
                        committed_snapshot=snapshot.name,
                        error=rebase_result.error,
                    )

            # 2d: Delete the committed file — only after the pivot
            # succeeded, or when no child exists.
            rm_result = self._shell.run(["rm", "-f", str(snapshot.path)], timeout=30)
            if not rm_result.success:
                mac_result = self._mac_failure(rm_result.stderr, rm_result.error)
                if mac_result is not None:
                    return mac_result
                return CommitResult(
                    success=False,
                    committed_snapshot=snapshot.name,
                    error=rm_result.error,
                )
            last_merged = snapshot.name

        # Deep verify: run qemu-img check on base image after commit
        if deep_verify:
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
                errors = data.get("errors", 0)
                if errors > 0:
                    return CommitResult(
                        success=False,
                        committed_snapshot="",
                        error=f"deep verify: {errors} errors in base image",
                    )
                leaks = data.get("leaks", 0)
                if leaks > 0:
                    return CommitResult(
                        success=False,
                        committed_snapshot="",
                        error=f"deep verify: {leaks} leaks in base image",
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

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _mac_failure(stderr: str, error: str | None) -> CommitResult | None:
        """Return a MAC-denial CommitResult, or None if not MAC-related."""
        mac_reason = detect_mac_denial(stderr)
        if mac_reason is None:
            return None
        return CommitResult(
            success=False,
            committed_snapshot="",
            error=f"blocked by {mac_reason}",
        )

    def _find_child(
        self,
        vm_config: VMConfig,
        snapshot: SnapshotInfo,
    ) -> str | None | CommitResult:
        """Find the child overlay of *snapshot* in ``snapshot_dir``.

        Scans ``vm_config.snapshot_dir`` for ``*.qcow2`` files and matches
        each file's resolved ``backing-filename`` against the snapshot
        path.  Returns the child path, ``None`` when no child exists, or
        a failure ``CommitResult`` when discovery itself failed (the
        caller must short-circuit — deleting without knowing whether a
        child exists would risk dangling backing references).
        """
        find_result = self._shell.run(
            [
                "find",
                str(vm_config.snapshot_dir),
                "-maxdepth",
                "1",
                "-type",
                "f",
                "-name",
                "*.qcow2",
            ],
            timeout=30,
        )
        if not find_result.success:
            return CommitResult(
                success=False,
                committed_snapshot=snapshot.name,
                error=f"child discovery failed: {find_result.error}",
            )

        snapshot_real = os.path.realpath(str(snapshot.path))
        for candidate in find_result.stdout.splitlines():
            candidate = candidate.strip()
            if not candidate:
                continue
            info_result = self._shell.run(
                ["qemu-img", "info", "--output=json", candidate],
                timeout=30,
            )
            if not info_result.success:
                return CommitResult(
                    success=False,
                    committed_snapshot=snapshot.name,
                    error=f"child discovery failed for {candidate}: {info_result.error}",
                )
            try:
                data = json.loads(info_result.stdout)
            except json.JSONDecodeError:
                return CommitResult(
                    success=False,
                    committed_snapshot=snapshot.name,
                    error=f"child discovery failed for {candidate}: "
                    "could not parse qemu-img info output",
                )
            backing = data.get("full-backing-filename") or data.get("backing-filename")
            if not isinstance(backing, str) or not backing:
                continue
            if not os.path.isabs(backing):
                backing = os.path.join(os.path.dirname(candidate), backing)
            if os.path.realpath(backing) == snapshot_real:
                return candidate
        return None
