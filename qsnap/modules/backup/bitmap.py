"""BitmapBackupProvider — NBD pull-model incremental backup via libvirt.

Implements ``IBackupProvider``.  Does NOT inherit from Core (design D1).
Dependency: ``IShell`` only.

Uses ``virsh backup-begin`` with a pull-model NBD Unix socket to export
dirty blocks, then ``qemu-img convert -n nbd:unix:<socket>`` to pull
them into a standalone qcow2 file on the target — no backing chain.

**NBD backup lifecycle:**

1. Remove any stale socket at ``/tmp/qsnap-backup-{pid}.sock``.
2. Create backup XML with NBD Unix socket and write to temp file.
3. If a prior qsnap checkpoint exists, pass ``--incremental <checkpoint>``
   to ``virsh backup-begin`` for dirty-block-only export.
4. ``virsh backup-begin --domain VM <backupxml> [--incremental <cp>]``
5. ``qemu-img convert -n nbd:unix:<socket> <target_file>``
6. Verify the target file (if ``target.verify != "off"``).
7. Delete prior checkpoint (if any) and create a new one for the next
   incremental run.
8. On failure, preserve all checkpoints for retry.
9. Socket is always cleaned up in a ``finally`` block.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import BackupResult, ShellResult, SnapshotInfo
from qsnap.utils.nbd import get_first_disk_target, nbd_full_export
from qsnap.utils.parsing import parse_timestamp
from qsnap.utils.verification import verify_backup

logger = logging.getLogger(__name__)


class BitmapBackupProvider(IBackupProvider):
    """Backup provider using NBD pull-model via ``virsh backup-begin``."""

    def __init__(
        self,
        shell: IShell,
        state: IStateManager | None = None,
    ) -> None:
        self._shell = shell
        self._state = state

    # ── IBackupProvider implementation ────────────────────────────────

    def transfer_missing(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
        rate_limit: str = "no",
        *,
        full_verify_before_rebase: str = "metadata",
    ) -> list[BackupResult]:
        """Transfer missing snapshots via NBD pull-model.

        See module docstring for the NBD backup lifecycle.

        ``rate_limit`` is accepted for interface compatibility but ignored
        — NBD-based transfers cannot be throttled via ``rsync --bwlimit``.

        ``full_verify_before_rebase`` is accepted for interface
        compatibility but ignored — the bitmap path does not use
        ``qemu-img rebase``.
        """
        existing = self.list(target)
        existing_names = {s.name for s in existing}

        target_hash = self._target_hash(str(target.path))
        prior_checkpoints = self._list_checkpoints_for_target(vm_config.name, target_hash)

        results: list[BackupResult] = []

        for snapshot in snapshots:
            if snapshot.name in existing_names:
                continue

            target_file = target.path / f"{snapshot.name}.qcow2"
            socket_path = f"/tmp/qsnap-backup-{os.getpid()}.sock"
            checkpoint_name = f"qsnap-{target_hash}-{snapshot.name}"

            # Determine prior checkpoint for incremental export.
            prior = prior_checkpoints[-1] if prior_checkpoints else None

            # Step 1: Remove stale socket.
            self._shell.run(["rm", "-f", socket_path], timeout=10)

            # Step 2: Build and write backup XML.
            backup_xml_path = self._write_backup_xml(socket_path)

            try:
                # Step 3: Start NBD export via virsh backup-begin.
                backup_cmd = [
                    "virsh",
                    "backup-begin",
                    "--domain",
                    vm_config.name,
                    str(backup_xml_path),
                ]
                if prior:
                    backup_cmd.extend(["--incremental", prior])

                backup_result = self._shell.run(backup_cmd, timeout=120)
                if not backup_result.success:
                    results.append(
                        BackupResult(
                            success=False,
                            snapshot_name=snapshot.name,
                            source_path=snapshot.path,
                            target_path=target_file,
                            bytes_transferred=0,
                            error=backup_result.error,
                        )
                    )
                    continue

                # Step 4: Pull dirty blocks via NBD.
                # libvirt's NBD server exports each disk under its
                # target device name (e.g., "vda").  We must specify
                # exportname in the NBD URI to connect to the correct
                # export.
                disk_target = get_first_disk_target(
                    self._shell,
                    vm_config.name,
                )
                nbd_uri = f"nbd:unix:{socket_path}"
                if disk_target:
                    nbd_uri = f"nbd:unix:{socket_path}:exportname={disk_target}"

                convert_cmd = [
                    "qemu-img",
                    "convert",
                    "-O",
                    "qcow2",
                    nbd_uri,
                    str(target_file),
                ]
                start_time = time.monotonic()
                convert_result = self._shell.run(convert_cmd, timeout=600)
                elapsed = time.monotonic() - start_time
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

                # Step 5: Verification (if enabled).
                verify_error = verify_backup(
                    self._shell,
                    str(snapshot.path),
                    str(target_file),
                    target.verify,
                )
                if verify_error is not None:
                    results.append(
                        BackupResult(
                            success=False,
                            snapshot_name=snapshot.name,
                            source_path=snapshot.path,
                            target_path=target_file,
                            bytes_transferred=0,
                            error=verify_error,
                        )
                    )
                    continue

                # Step 6: Delete prior checkpoint (if any).
                if prior:
                    del_cmd = [
                        "virsh",
                        "checkpoint-delete",
                        "--domain",
                        vm_config.name,
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

                # Step 7: Create new checkpoint for next incremental run.
                create_cmd = [
                    "virsh",
                    "checkpoint-create-as",
                    "--domain",
                    vm_config.name,
                    "--name",
                    checkpoint_name,
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
                        duration=elapsed,
                    )
                )

            finally:
                # Step 8: NBD job abort + socket cleanup (always, even on
                # failure).  Abort the virsh backup-begin job to release
                # the VM state change lock (mirrors nbd_full_export in
                # qsnap/utils/nbd.py).  domjobabort is idempotent — safe
                # to call when no job is running.  On failure, log a
                # WARNING but do NOT propagate the error — the socket
                # cleanup is the critical path and must still proceed.
                abort_cmd = [
                    "virsh",
                    "domjobabort",
                    "--domain",
                    vm_config.name,
                ]
                abort_result = self._shell.run(abort_cmd, timeout=30)
                if not abort_result.success:
                    logger.warning(
                        "virsh domjobabort failed for VM %s (job may have "
                        "already terminated): %s",
                        vm_config.name,
                        abort_result.error,
                    )
                # Socket cleanup.
                self._shell.run(["rm", "-f", socket_path], timeout=10)

        return results

    def create_full_backup(
        self,
        vm_name: str,
        source_snapshot: SnapshotInfo,
        target: TargetConfig,
        compress: bool = False,
        bucket_level: str = "monthly",
    ) -> BackupResult:
        """Create a standalone FULL backup via NBD full export (design D4).

        ``vm_name`` is the full, untruncated VM name (e.g.
        ``"3.Projects_opencode"``), passed from Core's
        ``vm_config.name``.  It is used directly for
        ``nbd_full_export()`` and ``full_name`` generation — the method
        SHALL NOT extract the VM name from the snapshot filename.

        Uses the shared :func:`nbd_full_export` helper (no
        ``--incremental`` flag) to produce a standalone qcow2 on the
        target.  No checkpoint is created or deleted — the checkpoint
        lifecycle remains exclusively in :meth:`transfer_missing` for
        incremental runs (design D3).

        Uses an atomic pattern: convert to a ``.tmp`` file, then rename
        on success.  On failure, the ``.tmp`` file is removed.

        When ``compress=True``, the ``-c`` flag is passed through to
        :func:`nbd_full_export` and on to ``qemu-img convert``,
        producing a compressed qcow2.
        """
        # Generate full backup name: vm.FULL.YYYYMMDD.qcow2
        date_str = source_snapshot.timestamp.strftime("%Y%m%d")
        full_name = f"{vm_name}.FULL.{date_str}"
        target_file = target.path / f"{full_name}.qcow2"
        tmp_file = target.path / f"{full_name}.qcow2.tmp"

        # Run NBD full-export to .tmp file (no --incremental, no
        # checkpoint — design D3, D4).  Compression is passed through
        # via the -c flag.
        nbd_result = nbd_full_export(self._shell, vm_name, str(tmp_file), compress=compress)
        if not nbd_result.success:
            # Remove .tmp on failure — no final file created.
            self._shell.run(["rm", "-f", str(tmp_file)], timeout=10)
            return BackupResult(
                success=False,
                snapshot_name=source_snapshot.name,
                source_path=source_snapshot.path,
                target_path=target_file,
                bytes_transferred=0,
                error=nbd_result.error,
            )

        # Atomic rename: mv .tmp to final name
        mv_cmd = ["mv", str(tmp_file), str(target_file)]
        mv_result = self._shell.run(mv_cmd, timeout=30)
        if not mv_result.success:
            return BackupResult(
                success=False,
                snapshot_name=source_snapshot.name,
                source_path=source_snapshot.path,
                target_path=target_file,
                bytes_transferred=0,
                error=mv_result.error,
            )

        # Get file size
        try:
            bytes_transferred = target_file.stat().st_size
        except OSError:
            bytes_transferred = 0

        # Record FULL in state (mirrors FileCopyBackupProvider parity —
        # design D4).  Called after successful FULL creation and atomic
        # rename, before returning success.
        if self._state is not None:
            self._state.record_full_backup(
                str(target.path),
                f"{full_name}.qcow2",
                source_snapshot.timestamp,
                bucket_level,
            )

        return BackupResult(
            success=True,
            snapshot_name=source_snapshot.name,
            source_path=source_snapshot.path,
            target_path=target_file,
            bytes_transferred=bytes_transferred,
            error=None,
        )

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
                "qemu-img",
                "info",
                "--output=json",
                str(file),
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
            "virsh",
            "checkpoint-list",
            "--name",
            "--domain",
            vm_name,
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

    def _list_checkpoints_for_target(self, vm_name: str, target_hash: str) -> list[str]:
        """Return qsnap checkpoints matching *target_hash*."""
        prefix = f"qsnap-{target_hash}-"
        return [cp for cp in self.list_checkpoints(vm_name) if cp.startswith(prefix)]

    @staticmethod
    def _target_hash(target_path: str) -> str:
        """Short hash of *target_path* for checkpoint naming."""
        return hashlib.md5(target_path.encode()).hexdigest()[:8]  # noqa: S324

    @staticmethod
    def _write_backup_xml(socket_path: str) -> Path:
        """Write a libvirt pull-model backup XML to a temp file.

        Returns the path to the temp file.
        """
        xml_content = (
            f"<domainbackup mode='pull'>\n"
            f"  <server transport='unix' socket='{socket_path}'/>\n"
            f"</domainbackup>\n"
        )
        fd, tmp_path = tempfile.mkstemp(prefix="qsnap-backup-", suffix=".xml")
        with os.fdopen(fd, "w") as f:
            f.write(xml_content)
        return Path(tmp_path)
