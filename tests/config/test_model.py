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

    # Mutating rate_limit also raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.rate_limit = "100M"  # type: ignore[misc]

    # Mutating new fault-tolerance fields also raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.auto_cleanup = False  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.state_backup_count = 5  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.deep_check_schedule = "weekly"  # type: ignore[misc]


def test_global_config_defaults():
    """GlobalConfig() with no arguments uses documented defaults."""
    cfg = GlobalConfig()
    assert cfg.timestamp_format == "long"
    assert cfg.preserve_day_of_week == "monday"
    assert cfg.state_dir == "/var/lib/qsnap/state"
    assert cfg.lockfile is None
    assert cfg.snapshot_preserve is None
    assert cfg.target_preserve is None
    assert cfg.rate_limit == "no"
    assert cfg.deferred_warn_count == "5"
    assert cfg.deferred_crit_count == "10"
    assert cfg.deferred_warn_age == "7d"
    assert cfg.deferred_crit_age == "14d"
    # Fault-tolerance safety fields (T0/T1 fast ON by default, T3 OFF).
    assert cfg.auto_cleanup is True
    assert cfg.state_backup_count == 2
    assert cfg.chain_verify_before_commit is True
    assert cfg.chain_verify_after_commit is True
    assert cfg.deep_check_schedule == "off"
    # Compress full backups defaults to True.
    assert cfg.compress is True


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
    # Deep verification fields (T2) default to False.
    assert vm.blockcommit_deep_verify is False
    assert vm.snapshot_deep_verify is False


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

    # rate_limit defaults to "no" on TargetConfig.
    assert default_target.rate_limit == "no"

    # Backup retry fields use their documented defaults.
    assert default_target.backup_retry_max == 3
    assert default_target.backup_retry_base == "2s"

    # New bucket-driven backup fields: compress defaults to True, copy_base to False.
    assert default_target.compress is True
    assert default_target.copy_base is False


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


# ---------------------------------------------------------------------------
# GlobalConfig.snapshot_preserve_min / target_preserve_min
# ---------------------------------------------------------------------------


def test_global_config_preserve_min_defaults_none(make_global_config):
    """GlobalConfig() has snapshot_preserve_min=None and target_preserve_min=None by default."""
    cfg = make_global_config()
    assert cfg.snapshot_preserve_min is None
    assert cfg.target_preserve_min is None


def test_global_config_preserve_min_set_from_constructor(make_global_config):
    """GlobalConfig(snapshot_preserve_min='2h', target_preserve_min='4h') sets the values."""
    cfg = make_global_config(snapshot_preserve_min="2h", target_preserve_min="4h")
    assert cfg.snapshot_preserve_min == "2h"
    assert cfg.target_preserve_min == "4h"


# ---------------------------------------------------------------------------
# TargetConfig.compress / copy_base — bucket-driven backup fields
# ---------------------------------------------------------------------------


def test_target_config_compress_default_true(make_target):
    """TargetConfig() has compress=True by default."""
    target = make_target()
    assert target.compress is True


def test_target_config_compress_explicit_false(make_target):
    """TargetConfig(compress=False) stores False."""
    target = make_target(compress=False)
    assert target.compress is False


def test_target_config_copy_base_default_false(make_target):
    """TargetConfig() has copy_base=False by default."""
    target = make_target()
    assert target.copy_base is False


def test_target_config_copy_base_explicit_true(make_target):
    """TargetConfig(copy_base=True) stores True."""
    target = make_target(copy_base=True)
    assert target.copy_base is True


def test_global_config_compress_default_true():
    """GlobalConfig() has compress=True by default."""
    cfg = GlobalConfig()
    assert cfg.compress is True


