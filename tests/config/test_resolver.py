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

With rsync/file-copy removed, verify always defaults to ``"metadata"``
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
