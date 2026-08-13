"""Tests for conftest fixtures and test fixture TOML configs.

Covers the ``conftest-fixtures`` group from the test plan:
- ``make_target`` fixture defaults and overrides for ``compress``.
- ``make_global_config`` fixture defaults and overrides for ``compress``
  and count-based retention fields.
- Test fixture TOML files parse correctly through ``ConfigFacade``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qsnap.config.facade import ConfigError, ConfigFacade
from qsnap.models.config import GlobalConfig, TargetConfig

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "configs"


# ──────────────────────────────────────────────────────────────────────────
# conftest fixture: make_target
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_make_target_defaults_compress_true(
    make_target,
) -> None:
    """make_target() defaults to compress=True."""
    target = make_target()
    assert isinstance(target, TargetConfig)
    assert target.compress is True


@pytest.mark.unit
def test_make_target_compress_false(
    make_target,
) -> None:
    """make_target(compress=False) overrides default."""
    target = make_target(compress=False)
    assert isinstance(target, TargetConfig)
    assert target.compress is False


@pytest.mark.unit
def test_make_target_accepts_path_kwarg(make_target) -> None:
    """make_target accepts a custom path."""
    target = make_target(path="/custom/backup/path")
    assert target.path == Path("/custom/backup/path")


# ──────────────────────────────────────────────────────────────────────────
# conftest fixture: make_global_config
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_make_global_config_defaults_compress_true(
    make_global_config,
) -> None:
    """make_global_config() defaults to compress=True."""
    cfg = make_global_config()
    assert isinstance(cfg, GlobalConfig)
    assert cfg.compress is True


@pytest.mark.unit
def test_make_global_config_compress_false(
    make_global_config,
) -> None:
    """make_global_config(compress=False) overrides the default."""
    cfg = make_global_config(compress=False)
    assert isinstance(cfg, GlobalConfig)
    assert cfg.compress is False


@pytest.mark.unit
def test_make_global_config_chain_length_defaults(
    make_global_config,
) -> None:
    """make_global_config() has snapshot_chain_length=None and
    snapshot_preserve_min=0.

    NOTE: the fixture pins snapshot_preserve_min=0 EXPLICITLY (its factory
    parameter default) — this is NOT the GlobalConfig dataclass default,
    which is 24.  Tests using the fixture get the floor disabled unless
    they pass snapshot_preserve_min explicitly."""
    cfg = make_global_config()
    assert cfg.snapshot_chain_length is None
    assert cfg.target_chain_length is None
    assert cfg.target_keep_generations is None
    assert cfg.snapshot_preserve_min == 0


@pytest.mark.unit
def test_make_global_config_overrides_chain_length(
    make_global_config,
) -> None:
    """make_global_config(snapshot_chain_length=168) overrides the default."""
    cfg = make_global_config(
        snapshot_chain_length=168,
        target_chain_length=100,
        target_keep_generations=2,
    )
    assert cfg.snapshot_chain_length == 168
    assert cfg.target_chain_length == 100
    assert cfg.target_keep_generations == 2


# ──────────────────────────────────────────────────────────────────────────
# Scenario 1: qsnap.toml.example is parseable with all new fields
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_example_config_parseable() -> None:
    """The project's qsnap.toml.example is parseable with all count-based
    retention fields."""
    example_path = Path(__file__).resolve().parent.parent.parent / "qsnap.toml.example"
    facade = ConfigFacade(example_path)

    # Should have at least one VM (debiantest).
    vms = facade.get_vms()
    assert len(vms) >= 1

    # Verify global defaults.
    global_cfg = facade.get_global()
    # Count-based retention fields — defaults are 72/168/2 when commented out.
    assert global_cfg.snapshot_chain_length == 72
    assert global_cfg.target_chain_length == 168
    assert global_cfg.target_keep_generations == 2
    assert global_cfg.snapshot_preserve_min == 24
    # Free-space gate fields — defaults are strict/0/1.0 when commented out.
    assert global_cfg.free_space_check == "strict"
    assert global_cfg.free_space_reserve == 0
    assert global_cfg.free_space_factor == 1.0

    # Verify VM de facto.
    vm = facade.get_vm("debiantest")
    assert vm.name == "debiantest"
    assert len(vm.targets) >= 1
    target = vm.targets[0]
    assert target.path == Path("/mnt/backup/debiantest")
    # Defaults.
    assert target.compress is True
    # Count-based retention fields — inherited from global defaults (72/168/2).
    assert vm.snapshot_chain_length == 72
    assert vm.target_chain_length == 168
    assert vm.target_keep_generations == 2
    assert vm.snapshot_preserve_min == 24


# ──────────────────────────────────────────────────────────────────────────
# TOML fixture: bucket_driven.toml
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_bucket_driven_toml_parses_without_error() -> None:
    """bucket_driven.toml parses without error — uses compress."""
    facade = ConfigFacade(FIXTURES / "bucket_driven.toml")
    vms = facade.get_vms()

    assert len(vms) == 2

    # vm_bucket: global compress=True, target compress=True.
    vm_bucket = facade.get_vm("vm_bucket")
    assert vm_bucket.name == "vm_bucket"
    target1 = next(t for t in vm_bucket.targets if t.path == Path("/mnt/backup/vm_bucket"))
    assert target1.compress is True

    # vm_no_compress: compress=False.
    vm_nc = facade.get_vm("vm_no_compress")
    target2 = next(t for t in vm_nc.targets if t.path == Path("/mnt/backup/vm_no_compress"))
    assert target2.compress is False

    # Global compress is True.
    assert facade.get_global().compress is True


@pytest.mark.unit
def test_bucket_driven_toml_no_deprecated_fields() -> None:
    """bucket_driven.toml has no full_every or full_compress — no warnings."""
    import tomllib

    with open(FIXTURES / "bucket_driven.toml", "rb") as f:
        raw = tomllib.load(f)

    for vm in raw.get("vm", []):
        for target in vm.get("target", []):
            assert "full_every" not in target, (
                "bucket_driven.toml should not contain deprecated full_every"
            )
            assert "full_compress" not in target, (
                "bucket_driven.toml should not contain deprecated full_compress"
            )


# ──────────────────────────────────────────────────────────────────────────
# TOML fixture: deprecated_fields.toml
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_deprecated_fields_toml_parses_with_logging_warnings(caplog) -> None:
    """deprecated_fields.toml parses, emitting deprecation log messages for
    full_every and full_compress."""
    import logging

    # Capture all log messages at WARNING level from the qsnap.config logger.
    with caplog.at_level(logging.WARNING, logger="qsnap.config"):
        facade = ConfigFacade(FIXTURES / "deprecated_fields.toml")

    # Verify that deprecation warnings were logged.
    warnings_text = " ".join(caplog.messages)
    assert "full_every is deprecated" in warnings_text, (
        "Expected deprecation warning for full_every"
    )
    assert "full_compress is deprecated" in warnings_text, (
        "Expected deprecation warning for full_compress"
    )

    # Verify parsing still produces correct results.
    vms = facade.get_vms()
    assert len(vms) == 2

    vm_dep = facade.get_vm("vm_deprecated")
    target = vm_dep.targets[0]
    # full_compress=True → compress=True (mapped with warning).
    assert target.compress is True

    # vm_full_every_only has full_every=14d but no full_compress.
    # compress inherits from global (default True).
    vm_fe = facade.get_vm("vm_full_every_only")
    target_fe = vm_fe.targets[0]
    assert target_fe.compress is True  # global default


@pytest.mark.unit
def test_deprecated_fields_toml_full_every_ignored_in_behavior(caplog) -> None:
    """full_every and incremental in deprecated_fields.toml do not affect
    behavior — they are logged as deprecation WARNINGs and silently ignored
    at runtime."""
    import logging

    with caplog.at_level(logging.WARNING, logger="qsnap.config"):
        facade = ConfigFacade(FIXTURES / "deprecated_fields.toml")

    # Verify that incremental deprecation WARNING was logged.
    warnings_text = " ".join(caplog.messages)
    assert "incremental is deprecated" in warnings_text, (
        "Expected deprecation warning for incremental"
    )

    # Both VMs should parse; the fact that deprecated fields are present
    # must not cause errors or change compress defaults.
    vm = facade.get_vm("vm_deprecated")
    target = vm.targets[0]
    # incremental field is removed — verify it does not exist.
    with pytest.raises(AttributeError):
        _ = target.incremental
    assert target.compress is True  # from full_compress mapping


# ──────────────────────────────────────────────────────────────────────────
# TOML fixture: full_backup.toml
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_full_backup_toml_parses_compress() -> None:
    """full_backup.toml parses correctly with compress field."""
    facade = ConfigFacade(FIXTURES / "full_backup.toml")
    vms = facade.get_vms()
    assert len(vms) == 2

    # vm_with_full: explicit compress=true.
    vm = facade.get_vm("vm_with_full")
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_with_full"))
    assert target.compress is True
    assert target.verify == "compare"

    # vm_no_full: no compress set → defaults.
    vm2 = facade.get_vm("vm_no_full")
    target2 = vm2.targets[0]
    assert target2.compress is True  # global default


@pytest.mark.unit
def test_full_backup_toml_no_deprecated_fields() -> None:
    """full_backup.toml has no deprecated full_every or full_compress keys."""
    import tomllib

    with open(FIXTURES / "full_backup.toml", "rb") as f:
        raw = tomllib.load(f)

    for vm in raw.get("vm", []):
        for target in vm.get("target", []):
            assert "full_every" not in target, (
                "full_backup.toml should not contain deprecated full_every"
            )
            assert "full_compress" not in target, (
                "full_backup.toml should not contain deprecated full_compress"
            )


# ──────────────────────────────────────────────────────────────────────────
# TOML fixture: safety_fields.toml
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_safety_fields_toml_parses_correctly() -> None:
    """safety_fields.toml parses correctly with all fault-tolerance fields."""
    facade = ConfigFacade(FIXTURES / "safety_fields.toml")
    global_cfg = facade.get_global()

    # Global safety fields.
    assert global_cfg.auto_cleanup is True
    assert global_cfg.state_backup_count == 3
    assert global_cfg.chain_verify_before_commit is True
    assert global_cfg.chain_verify_after_commit is False
    assert global_cfg.deep_check_schedule == "weekly"

    # critical-vm with deep verify and extended retry.
    critical = facade.get_vm("critical-vm")
    assert critical.blockcommit_deep_verify is True
    assert len(critical.targets) == 1
    assert critical.targets[0].backup_retry_max == 5
    assert critical.targets[0].backup_retry_base == "5s"
    assert critical.targets[0].verify == "compare"
    assert critical.targets[0].compress is True

    # standard-vm with default deep verify and standard retry.
    standard = facade.get_vm("standard-vm")
    assert standard.blockcommit_deep_verify is False
    assert len(standard.targets) == 1
    assert standard.targets[0].backup_retry_max == 2
    assert standard.targets[0].backup_retry_base == "1s"


@pytest.mark.unit
def test_safety_fields_toml_no_deprecated_fields() -> None:
    """safety_fields.toml has no deprecated full_every or full_compress keys."""
    import tomllib

    with open(FIXTURES / "safety_fields.toml", "rb") as f:
        raw = tomllib.load(f)

    for vm in raw.get("vm", []):
        for target in vm.get("target", []):
            assert "full_every" not in target, (
                "safety_fields.toml should not contain deprecated full_every"
            )
            assert "full_compress" not in target, (
                "safety_fields.toml should not contain deprecated full_compress"
            )


# ──────────────────────────────────────────────────────────────────────────
# conftest fixture: make_global_config — compression_type / backup_stall_timeout
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_make_global_config_defaults_compression_type_zstd(
    make_global_config,
) -> None:
    """make_global_config() defaults to compression_type='zstd'."""
    cfg = make_global_config()
    assert isinstance(cfg, GlobalConfig)
    assert cfg.compression_type == "zstd"


@pytest.mark.unit
def test_make_global_config_defaults_backup_stall_timeout(
    make_global_config,
) -> None:
    """make_global_config() defaults to backup_stall_timeout='30m'."""
    cfg = make_global_config()
    assert isinstance(cfg, GlobalConfig)
    assert cfg.backup_stall_timeout == "30m"


@pytest.mark.unit
def test_make_global_config_overrides_compression_type(
    make_global_config,
) -> None:
    """make_global_config(compression_type='zlib') overrides the default."""
    cfg = make_global_config(compression_type="zlib")
    assert isinstance(cfg, GlobalConfig)
    assert cfg.compression_type == "zlib"


@pytest.mark.unit
def test_make_global_config_overrides_backup_stall_timeout(
    make_global_config,
) -> None:
    """make_global_config(backup_stall_timeout='1h') overrides the default."""
    cfg = make_global_config(backup_stall_timeout="1h")
    assert isinstance(cfg, GlobalConfig)
    assert cfg.backup_stall_timeout == "1h"


# ──────────────────────────────────────────────────────────────────────────
# conftest fixture: make_target — compression_type / backup_stall_timeout
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_make_target_defaults_compression_type_zstd(make_target) -> None:
    """make_target() defaults to compression_type='zstd'."""
    target = make_target()
    assert isinstance(target, TargetConfig)
    assert target.compression_type == "zstd"


@pytest.mark.unit
def test_make_target_defaults_backup_stall_timeout(make_target) -> None:
    """make_target() defaults to backup_stall_timeout='30m'."""
    target = make_target()
    assert isinstance(target, TargetConfig)
    assert target.backup_stall_timeout == "30m"


@pytest.mark.unit
def test_make_target_overrides_compression_type(make_target) -> None:
    """make_target(compression_type='zlib') overrides the default."""
    target = make_target(compression_type="zlib")
    assert isinstance(target, TargetConfig)
    assert target.compression_type == "zlib"


@pytest.mark.unit
def test_make_target_overrides_backup_stall_timeout(make_target) -> None:
    """make_target(backup_stall_timeout='1h') overrides the default."""
    target = make_target(backup_stall_timeout="1h")
    assert isinstance(target, TargetConfig)
    assert target.backup_stall_timeout == "1h"


# ──────────────────────────────────────────────────────────────────────────
# TOML fixture: zstd_config.toml
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_zstd_config_toml_parses_without_error() -> None:
    """zstd_config.toml parses without error — compression_type / stall fields."""
    facade = ConfigFacade(FIXTURES / "zstd_config.toml")
    vms = facade.get_vms()

    assert len(vms) == 2

    # Global: compression_type="zlib", backup_stall_timeout="1h".
    global_cfg = facade.get_global()
    assert global_cfg.compression_type == "zlib"
    assert global_cfg.backup_stall_timeout == "1h"

    # vm_inherit: inherits global defaults.
    vm_inherit = facade.get_vm("vm_inherit")
    assert vm_inherit.name == "vm_inherit"
    target_inherit = next(t for t in vm_inherit.targets if t.path == Path("/mnt/backup/vm_inherit"))
    assert target_inherit.compression_type == "zlib"
    assert target_inherit.backup_stall_timeout == "1h"

    # vm_override: one target overrides compression_type, another inherits.
    vm_override = facade.get_vm("vm_override")
    assert vm_override.name == "vm_override"

    target_zstd = next(
        t for t in vm_override.targets if t.path == Path("/mnt/backup/vm_override_zstd")
    )
    assert target_zstd.compression_type == "zstd"  # target-level override
    assert target_zstd.backup_stall_timeout == "30m"  # target-level override

    target_inh = next(
        t for t in vm_override.targets if t.path == Path("/mnt/backup/vm_override_inherit")
    )
    assert target_inh.compression_type == "zlib"  # inherited from global
    assert target_inh.backup_stall_timeout == "1h"  # inherited from global


@pytest.mark.unit
def test_zstd_config_toml_no_deprecated_fields() -> None:
    """zstd_config.toml has no deprecated full_every or full_compress keys."""
    import tomllib

    with open(FIXTURES / "zstd_config.toml", "rb") as f:
        raw = tomllib.load(f)

    for vm in raw.get("vm", []):
        for target in vm.get("target", []):
            assert "full_every" not in target, (
                "zstd_config.toml should not contain deprecated full_every"
            )
            assert "full_compress" not in target, (
                "zstd_config.toml should not contain deprecated full_compress"
            )


# ──────────────────────────────────────────────────────────────────────────
# conftest fixture: make_global_config — convert_parallel
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_make_global_config_defaults_convert_parallel(
    make_global_config,
) -> None:
    """make_global_config() defaults to convert_parallel=4."""
    cfg = make_global_config()
    assert isinstance(cfg, GlobalConfig)
    assert cfg.convert_parallel == 4


# ──────────────────────────────────────────────────────────────────────────
# conftest fixture: make_global_config — convert_out_of_order
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_make_global_config_defaults_convert_out_of_order(
    make_global_config,
) -> None:
    """make_global_config() defaults to convert_out_of_order=True."""
    cfg = make_global_config()
    assert isinstance(cfg, GlobalConfig)
    assert cfg.convert_out_of_order is True


# ──────────────────────────────────────────────────────────────────────────
# TOML fixture: engine_config.toml
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_engine_config_toml_parses_correctly() -> None:
    """engine_config.toml parses correctly — convert_parallel
    / convert_out_of_order fields with inheritance cascade."""
    facade = ConfigFacade(FIXTURES / "engine_config.toml")
    vms = facade.get_vms()

    assert len(vms) == 2

    # Global: convert_parallel=2, convert_out_of_order=false.
    global_cfg = facade.get_global()
    assert global_cfg.convert_parallel == 2
    assert global_cfg.convert_out_of_order is False

    # vm_inherit: inherits both from global.
    vm_inherit = facade.get_vm("vm_inherit")
    assert vm_inherit.name == "vm_inherit"
    target_inherit = next(t for t in vm_inherit.targets if t.path == Path("/mnt/backup/vm_inherit"))
    assert target_inherit.convert_parallel == 2
    assert target_inherit.convert_out_of_order is False

    # vm_override: one target overrides, another inherits.
    vm_override = facade.get_vm("vm_override")
    assert vm_override.name == "vm_override"

    target_convert = next(
        t for t in vm_override.targets if t.path == Path("/mnt/backup/vm_override_convert")
    )
    assert target_convert.convert_parallel == 8
    assert target_convert.convert_out_of_order is True

    target_inh = next(
        t for t in vm_override.targets if t.path == Path("/mnt/backup/vm_override_inherit")
    )
    assert target_inh.convert_parallel == 2
    assert target_inh.convert_out_of_order is False


# ──────────────────────────────────────────────────────────────────────────
# conftest fixture: make_global_config — snapshot_preserve_min
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_make_global_config_snapshot_preserve_min() -> None:
    """GlobalConfig(snapshot_preserve_min=24) stores the value correctly.

    NOTE: the make_global_config fixture accepts snapshot_preserve_min as
    a parameter (default 0 — the floor disabled).  This test uses
    GlobalConfig directly to verify the dataclass field."""
    cfg = GlobalConfig(snapshot_preserve_min=24)
    assert cfg.snapshot_preserve_min == 24


@pytest.mark.unit
def test_make_global_config_accepts_snapshot_preserve_min(make_global_config) -> None:
    """make_global_config(snapshot_preserve_min=48) forwards the kwarg —
    the fixture default pins 0, an explicit value overrides it."""
    cfg = make_global_config(snapshot_preserve_min=48)
    assert cfg.snapshot_preserve_min == 48


# ──────────────────────────────────────────────────────────────────────────
# TOML fixture: vm_engine_options.toml — VM-level backup engine options
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_engine_options_toml_parses() -> None:
    """vm_engine_options.toml parses — VM-level engine options override
    globals and are inherited by targets.

    - vm_all_vm_level: all six engine options set at VM level; the target
      inherits every value.
    - vm_global_inherit: no engine options set — VM and target inherit
      the non-standard global defaults (verify defaults to "metadata"
      at the VM level because no global verify key exists).
    """
    facade = ConfigFacade(FIXTURES / "vm_engine_options.toml")
    vms = facade.get_vms()

    assert len(vms) == 3

    # vm_all_vm_level: all six options at VM level.
    vm_all = facade.get_vm("vm_all_vm_level")
    assert vm_all.compress is True
    assert vm_all.compression_type == "zstd"
    assert vm_all.convert_parallel == 8
    assert vm_all.convert_out_of_order is True
    assert vm_all.backup_stall_timeout == "30m"
    assert vm_all.verify == "compare"

    # Target inherits the VM values.
    assert len(vm_all.targets) == 1
    target_all = vm_all.targets[0]
    assert target_all.compress is True
    assert target_all.compression_type == "zstd"
    assert target_all.convert_parallel == 8
    assert target_all.convert_out_of_order is True
    assert target_all.backup_stall_timeout == "30m"
    assert target_all.verify == "compare"

    # vm_global_inherit: everything inherited from global defaults.
    vm_inh = facade.get_vm("vm_global_inherit")
    assert vm_inh.compress is False
    assert vm_inh.compression_type == "zlib"
    assert vm_inh.convert_parallel == 2
    assert vm_inh.convert_out_of_order is False
    assert vm_inh.backup_stall_timeout == "1h"
    assert vm_inh.verify == "metadata"

    # Target inherits from VM.
    assert len(vm_inh.targets) == 1
    target_inh = vm_inh.targets[0]
    assert target_inh.compress is False
    assert target_inh.compression_type == "zlib"
    assert target_inh.convert_parallel == 2
    assert target_inh.convert_out_of_order is False
    assert target_inh.backup_stall_timeout == "1h"
    assert target_inh.verify == "metadata"


@pytest.mark.unit
def test_vm_engine_options_toml_target_inheritance() -> None:
    """vm_engine_options.toml — vm_target_override resolves target-level
    overrides on top of VM-level engine options.

    The VM pins the global values explicitly; the target overrides
    compression_type, convert_parallel, and backup_stall_timeout while
    inheriting compress, convert_out_of_order, and verify from the VM.
    """
    facade = ConfigFacade(FIXTURES / "vm_engine_options.toml")

    vm_ovr = facade.get_vm("vm_target_override")

    # VM-level engine options.
    assert vm_ovr.compress is False
    assert vm_ovr.compression_type == "zlib"
    assert vm_ovr.convert_parallel == 2
    assert vm_ovr.convert_out_of_order is False
    assert vm_ovr.backup_stall_timeout == "1h"
    assert vm_ovr.verify == "metadata"

    # Target overrides some options.
    assert len(vm_ovr.targets) == 1
    target_ovr = vm_ovr.targets[0]
    assert target_ovr.compression_type == "zstd"
    assert target_ovr.convert_parallel == 4
    assert target_ovr.backup_stall_timeout == "30m"

    # Target inherits the rest from the VM.
    assert target_ovr.compress is False
    assert target_ovr.convert_out_of_order is False
    assert target_ovr.verify == "metadata"


@pytest.mark.unit
def test_make_vm_config_forwards_engine_option_kwargs(make_vm_config) -> None:
    """make_vm_config forwards backup engine option kwargs to VMConfig.

    The conftest helper accepts compress, compression_type,
    convert_parallel, convert_out_of_order, backup_stall_timeout, and
    verify as **kwargs and passes them straight to VMConfig.
    """
    vm = make_vm_config(
        "testvm",
        compress=False,
        compression_type="zlib",
        convert_parallel=8,
        convert_out_of_order=False,
        backup_stall_timeout="1h",
        verify="compare",
    )
    assert vm.compress is False
    assert vm.compression_type == "zlib"
    assert vm.convert_parallel == 8
    assert vm.convert_out_of_order is False
    assert vm.backup_stall_timeout == "1h"
    assert vm.verify == "compare"


# ──────────────────────────────────────────────────────────────────────────
# TOML fixtures: hysteresis_mode.toml / hysteresis_invalid.toml
# (hysteresis-snapshot-retention)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_hysteresis_mode_fixture_parses() -> None:
    """hysteresis_mode.toml parses: global H=72/L=24 + a cap, and a VM
    overriding the mode back to 'steady'."""
    facade = ConfigFacade(FIXTURES / "hysteresis_mode.toml")

    global_cfg = facade.get_global()
    assert global_cfg.snapshot_retention_mode == "hysteresis"
    assert global_cfg.snapshot_chain_length == 72
    assert global_cfg.snapshot_preserve_min == 24
    assert global_cfg.max_commits_per_run == 4

    # hyst_vm inherits the global hysteresis mode with valid H/L.
    hyst_vm = facade.get_vm("hyst_vm")
    assert hyst_vm.snapshot_retention_mode == "hysteresis"
    assert hyst_vm.snapshot_chain_length == 72
    assert hyst_vm.snapshot_preserve_min == 24

    # steady_vm overrides the global mode back to "steady".
    steady_vm = facade.get_vm("steady_vm")
    assert steady_vm.snapshot_retention_mode == "steady"


@pytest.mark.unit
def test_hysteresis_invalid_fixture_rejected() -> None:
    """hysteresis_invalid.toml (H=24/L=48) is rejected with a ConfigError
    naming both resolved values."""
    with pytest.raises(ConfigError, match="snapshot_chain_length=24"):
        ConfigFacade(FIXTURES / "hysteresis_invalid.toml")
