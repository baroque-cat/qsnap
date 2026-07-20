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

Covers mode-dependent ``verify`` default resolution (design D3/D8):
- File-copy defaults to ``"hash"``, bitmap defaults to ``"metadata"``
- Explicit ``verify`` takes precedence over the mode-dependent default
- Bitmap + ``verify="hash"`` warns and auto-downgrades to ``"metadata"``
"""

from __future__ import annotations

import logging
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


# ── mode-dependent verify default resolution ───────────────────────────


@pytest.mark.unit
def test_facade_resolves_hash_default_for_file_copy_mode() -> None:
    """File-copy mode with no explicit verify defaults to 'hash'."""
    facade = ConfigFacade(FIXTURES / "verify_mode_defaults.toml")
    vm = facade.get_vm("vm_fc_default")
    target = vm.targets[0]
    assert target.incremental_mode == "file-copy"
    assert target.verify == "hash"


@pytest.mark.unit
def test_facade_resolves_metadata_default_for_bitmap_mode() -> None:
    """Bitmap mode with no explicit verify defaults to 'metadata'."""
    facade = ConfigFacade(FIXTURES / "verify_mode_defaults.toml")
    vm = facade.get_vm("vm_bitmap_default")
    target = vm.targets[0]
    assert target.incremental_mode == "bitmap"
    assert target.verify == "metadata"


@pytest.mark.unit
def test_facade_explicit_verify_overrides_mode_default() -> None:
    """Explicit verify='metadata' beats file-copy's default of 'hash'."""
    facade = ConfigFacade(FIXTURES / "verify_mode_defaults.toml")
    vm = facade.get_vm("vm_fc_override")
    target = vm.targets[0]
    assert target.incremental_mode == "file-copy"
    assert target.verify == "metadata"


@pytest.mark.unit
def test_facade_verify_full_preserved_for_filecopy_downgraded_for_bitmap(caplog) -> None:
    """verify='full' is preserved for file-copy but downgraded to 'metadata'
    for bitmap mode (design D5: incremental NBD exports contain only dirty
    blocks; qemu-img compare will always mismatch against source with backing
    chain)."""
    with caplog.at_level(logging.WARNING):
        facade = ConfigFacade(FIXTURES / "verify_full_both.toml")

    vm_fc = facade.get_vm("vm_fc_full")
    assert vm_fc.targets[0].incremental_mode == "file-copy"
    assert vm_fc.targets[0].verify == "full"

    vm_bitmap = facade.get_vm("vm_bitmap_full")
    assert vm_bitmap.targets[0].incremental_mode == "bitmap"
    assert vm_bitmap.targets[0].verify == "metadata"

    warnings_text = " ".join(caplog.messages)
    assert "verify='full' is not supported in bitmap mode" in warnings_text
    assert "Downgrading to verify='metadata'" in warnings_text


@pytest.mark.unit
def test_facade_bitmap_mode_hash_warns_and_downgrades(caplog) -> None:
    """Bitmap mode + verify='hash' emits WARNING and downgrades to 'metadata'."""
    with caplog.at_level(logging.WARNING):
        facade = ConfigFacade(FIXTURES / "verify_bitmap_hash.toml")

    warnings_text = " ".join(caplog.messages)
    assert "not supported in bitmap mode" in warnings_text
    assert "Downgrading" in warnings_text

    vm = facade.get_vm("vm_bitmap_hash")
    target = vm.targets[0]
    assert target.incremental_mode == "bitmap"
    assert target.verify == "metadata"
