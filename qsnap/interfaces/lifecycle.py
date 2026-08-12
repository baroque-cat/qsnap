"""ILifecycleManager — abstract backing chain lifecycle interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from qsnap.models.config import VMConfig
from qsnap.models.results import CommitResult, SnapshotInfo


class ILifecycleManager(ABC):
    """Abstract interface for backing chain lifecycle management."""

    @abstractmethod
    def blockcommit(
        self,
        vm_config: VMConfig,
        snapshots_to_merge: list[SnapshotInfo],
        *,
        disk: str,
        base_image: Path,
        deep_verify: bool = False,
        timeout: int = 1800,
    ) -> CommitResult:
        """Merge snapshots of one disk into that disk's base image.

        Multi-disk (refactor): *disk* is the libvirt target device name
        (e.g. ``"vda"``) and *base_image* is this disk's base qcow2 path.
        All snapshots in *snapshots_to_merge* belong to *disk*.  When
        *deep_verify* is True, run ``qemu-img check`` on *base_image*
        after a successful commit and report corruptions.

        *timeout* is the maximum wall-clock time in seconds for the
        entire commit call.  The caller passes it from
        ``GlobalConfig.blockcommit_timeout``.
        """
        ...
