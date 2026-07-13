"""Unit tests for ConfigFacade TOML parsing (lexical + syntax).

Covers the ``config-parsing`` spec requirements:
- Minimal valid config parses into a VMConfig with required fields.
- Missing required VM field raises ConfigError.
- Malformed TOML raises ConfigError.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qsnap.config.facade import ConfigError, ConfigFacade
from qsnap.models.config import VMConfig

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "configs"


@pytest.mark.unit
def test_parse_minimal_valid_config() -> None:
    """A minimal valid TOML config parses into a single VM with required fields."""
    facade = ConfigFacade(FIXTURES / "minimal.toml")
    vms = facade.get_vms()

    assert len(vms) == 1

    vm = vms[0]
    assert vm.name == "testvm"
    # Required fields are present and correctly typed.
    assert vm.base_image == Path("/var/lib/libvirt/images/testvm.qcow2")
    assert vm.snapshot_dir == Path("/var/lib/libvirt/snapshots/testvm")
    # The VM has at least one target.
    assert len(vm.targets) >= 1
    assert isinstance(vm, VMConfig)


@pytest.mark.unit
def test_parse_missing_required_field_raises(tmp_path: Path) -> None:
    """Valid TOML that omits a required VM field raises ConfigError."""
    # Valid TOML syntax, but missing the required ``base_image`` field.
    config_text = (
        "[[vm]]\n" 'name = "testvm"\n' 'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
    )
    config_file = tmp_path / "missing_field.toml"
    config_file.write_text(config_text)

    with pytest.raises(ConfigError, match="Missing required VM field"):
        ConfigFacade(config_file)


@pytest.mark.unit
def test_parse_invalid_toml_raises() -> None:
    """Malformed TOML (unclosed string) raises ConfigError."""
    with pytest.raises(ConfigError, match="Invalid TOML"):
        ConfigFacade(FIXTURES / "invalid.toml")


@pytest.mark.unit
def test_config_parser_reads_lockfile_field_into_globalconfig() -> None:
    """The top-level ``lockfile`` key is parsed into GlobalConfig."""
    facade = ConfigFacade(FIXTURES / "global_fields.toml")
    assert facade.get_global().lockfile == "/var/lock/qsnap.lock"


@pytest.mark.unit
def test_config_parser_reads_timestamp_format_field() -> None:
    """The top-level ``timestamp_format`` key is parsed into GlobalConfig."""
    facade = ConfigFacade(FIXTURES / "global_fields.toml")
    assert facade.get_global().timestamp_format == "short"


@pytest.mark.unit
def test_config_parser_reads_preserve_day_of_week_field() -> None:
    """The top-level ``preserve_day_of_week`` key is parsed into GlobalConfig."""
    facade = ConfigFacade(FIXTURES / "global_fields.toml")
    assert facade.get_global().preserve_day_of_week == "wednesday"


@pytest.mark.unit
def test_config_parser_lockfile_defaults_to_none() -> None:
    """When no ``lockfile`` is set, GlobalConfig.lockfile defaults to None."""
    facade = ConfigFacade(FIXTURES / "minimal.toml")
    assert facade.get_global().lockfile is None


@pytest.mark.unit
def test_config_parser_timestamp_format_defaults_to_long() -> None:
    """When no ``timestamp_format`` is set, it defaults to 'long'."""
    facade = ConfigFacade(FIXTURES / "minimal.toml")
    assert facade.get_global().timestamp_format == "long"
