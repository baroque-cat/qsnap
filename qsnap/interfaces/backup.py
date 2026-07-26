"""IBackupProvider — abstract backup transfer interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import BackupResult, ShellResult, SnapshotInfo


class IBackupProvider(ABC):
    """Abstract interface for transferring and managing backups."""

    @abstractmethod
    def transfer_missing(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
        *,
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
        full_transfer_engine: str = "qemu-img-convert",
        convert_parallel: int = 4,
        convert_out_of_order: bool = True,
    ) -> list[BackupResult]:
        """Transfer snapshots not yet present at *target*.

        ``compression_type`` selects the compression algorithm for
        transfer (``"zstd"`` default, ``"zlib"`` alternative).  Only
        effective when ``target.compress`` is ``True``.

        ``stall_timeout`` is the stall-detection timeout in seconds for
        data-transfer commands.  When ``0``, stall detection is
        disabled.

        ``full_transfer_engine`` selects the FULL backup transfer
        engine (``"qemu-img-convert"`` default, ``"libnbd"``
        alternative).  Only affects FULL transfers — incrementals
        always use the ``pread``/``pwrite`` engine.

        ``convert_parallel`` maps to the ``qemu-img convert -m`` flag
        (range 1-8).  Only consumed when
        ``full_transfer_engine == "qemu-img-convert"``.

        ``convert_out_of_order`` maps to the ``qemu-img convert -W``
        flag.  Only consumed when
        ``full_transfer_engine == "qemu-img-convert"``.
        """
        ...

    @abstractmethod
    def list(self, target: TargetConfig) -> list[SnapshotInfo]:
        """List existing backups at *target*."""
        ...

    @abstractmethod
    def delete(self, backup: SnapshotInfo) -> ShellResult:
        """Delete a backup."""
        ...

    def create_full_backup(
        self,
        vm_name: str,
        source_snapshot: SnapshotInfo,
        target: TargetConfig,
        compress: bool = False,
        bucket_level: str = "monthly",
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
        full_transfer_engine: str = "qemu-img-convert",
        convert_parallel: int = 4,
        convert_out_of_order: bool = True,
    ) -> BackupResult:
        """Create a standalone full (anchor) backup via the NBD engine.

        ``vm_name`` is the full, untruncated VM name (e.g.
        ``"3.Projects_opencode"``), passed from Core's
        ``vm_config.name``.  Implementations SHALL NOT extract the VM
        name from the snapshot filename — the explicit parameter is the
        single source of truth (design D1: dependency injection over
        fragile parsing).

        ``bucket_level`` records which retention bucket triggered the
        FULL (e.g. ``"yearly"``, ``"monthly"``).

        ``compression_type`` selects the compression algorithm
        (``"zstd"`` default, ``"zlib"`` alternative).  Only effective
        when ``compress`` is ``True``.

        ``stall_timeout`` is the stall-detection timeout in seconds
        for the transfer.  When ``0``, stall detection is disabled.

        ``full_transfer_engine`` selects the FULL backup transfer
        engine (``"qemu-img-convert"`` default, ``"libnbd"``
        alternative).  When ``"qemu-img-convert"``, ``qemu-img
        convert`` is used.  When ``"libnbd"``, the pread/pwrite engine
        is used via ``_full_transfer_via_libnbd()``.

        ``convert_parallel`` maps to the ``qemu-img convert -m`` flag
        (range 1-8).  Only consumed when
        ``full_transfer_engine == "qemu-img-convert"``.

        ``convert_out_of_order`` maps to the ``qemu-img convert -W``
        flag.  Only consumed when
        ``full_transfer_engine == "qemu-img-convert"``.

        Default implementation raises ``NotImplementedError``.  Concrete
        providers that support full backups should override this.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support full backups")

    def list_checkpoints(self, vm_name: str) -> list[str]:
        """Return qsnap-owned checkpoint names for *vm_name*.

        Used by Core's orphan-checkpoint detection to identify
        checkpoints whose target no longer exists or has moved.
        On failure, returns an empty list (non-fatal).

        Default implementation returns an empty list.  Concrete
        providers that manage checkpoints should override this.
        """
        return []

    @staticmethod
    def target_hash(target_path: str) -> str:
        """Short hash of *target_path* for checkpoint naming.

        Default implementation returns an empty string.  Concrete
        providers that use target-hashed checkpoint names should
        override this.
        """
        return ""
