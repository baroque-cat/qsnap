"""MockConfigFacade — mock IConfigFacade for unit tests.

Returns preconfigured ``GlobalConfig``, ``list[VMConfig]``, and per-VM
``VMConfig``.
"""

from __future__ import annotations

from qsnap.interfaces.config import IConfigFacade
from qsnap.models.config import GlobalConfig, VMConfig


class MockConfigFacade(IConfigFacade):
    """Mock config facade that returns preconfigured data."""

    def __init__(
        self,
        global_config: GlobalConfig | None = None,
        vms: list[VMConfig] | None = None,
    ) -> None:
        self._global = global_config if global_config is not None else GlobalConfig()
        self._vms = vms if vms is not None else []
        self._vm_map: dict[str, VMConfig] = {vm.name: vm for vm in self._vms}

    def get_global(self) -> GlobalConfig:
        return self._global

    def get_vms(self) -> list[VMConfig]:
        return list(self._vms)

    def get_vm(self, name: str) -> VMConfig:
        if name not in self._vm_map:
            raise KeyError(f"VM not found: {name!r}")
        return self._vm_map[name]
