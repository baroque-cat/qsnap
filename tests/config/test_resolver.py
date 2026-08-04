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
    """Global snapshot_preserve_min=24, VM sets 0 → resolves to 0 (disables floor)."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
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
