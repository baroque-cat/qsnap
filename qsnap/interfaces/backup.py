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
        rate_limit: str = "no",
    ) -> list[BackupResult]:
        """Transfer snapshots not yet present at *target*.

        ``rate_limit`` is a rate-limit string (e.g. ``"500K"``, ``"100M"``)
        or ``"no"`` for unlimited.  Implementations that support throttling
        should apply it; others may ignore it.
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
    ) -> BackupResult:
        """Create a standalone full (anchor) backup via ``qemu-img convert``.

        ``vm_name`` is the full, untruncated VM name (e.g.
        ``"3.Projects_opencode"``), passed from Core's
        ``vm_config.name``.  Implementations SHALL NOT extract the VM
        name from the snapshot filename — the explicit parameter is the
        single source of truth (design D1: dependency injection over
        fragile parsing).

        ``bucket_level`` records which retention bucket triggered the
        FULL (e.g. ``"yearly"``, ``"monthly"``).

        Default implementation raises ``NotImplementedError``.  Concrete
        providers that support full backups should override this.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support full backups")
