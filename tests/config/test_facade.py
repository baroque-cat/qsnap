"""Integration tests for ConfigFacade (parser + resolver).

Covers the ``config-parsing`` spec requirements:
- Multiple VMs from a single config.
- VM lookup by name (existing and non-existent).
- Count-based retention (chain_length, keep_generations) parsing and validation.
- Inheritance resolution (global → VM → target).
- Deprecation warnings for old fields.
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
# Scenario 1: Global chain_length parsed from TOML
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_global_chain_length_parsed(tmp_path: Path) -> None:
    """TOML with snapshot_chain_length=168 parses correctly."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "snapshot_chain_length = 168\n"
        "target_chain_length = 100\n"
        "target_keep_generations = 2\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    facade = ConfigFacade(config_file)
    global_cfg = facade.get_global()

    assert global_cfg.snapshot_chain_length == 168
    assert global_cfg.target_chain_length == 100
    assert global_cfg.target_keep_generations == 2


# ──────────────────────────────────────────────────────────────────────────
# Scenario 2: VM chain_length overrides global
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_chain_length_overrides_global(tmp_path: Path) -> None:
    """VM-level snapshot_chain_length=336 overrides global snapshot_chain_length=168."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "snapshot_chain_length = 168\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "snapshot_chain_length = 336\n"
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    facade = ConfigFacade(config_file)
    global_cfg = facade.get_global()
    vm = facade.get_vm("testvm")

    assert global_cfg.snapshot_chain_length == 168
    assert vm.snapshot_chain_length == 336


# ──────────────────────────────────────────────────────────────────────────
# Scenario 3: Target chain_length overrides VM
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_target_chain_length_overrides_vm(tmp_path: Path) -> None:
    """Target-level target_chain_length=150 overrides VM-level target_chain_length=200."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "target_chain_length = 100\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "target_chain_length = 200\n"
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "  target_chain_length = 150\n"
        "\n"
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.target_chain_length == 200
    assert len(vm.targets) == 1
    assert vm.targets[0].target_chain_length == 150


# ──────────────────────────────────────────────────────────────────────────
# Scenario 4: Valid chain_length=1 accepted
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_valid_chain_length_accepted(tmp_path: Path) -> None:
    """chain_length=1 is accepted (minimum valid value)."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "snapshot_chain_length = 1\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    facade = ConfigFacade(config_file)
    assert facade.get_global().snapshot_chain_length == 1


# ──────────────────────────────────────────────────────────────────────────
# Scenario 5: chain_length=0 rejected (when explicitly set in TOML)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_zero_chain_length_rejected(tmp_path: Path) -> None:
    """chain_length=0 when explicitly set in TOML is rejected by ConfigFacade validation."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "snapshot_chain_length = 0\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    with pytest.raises(ConfigError, match="snapshot_chain_length must be >= 1"):
        ConfigFacade(config_file)


# ──────────────────────────────────────────────────────────────────────────
# Scenario 6: Negative keep_generations rejected
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_negative_keep_generations_rejected(tmp_path: Path) -> None:
    """keep_generations=-1 is rejected by ConfigFacade validation."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "target_keep_generations = -1\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    with pytest.raises(ConfigError, match="target_keep_generations must be >= 1"):
        ConfigFacade(config_file)


# ── Also reject negative chain_length ────────────────────────────────────


@pytest.mark.unit
def test_negative_chain_length_rejected(tmp_path: Path) -> None:
    """target_chain_length=-1 is rejected by ConfigFacade validation."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "target_chain_length = -1\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    with pytest.raises(ConfigError, match="target_chain_length must be >= 1"):
        ConfigFacade(config_file)


# ── chain_length must be integer ──────────────────────────────────────────


@pytest.mark.unit
def test_non_integer_chain_length_rejected(tmp_path: Path) -> None:
    """snapshot_chain_length='abc' (non-integer) is rejected."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'snapshot_chain_length = "abc"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    with pytest.raises(ConfigError, match="snapshot_chain_length must be an integer"):
        ConfigFacade(config_file)


# ──────────────────────────────────────────────────────────────────────────
# Scenario 7: full_every deprecation warning (verify still works)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_full_every_deprecation_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """full_every in TOML triggers a deprecation WARNING (count-driven)."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        '  full_every = "14d"\n'
        "\n"
    )

    with caplog.at_level(logging.WARNING, logger="qsnap.config"):
        facade = ConfigFacade(config_file)

    # Verify deprecation WARNING was emitted.
    warnings_text = " ".join(caplog.messages)
    assert "full_every is deprecated" in warnings_text, (
        "Expected deprecation warning for full_every"
    )

    # Verify parsing still succeeds — full_every is ignored.
    vm = facade.get_vm("testvm")
    assert vm.name == "testvm"
    assert len(vm.targets) == 1


