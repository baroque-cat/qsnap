"""Mock verification: MockConfigFacade implements IConfigFacade."""

from __future__ import annotations

from pathlib import Path

from qsnap.interfaces.config import IConfigFacade
from qsnap.models.config import GlobalConfig, VMConfig
from tests.mocks.mock_config import MockConfigFacade


def test_mock_config_is_iconfigfacade():
    """MockConfigFacade passes isinstance against IConfigFacade and the basic
    accessors (get_global, get_vms, get_vm) all work."""
    # Default facade — no VMs configured.
    facade = MockConfigFacade()
    assert isinstance(facade, IConfigFacade)

    global_cfg = facade.get_global()
    assert isinstance(global_cfg, GlobalConfig)

    assert facade.get_vms() == []

    # Facade with a preconfigured VM — verify get_vms and get_vm.
    vm = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    facade_with_vm = MockConfigFacade(vms=[vm])

    vms = facade_with_vm.get_vms()
    assert len(vms) == 1
    assert vms[0].name == "testvm"

    fetched = facade_with_vm.get_vm("testvm")
    assert fetched.name == "testvm"
    assert fetched is vm
