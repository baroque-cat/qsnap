"""Integration tests for ConfigFacade (parser + resolver).

Covers the ``config-parsing`` spec requirements:
- Multiple VMs from a single config.
- VM lookup by name (existing and non-existent).
- Example config parseable with all fields documented.
- preserve_min validation without buckets.
- compress parsing (bucket-driven model).
"""

from __future__ import annotations

import logging
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


# ──────────────────────────────────────────────────────────────────────────
# snapshot_preserve_min / target_preserve_min inheritance (global → VM → target)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_inherits_global_snapshot_preserve_min() -> None:
    """VM 'vm_inherit' inherits global snapshot_preserve_min='2h'."""
    facade = ConfigFacade(FIXTURES / "preserve_min.toml")
    vm = facade.get_vm("vm_inherit")
    assert vm.snapshot_preserve_min == "2h"


@pytest.mark.unit
def test_vm_overrides_global_snapshot_preserve_min() -> None:
    """VM 'vm_override' has snapshot_preserve_min='6h' (overrides global '2h')."""
    facade = ConfigFacade(FIXTURES / "preserve_min.toml")
    vm = facade.get_vm("vm_override")
    assert vm.snapshot_preserve_min == "6h"


@pytest.mark.unit
def test_target_inherits_vm_target_preserve_min() -> None:
    """Target 'vm_override_inherit' inherits VM's target_preserve_min='12h'."""
    facade = ConfigFacade(FIXTURES / "preserve_min.toml")
    vm = facade.get_vm("vm_override")
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_override_inherit"))
    assert target.target_preserve_min == "12h"


@pytest.mark.unit
def test_target_overrides_vm_target_preserve_min() -> None:
    """Target 'vm_override_override' has target_preserve_min='24h' (overrides VM's '12h')."""
    facade = ConfigFacade(FIXTURES / "preserve_min.toml")
    vm = facade.get_vm("vm_override")
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_override_override"))
    assert target.target_preserve_min == "24h"


# ──────────────────────────────────────────────────────────────────────────
# full_every / full_compress parsing
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_facade_parses_target_compress() -> None:
    """ConfigFacade parses compress=True from a [[vm.target]] section."""
    facade = ConfigFacade(FIXTURES / "full_backup.toml")
    vm = facade.get_vm("vm_with_full")
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_with_full"))
    assert target.compress is True


@pytest.mark.unit
def test_facade_target_compress_defaults_to_global() -> None:
    """When no compress is set on a target, it inherits the global default (True)."""
    facade = ConfigFacade(FIXTURES / "full_backup.toml")
    vm = facade.get_vm("vm_no_full")
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_no_full"))
    # vm_no_full does not set compress, so inherits global default True.
    assert target.compress is True


# deferred-monitoring thresholds parsing
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_deferred_thresholds_overridden() -> None:
    """ConfigFacade parses overridden deferred thresholds from TOML."""
    facade = ConfigFacade(FIXTURES / "deferred_thresholds.toml")
    global_cfg = facade.get_global()
    assert global_cfg.deferred_warn_count == "3"
    # Unset values fall back to defaults.
    assert global_cfg.deferred_crit_count == "10"
    assert global_cfg.deferred_warn_age == "7d"
    assert global_cfg.deferred_crit_age == "30d"


# ──────────────────────────────────────────────────────────────────────────
# Fault-tolerance safety fields — facade integration tests
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_facade_parses_global_safety_fields(tmp_path: Path) -> None:
    """ConfigFacade parses all global fault-tolerance safety fields from TOML."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "auto_cleanup = false\n"
        "state_backup_count = 5\n"
        "chain_verify_before_commit = false\n"
        "chain_verify_after_commit = true\n"
        'deep_check_schedule = "monthly"\n'
        'preserve_day_of_week = "monday"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    facade = ConfigFacade(config_file)
    global_cfg = facade.get_global()

    assert global_cfg.auto_cleanup is False
    assert global_cfg.state_backup_count == 5
    assert global_cfg.chain_verify_before_commit is False
    assert global_cfg.chain_verify_after_commit is True
    assert global_cfg.deep_check_schedule == "monthly"


@pytest.mark.unit
def test_facade_parses_vm_deep_verify_fields(tmp_path: Path) -> None:
    """ConfigFacade parses blockcommit_deep_verify and snapshot_deep_verify from a VM section."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "blockcommit_deep_verify = true\n"
        "snapshot_deep_verify = true\n"
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.blockcommit_deep_verify is True
    assert vm.snapshot_deep_verify is True