# ──────────────────────────────────────────────────────────────────────────
# Scenario 8: Global safety fields parsed (verify still works)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_global_safety_fields_parsed(tmp_path: Path) -> None:
    """ConfigFacade parses all global fault-tolerance safety fields from TOML."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "auto_cleanup = false\n"
        "state_backup_count = 5\n"
        "chain_verify_before_commit = false\n"
        "chain_verify_after_commit = true\n"
        'deep_check_schedule = "monthly"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    facade = ConfigFacade(config_file)
    global_cfg = facade.get_global()

    assert global_cfg.auto_cleanup is False
    assert global_cfg.state_backup_count == 5
    assert global_cfg.chain_verify_before_commit is False
    assert global_cfg.chain_verify_after_commit is True
    assert global_cfg.deep_check_schedule == "monthly"


# ──────────────────────────────────────────────────────────────────────────
# Scenario 9: Target compress parsed (verify still works)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_target_compress_parsed() -> None:
    """ConfigFacade parses compress=True from a [[vm.target]] section."""
    facade = ConfigFacade(FIXTURES / "full_backup.toml")
    vm = facade.get_vm("vm_with_full")
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_with_full"))
    assert target.compress is True


# ──────────────────────────────────────────────────────────────────────────
# Scenario 10: Target retry fields parsed (verify still works)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_target_retry_fields_parsed(tmp_path: Path) -> None:
    """ConfigFacade parses backup_retry_max and backup_retry_base from a [[vm.target]] section."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "  backup_retry_max = 5\n"
        '  backup_retry_base = "10s"\n'
        "\n"
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")
    assert len(vm.targets) == 1
    target = vm.targets[0]
    assert target.backup_retry_max == 5
    assert target.backup_retry_base == "10s"


# ──────────────────────────────────────────────────────────────────────────
# Additional validation: zero keep_generations rejected
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_zero_keep_generations_rejected(tmp_path: Path) -> None:
    """target_keep_generations=0 is rejected (minimum is 1)."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "target_keep_generations = 0\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    with pytest.raises(ConfigError, match="target_keep_generations must be >= 1"):
        ConfigFacade(config_file)


# ──────────────────────────────────────────────────────────────────────────
# Additional: VM-level target_chain_length overrides global
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_target_chain_length_overrides_global(tmp_path: Path) -> None:
    """VM-level target_chain_length=200 overrides global target_chain_length=100."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "target_chain_length = 100\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "target_chain_length = 200\n"
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    facade = ConfigFacade(config_file)
    global_cfg = facade.get_global()
    vm = facade.get_vm("testvm")

    assert global_cfg.target_chain_length == 100
    assert vm.target_chain_length == 200


# ──────────────────────────────────────────────────────────────────────────
# Additional: VM keep_generations overrides global
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_keep_generations_overrides_global(tmp_path: Path) -> None:
    """VM-level target_keep_generations=3 overrides global target_keep_generations=2."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "target_keep_generations = 2\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "target_keep_generations = 3\n"
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    facade = ConfigFacade(config_file)
    global_cfg = facade.get_global()
    vm = facade.get_vm("testvm")

    assert global_cfg.target_keep_generations == 2
    assert vm.target_keep_generations == 3


# ──────────────────────────────────────────────────────────────────────────
# Additional: deprecated retention fields emit WARNING only (not error)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_deprecated_snapshot_preserve_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """snapshot_preserve in TOML triggers deprecation WARNING, not ConfigError."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'snapshot_preserve = "24h 7d"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    with caplog.at_level(logging.WARNING, logger="qsnap.config"):
        facade = ConfigFacade(config_file)

    # Verify deprecation warning was emitted.
    deprecation_msgs = [m for m in caplog.messages if "deprecated" in m.lower()]
    assert len(deprecation_msgs) > 0, "Expected deprecation WARNING for snapshot_preserve"
    assert any("snapshot_preserve" in m for m in deprecation_msgs), (
        "WARNING should mention snapshot_preserve"
    )

    # Parsing succeeds despite deprecated field.
    assert facade.get_vm("testvm").name == "testvm"


