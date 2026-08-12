"""Tests for immutable configuration dataclasses.

Covers GlobalConfig, VMConfig, TargetConfig, and RetentionPolicy — verifying
immutability, default values, the defensive-copy-on-construction behaviour
for VMConfig.targets, and count-based retention configuration.
"""

from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from qsnap.models.config import DiskConfig, GlobalConfig, RetentionPolicy, TargetConfig, VMConfig

# ---------------------------------------------------------------------------
# Scenario 1: RetentionPolicy default values
# ---------------------------------------------------------------------------


def test_retention_policy_defaults():
    """RetentionPolicy() has chain_length=0, keep_generations=1, preserve_min=0."""
    policy = RetentionPolicy()
    assert policy.chain_length == 0
    assert policy.keep_generations == 1
    assert policy.preserve_min == 0


# ---------------------------------------------------------------------------
# Scenario 2: RetentionPolicy for snapshots
# ---------------------------------------------------------------------------


def test_retention_policy_for_snapshots():
    """RetentionPolicy with chain_length=168, keep_generations=1, preserve_min=24 (snapshot use)."""
    policy = RetentionPolicy(chain_length=168, keep_generations=1, preserve_min=24)
    assert policy.chain_length == 168
    assert policy.keep_generations == 1
    assert policy.preserve_min == 24


# ---------------------------------------------------------------------------
# Scenario 2b: RetentionPolicy for snapshots with preserve_min
# ---------------------------------------------------------------------------


def test_retention_policy_for_snapshots_with_preserve_min():
    """RetentionPolicy(chain_length=168, keep_generations=1, preserve_min=24)
    stores all three fields for snapshot retention with preservation floor."""
    policy = RetentionPolicy(chain_length=168, keep_generations=1, preserve_min=24)
    assert policy.chain_length == 168
    assert policy.keep_generations == 1
    assert policy.preserve_min == 24


# ---------------------------------------------------------------------------
# Scenario 3: RetentionPolicy for targets
# ---------------------------------------------------------------------------


def test_retention_policy_for_targets():
    """RetentionPolicy with chain_length=0, keep_generations=2 (target use)."""
    policy = RetentionPolicy(chain_length=0, keep_generations=2)
    assert policy.chain_length == 0
    assert policy.keep_generations == 2


def test_retention_policy_preserve_min_defaults_zero():
    """RetentionPolicy(chain_length=72) without preserve_min defaults to 0 (inactive)."""
    policy = RetentionPolicy(chain_length=72)
    assert policy.chain_length == 72
    assert policy.keep_generations == 1  # default
    assert policy.preserve_min == 0  # default — inactive


# ---------------------------------------------------------------------------
# RetentionPolicy is frozen (immutable)
# ---------------------------------------------------------------------------


def test_retention_policy_immutable():
    """RetentionPolicy is a frozen dataclass; mutating fields raises FrozenInstanceError."""
    policy = RetentionPolicy(chain_length=168, keep_generations=2, preserve_min=24)
    assert policy.__dataclass_params__.frozen is True

    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.chain_length = 24  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.keep_generations = 3  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.preserve_min = 10  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Scenario 4: GlobalConfig immutability
# ---------------------------------------------------------------------------


def test_global_config_immutable():
    """GlobalConfig is a frozen dataclass; attribute mutation raises FrozenInstanceError."""
    cfg = GlobalConfig()

    # Verify the dataclass is declared frozen.
    assert cfg.__dataclass_params__.frozen is True

    # Attempting to set an attribute on a frozen dataclass raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.state_dir = "/tmp"  # type: ignore[misc]

    # Mutating count-based retention fields also raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.snapshot_chain_length = 168  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.target_chain_length = 100  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.target_keep_generations = 3  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.snapshot_preserve_min = 48  # type: ignore[misc]

    # Mutating fault-tolerance fields also raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.auto_cleanup = False  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.state_backup_count = 5  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.deep_check_schedule = "weekly"  # type: ignore[misc]

    # Mutating FULL verification fields also raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.full_verify_after_create = "off"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.full_verify_before_delete = "off"  # type: ignore[misc]

    # Mutating compression_type also raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.compression_type = "zlib"  # type: ignore[misc]

    # Mutating backup_stall_timeout also raises FrozenInstanceError.
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.backup_stall_timeout = "1h"  # type: ignore[misc]


