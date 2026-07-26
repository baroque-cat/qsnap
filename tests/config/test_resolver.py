"""Unit tests for ConfigFacade option inheritance resolution.

Covers the ``config-parsing`` spec requirement: option inheritance from
global → VM → target.  Uses ``tests/fixtures/configs/inheritance.toml``
which defines:

- Global: ``snapshot_preserve = "24h 2d"``, ``target_preserve = "20d 10w"``
- ``vm_override``: overrides both (``snapshot_preserve = "48h 4d"``,
  ``target_preserve = "20d 10w"``)
  - target ``vm_override_inherit``: no override → inherits VM value
  - target ``vm_override_override``: ``target_preserve = "10d 5w"``
- ``vm_inherit``: inherits both globals

Verify always defaults to ``"metadata"``
(there is no mode-dependent default)."""

from __future__ import annotations

from pathlib import Path

import pytest

from qsnap.config.facade import ConfigFacade

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "configs"


@pytest.mark.unit
def test_vm_overrides_global_retention() -> None:
    """A VM-level snapshot_preserve overrides the global default."""
    facade = ConfigFacade(FIXTURES / "inheritance.toml")
    global_cfg = facade.get_global()
    vm = facade.get_vm("vm_override")

    # Global snapshot_preserve is "24h 2d"; the VM overrides to "48h 4d".
    assert global_cfg.snapshot_preserve == "24h 2d"
    assert vm.snapshot_preserve == "48h 4d"
    assert vm.snapshot_preserve != global_cfg.snapshot_preserve

    # VM also explicitly sets target_preserve at the VM level.
    assert vm.target_preserve == "20d 10w"


@pytest.mark.unit
def test_target_inherits_vm_retention() -> None:
    """A target without its own target_preserve inherits the VM-level value."""
    facade = ConfigFacade(FIXTURES / "inheritance.toml")
    vm = facade.get_vm("vm_override")

    # The first target does not override retention → inherits VM's value.
    inherit_target = vm.targets[0]
    assert inherit_target.path == Path("/mnt/backup/vm_override_inherit")

    # VM-level target_preserve is "20d 10w"; the target inherits it.
    assert vm.target_preserve == "20d 10w"
    assert inherit_target.target_preserve is not None
    assert inherit_target.target_preserve == vm.target_preserve
    assert inherit_target.target_preserve == "20d 10w"


@pytest.mark.unit
def test_target_overrides_vm_retention() -> None:
    """A target-level target_preserve overrides the VM-level value."""
    facade = ConfigFacade(FIXTURES / "inheritance.toml")
    vm = facade.get_vm("vm_override")

    # The second target explicitly overrides retention.
    override_target = vm.targets[1]
    assert override_target.path == Path("/mnt/backup/vm_override_override")

    # VM-level target_preserve is "20d 10w"; the target overrides to "10d 5w".
    assert vm.target_preserve == "20d 10w"
    assert override_target.target_preserve == "10d 5w"
    assert override_target.target_preserve != vm.target_preserve


# ──────────────────────────────────────────────────────────────────────────
# backup_create inheritance — global → VM → target
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_backup_create_global_inherited_by_target() -> None:
    """Global backup_create='onchange' → target inherits 'onchange' (no override)."""
    facade = ConfigFacade(FIXTURES / "backup_create.toml")
    assert facade.get_global().backup_create == "onchange"

    vm = facade.get_vm("vm_inherit")
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_inherit"))
    assert target.backup_create == "onchange"


@pytest.mark.unit
def test_backup_create_vm_overrides_global() -> None:
    """VM-level backup_create='always' overrides global 'onchange'.

    NOTE: This test exercises VM-level backup_create override.  As of the
    current implementation, ``_build_vm()`` passes ``global_cfg.backup_create``
    directly to ``_build_target()`` without resolving a VM-level override
    first — so this test may fail until VM-level resolution is implemented.
    See the ``_build_vm()`` method in ``qsnap/config/facade.py``.
    """
    facade = ConfigFacade(FIXTURES / "backup_create.toml")
    assert facade.get_global().backup_create == "onchange"

    vm = facade.get_vm("vm_override")
    # The VM overrides global backup_create to "always".
    # Target vm_override_inherit should inherit "always" from the VM.
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_override_inherit"))
    assert target.backup_create == "always"


@pytest.mark.unit
def test_backup_create_target_overrides_vm() -> None:
    """Target-level backup_create='onchange' overrides VM-level 'always'.

    NOTE: This test exercises target-level overriding VM-level backup_create.
    The target-level override via ``tgt_raw.get("backup_create", ...)`` works,
    but the VM-level override from ``_build_vm()`` is not yet implemented.
    When VM-level resolution is added, this test verifies that target
    overrides take precedence over VM overrides.
    """
    facade = ConfigFacade(FIXTURES / "backup_create.toml")
    assert facade.get_global().backup_create == "onchange"

    vm = facade.get_vm("vm_override")
    # The VM overrides global to "always".
    # Target vm_override_override explicitly sets "onchange" → overrides VM.
    target = next(t for t in vm.targets if t.path == Path("/mnt/backup/vm_override_override"))
    assert target.backup_create == "onchange"


