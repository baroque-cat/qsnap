"""Unit tests for ConfigFacade option inheritance resolution.

Covers the ``config-parsing`` spec requirement: option inheritance from
global → VM → target.  Uses ``tests/fixtures/configs/inheritance.toml``
and inline TOML for count-based chain_length / keep_generations inheritance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qsnap.config.facade import ConfigFacade

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "configs"


# ──────────────────────────────────────────────────────────────────────────
# Scenario 1: VM inherits chain_length from global
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_inherits_chain_length_from_global(tmp_path: Path) -> None:
    """VM without snapshot_chain_length inherits from global snapshot_chain_length=168."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "snapshot_chain_length = 168\n"
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
    vm = facade.get_vm("testvm")

    assert vm.snapshot_chain_length == 168


# ──────────────────────────────────────────────────────────────────────────
# Scenario 2: Target inherits chain_length from VM
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_target_inherits_chain_length_from_vm(tmp_path: Path) -> None:
    """Target without target_chain_length inherits from VM target_chain_length=200."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
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
        "\n"
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.target_chain_length == 200
    assert len(vm.targets) == 1
    assert vm.targets[0].target_chain_length == 200


# ──────────────────────────────────────────────────────────────────────────
# Scenario 3: Target overrides VM chain_length
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_target_overrides_vm_chain_length(tmp_path: Path) -> None:
    """Target overrides VM's target_chain_length: VM=200, target=150."""
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
    target = vm.targets[0]
    assert target.target_chain_length == 150
    assert target.target_chain_length != vm.target_chain_length


# ──────────────────────────────────────────────────────────────────────────
# Scenario 4: VM overrides global chain_length
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_overrides_global_chain_length(tmp_path: Path) -> None:
    """VM overrides global snapshot_chain_length: global=168, VM=336."""
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
    assert vm.snapshot_chain_length != global_cfg.snapshot_chain_length


# ──────────────────────────────────────────────────────────────────────────
# Scenario 5: Target overrides VM keep_generations
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_target_overrides_vm_keep_generations(tmp_path: Path) -> None:
    """Target overrides VM's target_keep_generations: VM=3, target=4."""
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
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "  target_keep_generations = 4\n"
        "\n"
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.target_keep_generations == 3
    target = vm.targets[0]
    assert target.target_keep_generations == 4
    assert target.target_keep_generations != vm.target_keep_generations


# ──────────────────────────────────────────────────────────────────────────
# Additional: Target inherits keep_generations from VM
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_target_inherits_keep_generations_from_vm(tmp_path: Path) -> None:
    """Target without target_keep_generations inherits from VM target_keep_generations=3."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "target_keep_generations = 3\n"
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "\n"
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.target_keep_generations == 3
    assert len(vm.targets) == 1
    assert vm.targets[0].target_keep_generations == 3


# ──────────────────────────────────────────────────────────────────────────
# Additional: VM inherits keep_generations from global
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_inherits_keep_generations_from_global(tmp_path: Path) -> None:
    """VM without target_keep_generations inherits from global target_keep_generations=2."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
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
    vm = facade.get_vm("testvm")

    assert vm.target_keep_generations == 2


# ──────────────────────────────────────────────────────────────────────────
# Scenario: VM inherits snapshot_preserve_min from global
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_inherits_snapshot_preserve_min_from_global(tmp_path: Path) -> None:
    """VM without snapshot_preserve_min inherits from global snapshot_preserve_min=24."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "snapshot_preserve_min = 24\n"
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
    vm = facade.get_vm("testvm")

    assert vm.snapshot_preserve_min == 24


# ──────────────────────────────────────────────────────────────────────────
# Scenario: VM overrides global snapshot_preserve_min
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_overrides_snapshot_preserve_min(tmp_path: Path) -> None:
    """VM overrides global snapshot_preserve_min: global=24, VM=48."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "snapshot_preserve_min = 24\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "snapshot_preserve_min = 48\n"
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    facade = ConfigFacade(config_file)
    global_cfg = facade.get_global()
    vm = facade.get_vm("testvm")

    assert global_cfg.snapshot_preserve_min == 24
    assert vm.snapshot_preserve_min == 48
    assert vm.snapshot_preserve_min != global_cfg.snapshot_preserve_min


