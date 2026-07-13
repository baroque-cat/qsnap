"""IChangeDetector — abstract change detection interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from qsnap.models.config import VMConfig
from qsnap.models.results import ChangeResult


class IChangeDetector(ABC):
    """Abstract interface for detecting whether a VM disk has changed."""

    @abstractmethod
    def has_changed(
        self, vm_config: VMConfig, disk: str | None = None
    ) -> ChangeResult:
        """Check whether the VM disk allocation has grown since last run.

        When *disk* is provided, scope detection to that specific disk.
        When omitted, use the first discovered disk (backward-compatible).
        """
        ...