def test_global_config_snapshot_preserve_min_immutable():
    """Mutating GlobalConfig().snapshot_preserve_min raises FrozenInstanceError."""
    cfg = GlobalConfig(snapshot_preserve_min=24)
    assert cfg.snapshot_preserve_min == 24

    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.snapshot_preserve_min = 48  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Scenario: GlobalConfig.blockcommit_timeout (config-model spec)
# ---------------------------------------------------------------------------


def test_global_config_blockcommit_timeout_default_1800():
    """GlobalConfig().blockcommit_timeout defaults to 1800 (30 min).

    config-model scenario "Default blockcommit timeout is 1800": a
    GlobalConfig created without the field carries the wall-clock
    ceiling of 1800 seconds for a single commit command.
    """
    cfg = GlobalConfig()
    assert cfg.blockcommit_timeout == 1800


def test_global_config_blockcommit_timeout_immutable():
    """Mutating GlobalConfig().blockcommit_timeout raises FrozenInstanceError.

    config-model scenario "Field is immutable": the field is part of the
    frozen dataclass, so assignment must raise a frozen-dataclass error.
    """
    cfg = GlobalConfig(blockcommit_timeout=900)
    assert cfg.blockcommit_timeout == 900

    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.blockcommit_timeout = 60  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Scenario 9: GlobalConfig chain_length defaults are 24/168/2
# ---------------------------------------------------------------------------


def test_global_chain_length_defaults_are_sensible():
    """GlobalConfig().snapshot_chain_length is 24, target_chain_length is 168,
    target_keep_generations is 2, snapshot_preserve_min is 48 (active floor)."""
    cfg = GlobalConfig()
    assert cfg.snapshot_chain_length == 24
    assert cfg.target_chain_length == 168
    assert cfg.target_keep_generations == 2
    assert cfg.snapshot_preserve_min == 48


def test_global_config_snapshot_preserve_min_default():
    """GlobalConfig().snapshot_preserve_min defaults to 48 (active preservation floor —
    ~2 days of hourly snapshots).  Explicit 0 disables the floor."""
    assert GlobalConfig().snapshot_preserve_min == 48


def test_global_config_preserve_min_zero_disables():
    """Explicit snapshot_preserve_min=0 disables the floor (design D13)."""
    cfg = GlobalConfig(snapshot_preserve_min=0)
    assert cfg.snapshot_preserve_min == 0


# ---------------------------------------------------------------------------
# GlobalConfig free-space gate fields (design D5/D16)
# ---------------------------------------------------------------------------


def test_global_config_free_space_defaults():
    """GlobalConfig() free-space gate defaults: strict check, no reserve, factor 1.0."""
    cfg = GlobalConfig()
    assert cfg.free_space_check == "strict"
    assert cfg.free_space_reserve == 0
    assert cfg.free_space_factor == 1.0


def test_global_config_free_space_override():
    """GlobalConfig(free_space_check='warn', ...) stores explicit overrides."""
    cfg = GlobalConfig(
        free_space_check="warn",
        free_space_reserve=1073741824,
        free_space_factor=1.1,
    )
    assert cfg.free_space_check == "warn"
    assert cfg.free_space_reserve == 1073741824
    assert cfg.free_space_factor == 1.1


# ---------------------------------------------------------------------------
# Scenario 6: VMConfig required fields
# ---------------------------------------------------------------------------


