"""BitmapBackupProvider — NBD pull-model incremental backup via libvirt.

Implements ``IBackupProvider``.  Does NOT inherit from Core (design D1).
Dependency: ``IShell`` only.

Uses ``virsh backup-begin`` with a pull-model NBD Unix socket to export
dirty blocks, then ``qemu-img convert -n nbd:unix:<socket>`` to pull
them into a standalone qcow2 file on the target — no backing chain.

**NBD backup lifecycle:**

1. Remove any stale socket at ``/tmp/qsnap-backup-{pid}.sock``.
2. Create backup XML with NBD Unix socket and write to temp file.  When
   a prior qsnap checkpoint exists, an ``<incremental>`` element naming
   that checkpoint is embedded in the XML (design D1 — the
   ``--incremental`` CLI flag does not exist in any version of virsh
   ``backup-begin``).
3. ``virsh backup-begin --domain VM <backupxml>`` starts the NBD export
   (full when no ``<incremental>`` element, incremental otherwise).
4. ``qemu-img convert -n nbd:unix:<socket> <target_file>``
5. Verify the target file (if ``target.verify != "off"``).
6. Delete prior checkpoint (if any) and create a new one for the next
   incremental run.
7. On failure, preserve all checkpoints for retry.
8. Socket is always cleaned up in a ``finally`` block.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import BackupResult, ShellResult, SnapshotInfo
from qsnap.utils.nbd import get_first_disk_target, nbd_full_export, write_backup_xml
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

    def _cleanup_partial_file(self, target_file: Path) -> None:
        """Best-effort deletion of a partially-transferred file.

        Called after a transfer or verification failure to remove the
        partial target file so retention cleanup does not find it and
        log a misleading ``[delete] removed backup`` message (design D2).
        Failures are logged but never propagated — the caller is already
        in a failure path.
        """
        try:
            result = self._shell.run(["rm", "-f", str(target_file)], timeout=10)
            if not result.success:
                logger.warning(
                    "Failed to delete partial backup file %s: %s",
                    target_file,
                    result.error,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning(
                "Failed to delete partial backup file %s: %s",
                target_file,
                exc,
            )

    def transfer_missing(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
        rate_limit: str = "no",
        *,
        full_verify_before_rebase: str = "metadata",
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
    ) -> list[BackupResult]:
        """Transfer missing snapshots via NBD pull-model.

        See module docstring for the NBD backup lifecycle.

        ``rate_limit`` is accepted for interface compatibility but ignored
        — NBD-based transfers cannot be throttled via ``rsync --bwlimit``.

        ``full_verify_before_rebase`` is accepted for interface
        compatibility but ignored — the bitmap path does not use
        ``qemu-img rebase``.

        ``compression_type`` selects the compression algorithm for the
        NBD convert command (``"zstd"`` default, ``"zlib"`` alternative).
        Only effective when ``target.compress`` is ``True``.

        ``stall_timeout`` is the stall-detection timeout in seconds for
        the convert command.  When ``0``, falls back to fixed-timeout
        :meth:`IShell.run`.
        """
        existing = self.list(target)
        existing_names = {s.name for s in existing}

        target_hash = self.target_hash(str(target.path))
        prior_checkpoints = self._list_checkpoints_for_target(vm_config.name, target_hash)

        results: list[BackupResult] = []

        for snapshot in snapshots:
            if snapshot.name in existing_names:
                continue

            target_file = target.path / f"{snapshot.name}.qcow2"
            socket_path = f"/tmp/qsnap-backup-{os.getpid()}.sock"

            # Determine prior checkpoint for incremental export.
            prior = prior_checkpoints[-1] if prior_checkpoints else None

            # Checkpoint-only creation when a FULL already exists in state
            # but no prior checkpoint is recorded (design D4).  The bucket
            # strategy's ``create_full_backup()`` already produced a FULL
            # with all data at this point in time; the checkpoint serves
            # only as the baseline for the next incremental run.  Creating
            # it without a data transfer avoids a redundant full NBD export.
            #
            # Guards:
            # - ``prior is None``: only when no prior checkpoint exists.
            # - ``self._state is not None``: fall through to full NBD
            #   export when state is unavailable (design D4.3).
            # - Snapshots already on target are skipped by the
            #   ``existing_names`` check above (design D4.4).
            if prior is None and self._state is not None:
                fulls = self._state.get_full_backups(str(target.path))
                if fulls:
                    self._create_checkpoint_only(vm_config.name, target_hash, snapshot.name)
                    logger.info(
                        "Created checkpoint qsnap-%s-%s without transfer (FULL exists in state)",
                        target_hash,
                        snapshot.name,
                    )
                    continue

            # Step 1: Remove stale socket.
            self._shell.run(["rm", "-f", socket_path], timeout=10)

            # Step 2: Build and write backup XML.  The incremental
            # checkpoint is passed via the <incremental> XML element,
            # NOT via a --incremental CLI flag (design D1 — the flag
            # does not exist in any version of virsh backup-begin).
            backup_xml_path = write_backup_xml(socket_path, incremental=prior)

            try:
                # Step 3: Start NBD export via virsh backup-begin.  The
                # incremental checkpoint is already embedded in the
                # backup XML via the <incremental> element (design D1).
                # No --incremental CLI flag is passed — it does not
                # exist in any version of virsh backup-begin.
                backup_cmd = [
                    "virsh",
                    "backup-begin",
                    "--domain",
                    vm_config.name,
                    str(backup_xml_path),
                ]

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
                ]
                # Compression: -c compresses the output qcow2.  When
                # compression_type is "zstd", adds -o compression_type=zstd
                # for 11x faster compression than the default zlib (D7).
                if target.compress:
                    convert_cmd.append("-c")
                    if compression_type == "zstd":
                        convert_cmd.extend(["-o", "compression_type=zstd"])
                convert_cmd.extend([nbd_uri, str(target_file)])
                start_time = time.monotonic()
                if stall_timeout > 0:
                    convert_result = self._shell.run_with_stall_detection(
                        convert_cmd,
                        output_file=target_file,
                        stall_timeout=stall_timeout,
                    )
                else:
                    convert_result = self._shell.run(convert_cmd, timeout=600)
                elapsed = time.monotonic() - start_time
                if not convert_result.success:
                    # Preserve checkpoint for retry.  Delete the partial
                    # target file so retention cleanup does not find it
                    # and log a misleading ``[delete] removed backup``
                    # message (design D2).
                    self._cleanup_partial_file(target_file)
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
                    # Delete the partially-transferred file so retention
                    # cleanup does not find it and log a misleading
                    # ``[delete] removed backup`` message (design D2).
                    self._cleanup_partial_file(target_file)
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
                self._create_checkpoint_only(vm_config.name, target_hash, snapshot.name)

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
                        "virsh domjobabort failed for VM %s (job may have already terminated): %s",
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
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
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
        producing a compressed qcow2.  When ``compression_type`` is
        ``"zstd"``, ``-o compression_type=zstd`` is added for faster
        compression.

        This method SHALL NOT call ``self._state.record_full_backup()``
        — state recording is Core's responsibility after post-create
        verification passes (design D4 — matches
        ``FileCopyBackupProvider`` behavior).
        """
        # Generate full backup name: vm.FULL.YYYYMMDD.qcow2
        date_str = source_snapshot.timestamp.strftime("%Y%m%d")
        full_name = f"{vm_name}.FULL.{date_str}"
        target_file = target.path / f"{full_name}.qcow2"
        tmp_file = target.path / f"{full_name}.qcow2.tmp"

        # Run NBD full-export to .tmp file (no --incremental, no
        # checkpoint — design D3, D4).  Compression is passed through
        # via the -c flag.
        nbd_result = nbd_full_export(
            self._shell,
            vm_name,
            str(tmp_file),
            compress=compress,
            compression_type=compression_type,
            stall_timeout=stall_timeout,
        )
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

        # State recording is Core's responsibility after post-create
        # verification passes (design D4 — matches FileCopyBackupProvider
        # behavior, which also does not self-record).  The provider SHALL
        # NOT call self._state.record_full_backup() here.

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
        ``qsnap-`` prefix.  On command failure (e.g., VM not defined,
        libvirt not running), logs a WARNING and returns an empty list —
        callers treat this as "no checkpoints" (non-fatal).
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
            logger.warning(
                "Failed to list checkpoints for VM %s: %s",
                vm_name,
                result.error,
            )
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

    def _create_checkpoint_only(self, vm_name: str, target_hash: str, snapshot_name: str) -> bool:
        """Create a libvirt checkpoint without a data transfer.

        Used in two places (DRY): (1) the checkpoint-only path when a
        FULL already exists in state (design D4), and (2) Step 7 after a
        successful incremental transfer.  The checkpoint serves as the
        baseline for the next incremental run.

        Returns ``True`` on success, ``False`` on failure (logged as
        WARNING; callers continue regardless — checkpoint creation is
        not fatal to the current transfer).
        """
        checkpoint_name = f"qsnap-{target_hash}-{snapshot_name}"
        create_cmd = [
            "virsh",
            "checkpoint-create-as",
            "--domain",
            vm_name,
            "--name",
            checkpoint_name,
        ]
        create_result = self._shell.run(create_cmd, timeout=120)
        if not create_result.success:
            logger.warning(
                "Failed to create checkpoint %s for VM %s: %s",
                checkpoint_name,
                vm_name,
                create_result.error,
            )
            return False
        return True

    @staticmethod
    def target_hash(target_path: str) -> str:
        """Short hash of *target_path* for checkpoint naming."""
        return hashlib.md5(target_path.encode()).hexdigest()[:8]  # noqa: S324