# ──────────────────────────────────────────────────────────────────────────
# Additional: VM-level chain_length validation (reject 0 at VM level)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_zero_chain_length_rejected(tmp_path: Path) -> None:
    """VM-level snapshot_chain_length=0 is rejected."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "snapshot_chain_length = 0\n"
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    with pytest.raises(ConfigError, match="snapshot_chain_length must be >= 1"):
        ConfigFacade(config_file)


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
# incremental deprecation WARNING
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_incremental_toml_key_logs_deprecation_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Parse a TOML config with ``incremental = true`` in a target; verify a
    WARNING is logged with message containing 'deprecated' and 'bitmap-based',
    the config parses successfully (no error raised), and the target is created
    without an ``incremental`` attribute."""
    import logging

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "  incremental = true\n"
        "\n"
    )

    with caplog.at_level(logging.WARNING, logger="qsnap.config"):
        facade = ConfigFacade(config_file)

    # Verify deprecation WARNING was emitted.
    warnings_text = " ".join(caplog.messages)
    assert "incremental is deprecated" in warnings_text, (
        f"Expected 'incremental is deprecated' in WARNINGs, got: {warnings_text!r}"
    )
    assert "bitmap-based" in warnings_text, (
        f"Expected 'bitmap-based' in WARNINGs, got: {warnings_text!r}"
    )

    # Verify parsing succeeds — no error raised.
    vm = facade.get_vm("testvm")
    assert vm.name == "testvm"
    assert len(vm.targets) == 1
    target = vm.targets[0]
    assert target.path == Path("/mnt/backup/testvm")

    # Verify the target has no 'incremental' attribute.
    with pytest.raises(AttributeError):
        _ = target.incremental


# ──────────────────────────────────────────────────────────────────────────
# Multi-disk parsing & validation
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_multi_disk_fixture_parses():
    """multi_disk.toml parses into a VMConfig with correct per-disk configs.

    - VM has two disks (vda, vdb).
    - vda has no per-disk snapshot_dir override → inherits VM-level dir.
    - vdb has its own snapshot_dir override.
    - Both disks have correct targets and base_images.
    - The shared [[vm.target]] is present.
    """
    facade = ConfigFacade(FIXTURES / "multi_disk.toml")
    vm = facade.get_vm("multi_disk_vm")

    assert vm.name == "multi_disk_vm"
    assert len(vm.disks) == 2

    # vda — inherits VM-level snapshot_dir.
    vda = vm.get_disk("vda")
    assert vda is not None
    assert vda.target == "vda"
    assert vda.base_image == Path("/var/lib/libvirt/images/multi_disk_vm_vda.qcow2")
    assert vda.snapshot_dir is None  # inherits from VM
    assert vm.snapshot_dir_for(vda) == Path("/var/lib/libvirt/snapshots/multi_disk_vm")

    # vdb — has its own per-disk snapshot_dir override.
    vdb = vm.get_disk("vdb")
    assert vdb is not None
    assert vdb.target == "vdb"
    assert vdb.base_image == Path("/var/lib/libvirt/images/multi_disk_vm_vdb.qcow2")
    assert vdb.snapshot_dir == Path("/var/lib/libvirt/snapshots/multi_disk_vm_vdb")
    assert vm.snapshot_dir_for(vdb) == Path("/var/lib/libvirt/snapshots/multi_disk_vm_vdb")

    # Shared target is present.
    assert len(vm.targets) == 1
    assert vm.targets[0].path == Path("/mnt/backup/multi_disk_vm")


@pytest.mark.unit
def test_multi_disk_shared_snapshot_dir_rejected(tmp_path: Path):
    """Two disks inheriting the same VM-level snapshot_dir raise ConfigError.

    Both vda and vdb have no per-disk override, so both resolve to the
    VM-level directory.  The validation must detect the conflict and
    raise ConfigError with "share snapshot_dir".
    """
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/vda.qcow2"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vdb"\n'
        '  base_image = "/tmp/vdb.qcow2"\n'
    )
    with pytest.raises(ConfigError, match="share snapshot_dir"):
        ConfigFacade(config_file)


@pytest.mark.unit
def test_multi_disk_identical_per_disk_override_rejected(tmp_path: Path):
    """Two disks with the same per-disk snapshot_dir override raise ConfigError.

    Even though both explicitly set snapshot_dir, if they point to the
    same directory the validation must detect the conflict.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/vda.qcow2"\n'
        '  snapshot_dir = "/tmp/shared_snaps"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vdb"\n'
        '  base_image = "/tmp/vdb.qcow2"\n'
        '  snapshot_dir = "/tmp/shared_snaps"\n'
    )
    with pytest.raises(ConfigError, match="share snapshot_dir"):
        ConfigFacade(config_file)


@pytest.mark.unit
def test_multi_disk_trailing_slash_equivalent_path_detected(tmp_path: Path):
    """Paths differing only by trailing slash are detected as shared.

    Uses os.path.normpath, so '/tmp/snaps' and '/tmp/snaps/' are
    considered the same directory.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/vda.qcow2"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vdb"\n'
        '  base_image = "/tmp/vdb.qcow2"\n'
        '  snapshot_dir = "/tmp/snaps/"\n'
    )
    with pytest.raises(ConfigError, match="share snapshot_dir"):
        ConfigFacade(config_file)


