"""Tests for conftest fixtures and test fixture TOML configs.

Covers the ``conftest-fixtures`` group from the test plan:
- ``make_target`` fixture defaults and overrides for ``compress`` / ``copy_base``.
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
def test_make_target_defaults_compress_true_copy_base_false(
    make_target,
) -> None:
    """make_target() defaults to compress=True, copy_base=False."""
    target = make_target()
    assert isinstance(target, TargetConfig)
    assert target.compress is True
    assert target.copy_base is False


@pytest.mark.unit
def test_make_target_compress_false_copy_base_true(
    make_target,
) -> None:
    """make_target(compress=False, copy_base=True) overrides defaults."""
    target = make_target(compress=False, copy_base=True)
    assert isinstance(target, TargetConfig)
    assert target.compress is False
    assert target.copy_base is True


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


# ──────────────────────────────────────────────────────────────────────────
# conftest fixture: mock_shell (rsync check)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_mock_shell_knows_rsync(mock_shell) -> None:
    """mock_shell fixture has 'which rsync' returning success."""
    result = mock_shell.run(["which", "rsync"], timeout=30)
    assert result.success is True
    assert "rsync" in result.stdout

    # Also verify 'which virsh' and 'which qemu-img' are configured.
    result_virsh = mock_shell.run(["which", "virsh"], timeout=30)
    assert result_virsh.success is True
    assert "virsh" in result_virsh.stdout

    result_qemu = mock_shell.run(["which", "qemu-img"], timeout=30)
    assert result_qemu.success is True
    assert "qemu-img" in result_qemu.stdout


# ──────────────────────────────────────────────────────────────────────────
# TOML fixture: bucket_driven.toml
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_bucket_driven_toml_parses_without_error() -> None:
    """bucket_driven.toml parses without error — uses compress/copy_base."""
    facade = ConfigFacade(FIXTURES / "bucket_driven.toml")
    vms = facade.get_vms()

    assert len(vms) == 2

    # vm_bucket: global compress=True, target compress=True, copy_base=False.
    vm_bucket = facade.get_vm("vm_bucket")
    assert vm_bucket.name == "vm_bucket"
    target1 = next(t for t in vm_bucket.targets if t.path == Path("/mnt/backup/vm_bucket"))
    assert target1.compress is True
    assert target1.copy_base is False

    # vm_no_compress: compress=False, copy_base=True.
    vm_nc = facade.get_vm("vm_no_compress")
    target2 = next(t for t in vm_nc.targets if t.path == Path("/mnt/backup/vm_no_compress"))
    assert target2.compress is False
    assert target2.copy_base is True

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
    # copy_base defaults to False.
    assert target.copy_base is False

    # vm_full_every_only has full_every=14d but no full_compress.
    # compress inherits from global (default True).
    vm_fe = facade.get_vm("vm_full_every_only")
    target_fe = vm_fe.targets[0]
    assert target_fe.compress is True  # global default


@pytest.mark.unit
def test_deprecated_fields_toml_full_every_ignored_in_behavior() -> None:
    """full_every in deprecated_fields.toml does not affect compress/copy_base
    — it is silently ignored at runtime."""
    facade = ConfigFacade(FIXTURES / "deprecated_fields.toml")

    # Both VMs should parse; the fact that full_every is present
    # must not cause errors or change compress/copy_base defaults.
    vm = facade.get_vm("vm_deprecated")
    target = vm.targets[0]
    assert target.incremental is True
    assert target.compress is True  # from full_compress mapping
    assert target.copy_base is False


# ──────────────────────────────────────────────────────────────────────────
# TOML fixture: full_backup.toml
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_full_backup_toml_parses_compress_and_copy_base() -> None:
    """full_backup.toml parses correctly with compress/copy_base fields."""
    facade = ConfigFacade(FIXTURES / "full_backup.toml")
    vms = facade.get_vms()
    assert len(vms) == 2

    # vm_with_full: explicit compress=true, copy_base=false.
    vm = facade.get_vm("vm_with_full")
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_with_full"))
    assert target.compress is True
    assert target.copy_base is False
    assert target.verify == "hash"

    # vm_no_full: no compress/copy_base set → defaults.
    vm2 = facade.get_vm("vm_no_full")
    target2 = vm2.targets[0]
    assert target2.compress is True  # global default
    assert target2.copy_base is False  # default


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
    assert critical.targets[0].copy_base is False

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