@pytest.mark.unit
def test_facade_parses_target_retry_fields(tmp_path: Path) -> None:
    """ConfigFacade parses backup_retry_max and backup_retry_base from a [[vm.target]] section."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "  backup_retry_max = 5\n"
        '  backup_retry_base = "10s"\n'
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")
    assert len(vm.targets) == 1
    target = vm.targets[0]
    assert target.backup_retry_max == 5
    assert target.backup_retry_base == "10s"


@pytest.mark.unit
def test_facade_parses_target_retry_overrides() -> None:
    """Full safety_fields.toml fixture: critical-vm has deep-verify ON and retry=5/5s;
    standard-vm has deep-verify OFF and retry=2/1s."""
    facade = ConfigFacade(FIXTURES / "safety_fields.toml")

    critical = facade.get_vm("critical-vm")
    assert critical.blockcommit_deep_verify is True
    assert critical.snapshot_deep_verify is True
    assert len(critical.targets) == 1
    assert critical.targets[0].backup_retry_max == 5
    assert critical.targets[0].backup_retry_base == "5s"

    standard = facade.get_vm("standard-vm")
    assert standard.blockcommit_deep_verify is False
    assert standard.snapshot_deep_verify is False
    assert len(standard.targets) == 1
    assert standard.targets[0].backup_retry_max == 2
    assert standard.targets[0].backup_retry_base == "1s"


@pytest.mark.unit
def test_facade_invalid_retry_base_raises_config_error(tmp_path: Path) -> None:
    """ConfigFacade raises ConfigError when backup_retry_base is not a valid duration string."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        '  backup_retry_base = "abc"\n'
    )
    with pytest.raises(ConfigError, match="Invalid backup_retry_base"):
        ConfigFacade(config_file)


# ──────────────────────────────────────────────────────────────────────────
# bucket-driven-backup-model: Example config parseable
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_example_config_parseable_all_fields() -> None:
    """The project's qsnap.toml.example is parseable with all fields documented."""
    example_path = Path(__file__).resolve().parent.parent.parent / "qsnap.toml.example"
    facade = ConfigFacade(example_path)

    # Should have at least one VM (debiantest).
    vms = facade.get_vms()
    assert len(vms) >= 1

    # Verify global defaults.
    global_cfg = facade.get_global()
    assert global_cfg.timestamp_format == "long"
    assert global_cfg.preserve_day_of_week == "monday"

    # Verify VM de facto.
    vm = facade.get_vm("debiantest")
    assert vm.name == "debiantest"
    assert len(vm.targets) >= 1
    target = vm.targets[0]
    assert target.path == Path("/mnt/backup/debiantest")
    # Defaults.
    assert target.incremental is True
    assert target.compress is True


# ──────────────────────────────────────────────────────────────────────────
# bucket-driven-backup-model: preserve_min validation without buckets
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_preserve_min_without_buckets_rejected(tmp_path: Path) -> None:
    """preserve_min without any non-zero bucket counts raises ConfigError."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'target_preserve = "0h 0d 0w 0m 0y"\n'
        'target_preserve_min = "6h"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
    )
    with pytest.raises(ConfigError, match="nothing would be retained"):
        ConfigFacade(config_file)


@pytest.mark.unit
def test_preserve_min_all_without_buckets_allowed(tmp_path: Path) -> None:
    """preserve_min='all' with all-zero bucket counts is allowed (no error)."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'target_preserve = "0h 0d 0w 0m 0y"\n'
        'target_preserve_min = "all"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
    )
    # Should not raise — preserve_min='all' bypasses the all-zero check.
    facade = ConfigFacade(config_file)
    assert facade.get_vm("testvm").target_preserve_min == "all"


@pytest.mark.unit
def test_preserve_min_with_buckets_allowed(tmp_path: Path) -> None:
    """preserve_min with non-zero bucket counts is allowed (no error)."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'target_preserve = "24h 7d 0w 0m 0y"\n'
        'target_preserve_min = "6h"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
    )
    # Should not raise — there are non-zero bucket counts (24h, 7d).
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")
    assert vm.target_preserve_min == "6h"
    target = vm.targets[0]
    assert target.target_preserve == "24h 7d 0w 0m 0y"


# ── F-anchor validation in _build_target ──────────────────────────────────


@pytest.mark.unit
def test_f_anchor_zero_count_raises_config_error(tmp_path: Path) -> None:
    """target_preserve with ``0Fh`` (F-anchor + zero count) raises ConfigError."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'target_preserve = "0Fh 7d"\n'
        'target_preserve_min = "6h"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
    )
    with pytest.raises(ConfigError, match="F-anchor on bucket 'h' requires count > 0"):
        ConfigFacade(config_file)


