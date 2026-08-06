"""ISnapshotProvider — abstract snapshot management interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path

from qsnap.models.config import VMConfig
from qsnap.models.results import ShellResult, SnapshotInfo, SnapshotResult, SnapshotSpec


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
        """Create an external disk-only snapshot (single disk).

        When *quiesce* is ``True``, pass ``--quiesce`` to virsh
        (requires qemu-guest-agent in the VM).
        """
        ...

    @abstractmethod
    def create_multi(
        self,
        vm_config: VMConfig,
        specs: Sequence[SnapshotSpec],
        quiesce: bool = False,
    ) -> list[SnapshotResult]:
        """Create external disk-only snapshots for multiple disks in ONE
        ``virsh snapshot-create-as`` call.

        All disks are snapshotted under a single guest-agent freeze
        (when ``quiesce=True``) with ``--atomic`` for all-or-nothing
        creation.  Returns one :class:`SnapshotResult` per spec, in
        spec order.  This is the preferred method for multi-disk VMs;
        single-disk :meth:`create` remains for compatibility and tests
        (design D8/D9).
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
