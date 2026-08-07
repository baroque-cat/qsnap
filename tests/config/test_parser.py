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
    assert vm.disks[0].base_image == Path("/var/lib/libvirt/images/testvm.qcow2")
    assert vm.snapshot_dir == Path("/var/lib/libvirt/snapshots/testvm")
    # The VM has at least one target.
    assert len(vm.targets) >= 1
    assert isinstance(vm, VMConfig)


@pytest.mark.unit
def test_parse_missing_required_field_raises(tmp_path: Path) -> None:
    """Valid TOML that omits required [[vm.disk]] section raises ConfigError."""
    # Valid TOML syntax, but missing the required [[vm.disk]] section.
    config_text = '[[vm]]\nname = "testvm"\nsnapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
    config_file = tmp_path / "missing_field.toml"
    config_file.write_text(config_text)

    with pytest.raises(ConfigError, match="must define at least one"):
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
def test_config_parser_lockfile_defaults_to_none() -> None:
    """When no ``lockfile`` is set, GlobalConfig.lockfile defaults to None."""
    facade = ConfigFacade(FIXTURES / "minimal.toml")
    assert facade.get_global().lockfile is None


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


# ──────────────────────────────────────────────────────────────────────────
# bucket-driven-backup-model: Deprecated field handling
# ──────────────────────────────────────────────────────────────────────────


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
    # compress is not present in global_fields.toml → defaults to True.
    assert global_cfg.compress is True

    # VMs are still parsed (end-to-end confidence).
    vms = facade.get_vms()
    assert len(vms) == 1
    assert vms[0].name == "testvm"


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
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        "\n"
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
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        "\n"
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
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        "\n"
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
    )
    config_file = tmp_path / "high_parallel.toml"
    config_file.write_text(config_text)

    with pytest.raises(ConfigError, match="Invalid convert_parallel"):
        ConfigFacade(config_file)


# ──────────────────────────────────────────────────────────────────────────
# vm-level-backup-engine-options: VM-level engine option parsing
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_level_compression_type_parsed(tmp_path: Path) -> None:
    """VM-level compression_type="zlib" is parsed into VMConfig."""
    config_text = (
        "[[vm]]\n"
        'name = "web01"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/web01"\n'
        'compression_type = "zlib"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/web01.qcow2"\n'
        "\n"
        "[[vm.target]]\n"
        'path = "/mnt/backup/web01"\n'
    )
    config_file = tmp_path / "vm_compression_type.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    vm = facade.get_vm("web01")
    assert vm.compression_type == "zlib"


@pytest.mark.unit
def test_vm_level_backup_stall_timeout_parsed(tmp_path: Path) -> None:
    """VM-level backup_stall_timeout="45m" is parsed into VMConfig."""
    config_text = (
        "[[vm]]\n"
        'name = "web01"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/web01"\n'
        'backup_stall_timeout = "45m"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/web01.qcow2"\n'
        "\n"
        "[[vm.target]]\n"
        'path = "/mnt/backup/web01"\n'
    )
    config_file = tmp_path / "vm_stall_timeout.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    vm = facade.get_vm("web01")
    assert vm.backup_stall_timeout == "45m"


@pytest.mark.unit
def test_vm_level_convert_parallel_accepted(tmp_path: Path) -> None:
    """VM-level convert_parallel=8 (upper boundary of range 1-8) is accepted."""
    config_text = (
        "[[vm]]\n"
        'name = "web01"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/web01"\n'
        "convert_parallel = 8\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/web01.qcow2"\n'
        "\n"
        "[[vm.target]]\n"
        'path = "/mnt/backup/web01"\n'
    )
    config_file = tmp_path / "vm_convert_parallel.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    vm = facade.get_vm("web01")
    assert vm.convert_parallel == 8


@pytest.mark.unit
def test_vm_level_convert_parallel_above_range_raises(tmp_path: Path) -> None:
    """VM-level convert_parallel=9 (above range 1-8) raises ConfigError naming the VM."""
    config_text = (
        "[[vm]]\n"
        'name = "web01"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/web01"\n'
        "convert_parallel = 9\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/web01.qcow2"\n'
        "\n"
        "[[vm.target]]\n"
        'path = "/mnt/backup/web01"\n'
    )
    config_file = tmp_path / "vm_high_parallel.toml"
    config_file.write_text(config_text)

    with pytest.raises(ConfigError, match="1-8") as exc_info:
        ConfigFacade(config_file)
    assert "web01" in str(exc_info.value)


@pytest.mark.unit
def test_vm_level_all_six_engine_options_parsed(tmp_path: Path) -> None:
    """All six VM-level backup engine options are parsed into the VMConfig."""
    config_text = (
        "[[vm]]\n"
        'name = "web01"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/web01"\n'
        "compress = false\n"
        'compression_type = "zlib"\n'
        "convert_parallel = 8\n"
        "convert_out_of_order = false\n"
        'backup_stall_timeout = "1h"\n'
        'verify = "compare"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/web01.qcow2"\n'
        "\n"
        "[[vm.target]]\n"
        'path = "/mnt/backup/web01"\n'
    )
    config_file = tmp_path / "vm_all_engine_options.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    vm = facade.get_vm("web01")
    assert vm.compress is False
    assert vm.compression_type == "zlib"
    assert vm.convert_parallel == 8
    assert vm.convert_out_of_order is False
    assert vm.backup_stall_timeout == "1h"
    assert vm.verify == "compare"


@pytest.mark.unit
def test_vm_level_invalid_compression_type_names_vm(tmp_path: Path) -> None:
    """VM-level invalid compression_type raises ConfigError naming the VM and valid values."""
    config_text = (
        "[[vm]]\n"
        'name = "web01"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/web01"\n'
        'compression_type = "lz4"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/web01.qcow2"\n'
        "\n"
        "[[vm.target]]\n"
        'path = "/mnt/backup/web01"\n'
    )
    config_file = tmp_path / "vm_bad_compression.toml"
    config_file.write_text(config_text)

    with pytest.raises(ConfigError) as exc_info:
        ConfigFacade(config_file)
    message = str(exc_info.value)
    assert "web01" in message
    assert "zlib" in message
    assert "zstd" in message


@pytest.mark.unit
def test_invalid_compression_type_raises_config_error(tmp_path: Path) -> None:
    """Global-level invalid compression_type raises ConfigError with a valid-values hint."""
    config_text = (
        'compression_type = "lz4"\n'
        "[[vm]]\n"
        'name = "web01"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/web01"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/web01.qcow2"\n'
        "\n"
        "[[vm.target]]\n"
        'path = "/mnt/backup/web01"\n'
    )
    config_file = tmp_path / "global_bad_compression.toml"
    config_file.write_text(config_text)

    with pytest.raises(ConfigError) as exc_info:
        ConfigFacade(config_file)
    message = str(exc_info.value)
    assert "compression_type" in message
    assert "zlib" in message
    assert "zstd" in message


@pytest.mark.unit
def test_invalid_backup_stall_timeout_raises_config_error(tmp_path: Path) -> None:
    """Global-level invalid backup_stall_timeout raises ConfigError."""
    config_text = (
        'backup_stall_timeout = "abc"\n'
        "[[vm]]\n"
        'name = "web01"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/web01"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/web01.qcow2"\n'
        "\n"
        "[[vm.target]]\n"
        'path = "/mnt/backup/web01"\n'
    )
    config_file = tmp_path / "global_bad_stall_timeout.toml"
    config_file.write_text(config_text)

    with pytest.raises(ConfigError, match="backup_stall_timeout"):
        ConfigFacade(config_file)
