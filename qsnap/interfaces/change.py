"""IChangeDetector — abstract change detection interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from qsnap.models.config import VMConfig
from qsnap.models.results import ChangeResult


class IChangeDetector(ABC):
    """Abstract interface for detecting whether a VM disk has changed."""

    @abstractmethod
    def has_changed(self, vm_config: VMConfig, disk: str) -> ChangeResult:
        """Check whether the given disk's allocation has grown since last run.

        Detection is per-disk (multi-disk refactor): *disk* is the libvirt
        target device name (e.g. ``"vda"``) to check.  Callers iterate all
        configured disks and aggregate the results.
        """
        ...
