"""IBackupProvider — abstract backup transfer interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import BackupInfo, BackupResult, ShellResult


class IBackupProvider(ABC):
    """Abstract interface for transferring and managing backups."""

    # ── New (orthogonal) API ────────────────────────────────────────

    @abstractmethod
    def run_backup(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        disk: DiskConfig,
        *,
        force_full: bool = False,
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
        convert_parallel: int = 4,
        convert_out_of_order: bool = True,
    ) -> BackupResult:
        """Create exactly one backup for *disk* on *target*.

        The provider decides the backup kind autonomously: no checkpoint
        exists for this VM+target+disk → FULL; a checkpoint exists →
        delta of dirty blocks since the newest checkpoint.

        When *force_full* is ``True`` the provider SHALL create a FULL
        even when a checkpoint already exists.

        ``compression_type`` selects the compression algorithm for
        transfer (``"zstd"`` default, ``"zlib"`` alternative).  Only
        effective when ``target.compress`` is ``True`` and a FULL
        export is pulled.

        ``stall_timeout`` is the stall-detection timeout in seconds for
        data-transfer commands.  When ``0``, stall detection is
        disabled.

        ``convert_parallel`` maps to the ``qemu-img convert -m`` flag
        (range 1-8).

        ``convert_out_of_order`` maps to the ``qemu-img convert -W``
        flag.
        """
        ...

    # ── Discovery API ────────────────────────────────────────────────

    @abstractmethod
    def list(self, target: TargetConfig) -> list[BackupInfo]:
        """List existing backups at *target*."""
        ...

    @abstractmethod
    def delete(self, backup: BackupInfo) -> ShellResult:
        """Delete a backup."""
        ...

    def list_checkpoints(self, vm_name: str) -> list[str]:
        """Return qsnap-owned checkpoint names for *vm_name*."""
        return []

    @staticmethod
    def target_hash(target_path: str) -> str:
        """Short hash of *target_path* for checkpoint naming."""
        return ""
