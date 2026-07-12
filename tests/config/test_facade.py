"""Integration tests for ConfigFacade (parser + resolver).

Covers the ``config-parsing`` spec requirements:
- Multiple VMs from a single config.
- VM lookup by name (existing and non-existent).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qsnap.config.facade import ConfigFacade
from qsnap.models.config import VMConfig

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "configs"


@pytest.mark.unit
def test_facade_multiple_vms() -> None:
    """A config with two [[vm]] sections yields two VMConfigs."""
    facade = ConfigFacade(FIXTURES / "multi_vm.toml")
    vms = facade.get_vms()

    assert len(vms) == 2
    names = [vm.name for vm in vms]
    assert "vm1" in names
    assert "vm2" in names


@pytest.mark.unit
def test_facade_get_vm_existing() -> None:
    """get_vm returns the VMConfig for an existing VM name."""
    facade = ConfigFacade(FIXTURES / "multi_vm.toml")
    vm = facade.get_vm("vm1")

    assert isinstance(vm, VMConfig)
    assert vm.name == "vm1"


@pytest.mark.unit
def test_facade_get_vm_nonexistent_raises() -> None:
    """get_vm raises KeyError for a VM name that does not exist."""
    facade = ConfigFacade(FIXTURES / "multi_vm.toml")

    with pytest.raises(KeyError, match="VM not found"):
        facade.get_vm("nonexistent_vm")
