"""Tests for conftest fixtures and test fixture TOML configs.

Covers the ``conftest-fixtures`` group from the test plan:
- ``make_target`` fixture defaults and overrides for ``compress``.
- ``make_global_config`` fixture defaults and overrides for ``compress``.
- Test fixture TOML files parse correctly through ``ConfigFacade``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qsnap.config.facade import ConfigFacade
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
def test_deprecated_fields_toml_full_every_ignored_in_behavior() -> None:
    """full_every in deprecated_fields.toml does not affect compress
    — it is silently ignored at runtime."""
    facade = ConfigFacade(FIXTURES / "deprecated_fields.toml")

    # Both VMs should parse; the fact that full_every is present
    # must not cause errors or change compress defaults.
    vm = facade.get_vm("vm_deprecated")
    target = vm.targets[0]
    assert target.incremental is True
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
    assert target.verify == "hash"

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
    assert critical.snapshot_deep_verify is True
    assert len(critical.targets) == 1
    assert critical.targets[0].backup_retry_max == 5
    assert critical.targets[0].backup_retry_base == "5s"
    assert critical.targets[0].verify == "full"
    assert critical.targets[0].compress is True

    # standard-vm with default deep verify and standard retry.
    standard = facade.get_vm("standard-vm")
    assert standard.blockcommit_deep_verify is False
    assert standard.snapshot_deep_verify is False
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