# ──────────────────────────────────────────────────────────────────────────
# Scenario: VM sets snapshot_preserve_min to 0 (disables floor)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_sets_snapshot_preserve_min_to_zero(tmp_path: Path) -> None:
    """Global snapshot_preserve_min=24, VM sets 0 in steady mode → resolves to 0 (disables floor).

    ``snapshot_preserve_min = 0`` is a steady-mode-only concept — hysteresis
    validation requires ``snapshot_preserve_min >= 1``, so the config must
    opt into steady mode explicitly.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'snapshot_retention_mode = "steady"\n'
        "snapshot_preserve_min = 24\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "snapshot_preserve_min = 0\n"
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    facade = ConfigFacade(config_file)
    global_cfg = facade.get_global()
    vm = facade.get_vm("testvm")

    assert global_cfg.snapshot_preserve_min == 24
    assert vm.snapshot_preserve_min == 0


# ──────────────────────────────────────────────────────────────────────────
# Scenario: VM inherits free-space gate fields from global
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_inherits_free_space_check_from_global(tmp_path: Path) -> None:
    """VM without free-space fields inherits all three from the global section."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'free_space_check = "warn"\n'
        "free_space_reserve = 1073741824\n"
        "free_space_factor = 1.1\n"
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
    vm = facade.get_vm("testvm")

    assert global_cfg.free_space_check == "warn"
    assert global_cfg.free_space_reserve == 1073741824
    assert global_cfg.free_space_factor == 1.1
    assert vm.free_space_check == "warn"
    assert vm.free_space_reserve == 1073741824
    assert vm.free_space_factor == 1.1


@pytest.mark.unit
def test_vm_overrides_free_space_check_from_global(tmp_path: Path) -> None:
    """VM-level free_space_check overrides the global value."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'free_space_check = "strict"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'free_space_check = "off"\n'
        "free_space_reserve = 1048576\n"
        "\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    facade = ConfigFacade(config_file)
    global_cfg = facade.get_global()
    vm = facade.get_vm("testvm")

    assert global_cfg.free_space_check == "strict"
    assert vm.free_space_check == "off"
    assert vm.free_space_reserve == 1048576
    # Unset VM-level factor still inherits from the global default.
    assert vm.free_space_factor == global_cfg.free_space_factor


# ──────────────────────────────────────────────────────────────────────────
# Scenario: Valid snapshot_preserve_min accepted
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_valid_snapshot_preserve_min_accepted(tmp_path: Path) -> None:
    """TOML with snapshot_preserve_min=24 is accepted and stored."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "snapshot_preserve_min = 24\n"
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
    vm = facade.get_vm("testvm")

    assert global_cfg.snapshot_preserve_min == 24
    assert vm.snapshot_preserve_min == 24


# ──────────────────────────────────────────────────────────────────────────
# Scenario: Negative snapshot_preserve_min raises ConfigError
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_negative_snapshot_preserve_min_raises_config_error(
    tmp_path: Path,
) -> None:
    """TOML with snapshot_preserve_min=-1 raises ConfigError."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "snapshot_preserve_min = -1\n"
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
    from qsnap.config.facade import ConfigError

    with pytest.raises(ConfigError, match="snapshot_preserve_min"):
        ConfigFacade(config_file)


# ──────────────────────────────────────────────────────────────────────────
# vm-level-backup-engine-options: engine option inheritance (global → VM → target)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_vm_overrides_global_engine_option(tmp_path: Path) -> None:
    """VM-level compression_type overrides the global compression_type."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'compression_type = "zstd"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'compression_type = "zlib"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    facade = ConfigFacade(config_file)
    global_cfg = facade.get_global()
    vm = facade.get_vm("testvm")

    assert global_cfg.compression_type == "zstd"
    assert vm.compression_type == "zlib"
    assert vm.compression_type != global_cfg.compression_type


@pytest.mark.unit
def test_target_inherits_vm_engine_option(tmp_path: Path) -> None:
    """Target without convert_parallel inherits the VM-level convert_parallel=8."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "convert_parallel = 8\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "\n"
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.convert_parallel == 8
    assert len(vm.targets) == 1
    assert vm.targets[0].convert_parallel == 8


@pytest.mark.unit
def test_target_overrides_vm_engine_option(tmp_path: Path) -> None:
    """Target overrides the VM-level compression_type: VM=zlib, target=zstd."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'compression_type = "zstd"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'compression_type = "zlib"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        '  compression_type = "zstd"\n'
        "\n"
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.compression_type == "zlib"
    target = vm.targets[0]
    assert target.compression_type == "zstd"
    assert target.compression_type != vm.compression_type


