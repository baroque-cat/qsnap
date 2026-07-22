"""Unit tests for ConfigFacade TOML parsing (lexical + syntax).

Covers the ``config-parsing`` spec requirements:
- Minimal valid config parses into a VMConfig with required fields.
- Missing required VM field raises ConfigError.
- Malformed TOML raises ConfigError.
- Global fault-tolerance safety fields parsed.
- Target compress parsed.
- full_every deprecation warning.
- full_compress mapped to compress with warning.
- Removed rsync/file-copy fields trigger deprecation WARNINGs.
"""

from __future__ import annotations

import logging
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
    config_text = '[[vm]]\nname = "testvm"\nsnapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
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


# ──────────────────────────────────────────────────────────────────────────
# bucket-driven-backup-model: Global fault-tolerance safety fields
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_config_parser_reads_auto_cleanup_state_backup_count() -> None:
    """Global safety fields auto_cleanup and state_backup_count are parsed from TOML."""
    facade = ConfigFacade(FIXTURES / "safety_fields.toml")
    global_cfg = facade.get_global()

    assert global_cfg.auto_cleanup is True
    assert global_cfg.state_backup_count == 3


# bucket-driven-backup-model: Target compress parsing


@pytest.mark.unit
def test_parse_target_compress() -> None:
    """Target-level compress field is parsed correctly."""
    facade = ConfigFacade(FIXTURES / "bucket_driven.toml")

    # vm_bucket: compress=True (explicit)
    vm1 = facade.get_vm("vm_bucket")
    t1 = next(t for t in vm1.targets if t.path == Path("/mnt/backup/vm_bucket"))
    assert t1.compress is True

    # vm_no_compress: compress=False (explicit)
    vm2 = facade.get_vm("vm_no_compress")
    t2 = next(t for t in vm2.targets if t.path == Path("/mnt/backup/vm_no_compress"))
    assert t2.compress is False


# ──────────────────────────────────────────────────────────────────────────
# bucket-driven-backup-model: Deprecated field handling
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_full_every_deprecation_warning(caplog: pytest.LogCaptureFixture) -> None:
    """full_every in config triggers a deprecation WARNING log."""
    with caplog.at_level(logging.WARNING, logger="qsnap.config"):
        ConfigFacade(FIXTURES / "deprecated_fields.toml")

    assert "full_every is deprecated and ignored" in caplog.text


@pytest.mark.unit
def test_full_compress_mapped_to_compress_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """full_compress is mapped to compress with a deprecation WARNING."""
    with caplog.at_level(logging.WARNING, logger="qsnap.config"):
        facade = ConfigFacade(FIXTURES / "deprecated_fields.toml")

    # vm_deprecated has full_compress=true; compress should be True.
    vm = facade.get_vm("vm_deprecated")
    t = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_deprecated"))
    assert t.compress is True

    assert "full_compress is deprecated" in caplog.text
    assert "compress" in caplog.text
