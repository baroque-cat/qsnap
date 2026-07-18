"""Integration tests for ConfigFacade (parser + resolver).

Covers the ``config-parsing`` spec requirements:
- Multiple VMs from a single config.
- VM lookup by name (existing and non-existent).
- Example config parseable with all fields documented.
- preserve_min validation without buckets.
- compress / copy_base parsing (bucket-driven model).
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
def test_facade_parses_target_copy_base() -> None:
    """ConfigFacade parses copy_base=False from a [[vm.target]] section."""
    facade = ConfigFacade(FIXTURES / "full_backup.toml")
    vm = facade.get_vm("vm_with_full")
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_with_full"))
    assert target.copy_base is False


@pytest.mark.unit
def test_facade_target_compress_defaults_to_global() -> None:
    """When no compress is set on a target, it inherits the global default (True)."""
    facade = ConfigFacade(FIXTURES / "full_backup.toml")
    vm = facade.get_vm("vm_no_full")
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_no_full"))
    # vm_no_full does not set compress, so inherits global default True.
    assert target.compress is True
    # copy_base defaults to False.
    assert target.copy_base is False


# ──────────────────────────────────────────────────────────────────────────
# rate_limit parsing (global default + target override + validation)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_global_rate_limit_parsed() -> None:
    """ConfigFacade parses a top-level rate_limit='100M' into GlobalConfig."""
    facade = ConfigFacade(FIXTURES / "rate_limit_global.toml")
    assert facade.get_global().rate_limit == "100M"


@pytest.mark.unit
def test_invalid_rate_limit_raises_config_error() -> None:
    """An invalid global rate_limit='abc' raises ConfigError."""
    with pytest.raises(ConfigError, match="Invalid global rate_limit"):
        ConfigFacade(FIXTURES / "rate_limit_invalid.toml")


@pytest.mark.unit
def test_target_overrides_global_rate_limit() -> None:
    """A target-level rate_limit='500K' overrides the global '100M'."""
    facade = ConfigFacade(FIXTURES / "rate_limit_target_override.toml")
    vm = facade.get_vm("testvm")
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/testvm"))
    assert target.rate_limit == "500K"


@pytest.mark.unit
def test_target_inherits_global_rate_limit() -> None:
    """A target with no rate_limit inherits the global '100M'."""
    facade = ConfigFacade(FIXTURES / "rate_limit_global.toml")
    vm = facade.get_vm("testvm")
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/testvm"))
    assert target.rate_limit == "100M"


# ──────────────────────────────────────────────────────────────────────────
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
    assert target.copy_base is False


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
def test_facade_parses_full_verify_after_create_hash(tmp_path: Path) -> None:
    """ConfigFacade parses full_verify_after_create='hash' from the global section."""
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
    facade = ConfigFacade(config_file)
    assert facade.get_global().full_verify_after_create == "hash"


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
