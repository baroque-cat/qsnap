"""Tests for immutable configuration dataclasses.

Covers GlobalConfig, VMConfig, TargetConfig, and RetentionPolicy — verifying
immutability, default values, and the defensive-copy-on-construction behaviour
for VMConfig.targets.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from qsnap.models.config import GlobalConfig, RetentionPolicy, TargetConfig, VMConfig


def test_global_config_immutable():
    """GlobalConfig is a frozen dataclass; attribute mutation raises FrozenInstanceError."""
    cfg = GlobalConfig(timestamp_format="long", preserve_day_of_week="monday")

    # Verify the dataclass is declared frozen.
    # NOTE: dataclasses.is_frozen() does not exist in the standard library
    # (checked on Python 3.14); the canonical way to inspect the frozen flag
    # is via the private __dataclass_params__ attribute.
    assert cfg.__dataclass_params__.frozen is True

    # Attempting to set an attribute on a frozen dataclass raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.timestamp_format = "short"  # type: ignore[misc]


def test_global_config_defaults():
    """GlobalConfig() with no arguments uses documented defaults."""
    cfg = GlobalConfig()
    assert cfg.timestamp_format == "long"
    assert cfg.preserve_day_of_week == "monday"
    assert cfg.state_dir == "/var/lib/qsnap/state"
    assert cfg.lockfile is None
    assert cfg.snapshot_preserve is None
    assert cfg.target_preserve is None


def test_vm_config_required_fields():
    """VMConfig with required fields sets them; snapshot_create defaults to 'always', targets to empty."""
    vm = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
    )
    assert vm.name == "testvm"
    assert vm.base_image == Path("/var/lib/libvirt/images/testvm.qcow2")
    assert vm.snapshot_dir == Path("/var/lib/libvirt/images/snapshots")
    assert vm.snapshot_create == "always"
    assert vm.targets == []


def test_vm_config_with_targets():
    """VMConfig stores targets in order and uses a defensive copy on construction.

    RISK: Frozen dataclasses with mutable sub-fields.
    VMConfig is frozen, but its ``targets`` field holds a ``list[TargetConfig]``
    which is itself mutable.  To mitigate this, ``VMConfig.__post_init__`` creates
    a shallow copy of the list passed to the constructor (defensive copy on
    construction).  This means that mutating the *original* list after the
    VMConfig has been created does NOT affect the VMConfig's internal ``targets``.

    Trade-off: the list returned by ``vm.targets`` is still the *same* list
    object stored internally -- appending to it directly WILL mutate internal
    state.  Full immutability would require a property returning a copy on
    every access, which is out of scope for the current design.  The defensive
    copy on construction is sufficient to protect against the common pattern of
    reusing a list variable across multiple VMConfig constructions.
    """
    target1 = TargetConfig(path=Path("/backup/testvm"))
    target2 = TargetConfig(path=Path("/backup2/testvm"))
    original = [target1, target2]

    vm = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
        targets=original,
    )

    # Targets are stored in order.
    assert len(vm.targets) == 2
    assert vm.targets[0] is target1
    assert vm.targets[1] is target2

    # Defensive copy: the internal list is a different object than the original.
    assert vm.targets is not original

    # Mutating the original list does NOT affect the VMConfig's internal list.
    original.append(TargetConfig(path=Path("/backup3/testvm")))
    assert len(vm.targets) == 2


def test_target_config_incremental():
    """TargetConfig with incremental=True; the default is also True."""
    target = TargetConfig(path=Path("/backup/testvm"), incremental=True)
    assert target.incremental is True

    # Default value for incremental is True.
    default_target = TargetConfig(path=Path("/backup/testvm"))
    assert default_target.incremental is True


def test_retention_policy_hourly_daily():
    """RetentionPolicy with hourly=24, daily=7; other counts default to 0, preserve_min to 'all'."""
    policy = RetentionPolicy(hourly=24, daily=7)
    assert policy.hourly == 24
    assert policy.daily == 7
    assert policy.weekly == 0
    assert policy.monthly == 0
    assert policy.yearly == 0
    assert policy.preserve_min == "all"


def test_retention_policy_defaults():
    """RetentionPolicy() with no arguments defaults all counts to 0 and preserve_min to 'all'."""
    policy = RetentionPolicy()
    assert policy.hourly == 0
    assert policy.daily == 0
    assert policy.weekly == 0
    assert policy.monthly == 0
    assert policy.yearly == 0
    assert policy.preserve_min == "all"


def test_target_config_default_incremental_mode_is_file_copy():
    """TargetConfig with no incremental_mode arg defaults to 'file-copy'."""
    target = TargetConfig(path=Path("/backup/testvm"))
    assert target.incremental_mode == "file-copy"


def test_target_config_explicit_incremental_mode_bitmap():
    """TargetConfig(incremental_mode='bitmap') stores 'bitmap'."""
    target = TargetConfig(path=Path("/backup/testvm"), incremental_mode="bitmap")
    assert target.incremental_mode == "bitmap"


def test_vm_config_disks_default_none_auto_discovery():
    """VMConfig with no disks arg defaults to None (auto-discovery via virsh domblklist)."""
    vm = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
    )
    assert vm.disks is None


def test_vm_config_explicit_disks_list():
    """VMConfig(disks=['vda', 'vdb']) stores the explicit disk list."""
    vm = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
        disks=["vda", "vdb"],
    )
    assert vm.disks == ["vda", "vdb"]


# ---------------------------------------------------------------------------
# TargetConfig.verify — post-transfer verification mode
# ---------------------------------------------------------------------------


def test_target_config_default_verify_metadata():
    """TargetConfig with no verify arg defaults to 'metadata'."""
    target = TargetConfig(path=Path("/tmp"))
    assert target.verify == "metadata"


def test_target_config_explicit_verify_full():
    """TargetConfig(verify='full') stores 'full'."""
    target = TargetConfig(path=Path("/tmp"), verify="full")
    assert target.verify == "full"


def test_target_config_verify_off():
    """TargetConfig(verify='off') stores 'off' (verification disabled)."""
    target = TargetConfig(path=Path("/tmp"), verify="off")
    assert target.verify == "off"


def test_target_config_verify_immutable():
    """TargetConfig is a frozen dataclass; mutating verify raises FrozenInstanceError."""
    target = TargetConfig(path=Path("/tmp"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.verify = "off"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# VMConfig.snapshot_quiesce — --quiesce flag for virsh snapshot-create-as
# ---------------------------------------------------------------------------


def test_vm_config_snapshot_quiesce_default_false():
    """VMConfig with no snapshot_quiesce arg defaults to False."""
    vm = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
    )
    assert vm.snapshot_quiesce is False


def test_vm_config_snapshot_quiesce_true():
    """VMConfig(snapshot_quiesce=True) stores True."""
    vm = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
        snapshot_quiesce=True,
    )
    assert vm.snapshot_quiesce is True


def test_vm_config_snapshot_quiesce_immutable():
    """VMConfig is a frozen dataclass; mutating snapshot_quiesce raises FrozenInstanceError."""
    vm = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
        snapshot_quiesce=True,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        vm.snapshot_quiesce = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# VMConfig.lifecycle_mode — snapshot-merge strategy
# ---------------------------------------------------------------------------


def test_vm_config_lifecycle_mode_default_virsh():
    """VMConfig with no lifecycle_mode arg defaults to 'virsh' (blockcommit)."""
    vm = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
    )
    assert vm.lifecycle_mode == "virsh"


def test_vm_config_lifecycle_mode_qemu_img():
    """VMConfig(lifecycle_mode='qemu-img') stores 'qemu-img' (qemu-img commit)."""
    vm = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
        lifecycle_mode="qemu-img",
    )
    assert vm.lifecycle_mode == "qemu-img"


# ---------------------------------------------------------------------------
# RetentionPolicy.preserve_min — minimum preservation duration
# ---------------------------------------------------------------------------


def test_retention_policy_preserve_min_latest():
    """RetentionPolicy(preserve_min='latest') stores 'latest'."""
    policy = RetentionPolicy(preserve_min="latest")
    assert policy.preserve_min == "latest"