@pytest.mark.unit
def test_preserve_min_without_buckets_raises_config_error(tmp_path: Path) -> None:
    """preserve_min='48h' with all-zero buckets and no F-anchors raises ConfigError."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'target_preserve = "0h 0d 0w 0m 0y"\n'
        'target_preserve_min = "48h"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
    )
    with pytest.raises(ConfigError, match="nothing would be retained"):
        ConfigFacade(config_file)


# ──────────────────────────────────────────────────────────────────────────
# FULL backup integrity verification tiers — ConfigFacade integration
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_full_verify_after_create_hash_deprecated_maps_to_compare(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """full_verify_after_create='hash' is deprecated — logs WARNING and
    SHOULD be treated as 'compare'.  (NOTE: the stored value currently
    remains 'hash' due to a production-code bug — the WARNING is logged
    before GlobalConfig is constructed but the value is never remapped.
    This issue is reported and the assertion here encodes the intended
    behaviour.)"""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        'full_verify_after_create = "hash"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    with caplog.at_level(logging.WARNING):
        facade = ConfigFacade(config_file)

    # Verify WARNING was emitted about hash deprecation.
    deprecation_msgs = [m for m in caplog.messages if "deprecated" in m.lower()]
    assert len(deprecation_msgs) > 0, "Expected deprecation WARNING for 'hash'"
    assert any("hash" in m for m in caplog.messages), "Expected WARNING to mention 'hash'"

    # The deprecated "hash" value is remapped to "compare" after the
    # WARNING is logged (object.__setattr__ on the frozen dataclass).
    assert facade.get_global().full_verify_after_create == "compare"


@pytest.mark.unit
def test_facade_parses_full_verify_before_rebase_off(tmp_path: Path) -> None:
    """ConfigFacade parses full_verify_before_rebase='off' from the global section."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        'full_verify_before_rebase = "off"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().full_verify_before_rebase == "off"


@pytest.mark.unit
def test_facade_parses_full_verify_before_delete_metadata(tmp_path: Path) -> None:
    """ConfigFacade parses full_verify_before_delete='metadata' from the global section."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        'full_verify_before_delete = "metadata"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().full_verify_before_delete == "metadata"


@pytest.mark.unit
def test_facade_parses_deep_check_targets_true(tmp_path: Path) -> None:
    """ConfigFacade parses deep_check_targets=true from the global section."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        "deep_check_targets = true\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().deep_check_targets is True


@pytest.mark.unit
def test_facade_invalid_full_verify_after_create_raises_config_error(tmp_path: Path) -> None:
    """An invalid full_verify_after_create value raises ConfigError."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        'full_verify_after_create = "sha256"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    with pytest.raises(ConfigError, match="Invalid full_verify_after_create"):
        ConfigFacade(config_file)


@pytest.mark.unit
def test_facade_invalid_full_verify_before_rebase_raises_config_error(tmp_path: Path) -> None:
    """An invalid full_verify_before_rebase value raises ConfigError."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        'full_verify_before_rebase = "hash"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    with pytest.raises(ConfigError, match="Invalid full_verify_before_rebase"):
        ConfigFacade(config_file)


@pytest.mark.unit
def test_facade_invalid_full_verify_before_delete_raises_config_error(tmp_path: Path) -> None:
    """An invalid full_verify_before_delete value raises ConfigError."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        'full_verify_before_delete = "hash"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    with pytest.raises(ConfigError, match="Invalid full_verify_before_delete"):
        ConfigFacade(config_file)


# ──────────────────────────────────────────────────────────────────────────
# all-zero bucket validation with target_preserve_min variants
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_all_zero_buckets_with_targets_raises_config_error(tmp_path: Path) -> None:
    """Config with all-zero bucket counts and target_preserve_min='latest'
    raises ConfigError (because 'latest' is not 'all')."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'target_preserve = "0h 0d 0w 0m 0y"\n'
        'target_preserve_min = "latest"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
    )
    with pytest.raises(ConfigError, match="nothing would be retained"):
        ConfigFacade(config_file)