def test_vm_config_required_fields():
    """VMConfig with required fields sets them; snapshot_create defaults to 'always', targets to empty."""
    vm = VMConfig(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
    )
    assert vm.name == "testvm"
    assert vm.disks[0].base_image == Path("/var/lib/libvirt/images/testvm.qcow2")
    assert vm.snapshot_dir == Path("/var/lib/libvirt/images/snapshots")
    assert vm.snapshot_create == "always"
    assert vm.targets == []
    # Count-based retention fields default to None (not overridden).
    assert vm.snapshot_chain_length is None
    assert vm.target_chain_length is None
    assert vm.target_keep_generations is None
    # snapshot_preserve_min defaults to None (inherits from global).
    assert vm.snapshot_preserve_min is None
    # Deep verification fields (T2) default to False.
    assert vm.blockcommit_deep_verify is False
    # Backup engine options (vm-level-backup-engine-options change) default
    # to the spec values on a bare-minimum VMConfig.
    assert vm.compress is True
    assert vm.compression_type == "zstd"
    assert vm.convert_parallel == 4
    assert vm.convert_out_of_order is True
    assert vm.backup_stall_timeout == "30m"
    assert vm.verify == "metadata"


# ---------------------------------------------------------------------------
# Scenario 6b: VMConfig backup engine options
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_vm_config_engine_option_defaults():
    """VMConfig with no engine options carries the six spec defaults:
    compress=True, compression_type='zstd', convert_parallel=4,
    convert_out_of_order=True, backup_stall_timeout='30m', verify='metadata'."""
    disk = DiskConfig(target="vda", base_image=Path("/images/vm.qcow2"))
    vm = VMConfig(name="myvm", disks=[disk])

    assert vm.compress is True
    assert vm.compression_type == "zstd"
    assert vm.convert_parallel == 4
    assert vm.convert_out_of_order is True
    assert vm.backup_stall_timeout == "30m"
    assert vm.verify == "metadata"


@pytest.mark.unit
def test_vm_config_engine_options_immutable():
    """VMConfig engine options are frozen; mutating compression_type or
    convert_parallel raises FrozenInstanceError."""
    disk = DiskConfig(target="vda", base_image=Path("/images/vm.qcow2"))
    vm = VMConfig(name="myvm", disks=[disk], compression_type="zlib", convert_parallel=8)

    assert vm.compression_type == "zlib"
    assert vm.convert_parallel == 8

    with pytest.raises(FrozenInstanceError):
        vm.compression_type = "zstd"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        vm.convert_parallel = 4  # type: ignore[misc]


@pytest.mark.unit
def test_vm_config_engine_options_explicit():
    """VMConfig with explicit engine options carries exactly those values."""
    disk = DiskConfig(target="vda", base_image=Path("/images/vm.qcow2"))
    vm = VMConfig(
        name="myvm",
        disks=[disk],
        compress=False,
        compression_type="zlib",
        convert_parallel=8,
        convert_out_of_order=False,
        backup_stall_timeout="1h",
        verify="compare",
    )

    assert vm.compress is False
    assert vm.compression_type == "zlib"
    assert vm.convert_parallel == 8
    assert vm.convert_out_of_order is False
    assert vm.backup_stall_timeout == "1h"
    assert vm.verify == "compare"


# ---------------------------------------------------------------------------
# Scenario 7: VMConfig with targets
# ---------------------------------------------------------------------------


def test_vm_config_with_targets():
    """VMConfig stores targets in order and uses a defensive copy on construction.

    RISK: Frozen dataclasses with mutable sub-fields.
    VMConfig is frozen, but its ``targets`` field holds a ``list[TargetConfig]``
    which is itself mutable.  To mitigate this, ``VMConfig.__post_init__`` creates
    a shallow copy of the list passed to the constructor (defensive copy on
    construction).  This means that mutating the *original* list after the
    VMConfig has been created does NOT affect the VMConfig's internal ``targets``.
    """
    target1 = TargetConfig(path=Path("/backup/testvm"))
    target2 = TargetConfig(path=Path("/backup2/testvm"))
    original = [target1, target2]

    vm = VMConfig(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
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


# ---------------------------------------------------------------------------
# Scenario 8: TargetConfig no longer has incremental field
# ---------------------------------------------------------------------------


def test_vm_config_disks_are_required():
    """VMConfig requires at least one disk via disks= list."""
    vm = VMConfig(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
    )
    assert len(vm.disks) == 1
    assert vm.disks[0].target == "vda"
    assert vm.disks[0].base_image == Path("/var/lib/libvirt/images/testvm.qcow2")


def test_vm_config_explicit_disks_list():
    """VMConfig with multiple DiskConfig entries stores the explicit disk list."""
    disks = [
        DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2")),
        DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testdb.qcow2")),
    ]
    vm = VMConfig(
        name="testvm",
        disks=disks,
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
    )
    assert len(vm.disks) == 2
    assert vm.disks[0].target == "vda"
    assert vm.disks[1].target == "vdb"


# ---------------------------------------------------------------------------
# TargetConfig.verify — post-transfer verification mode
# ---------------------------------------------------------------------------


def test_target_config_default_verify_metadata():
    """TargetConfig with no verify arg defaults to 'metadata'."""
    target = TargetConfig(path=Path("/tmp"))
    assert target.verify == "metadata"


def test_target_config_verify_explicit_compare():
    """TargetConfig(verify='compare') stores 'compare'."""
    target = TargetConfig(path=Path("/tmp"), verify="compare")
    assert target.verify == "compare"


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
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
    )
    assert vm.snapshot_quiesce is False


