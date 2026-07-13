"""Integration tests for ConfigFacade (parser + resolver).

Covers the ``config-parsing`` spec requirements:
- Multiple VMs from a single config.
- VM lookup by name (existing and non-existent).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qsnap.config.facade import ConfigError, ConfigFacade
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


# ──────────────────────────────────────────────────────────────────────────
# 6. preserve_day_of_week validation
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_preserve_day_of_week_valid_value_accepted() -> None:
    """A valid preserve_day_of_week value is accepted and stored as-is."""
    facade = ConfigFacade(FIXTURES / "preserve_dow_valid.toml")
    assert facade.get_global().preserve_day_of_week == "friday"


@pytest.mark.unit
def test_preserve_day_of_week_invalid_value_raises_configerror() -> None:
    """An invalid preserve_day_of_week raises ConfigError."""
    with pytest.raises(ConfigError, match="Invalid preserve_day_of_week"):
        ConfigFacade(FIXTURES / "preserve_dow_invalid.toml")


@pytest.mark.unit
def test_preserve_day_of_week_case_insensitive_accepted(tmp_path: Path) -> None:
    """preserve_day_of_week is case-insensitive; uppercase is accepted."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "FRIDAY"\n\n'
        '[[vm]]\nname = "testvm"\nbase_image = "/tmp/test.qcow2"\nsnapshot_dir = "/tmp/snaps"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().preserve_day_of_week == "FRIDAY"


@pytest.mark.unit
@pytest.mark.parametrize(
    "day",
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
)
def test_preserve_day_of_week_all_seven_days_accepted(day: str, tmp_path: Path) -> None:
    """Every day of the week is a valid preserve_day_of_week value."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f'preserve_day_of_week = "{day}"\n\n'
        '[[vm]]\nname = "testvm"\nbase_image = "/tmp/test.qcow2"\nsnapshot_dir = "/tmp/snaps"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().preserve_day_of_week == day