def test_target_inherits_compress_from_global():
    """TargetConfig field exists for compress; global default is True and can be
    overridden by TargetConfig.  The inheritance is resolved by ConfigFacade,
    but we verify the dataclass field exists and accepts the value."""
    global_cfg = GlobalConfig(compress=False)
    assert global_cfg.compress is False

    target = TargetConfig(path=Path("/backup/testvm"), compress=True)
    assert target.compress is True

    # Default target inherits nothing directly; ConfigFacade wires inheritance.
    target_default = TargetConfig(path=Path("/backup/testvm"))
    assert target_default.compress is True


# ---------------------------------------------------------------------------
# VMConfig.snapshot_preserve_min / target_preserve_min
# ---------------------------------------------------------------------------


def test_vm_config_preserve_min_fields_exist(make_vm_config):
    """VMConfig has snapshot_preserve_min and target_preserve_min fields, defaulting to None."""
    vm = make_vm_config()
    assert vm.snapshot_preserve_min is None
    assert vm.target_preserve_min is None


# ---------------------------------------------------------------------------
# GlobalConfig.rate_limit / deferred thresholds
# ---------------------------------------------------------------------------


def test_global_config_rate_limit_defaults_no():
    """GlobalConfig() defaults rate_limit to 'no' (unlimited)."""
    cfg = GlobalConfig()
    assert cfg.rate_limit == "no"


def test_global_config_rate_limit_frozen():
    """GlobalConfig is frozen; mutating rate_limit raises FrozenInstanceError."""
    cfg = GlobalConfig(rate_limit="100M")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.rate_limit = "500K"  # type: ignore[misc]


def test_target_config_rate_limit_frozen():
    """TargetConfig is frozen; mutating rate_limit raises FrozenInstanceError."""
    target = TargetConfig(path=Path("/mnt/backup/testvm"), rate_limit="100M")
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.rate_limit = "500K"  # type: ignore[misc]


def test_global_config_deferred_thresholds_defaults():
    """GlobalConfig() defaults all four deferred-monitoring thresholds."""
    cfg = GlobalConfig()
    assert cfg.deferred_warn_count == "5"
    assert cfg.deferred_crit_count == "10"
    assert cfg.deferred_warn_age == "7d"
    assert cfg.deferred_crit_age == "14d"


def test_global_config_deferred_thresholds_frozen():
    """GlobalConfig is frozen; mutating deferred fields raises FrozenInstanceError."""
    cfg = GlobalConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.deferred_warn_count = "3"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.deferred_crit_count = "20"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.deferred_warn_age = "1d"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.deferred_crit_age = "30d"  # type: ignore[misc]


def test_global_config_has_rate_limit_field():
    """GlobalConfig dataclass has a 'rate_limit' field."""
    field_names = {f.name for f in dataclasses.fields(GlobalConfig)}
    assert "rate_limit" in field_names


def test_global_config_has_deferred_threshold_fields():
    """GlobalConfig dataclass has all four deferred-monitoring fields."""
    field_names = {f.name for f in dataclasses.fields(GlobalConfig)}
    assert "deferred_warn_count" in field_names
    assert "deferred_crit_count" in field_names
    assert "deferred_warn_age" in field_names
    assert "deferred_crit_age" in field_names


def test_target_config_has_rate_limit_field():
    """TargetConfig dataclass has a 'rate_limit' field."""
    field_names = {f.name for f in dataclasses.fields(TargetConfig)}
    assert "rate_limit" in field_names


# ---------------------------------------------------------------------------
# Fixture support — make_global_config / make_target accept new kwargs
# ---------------------------------------------------------------------------


def test_make_global_config_accepts_rate_limit_kwarg(make_global_config):
    """make_global_config fixture forwards the rate_limit kwarg to GlobalConfig."""
    cfg = make_global_config(rate_limit="100M")
    assert cfg.rate_limit == "100M"