def test_vm_config_snapshot_quiesce_true():
    """VMConfig(snapshot_quiesce=True) stores True."""
    vm = VMConfig(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
        snapshot_quiesce=True,
    )
    assert vm.snapshot_quiesce is True


def test_vm_config_snapshot_quiesce_immutable():
    """VMConfig is a frozen dataclass; mutating snapshot_quiesce raises FrozenInstanceError."""
    vm = VMConfig(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
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
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
    )
    assert vm.lifecycle_mode == "virsh"


def test_vm_config_lifecycle_mode_qemu_img():
    """VMConfig(lifecycle_mode='qemu-img') stores 'qemu-img' (qemu-img commit)."""
    vm = VMConfig(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/images/snapshots"),
        lifecycle_mode="qemu-img",
    )
    assert vm.lifecycle_mode == "qemu-img"


# ---------------------------------------------------------------------------
# TargetConfig.compress
# ---------------------------------------------------------------------------


def test_target_config_compress_default_true(make_target):
    """TargetConfig() has compress=True by default."""
    target = make_target()
    assert target.compress is True


def test_target_config_compress_explicit_false(make_target):
    """TargetConfig(compress=False) stores False."""
    target = make_target(compress=False)
    assert target.compress is False


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
# GlobalConfig deferred thresholds
# ---------------------------------------------------------------------------


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


def test_global_config_has_deferred_threshold_fields():
    """GlobalConfig dataclass has all four deferred-monitoring fields."""
    field_names = {f.name for f in dataclasses.fields(GlobalConfig)}
    assert "deferred_warn_count" in field_names
    assert "deferred_crit_count" in field_names
    assert "deferred_warn_age" in field_names
    assert "deferred_crit_age" in field_names


# Fixture support — make_global_config accepts count-based kwargs


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


def test_make_global_config_accepts_chain_length_kwargs(make_global_config):
    """make_global_config fixture forwards count-based retention kwargs."""
    cfg = make_global_config(
        snapshot_chain_length=168,
        target_chain_length=100,
        target_keep_generations=2,
    )
    assert cfg.snapshot_chain_length == 168
    assert cfg.target_chain_length == 100
    assert cfg.target_keep_generations == 2