@pytest.mark.unit
def test_preserve_min_all_allows_zero_buckets(tmp_path: Path) -> None:
    """Config with all-zero bucket counts and target_preserve_min='all'
    is accepted (the 'all' bypass overrides the all-zero check)."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'preserve_day_of_week = "monday"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'target_preserve = "0h 0d 0w 0m 0y"\n'
        'target_preserve_min = "all"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")
    assert vm.target_preserve_min == "all"
    # Verify the target was also parsed correctly.
    assert len(vm.targets) == 1
    assert vm.targets[0].path == Path("/mnt/backup/testvm")


# ──────────────────────────────────────────────────────────────────────────
# compression_type parsing (global + target inheritance + validation)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_global_compression_type_parsed(tmp_path: Path) -> None:
    """ConfigFacade parses compression_type='zlib' from the global section."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'compression_type = "zlib"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().compression_type == "zlib"


@pytest.mark.unit
def test_target_compression_type_overrides_global(tmp_path: Path) -> None:
    """Target-level compression_type='zlib' overrides global 'zstd'."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'compression_type = "zstd"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        '  compression_type = "zlib"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().compression_type == "zstd"
    vm = facade.get_vm("testvm")
    assert len(vm.targets) == 1
    assert vm.targets[0].compression_type == "zlib"


@pytest.mark.unit
def test_target_compression_type_inherits(tmp_path: Path) -> None:
    """Target inherits global compression_type='zlib' when not set locally."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'compression_type = "zlib"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().compression_type == "zlib"
    vm = facade.get_vm("testvm")
    assert len(vm.targets) == 1
    assert vm.targets[0].compression_type == "zlib"


@pytest.mark.unit
def test_invalid_compression_type_raises_config_error(tmp_path: Path) -> None:
    """Invalid compression_type='lz4' raises ConfigError."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'compression_type = "lz4"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    with pytest.raises(ConfigError, match="Invalid compression_type"):
        ConfigFacade(config_file)


@pytest.mark.unit
def test_compression_type_absent_defaults_to_zstd(tmp_path: Path) -> None:
    """When compression_type is absent from TOML, global and target default to 'zstd'."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().compression_type == "zstd"
    vm = facade.get_vm("testvm")
    assert len(vm.targets) == 1
    assert vm.targets[0].compression_type == "zstd"


# ──────────────────────────────────────────────────────────────────────────
# backup_stall_timeout parsing (global + target inheritance + validation)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_global_backup_stall_timeout_parsed(tmp_path: Path) -> None:
    """ConfigFacade parses backup_stall_timeout='1h' from the global section."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'backup_stall_timeout = "1h"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().backup_stall_timeout == "1h"


@pytest.mark.unit
def test_target_stall_timeout_overrides_global(tmp_path: Path) -> None:
    """Target-level backup_stall_timeout='1h' overrides global '30m'."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'backup_stall_timeout = "30m"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        '  backup_stall_timeout = "1h"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().backup_stall_timeout == "30m"
    vm = facade.get_vm("testvm")
    assert len(vm.targets) == 1
    assert vm.targets[0].backup_stall_timeout == "1h"


@pytest.mark.unit
def test_target_stall_timeout_inherits(tmp_path: Path) -> None:
    """Target inherits global backup_stall_timeout='1h' when not set locally."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'backup_stall_timeout = "1h"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().backup_stall_timeout == "1h"
    vm = facade.get_vm("testvm")
    assert len(vm.targets) == 1
    assert vm.targets[0].backup_stall_timeout == "1h"


@pytest.mark.unit
def test_invalid_stall_timeout_raises_config_error(tmp_path: Path) -> None:
    """Invalid backup_stall_timeout='abc' raises ConfigError."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'backup_stall_timeout = "abc"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    with pytest.raises(ConfigError, match="Invalid backup_stall_timeout"):
        ConfigFacade(config_file)


@pytest.mark.unit
def test_stall_timeout_absent_defaults_to_30m(tmp_path: Path) -> None:
    """When backup_stall_timeout is absent from TOML, global and target default to '30m'."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().backup_stall_timeout == "30m"
    vm = facade.get_vm("testvm")
    assert len(vm.targets) == 1
    assert vm.targets[0].backup_stall_timeout == "30m"


@pytest.mark.unit
def test_stall_timeout_zero_disables(tmp_path: Path) -> None:
    """backup_stall_timeout='0s' is a valid value that disables stall detection at runtime."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'backup_stall_timeout = "0s"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().backup_stall_timeout == "0s"