# ──────────────────────────────────────────────────────────────────────────
# change_detection_mode parse-time default
# ──────────────────────────────────────────────────────────────────────────


def _write_single_disk_config(tmp_path: Path, extra_vm_keys: str = "") -> Path:
    """Write a minimal single-disk TOML config, optionally with extra [[vm]] keys."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        f"{extra_vm_keys}"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/vda.qcow2"\n'
    )
    return config_file


@pytest.mark.unit
def test_change_detection_mode_default_parses_as_allocation_map(tmp_path: Path):
    """A [[vm]] without change_detection_mode parses to the spec default.

    The parser fallback must match the VMConfig dataclass default and the
    change-detection spec: "allocation-map".
    """
    config_file = _write_single_disk_config(tmp_path)
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.change_detection_mode == "allocation-map"


@pytest.mark.unit
def test_change_detection_mode_explicit_allocation_size_preserved(tmp_path: Path):
    """An explicit change_detection_mode = "allocation-size" passes through."""
    config_file = _write_single_disk_config(tmp_path, 'change_detection_mode = "allocation-size"\n')
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.change_detection_mode == "allocation-size"


# ──────────────────────────────────────────────────────────────────────────
# Proactive free-space gate fields — parsing and validation (design D5/D16)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_free_space_fields_parsed() -> None:
    """free_space_fields.toml parses valid free-space gate fields into GlobalConfig."""
    facade = ConfigFacade(FIXTURES / "free_space_fields.toml")
    global_cfg = facade.get_global()

    assert global_cfg.free_space_check == "warn"
    assert global_cfg.free_space_reserve == 1073741824
    assert global_cfg.free_space_factor == 1.1

    # VM inherits the global values when it omits them.
    vm = facade.get_vm("testvm")
    assert vm.free_space_check == "warn"
    assert vm.free_space_reserve == 1073741824
    assert vm.free_space_factor == 1.1


@pytest.mark.unit
def test_invalid_free_space_check_raises_config_error() -> None:
    """free_space_check='hard' raises ConfigError naming strict/warn/off."""
    with pytest.raises(ConfigError, match="strict.*warn.*off"):
        ConfigFacade(FIXTURES / "invalid_free_space_check.toml")


@pytest.mark.unit
def test_negative_free_space_reserve_raises_config_error() -> None:
    """free_space_reserve=-1 raises ConfigError indicating >= 0."""
    with pytest.raises(ConfigError, match="free_space_reserve must be >= 0"):
        ConfigFacade(FIXTURES / "negative_free_space_reserve.toml")


@pytest.mark.unit
def test_free_space_factor_below_one_raises_config_error() -> None:
    """free_space_factor=0.5 raises ConfigError indicating >= 1.0."""
    with pytest.raises(ConfigError, match="free_space_factor must be >= 1.0"):
        ConfigFacade(FIXTURES / "low_free_space_factor.toml")


@pytest.mark.unit
def test_free_space_fields_absent_use_defaults(tmp_path: Path) -> None:
    """A config omitting all three free-space fields uses strict/0/1.0 defaults."""
    config_file = _write_single_disk_config(tmp_path)
    facade = ConfigFacade(config_file)
    global_cfg = facade.get_global()

    assert global_cfg.free_space_check == "strict"
    assert global_cfg.free_space_reserve == 0
    assert global_cfg.free_space_factor == 1.0

    # VM-level fields resolve from the global defaults.
    vm = facade.get_vm("testvm")
    assert vm.free_space_check == "strict"
    assert vm.free_space_reserve == 0
    assert vm.free_space_factor == 1.0


@pytest.mark.unit
def test_preserve_min_default_resolves_to_48() -> None:
    """preserve_min_default.toml omits snapshot_preserve_min at all levels;
    both GlobalConfig and VMConfig resolve to the default 48."""
    facade = ConfigFacade(FIXTURES / "preserve_min_default.toml")
    global_cfg = facade.get_global()
    vm = facade.get_vm("testvm")

    assert global_cfg.snapshot_preserve_min == 48
    assert vm.snapshot_preserve_min == 48


@pytest.mark.unit
def test_global_fields_toml_free_space_assertions() -> None:
    """global_fields.toml carries free-space fields alongside all other globals."""
    facade = ConfigFacade(FIXTURES / "global_fields.toml")
    global_cfg = facade.get_global()

    assert global_cfg.free_space_check == "warn"
    assert global_cfg.free_space_reserve == 1073741824
    assert global_cfg.free_space_factor == 1.1