@pytest.mark.unit
def test_target_inherits_vm_verify(tmp_path: Path) -> None:
    """Target without verify inherits the VM-level verify="check"."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'verify = "check"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "\n"
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.verify == "check"
    assert len(vm.targets) == 1
    assert vm.targets[0].verify == "check"


@pytest.mark.unit
def test_target_overrides_vm_verify(tmp_path: Path) -> None:
    """Target overrides the VM-level verify: VM=check, target=off."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'verify = "check"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        '  verify = "off"\n'
        "\n"
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.verify == "check"
    target = vm.targets[0]
    assert target.verify == "off"
    assert target.verify != vm.verify


@pytest.mark.unit
def test_vm_inherits_all_engine_options_from_global(tmp_path: Path) -> None:
    """VM without engine options inherits all five from global plus verify='metadata'."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "compress = false\n"
        'compression_type = "zlib"\n'
        "convert_parallel = 2\n"
        "convert_out_of_order = false\n"
        'backup_stall_timeout = "1h"\n'
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.compress is False
    assert vm.compression_type == "zlib"
    assert vm.convert_parallel == 2
    assert vm.convert_out_of_order is False
    assert vm.backup_stall_timeout == "1h"
    assert vm.verify == "metadata"


@pytest.mark.unit
def test_vm_engine_options_feed_target_resolution(tmp_path: Path) -> None:
    """VM-level compression_type and convert_parallel flow into targets."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        'compression_type = "zlib"\n'
        "convert_parallel = 8\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "\n"
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    target = vm.targets[0]
    assert target.compression_type == "zlib"
    assert target.convert_parallel == 8


@pytest.mark.unit
def test_target_inherits_vm_convert_out_of_order(tmp_path: Path) -> None:
    """Target without convert_out_of_order inherits the VM-level False."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "convert_out_of_order = false\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "\n"
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.convert_out_of_order is False
    assert len(vm.targets) == 1
    assert vm.targets[0].convert_out_of_order is False


# ──────────────────────────────────────────────────────────────────────────
# hysteresis-snapshot-retention: snapshot_retention_mode inheritance
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_snapshot_retention_mode_vm_override_wins(tmp_path: Path) -> None:
    """config-model scenario "VM override wins": global sets 'steady' and one
    VM sets 'hysteresis' with valid H/L — that VM resolves 'hysteresis'
    while all other VMs resolve 'steady'."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'snapshot_retention_mode = "steady"\n'
        "snapshot_chain_length = 72\n"
        "snapshot_preserve_min = 24\n"
        "\n"
        "[[vm]]\n"
        'name = "hyst_vm"\n'
        'snapshot_dir = "/tmp/snaps_hyst"\n'
        'snapshot_retention_mode = "hysteresis"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/hyst.qcow2"\n'
        "\n"
        "[[vm]]\n"
        'name = "steady_vm"\n'
        'snapshot_dir = "/tmp/snaps_steady"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/steady.qcow2"\n'
    )
    facade = ConfigFacade(config_file)

    assert facade.get_global().snapshot_retention_mode == "steady"
    hyst_vm = facade.get_vm("hyst_vm")
    assert hyst_vm.snapshot_retention_mode == "hysteresis"
    # The hysteresis VM inherits the global H/L that validate its bounds.
    assert hyst_vm.snapshot_chain_length == 72
    assert hyst_vm.snapshot_preserve_min == 24
    # Other VMs resolve the global default.
    assert facade.get_vm("steady_vm").snapshot_retention_mode == "steady"


@pytest.mark.unit
def test_max_commits_per_run_is_global_only(tmp_path: Path) -> None:
    """max_commits_per_run is global-only: it resolves on GlobalConfig, has
    no VM or target-level effect, and placing it in a [[vm]] section raises
    ConfigError with a cross-level hint."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "max_commits_per_run = 5\n"
        "\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
    )
    facade = ConfigFacade(config_file)

    # Global-only: the cap resolves at the global level.
    assert facade.get_global().max_commits_per_run == 5

    # No VM- or target-level attribute exists (no effect on either level).
    vm = facade.get_vm("testvm")
    assert not hasattr(vm, "max_commits_per_run")
    assert len(vm.targets) == 1
    assert not hasattr(vm.targets[0], "max_commits_per_run")

    # Placing the key in a [[vm]] section is rejected with a hint to [global].
    from qsnap.config.facade import ConfigError

    bad_config = tmp_path / "bad.toml"
    bad_config.write_text(
        "[[vm]]\n"
        'name = "testvm"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "max_commits_per_run = 3\n"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/tmp/test.qcow2"\n'
    )
    with pytest.raises(ConfigError) as exc_info:
        ConfigFacade(bad_config)
    message = str(exc_info.value)
    assert "max_commits_per_run" in message
    assert "[global]" in message, message