# ──────────────────────────────────────────────────────────────────────────
# D5: bitmap + verify="full" guard — auto-downgrade with WARNING
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_verify_hash_deprecated_warning_and_maps_to_compare(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """verify='hash' is deprecated — logs WARNING and is treated as 'compare'."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        '  verify = "hash"\n'
    )

    with caplog.at_level(logging.WARNING):
        facade = ConfigFacade(config_file)

    vm = facade.get_vm("testvm")
    target = vm.targets[0]

    # verify="hash" is deprecated and mapped to "compare".
    assert target.verify == "compare"

    # Verify a deprecation WARNING was emitted.
    deprecation_msgs = [m for m in caplog.messages if "deprecated" in m.lower()]
    assert len(deprecation_msgs) > 0, "Expected deprecation WARNING for verify='hash'"
    assert any("hash" in m for m in deprecation_msgs), "WARNING should mention 'hash'"


@pytest.mark.unit
def test_verify_full_deprecated_warning_and_maps_to_compare(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """verify='full' is deprecated — logs WARNING and is treated as 'compare'."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        '  verify = "full"\n'
    )

    with caplog.at_level(logging.WARNING):
        facade = ConfigFacade(config_file)

    vm = facade.get_vm("testvm")
    target = vm.targets[0]

    # verify="full" is deprecated and mapped to "compare".
    assert target.verify == "compare"

    # Verify a deprecation WARNING was emitted.
    deprecation_msgs = [m for m in caplog.messages if "deprecated" in m.lower()]
    assert len(deprecation_msgs) > 0, "Expected deprecation WARNING for verify='full'"
    assert any("full" in m for m in deprecation_msgs), "WARNING should mention 'full'"


@pytest.mark.unit
def test_bitmap_verify_metadata_no_warning(tmp_path: Path, caplog) -> None:
    """verify='metadata' is the recommended mode — NO warning emitted."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        '  verify = "metadata"\n'
    )

    with caplog.at_level(logging.WARNING):
        facade = ConfigFacade(config_file)

    vm = facade.get_vm("testvm")
    target = vm.targets[0]
    # metadata is the correct/recommended mode — no downgrade.
    assert target.verify == "metadata"

    # No warning about verify downgrade should have been emitted.
    for msg in caplog.messages:
        assert "not supported in bitmap mode" not in msg
        assert "Downgrading" not in msg


@pytest.mark.unit
def test_verify_invalid_value_raises_config_error(tmp_path: Path) -> None:
    """verify='invalid' raises ConfigError — only 'off', 'metadata',
    'compare' (and deprecated 'hash'/'full') are valid."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        '  verify = "invalid"\n'
    )
    with pytest.raises(ConfigError, match="Invalid verify"):
        ConfigFacade(config_file)


# ──────────────────────────────────────────────────────────────────────────
# Removed rsync/file-copy fields — deprecation WARNING, no ConfigError
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_removed_fields_trigger_deprecation_warnings(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """TOML containing removed fields (incremental_mode, rate_limit, copy_base
    at target-level and rate_limit at global-level) triggers a deprecation
    WARNING for each field, does NOT raise ConfigError, and produces valid
    default values for surviving fields."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'rate_limit = "100M"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "  incremental = true\n"
        '  incremental_mode = "file-copy"\n'
        '  rate_limit = "500K"\n'
        "  copy_base = true\n"
    )

    with caplog.at_level(logging.WARNING, logger="qsnap.config"):
        facade = ConfigFacade(config_file)

    # Each removed field triggers a deprecation WARNING naming the field.
    warnings_text = " ".join(caplog.messages)
    assert "incremental_mode" in warnings_text, "Expected deprecation warning for incremental_mode"
    assert "rate_limit" in warnings_text, "Expected deprecation warning for rate_limit"
    assert "copy_base" in warnings_text, "Expected deprecation warning for copy_base"

    # Verify the global rate_limit warning was emitted (global-level).
    assert any("rate_limit" in msg for msg in caplog.messages), (
        "Expected deprecation warning for global rate_limit"
    )

    # No ConfigError should have been raised — the config parses fine.
    vm = facade.get_vm("testvm")
    assert vm.name == "testvm"
    target = vm.targets[0]
    # TargetConfig carries valid defaults for surviving fields.
    assert isinstance(target.incremental, bool)
    assert target.incremental is True
    assert target.verify == "metadata"
    assert target.compress is True
    assert target.compression_type == "zstd"
    # GlobalConfig carries valid defaults.
    global_cfg = facade.get_global()
    assert global_cfg.compress is True