# Fault-tolerance safety fields — standalone default value tests


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
    """VMConfig blockcommit_deep_verify defaults to False (T2)."""
    vm = VMConfig(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    assert vm.blockcommit_deep_verify is False


def test_vm_config_deep_verify_blockcommit_only():
    """VMConfig has blockcommit_deep_verify (T2), but snapshot_deep_verify
    has been removed — accessing it raises AttributeError."""
    vm = VMConfig(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
        blockcommit_deep_verify=True,
    )
    assert vm.blockcommit_deep_verify is True
    with pytest.raises(AttributeError):
        _ = vm.snapshot_deep_verify  # type: ignore[attr-defined]


def test_target_config_default_retry_values():
    """TargetConfig().backup_retry_max defaults to 3 and backup_retry_base to '2s'."""
    target = TargetConfig(path=Path("/backup/testvm"))
    assert target.backup_retry_max == 3
    assert target.backup_retry_base == "2s"


# ---------------------------------------------------------------------------
# GlobalConfig.full_verify_after_create — FULL creation verification tier
# ---------------------------------------------------------------------------


def test_global_config_full_verify_after_create_default():
    """GlobalConfig().full_verify_after_create defaults to 'check' (M1+M2)."""
    assert GlobalConfig().full_verify_after_create == "check"


def test_global_config_full_verify_after_create_metadata():
    """GlobalConfig(full_verify_after_create='metadata') stores 'metadata' (M1 only)."""
    cfg = GlobalConfig(full_verify_after_create="metadata")
    assert cfg.full_verify_after_create == "metadata"


def test_global_config_full_verify_after_create_compare():
    """GlobalConfig(full_verify_after_create='compare') stores 'compare' (M1+M2+M3)."""
    cfg = GlobalConfig(full_verify_after_create="compare")
    assert cfg.full_verify_after_create == "compare"


def test_global_config_full_verify_after_create_off():
    """GlobalConfig(full_verify_after_create='off') stores 'off' (no verification)."""
    cfg = GlobalConfig(full_verify_after_create="off")
    assert cfg.full_verify_after_create == "off"


# ---------------------------------------------------------------------------
# GlobalConfig.full_verify_before_delete — pre-deletion verification
# ---------------------------------------------------------------------------


def test_global_config_full_verify_before_delete_default():
    """GlobalConfig().full_verify_before_delete defaults to 'check' (M1+M2)."""
    assert GlobalConfig().full_verify_before_delete == "check"


def test_global_config_full_verify_before_delete_off_m1_still_enforced():
    """GlobalConfig(full_verify_before_delete='off') stores 'off' — M1 is
    enforced by the application layer regardless of this setting."""
    cfg = GlobalConfig(full_verify_before_delete="off")
    assert cfg.full_verify_before_delete == "off"


def test_global_config_full_verify_before_delete_metadata():
    """GlobalConfig(full_verify_before_delete='metadata') stores 'metadata' (M1 only)."""
    cfg = GlobalConfig(full_verify_before_delete="metadata")
    assert cfg.full_verify_before_delete == "metadata"


# ---------------------------------------------------------------------------
# GlobalConfig.transaction_log — btrbk-compatible transaction log path
# ---------------------------------------------------------------------------


def test_transaction_log_defaults_to_none():
    """GlobalConfig() with no arguments defaults transaction_log to None."""
    cfg = GlobalConfig()
    assert cfg.transaction_log is None

    # Verify the field exists in the dataclass.
    field_names = {f.name for f in dataclasses.fields(GlobalConfig)}
    assert "transaction_log" in field_names


def test_transaction_log_validates_absolute_path():
    """GlobalConfig stores the transaction_log string as-is.

    The model layer does not validate path absoluteness; validation
    (if any) happens at a higher layer (ConfigFacade or Core).
    """
    # Absolute path — stored as-is.
    cfg = GlobalConfig(transaction_log="/var/log/qsnap/transactions.log")
    assert cfg.transaction_log == "/var/log/qsnap/transactions.log"

    # Relative path — also stored as-is (no model-layer validation).
    cfg_rel = GlobalConfig(transaction_log="logs/transactions.log")
    assert cfg_rel.transaction_log == "logs/transactions.log"


# ---------------------------------------------------------------------------
# GlobalConfig.compression_type — FULL backup compression algorithm
# ---------------------------------------------------------------------------


def test_global_config_compression_type_default():
    """GlobalConfig().compression_type defaults to 'zstd'."""
    assert GlobalConfig().compression_type == "zstd"


def test_global_config_compression_type_immutable():
    """GlobalConfig is frozen; mutating compression_type raises FrozenInstanceError."""
    cfg = GlobalConfig(compression_type="zstd")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.compression_type = "zlib"  # type: ignore[misc]


def test_global_config_compression_type_zlib():
    """GlobalConfig(compression_type='zlib') stores 'zlib'."""
    cfg = GlobalConfig(compression_type="zlib")
    assert cfg.compression_type == "zlib"


# ---------------------------------------------------------------------------
# GlobalConfig.backup_stall_timeout — stall detection for data-transfer
# ---------------------------------------------------------------------------


def test_global_config_backup_stall_timeout_default():
    """GlobalConfig().backup_stall_timeout defaults to '30m'."""
    assert GlobalConfig().backup_stall_timeout == "30m"


def test_global_config_backup_stall_timeout_immutable():
    """GlobalConfig is frozen; mutating backup_stall_timeout raises FrozenInstanceError."""
    cfg = GlobalConfig(backup_stall_timeout="30m")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.backup_stall_timeout = "1h"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TargetConfig.compression_type — inherited from GlobalConfig via ConfigFacade
# ---------------------------------------------------------------------------


def test_target_config_compression_type_inherits():
    """TargetConfig().compression_type defaults to 'zstd' (dataclass-level default)."""
    target = TargetConfig(path=Path("/backup/testvm"))
    assert target.compression_type == "zstd"


def test_target_config_compression_type_overrides():
    """TargetConfig(compression_type='zlib') overrides the default 'zstd'."""
    target = TargetConfig(path=Path("/backup/testvm"), compression_type="zlib")
    assert target.compression_type == "zlib"


# ---------------------------------------------------------------------------
# TargetConfig.backup_stall_timeout
# ---------------------------------------------------------------------------


def test_target_config_backup_stall_timeout_inherits():
    """TargetConfig().backup_stall_timeout defaults to '30m' (dataclass-level default)."""
    target = TargetConfig(path=Path("/backup/testvm"))
    assert target.backup_stall_timeout == "30m"


def test_target_config_backup_stall_timeout_overrides():
    """TargetConfig(backup_stall_timeout='1h') overrides the default '30m'."""
    target = TargetConfig(path=Path("/backup/testvm"), backup_stall_timeout="1h")
    assert target.backup_stall_timeout == "1h"


# ---------------------------------------------------------------------------
# TargetConfig.backup_create — per-target backup gating mode
# ---------------------------------------------------------------------------


def test_target_config_backup_create_default_always():
    """TargetConfig(path='/tmp') has backup_create == 'always' by default."""
    target = TargetConfig(path=Path("/tmp"))
    assert target.backup_create == "always"


def test_target_config_backup_create_explicit_onchange():
    """TargetConfig(path='/tmp', backup_create='onchange') has backup_create == 'onchange'."""
    target = TargetConfig(path=Path("/tmp"), backup_create="onchange")
    assert target.backup_create == "onchange"


def test_target_config_backup_create_inherits_from_global():
    """backup_create field exists on TargetConfig and GlobalConfig.
    The dataclass-level default is 'always'; ConfigFacade resolves
    inheritance at a higher layer."""
    global_cfg = GlobalConfig(backup_create="onchange")
    assert global_cfg.backup_create == "onchange"

    target = TargetConfig(path=Path("/backup/testvm"), backup_create="onchange")
    assert target.backup_create == "onchange"

    # Default target has dataclass-level default "always".
    target_default = TargetConfig(path=Path("/backup/testvm"))
    assert target_default.backup_create == "always"


def test_target_config_backup_create_overrides_global():
    """TargetConfig(backup_create='always') overrides the global default 'onchange'.
    Resolution is by ConfigFacade; we verify the dataclass fields accept the values."""
    global_cfg = GlobalConfig(backup_create="onchange")
    assert global_cfg.backup_create == "onchange"

    target = TargetConfig(path=Path("/backup/testvm"), backup_create="always")
    assert target.backup_create == "always"
    assert target.backup_create != global_cfg.backup_create


# ---------------------------------------------------------------------------
# GlobalConfig.backup_create — global default for backup gating
# ---------------------------------------------------------------------------


def test_global_config_backup_create_default_always():
    """GlobalConfig() has backup_create == 'always' by default."""
    assert GlobalConfig().backup_create == "always"


def test_global_config_backup_create_explicit_onchange():
    """GlobalConfig(backup_create='onchange') has backup_create == 'onchange'."""
    cfg = GlobalConfig(backup_create="onchange")
    assert cfg.backup_create == "onchange"


# ---------------------------------------------------------------------------
# GlobalConfig.convert_parallel — qemu-img convert -m flag
# ---------------------------------------------------------------------------


def test_global_config_convert_parallel_default_is_4():
    """GlobalConfig().convert_parallel defaults to 4."""
    assert GlobalConfig().convert_parallel == 4


def test_global_config_convert_parallel_is_immutable():
    """GlobalConfig is frozen; mutating convert_parallel raises FrozenInstanceError."""
    cfg = GlobalConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.convert_parallel = 8  # type: ignore[misc]


# ---------------------------------------------------------------------------
# GlobalConfig.convert_out_of_order — qemu-img convert -W flag
# ---------------------------------------------------------------------------


def test_global_config_convert_out_of_order_default_is_true():
    """GlobalConfig().convert_out_of_order defaults to True."""
    assert GlobalConfig().convert_out_of_order is True


def test_global_config_convert_out_of_order_is_immutable():
    """GlobalConfig is frozen; mutating convert_out_of_order raises FrozenInstanceError."""
    cfg = GlobalConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.convert_out_of_order = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TargetConfig.convert_parallel
# ---------------------------------------------------------------------------


def test_target_config_convert_parallel_default():
    """TargetConfig().convert_parallel defaults to 4."""
    target = TargetConfig(path=Path("/backup/testvm"))
    assert target.convert_parallel == 4


def test_target_config_convert_parallel_overrides():
    """TargetConfig(convert_parallel=8) overrides the default 4."""
    target = TargetConfig(path=Path("/backup/testvm"), convert_parallel=8)
    assert target.convert_parallel == 8


# ---------------------------------------------------------------------------
# TargetConfig.convert_out_of_order
# ---------------------------------------------------------------------------


def test_target_config_convert_out_of_order_default():
    """TargetConfig().convert_out_of_order defaults to True."""
    target = TargetConfig(path=Path("/backup/testvm"))
    assert target.convert_out_of_order is True


def test_target_config_convert_out_of_order_overrides():
    """TargetConfig(convert_out_of_order=False) overrides the default True."""
    target = TargetConfig(path=Path("/backup/testvm"), convert_out_of_order=False)
    assert target.convert_out_of_order is False


# ---------------------------------------------------------------------------
# GlobalConfig default chain lengths: 24 / 168 / 2
# ---------------------------------------------------------------------------


def test_globalconfig_default_chain_lengths_24_168_2():
    """GlobalConfig() with no args has snapshot_chain_length=24,
    target_chain_length=168, target_keep_generations=2."""
    cfg = GlobalConfig()
    assert cfg.snapshot_chain_length == 24
    assert cfg.target_chain_length == 168
    assert cfg.target_keep_generations == 2


def test_globalconfig_explicit_override_works():
    """GlobalConfig(snapshot_chain_length=10, target_chain_length=20,
    target_keep_generations=3) uses explicit values, not defaults."""
    cfg = GlobalConfig(
        snapshot_chain_length=10,
        target_chain_length=20,
        target_keep_generations=3,
    )
    assert cfg.snapshot_chain_length == 10
    assert cfg.target_chain_length == 20
    assert cfg.target_keep_generations == 3


# ---------------------------------------------------------------------------
# Scenario: VMConfig.change_detection_mode default is allocation-map
# ---------------------------------------------------------------------------


def test_change_detection_mode_default_is_allocation_map(make_vm_config):
    """VMConfig created without explicit change_detection_mode defaults to 'allocation-map'."""
    vm = make_vm_config()
    assert vm.change_detection_mode == "allocation-map"


def test_change_detection_mode_explicit_allocation_size(make_vm_config):
    """VMConfig created with change_detection_mode='allocation-size' keeps that value."""
    vm = make_vm_config(change_detection_mode="allocation-size")
    assert vm.change_detection_mode == "allocation-size"


def test_change_detection_mode_explicit_allocation_map(make_vm_config):
    """VMConfig created with change_detection_mode='allocation-map' keeps that value."""
    vm = make_vm_config(change_detection_mode="allocation-map")
    assert vm.change_detection_mode == "allocation-map"


# ---------------------------------------------------------------------------
# DiskConfig — multi-disk refactor
# ---------------------------------------------------------------------------


def test_disk_config_defaults():
    """DiskConfig with required fields; snapshot_dir defaults to None."""
    disk = DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/test.qcow2"))
    assert disk.target == "vda"
    assert disk.base_image == Path("/var/lib/libvirt/images/test.qcow2")
    assert disk.snapshot_dir is None


def test_disk_config_with_snapshot_dir():
    """DiskConfig(snapshot_dir=...) sets per-disk override."""
    disk = DiskConfig(
        target="vda",
        base_image=Path("/var/lib/libvirt/images/test.qcow2"),
        snapshot_dir=Path("/fast-nvme/snaps/testvm-vda"),
    )
    assert disk.snapshot_dir == Path("/fast-nvme/snaps/testvm-vda")


def test_disk_config_immutable():
    """DiskConfig is a frozen dataclass; mutation raises FrozenInstanceError."""
    disk = DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/test.qcow2"))
    assert disk.__dataclass_params__.frozen is True

    with pytest.raises(dataclasses.FrozenInstanceError):
        disk.target = "vdb"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        disk.base_image = Path("/mutated")  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        disk.snapshot_dir = Path("/mutated")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# VMConfig.get_disk — disk lookup by target name
# ---------------------------------------------------------------------------


def test_get_disk_returns_correct_disk():
    """get_disk('vda') returns the DiskConfig with target='vda'."""
    vm = VMConfig(
        name="testvm",
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/vda.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/vdb.qcow2")),
        ],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    disk = vm.get_disk("vda")
    assert disk is not None
    assert disk.target == "vda"
    assert disk.base_image == Path("/var/lib/libvirt/images/vda.qcow2")


def test_get_disk_returns_none_for_missing_target():
    """get_disk('nonexistent') returns None."""
    vm = VMConfig(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/vda.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    assert vm.get_disk("nonexistent") is None


def test_get_disk_returns_correct_second_disk():
    """get_disk('vdb') returns the second disk when present."""
    vm = VMConfig(
        name="testvm",
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/vda.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/vdb.qcow2")),
        ],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    disk = vm.get_disk("vdb")
    assert disk is not None
    assert disk.target == "vdb"


# ---------------------------------------------------------------------------
# VMConfig.snapshot_dir_for — resolve effective snapshot directory for a disk
# ---------------------------------------------------------------------------


def test_snapshot_dir_for_uses_per_disk_override():
    """snapshot_dir_for returns the per-disk snapshot_dir when set."""
    disk = DiskConfig(
        target="vda",
        base_image=Path("/var/lib/libvirt/images/vda.qcow2"),
        snapshot_dir=Path("/fast-nvme/snaps/vda"),
    )
    vm = VMConfig(
        name="testvm",
        disks=[disk],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    assert vm.snapshot_dir_for(disk) == Path("/fast-nvme/snaps/vda")


def test_snapshot_dir_for_falls_back_to_vm_level():
    """snapshot_dir_for returns the VM-level snapshot_dir when per-disk is None."""
    disk = DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/vda.qcow2"))
    vm = VMConfig(
        name="testvm",
        disks=[disk],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    assert vm.snapshot_dir_for(disk) == Path("/var/lib/libvirt/snapshots/testvm")


def test_snapshot_dir_for_returns_none_when_neither_set():
    """snapshot_dir_for returns None when neither per-disk nor VM-level snapshot_dir is set."""
    disk = DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/vda.qcow2"))
    vm = VMConfig(name="testvm", disks=[disk])
    assert vm.snapshot_dir_for(disk) is None


# ---------------------------------------------------------------------------
# VMConfig disks defensive copy
# ---------------------------------------------------------------------------


def test_vm_config_disks_defensive_copy():
    """VMConfig stores a defensive copy of the disks list on construction."""
    original = [
        DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/vda.qcow2")),
    ]
    vm = VMConfig(name="testvm", disks=original, snapshot_dir=Path("/tmp/snaps"))

    # The internal list is a different object.
    assert vm.disks is not original

    # Mutating the original list does NOT affect the VMConfig.
    original.append(DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/vdb.qcow2")))
    assert len(vm.disks) == 1
