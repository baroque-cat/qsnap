"""ExternalSnapshotProvider — external disk-only snapshot management via virsh.

Implements ``ISnapshotProvider``.  Does NOT inherit from Core (design D1):
the only dependency is ``IShell``.  All virsh/qemu-img calls go through
the injected shell abstraction.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from qsnap.interfaces.shell import IShell
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import VMConfig
from qsnap.models.results import ShellResult, SnapshotInfo, SnapshotResult
from qsnap.modules.backup.verification import _file_sha256
from qsnap.utils.parsing import parse_domblklist_path, parse_timestamp

logger = logging.getLogger(__name__)


class ExternalSnapshotProvider(ISnapshotProvider):
    """External disk-only snapshot provider using ``virsh snapshot-create-as``."""

    def __init__(self, shell: IShell) -> None:
        self._shell = shell

    # ── ISnapshotProvider implementation ──────────────────────────────

    def create(
        self,
        vm_config: VMConfig,
        snapshot_name: str,
        disk: str,
        snapshot_path: Path,
        quiesce: bool = False,
    ) -> SnapshotResult:
        """Create an external disk-only snapshot.

        1. ``virsh snapshot-create-as --disk-only --atomic --no-metadata``
           (with ``--quiesce`` when *quiesce* is True, timeout 180s)
        2. ``chmod g+rw,o+r`` on the new snapshot file
        3. ``qemu-img info --output=json`` to read ``actual-size``
        """
        # Step 1: virsh snapshot-create-as
        create_cmd = [
            "virsh",
            "snapshot-create-as",
            "--domain",
            vm_config.name,
            "--name",
            snapshot_name,
            "--diskspec",
            f"{disk},file={snapshot_path},snapshot=external",
            "--disk-only",
            "--atomic",
            "--no-metadata",
        ]
        if quiesce:
            create_cmd.append("--quiesce")
        timeout = 180 if quiesce else 120
        create_result = self._shell.run(create_cmd, timeout=timeout)
        if not create_result.success:
            return SnapshotResult(
                success=False,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=0,
                error=create_result.error,
            )

        # Step 2: chmod g+rw,o+r
        chmod_cmd = ["chmod", "g+rw,o+r", str(snapshot_path)]
        chmod_result = self._shell.run(chmod_cmd, timeout=30)
        if not chmod_result.success:
            return SnapshotResult(
                success=False,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=0,
                error=chmod_result.error,
            )

        # Step 3: qemu-img info to get actual-size
        # --force-share: the snapshot file may be the active layer of a
        # running VM, which has an exclusive write lock.  --force-share
        # requests a shared lock for this metadata-only read (design D5).
        info_cmd = [
            "qemu-img",
            "info",
            "--force-share",
            "--output=json",
            str(snapshot_path),
        ]
        info_result = self._shell.run(info_cmd, timeout=60)
        if not info_result.success:
            return SnapshotResult(
                success=False,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=0,
                error=info_result.error,
            )

        try:
            info = json.loads(info_result.stdout)
            actual_size = int(info["actual-size"])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            return SnapshotResult(
                success=False,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=0,
                error=f"Failed to parse qemu-img info output: {exc}",
            )

        # Step 4: Compute SHA-256 hash of the snapshot file
        content_hash: str | None = None
        try:
            content_hash = _file_sha256(snapshot_path)
        except OSError as exc:
            logger.warning(
                "Failed to compute SHA-256 for %s: %s", snapshot_path, exc
            )

        return SnapshotResult(
            success=True,
            name=snapshot_name,
            path=snapshot_path,
            new_allocation=actual_size,
            error=None,
            content_hash=content_hash,
        )

    def list(self, vm_config: VMConfig) -> list[SnapshotInfo]:
        """List existing snapshots via the backing chain of the active disk.

        1. ``virsh domblklist`` to find the active disk path.
        2. ``qemu-img info --force-share --backing-chain --output=json``.
        3. Skip the first element (base image); build ``SnapshotInfo`` for
           each subsequent chain element.
        """
        # Step 1: Get active disk path via domblklist
        domblklist_cmd = ["virsh", "domblklist", "--domain", vm_config.name]
        domblklist_result = self._shell.run(domblklist_cmd, timeout=30)
        if not domblklist_result.success:
            return []

        try:
            active_disk = parse_domblklist_path(domblklist_result.stdout)
        except ValueError:
            return []

        # Step 2: qemu-img info --backing-chain
        chain_cmd = [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            active_disk,
        ]
        chain_result = self._shell.run(chain_cmd, timeout=60)
        if not chain_result.success:
            return []

        try:
            chain = json.loads(chain_result.stdout)
        except json.JSONDecodeError:
            return []

        if not isinstance(chain, list) or len(chain) <= 1:
            return []

        # Step 3: Build SnapshotInfo for each element after the base
        snapshots: list[SnapshotInfo] = []
        for element in chain[1:]:
            filename = element.get("filename", "")
            name = Path(filename).stem
            actual_size = int(element.get("actual-size", 0))
            timestamp = parse_timestamp(name, Path(filename))
            snapshots.append(
                SnapshotInfo(
                    name=name,
                    path=Path(filename),
                    timestamp=timestamp,
                    allocation=actual_size,
                )
            )

        snapshots.sort(key=lambda s: s.timestamp)
        return snapshots

    def delete(self, snapshot: SnapshotInfo) -> ShellResult:
        """Delete a snapshot file via ``rm -f``."""
        cmd = ["rm", "-f", str(snapshot.path)]
        return self._shell.run(cmd, timeout=30)