def test_make_global_config_accepts_deferred_kwargs(make_global_config):
    """make_global_config fixture forwards all four deferred-threshold kwargs."""
    cfg = make_global_config(
        deferred_warn_count="3",
        deferred_crit_count="20",
        deferred_warn_age="1d",
        deferred_crit_age="30d",
    )
    assert cfg.deferred_warn_count == "3"
    assert cfg.deferred_crit_count == "20"
    assert cfg.deferred_warn_age == "1d"
    assert cfg.deferred_crit_age == "30d"


def test_make_target_accepts_rate_limit_kwarg(make_target):
    """make_target fixture forwards the rate_limit kwarg to TargetConfig."""
    target = make_target(rate_limit="500K")
    assert target.rate_limit == "500K"


# ---------------------------------------------------------------------------
# Fault-tolerance safety fields — standalone default value tests
# ---------------------------------------------------------------------------


def test_global_config_default_auto_cleanup_true():
    """GlobalConfig().auto_cleanup defaults to True (T0 safe fast-ON)."""
    assert GlobalConfig().auto_cleanup is True


def test_global_config_default_state_backup_count():
    """GlobalConfig().state_backup_count defaults to 2."""
    assert GlobalConfig().state_backup_count == 2


def test_global_config_chain_verify_defaults_true():
    """GlobalConfig().chain_verify_before_commit and chain_verify_after_commit default to True."""
    cfg = GlobalConfig()
    assert cfg.chain_verify_before_commit is True
    assert cfg.chain_verify_after_commit is True


def test_global_config_default_deep_check_schedule_off():
    """GlobalConfig().deep_check_schedule defaults to 'off' (T3 heavy-OFF)."""
    assert GlobalConfig().deep_check_schedule == "off"


def test_vm_config_deep_verify_defaults_false():
    """VMConfig blockcommit_deep_verify and snapshot_deep_verify default to False (T2)."""
    vm = VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    assert vm.blockcommit_deep_verify is False
    assert vm.snapshot_deep_verify is False


def test_target_config_default_retry_values():
    """TargetConfig().backup_retry_max defaults to 3 and backup_retry_base to '2s'."""
    target = TargetConfig(path=Path("/backup/testvm"))
    assert target.backup_retry_max == 3
    assert target.backup_retry_base == "2s"


# ---------------------------------------------------------------------------
# RetentionPolicy anchor fields — multi-level FULL anchors
# ---------------------------------------------------------------------------


def test_retention_policy_anchor_fields_default_false():
    """RetentionPolicy(hourly=24, daily=7) — all anchor_* fields default to False."""
    policy = RetentionPolicy(hourly=24, daily=7)
    assert policy.anchor_hourly is False
    assert policy.anchor_daily is False
    assert policy.anchor_weekly is False
    assert policy.anchor_monthly is False
    assert policy.anchor_yearly is False


def test_retention_policy_anchor_fields_explicit():
    """RetentionPolicy(daily=7, anchor_daily=True, weekly=4, anchor_weekly=True)
    stores the explicit True values and leaves others as False."""
    policy = RetentionPolicy(daily=7, anchor_daily=True, weekly=4, anchor_weekly=True)
    assert policy.anchor_daily is True
    assert policy.anchor_weekly is True
    assert policy.anchor_hourly is False
    assert policy.anchor_monthly is False
    assert policy.anchor_yearly is False


def test_retention_policy_anchor_fields_immutable():
    """RetentionPolicy with anchor_daily=True is frozen;
    mutating anchor_daily raises FrozenInstanceError."""
    policy = RetentionPolicy(anchor_daily=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.anchor_daily = False  # type: ignore[misc]


def test_retention_policy_preserve_min_latest_with_f_anchors():
    """RetentionPolicy(preserve_min='latest', anchor_daily=True, daily=0)
    stores anchor_daily=True even when daily count is zero.
    (Parsing-time rejection of zero F is done at ConfigFacade level, not model.)"""
    policy = RetentionPolicy(preserve_min="latest", anchor_daily=True, daily=0)
    assert policy.preserve_min == "latest"
    assert policy.anchor_daily is True
    assert policy.daily == 0
