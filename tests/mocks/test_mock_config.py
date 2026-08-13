"""Mock verification: MockConfigFacade implements IConfigFacade."""

from __future__ import annotations

from pathlib import Path

from qsnap.interfaces.config import IConfigFacade
from qsnap.models.config import DiskConfig, GlobalConfig, VMConfig
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
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    facade_with_vm = MockConfigFacade(vms=[vm])

    vms = facade_with_vm.get_vms()
    assert len(vms) == 1
    assert vms[0].name == "testvm"

    fetched = facade_with_vm.get_vm("testvm")
    assert fetched.name == "testvm"
    assert fetched is vm


def test_mock_config_global_carries_fault_tolerance_defaults():
    """MockConfigFacade's default GlobalConfig carries the fault-tolerance
    defaults (fault-tolerance-hardening change).

    Core tests rely on the mock's global carrying ``snapshot_preserve_min=24``
    (active preservation floor / hysteresis collapse floor) and the free-space
    gate defaults (``free_space_check="strict"``, ``free_space_reserve=0``,
    ``free_space_factor=1.0``) so that pipeline behavior matches production
    defaults without per-test configuration.
    """
    facade = MockConfigFacade()
    global_cfg = facade.get_global()

    assert global_cfg.snapshot_preserve_min == 24
    assert global_cfg.free_space_check == "strict"
    assert global_cfg.free_space_reserve == 0
    assert global_cfg.free_space_factor == 1.0
