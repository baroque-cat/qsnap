"""ISnapshotProvider — abstract snapshot management interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from qsnap.models.config import VMConfig
from qsnap.models.results import ShellResult, SnapshotInfo, SnapshotResult


class ISnapshotProvider(ABC):
    """Abstract interface for creating, listing, and deleting snapshots."""

    @abstractmethod
    def create(
        self,
        vm_config: VMConfig,
        snapshot_name: str,
        disk: str,
        snapshot_path: Path,
        quiesce: bool = False,
    ) -> SnapshotResult:
        """Create an external disk-only snapshot.

        When *quiesce* is ``True``, pass ``--quiesce`` to virsh
        (requires qemu-guest-agent in the VM).
        """
        ...

    @abstractmethod
    def list(self, vm_config: VMConfig) -> list[SnapshotInfo]:
        """List existing snapshots for *vm_config*."""
        ...

    @abstractmethod
    def delete(self, snapshot: SnapshotInfo) -> ShellResult:
        """Delete a snapshot."""
        ...
