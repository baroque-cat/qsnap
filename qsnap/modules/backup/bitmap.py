"""BitmapBackupProvider — dirty-block incremental backup via QEMU checkpoints.

Implements ``IBackupProvider``.  Does NOT inherit from Core (design D1).
Dependency: ``IShell`` only.

Uses ``virsh checkpoint-create-as`` and ``qemu-img convert --bitmap`` to
transfer only dirty blocks between checkpoints (design D3).  Produces
standalone qcow2 files on the target — no backing chain.

**Checkpoint lifecycle:**

1. If a prior qsnap checkpoint exists for this VM+target, extract dirty
   blocks via ``qemu-img convert --bitmap <prior_checkpoint>``.
2. If no prior checkpoint (first backup), perform a full ``qemu-img
   convert`` (no ``--bitmap`` flag).
3. After successful transfer, delete the prior checkpoint and create a
   new one for the next incremental run.
4. On failure, preserve all checkpoints for retry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.shell import IShell
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import BackupResult, ShellResult, SnapshotInfo
from qsnap.utils.parsing import parse_timestamp

logger = logging.getLogger(__name__)

_MIN_QEMU_MAJOR = 5
_MIN_QEMU_MINOR = 1


class BitmapBackupProvider(IBackupProvider):
    """Backup provider using dirty-bitmap extraction via checkpoints."""

    def __init__(self, shell: IShell) -> None:
        self._shell = shell
        self._check_qemu_version()

    # ── construction helpers ──────────────────────────────────────────

    def _check_qemu_version(self) -> None:
        """Verify QEMU >= 5.1 via ``qemu-img --version``.

        Raises:
            RuntimeError: If QEMU version is < 5.1 or cannot be parsed.
        """
        result = self._shell.run(["qemu-img", "--version"], timeout=30)
        if not result.success:
            raise RuntimeError(
                f"Cannot determine qemu-img version: {result.error}"
            )

        # Output looks like: "qemu-img version 8.2.0 (qemu-8.2.0)"
        match = re.search(r"version\s+(\d+)\.(\d+)", result.stdout)
        if not match:
            raise RuntimeError(
                f"Cannot parse qemu-img version from: {result.stdout!r}"
            )

        major = int(match.group(1))
        minor = int(match.group(2))

        if major < _MIN_QEMU_MAJOR or (
            major == _MIN_QEMU_MAJOR and minor < _MIN_QEMU_MINOR
        ):
            raise RuntimeError(
                f"qemu-img version {major}.{minor} is too old; "
                f"bitmap backup requires QEMU >= {_MIN_QEMU_MAJOR}.{_MIN_QEMU_MINOR}"
            )

    # ── IBackupProvider implementation ────────────────────────────────

    def transfer_missing(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
    ) -> list[BackupResult]:
        """Transfer missing snapshots via dirty-bitmap extraction.

        See module docstring for the checkpoint lifecycle.
        """
        existing = self.list(target)
        existing_names = {s.name for s in existing}

        target_hash = self._target_hash(str(target.path))
        prior_checkpoints = self._list_checkpoints_for_target(
            vm_config.name, target_hash
        )

        results: list[BackupResult] = []

        for snapshot in snapshots:
            if snapshot.name in existing_names:
                continue

            target_file = target.path / f"{snapshot.name}.qcow2"
            checkpoint_name = f"qsnap-{target_hash}-{snapshot.name}"

            # Determine prior checkpoint for incremental extraction.
            prior = prior_checkpoints[-1] if prior_checkpoints else None

            # Step 1: Extract dirty blocks (or full copy on first run).
            if prior:
                convert_cmd = [
                    "qemu-img", "convert",
                    "-f", "qcow2", "-O", "qcow2",
                    "--bitmap", prior,
                    str(snapshot.path),
                    str(target_file),
                ]
            else:
                convert_cmd = [
                    "qemu-img", "convert",
                    "-f", "qcow2", "-O", "qcow2",
                    str(snapshot.path),
                    str(target_file),
                ]

            convert_result = self._shell.run(convert_cmd, timeout=600)
            if not convert_result.success:
                # Preserve checkpoint for retry.
                results.append(
                    BackupResult(
                        success=False,
                        snapshot_name=snapshot.name,
                        source_path=snapshot.path,
                        target_path=target_file,
                        bytes_transferred=0,
                        error=convert_result.error,
                    )
                )
                continue

            # Step 2: Delete prior checkpoint (if any).
            if prior:
                del_cmd = [
                    "virsh", "checkpoint-delete",
                    "--domain", vm_config.name,
                    prior,
                    "--metadata",
                ]
                del_result = self._shell.run(del_cmd, timeout=30)
                if not del_result.success:
                    logger.warning(
                        "Failed to delete prior checkpoint %s for VM %s: %s",
                        prior,
                        vm_config.name,
                        del_result.error,
                    )

            # Step 3: Create new checkpoint for next incremental run.
            create_cmd = [
                "virsh", "checkpoint-create-as",
                "--domain", vm_config.name,
                "--name", checkpoint_name,
            ]
            create_result = self._shell.run(create_cmd, timeout=120)
            if not create_result.success:
                logger.warning(
                    "Failed to create checkpoint %s for VM %s: %s",
                    checkpoint_name,
                    vm_config.name,
                    create_result.error,
                )

            # Get file size for bytes_transferred.
            try:
                bytes_transferred = target_file.stat().st_size
            except OSError:
                bytes_transferred = 0

            results.append(
                BackupResult(
                    success=True,
                    snapshot_name=snapshot.name,
                    source_path=snapshot.path,
                    target_path=target_file,
                    bytes_transferred=bytes_transferred,
                    error=None,
                )
            )

        return results

    def list(self, target: TargetConfig) -> list[SnapshotInfo]:
        """List existing backups at *target*.

        Scans ``target.path`` for ``*.qcow2`` files and obtains metadata
        via ``qemu-img info --output=json``.  Returns an empty list if
        the target directory does not exist.
        """
        if not target.path.exists():
            return []

        snapshots: list[SnapshotInfo] = []
        for file in target.path.glob("*.qcow2"):
            info_cmd = [
                "qemu-img", "info", "--output=json", str(file),
            ]
            info_result = self._shell.run(info_cmd, timeout=60)
            if not info_result.success:
                continue

            try:
                info = json.loads(info_result.stdout)
            except json.JSONDecodeError:
                continue

            name = file.stem
            actual_size = int(info.get("actual-size", 0))
            timestamp = parse_timestamp(name, file)

            snapshots.append(
                SnapshotInfo(
                    name=name,
                    path=file,
                    timestamp=timestamp,
                    allocation=actual_size,
                )
            )

        snapshots.sort(key=lambda s: s.timestamp)
        return snapshots

    def delete(self, backup: SnapshotInfo) -> ShellResult:
        """Delete a backup file via ``rm -f``."""
        cmd = ["rm", "-f", str(backup.path)]
        return self._shell.run(cmd, timeout=30)

    # ── bitmap-specific helpers ───────────────────────────────────────

    def list_checkpoints(self, vm_name: str) -> list[str]:
        """Return qsnap-owned checkpoint names for *vm_name*.

        Calls ``virsh checkpoint-list --name <vm>`` and filters by the
        ``qsnap-`` prefix.
        """
        cmd = [
            "virsh", "checkpoint-list",
            "--name",
            "--domain", vm_name,
        ]
        result = self._shell.run(cmd, timeout=30)
        if not result.success:
            return []

        checkpoints: list[str] = []
        for line in result.stdout.strip().splitlines():
            name = line.strip()
            if name.startswith("qsnap-"):
                checkpoints.append(name)
        return checkpoints

    def _list_checkpoints_for_target(
        self, vm_name: str, target_hash: str
    ) -> list[str]:
        """Return qsnap checkpoints matching *target_hash*."""
        prefix = f"qsnap-{target_hash}-"
        return [
            cp for cp in self.list_checkpoints(vm_name)
            if cp.startswith(prefix)
        ]

    @staticmethod
    def _target_hash(target_path: str) -> str:
        """Short hash of *target_path* for checkpoint naming."""
        return hashlib.md5(target_path.encode()).hexdigest()[:8]  # noqa: S324
