"""IConfigFacade — abstract read-only config API."""

from __future__ import annotations

from abc import ABC, abstractmethod

from qsnap.models.config import GlobalConfig, VMConfig


class IConfigFacade(ABC):
    """Abstract read-only configuration facade.

    ``ConfigFacade`` is the root of truth.  No module reads the config file
    or accesses raw config objects — every module receives a fully
    resolved, immutable config dataclass from ``Core``.
    """

    @abstractmethod
    def get_global(self) -> GlobalConfig:
        """Return the resolved global configuration."""
        ...

    @abstractmethod
    def get_vms(self) -> list[VMConfig]:
        """Return all configured VMs."""
        ...

    @abstractmethod
    def get_vm(self, name: str) -> VMConfig:
        """Return the VMConfig for *name*, or raise if not found."""
        ...
