"""ILifecycleManager — abstract backing chain lifecycle interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

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
        deep_verify: bool = False,
    ) -> CommitResult:
        """Merge snapshots into their backing file via ``virsh blockcommit``.

        When *deep_verify* is True, run ``qemu-img check`` on the base
        image after a successful commit and report corruptions.
        """
        ...
