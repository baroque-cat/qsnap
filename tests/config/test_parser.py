"""Unit tests for ConfigFacade TOML parsing (lexical + syntax).

Covers the ``config-parsing`` spec requirements:
- Minimal valid config parses into a VMConfig with required fields.
- Missing required VM field raises ConfigError.
- Malformed TOML raises ConfigError.
- Global fault-tolerance safety fields parsed.
- Target compress parsed.
- full_every deprecation warning.
- full_compress mapped to compress with warning.
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


# ──────────────────────────────────────────────────────────────────────────
# fast-compressed-full-backup: [global] section parsing (design D4)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_parse_global_section() -> None:
    """Load global_section.toml fixture; assert [global] section keys are parsed."""
    facade = ConfigFacade(FIXTURES / "global_section.toml")
    global_cfg = facade.get_global()

    # compress = false (set inside [global]).
    assert global_cfg.compress is False
    # lockfile set inside [global].
    assert global_cfg.lockfile == "/run/qsnap.lock"


@pytest.mark.unit
def test_top_level_overrides_global_section() -> None:
    """Load global_section_override.toml; top-level compress=True overrides [global] compress=False."""
    facade = ConfigFacade(FIXTURES / "global_section_override.toml")

    # Top-level compress=True must take precedence over [global] compress=False.
    assert facade.get_global().compress is True


@pytest.mark.unit
def test_no_global_section_backward_compatible() -> None:
    """Existing global_fields.toml (top-level keys only, no [global]) parses correctly; no regression."""
    facade = ConfigFacade(FIXTURES / "global_fields.toml")
    global_cfg = facade.get_global()

    # Core fields from the fixture must parse as before.
    assert global_cfg.lockfile == "/var/lock/qsnap.lock"
    assert global_cfg.timestamp_format == "short"
    # compress is not present in global_fields.toml → defaults to True.
    assert global_cfg.compress is True

    # VMs are still parsed (end-to-end confidence).
    vms = facade.get_vms()
    assert len(vms) == 1
    assert vms[0].name == "testvm"


# ──────────────────────────────────────────────────────────────────────────
# configurable-full-backup-engine: full_transfer_engine validation
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_valid_full_transfer_engine_accepted(tmp_path: Path) -> None:
    """Global full_transfer_engine='libnbd' parses successfully, value stored in GlobalConfig."""
    config_text = (
        'full_transfer_engine = "libnbd"\n'
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
    )
    config_file = tmp_path / "valid_engine.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    assert facade.get_global().full_transfer_engine == "libnbd"


@pytest.mark.unit
def test_invalid_full_transfer_engine_raises_config_error(tmp_path: Path) -> None:
    """Global full_transfer_engine='invalid-engine' raises ConfigError."""
    config_text = (
        'full_transfer_engine = "invalid-engine"\n'
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
    )
    config_file = tmp_path / "invalid_engine.toml"
    config_file.write_text(config_text)

    with pytest.raises(ConfigError, match="Invalid full_transfer_engine"):
        ConfigFacade(config_file)


# ──────────────────────────────────────────────────────────────────────────
# configurable-full-backup-engine: convert_parallel validation
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_valid_convert_parallel_accepted(tmp_path: Path) -> None:
    """Global convert_parallel=8 parses successfully (upper boundary of range 1-8)."""
    config_text = (
        "convert_parallel = 8\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
    )
    config_file = tmp_path / "valid_parallel.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    assert facade.get_global().convert_parallel == 8


@pytest.mark.unit
def test_convert_parallel_below_range_raises_config_error(tmp_path: Path) -> None:
    """Global convert_parallel=0 (below valid range 1-8) raises ConfigError."""
    config_text = (
        "convert_parallel = 0\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
    )
    config_file = tmp_path / "low_parallel.toml"
    config_file.write_text(config_text)

    with pytest.raises(ConfigError, match="Invalid convert_parallel"):
        ConfigFacade(config_file)


@pytest.mark.unit
def test_convert_parallel_above_range_raises_config_error(tmp_path: Path) -> None:
    """Global convert_parallel=9 (above valid range 1-8) raises ConfigError."""
    config_text = (
        "convert_parallel = 9\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
    )
    config_file = tmp_path / "high_parallel.toml"
    config_file.write_text(config_text)

    with pytest.raises(ConfigError, match="Invalid convert_parallel"):
        ConfigFacade(config_file)
