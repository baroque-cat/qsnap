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
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
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
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "target_chain_length = 200\n"
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
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
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "target_chain_length = 200\n"
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "  target_chain_length = 150\n"
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
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "snapshot_chain_length = 336\n"
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
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "target_keep_generations = 3\n"
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
        "  target_keep_generations = 4\n"
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
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
        "target_keep_generations = 3\n"
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/testvm"\n'
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
        'base_image = "/tmp/test.qcow2"\n'
        'snapshot_dir = "/tmp/snaps"\n'
    )
    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")

    assert vm.target_keep_generations == 2