# ──────────────────────────────────────────────────────────────────────────
# fast-compressed-full-backup: [global] section inheritance to target
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_global_section_inheritance_to_target() -> None:
    """[global] compress=False in global_section.toml propagates to a target
    that does not override compress.
    """
    facade = ConfigFacade(FIXTURES / "global_section.toml")
    vm = facade.get_vm("testvm")
    target = vm.targets[0]

    assert target.path == Path("/mnt/backup/testvm")
    # compress is not set on the target → inherits [global] compress=False.
    assert target.compress is False


# ──────────────────────────────────────────────────────────────────────────
# configurable-full-backup-engine: full_transfer_engine inheritance
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_target_full_transfer_engine_inherits_from_global(tmp_path: Path) -> None:
    """Global full_transfer_engine='libnbd' → target inherits 'libnbd' (no override)."""
    config_text = (
        'full_transfer_engine = "libnbd"\n'
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
    )
    config_file = tmp_path / "inherit_engine.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    assert facade.get_global().full_transfer_engine == "libnbd"

    vm = facade.get_vm("testvm")
    target = vm.targets[0]
    assert target.full_transfer_engine == "libnbd"


@pytest.mark.unit
def test_target_full_transfer_engine_overrides_global(tmp_path: Path) -> None:
    """Global full_transfer_engine='libnbd'; target overrides to 'qemu-img-convert'."""
    config_text = (
        'full_transfer_engine = "libnbd"\n'
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
        'full_transfer_engine = "qemu-img-convert"\n'
    )
    config_file = tmp_path / "override_engine.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    assert facade.get_global().full_transfer_engine == "libnbd"

    vm = facade.get_vm("testvm")
    target = vm.targets[0]
    assert target.full_transfer_engine == "qemu-img-convert"


@pytest.mark.unit
def test_target_full_transfer_engine_default_is_qemu_img_convert(tmp_path: Path) -> None:
    """When neither global nor target sets full_transfer_engine, defaults to 'qemu-img-convert'."""
    config_text = (
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
    )
    config_file = tmp_path / "default_engine.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    vm = facade.get_vm("testvm")
    target = vm.targets[0]
    assert target.full_transfer_engine == "qemu-img-convert"


# ──────────────────────────────────────────────────────────────────────────
# configurable-full-backup-engine: convert_parallel inheritance
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_target_convert_parallel_inherits_from_global(tmp_path: Path) -> None:
    """Global convert_parallel=2 → target inherits 2 (no override)."""
    config_text = (
        "convert_parallel = 2\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
    )
    config_file = tmp_path / "inherit_parallel.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    assert facade.get_global().convert_parallel == 2

    vm = facade.get_vm("testvm")
    target = vm.targets[0]
    assert target.convert_parallel == 2


@pytest.mark.unit
def test_target_convert_parallel_overrides_global(tmp_path: Path) -> None:
    """Global convert_parallel=2; target overrides to 8."""
    config_text = (
        "convert_parallel = 2\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
        "convert_parallel = 8\n"
    )
    config_file = tmp_path / "override_parallel.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    assert facade.get_global().convert_parallel == 2

    vm = facade.get_vm("testvm")
    target = vm.targets[0]
    assert target.convert_parallel == 8


# ──────────────────────────────────────────────────────────────────────────
# configurable-full-backup-engine: convert_out_of_order inheritance
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_target_convert_out_of_order_inherits_from_global(tmp_path: Path) -> None:
    """Global convert_out_of_order=false → target inherits false (no override)."""
    config_text = (
        "convert_out_of_order = false\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
    )
    config_file = tmp_path / "inherit_order.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    assert facade.get_global().convert_out_of_order is False

    vm = facade.get_vm("testvm")
    target = vm.targets[0]
    assert target.convert_out_of_order is False


@pytest.mark.unit
def test_target_convert_out_of_order_overrides_global(tmp_path: Path) -> None:
    """Global convert_out_of_order=false; target overrides to true."""
    config_text = (
        "convert_out_of_order = false\n"
        "[[vm]]\n"
        'name = "testvm"\n'
        'base_image = "/var/lib/libvirt/images/testvm.qcow2"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/testvm"\n'
        "[[vm.target]]\n"
        'path = "/mnt/backup/testvm"\n'
        "convert_out_of_order = true\n"
    )
    config_file = tmp_path / "override_order.toml"
    config_file.write_text(config_text)

    facade = ConfigFacade(config_file)
    assert facade.get_global().convert_out_of_order is False

    vm = facade.get_vm("testvm")
    target = vm.targets[0]
    assert target.convert_out_of_order is True
