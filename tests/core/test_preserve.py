"""Tests for Core preserve-snapshots and preserve-backups flags.

When ``preserve_snapshots`` is True, ``_blockcommit_snapshots`` must
skip the ``blockcommit`` call.  When ``preserve_backups`` is True,
``_backup_target`` must skip the ``delete`` call.  Retention is still
evaluated in both cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from qsnap.core import Core
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import (
    BackupResult,
    RetentionResult,
    SnapshotInfo,
)
from tests.mocks import MockConfigFacade

# ── test_preserve_snapshots_defaults_to_false ────────────────────────────


def test_preserve_snapshots_defaults_to_false(
    mock_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Both preserve flags default to ``False``."""
    core = Core(
        config=mock_config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    assert core.preserve_snapshots is False
    assert core.preserve_backups is False


# ── test_preserve_snapshots_skips_blockcommit_call ───────────────────────


def test_preserve_snapshots_skips_blockcommit_call(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When ``preserve_snapshots`` is True, blockcommit is not called."""
    vm = make_vm_config(
        name="testvm",
        targets=[make_target()],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.preserve_snapshots = True

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(2):
        snap = SnapshotInfo(
            name=f"snap{i + 1}",
            path=Path(f"/tmp/snap{i + 1}.qcow2"),
            timestamp=base + timedelta(hours=i),
            allocation=1000 * (i + 1),
        )
        mock_state.record_snapshot("testvm", snap)

    with (
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=["snap1"], remove=["snap2"]),
        ),
        patch.object(
            mock_factory._lifecycle_manager,
            "blockcommit",
            wraps=mock_factory._lifecycle_manager.blockcommit,
        ) as bc_spy,
        patch.object(
            mock_factory,
            "create_retention_engine",
            wraps=mock_factory.create_retention_engine,
        ) as retention_spy,
    ):
        result = core.run()

    assert result.success is True
    bc_spy.assert_not_called()
    assert retention_spy.called


# ── test_preserve_backups_skips_provider_delete_calls ────────────────────


def test_preserve_backups_skips_provider_delete_calls(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When ``preserve_backups`` is True, backup delete is not called."""
    vm = make_vm_config(
        name="testvm",
        targets=[make_target()],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.preserve_backups = True

    base = datetime(2025, 7, 13, 10, 0)
    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=base,
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    backups = [
        SnapshotInfo(
            name="snap1",
            path=Path("/mnt/backup/snap1.qcow2"),
            timestamp=base,
            allocation=1000,
        ),
        SnapshotInfo(
            name="snap2",
            path=Path("/mnt/backup/snap2.qcow2"),
            timestamp=base + timedelta(hours=1),
            allocation=2000,
        ),
    ]

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=backups),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=["snap1"], remove=["snap2"]),
        ),
        patch.object(
            mock_factory._backup_provider,
            "delete",
            wraps=mock_factory._backup_provider.delete,
        ) as del_spy,
        patch.object(
            mock_factory,
            "create_retention_engine",
            wraps=mock_factory.create_retention_engine,
        ) as retention_spy,
    ):
        result = core.run()

    assert result.success is True
    del_spy.assert_not_called()
    assert retention_spy.called


# ── test_preserve_both_skips_all_deletion ────────────────────────────────


def test_preserve_both_skips_all_deletion(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When both preserve flags are True, neither blockcommit nor delete is called."""
    vm = make_vm_config(
        name="testvm",
        targets=[make_target()],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.preserve_snapshots = True
    core.preserve_backups = True

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(2):
        snap = SnapshotInfo(
            name=f"snap{i + 1}",
            path=Path(f"/tmp/snap{i + 1}.qcow2"),
            timestamp=base + timedelta(hours=i),
            allocation=1000 * (i + 1),
        )
        mock_state.record_snapshot("testvm", snap)

    backups = [
        SnapshotInfo(
            name="snap2",
            path=Path("/mnt/backup/snap2.qcow2"),
            timestamp=base + timedelta(hours=1),
            allocation=2000,
        ),
    ]

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=backups),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=["snap1"], remove=["snap2"]),
        ),
        patch.object(
            mock_factory._lifecycle_manager,
            "blockcommit",
            wraps=mock_factory._lifecycle_manager.blockcommit,
        ) as bc_spy,
        patch.object(
            mock_factory._backup_provider,
            "delete",
            wraps=mock_factory._backup_provider.delete,
        ) as del_spy,
    ):
        result = core.run()

    assert result.success is True
    bc_spy.assert_not_called()
    del_spy.assert_not_called()


# ── test_preserve_mode_failed_backup_error_reported_no_deletion ──────────


def test_preserve_mode_failed_backup_error_reported_no_deletion(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When ``preserve_backups`` is True and transfer fails, delete is not called."""
    vm = make_vm_config(
        name="testvm",
        targets=[make_target()],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.preserve_backups = True

    base = datetime(2025, 7, 13, 10, 0)
    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=base,
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    failed_transfer = [
        BackupResult(
            success=False,
            snapshot_name="snap1",
            source_path=Path("/tmp/snap1.qcow2"),
            target_path=Path("/mnt/backup/snap1.qcow2"),
            bytes_transferred=0,
            error="transfer failed",
        )
    ]

    backups = [
        SnapshotInfo(
            name="snap1",
            path=Path("/mnt/backup/snap1.qcow2"),
            timestamp=base,
            allocation=1000,
        ),
    ]

    with (
        patch.object(
            mock_factory._backup_provider,
            "transfer_missing",
            return_value=failed_transfer,
        ),
        patch.object(mock_factory._backup_provider, "list", return_value=backups),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=[], remove=["snap1"]),
        ),
        patch.object(
            mock_factory._backup_provider,
            "delete",
            wraps=mock_factory._backup_provider.delete,
        ) as del_spy,
    ):
        core.run()

    del_spy.assert_not_called()


# ── test_preserve_snapshots_retention_still_evaluated ─────────────────────


def test_preserve_snapshots_retention_still_evaluated(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When ``preserve_snapshots`` is True, retention is still evaluated."""
    vm = make_vm_config(
        name="testvm",
        targets=[make_target()],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.preserve_snapshots = True

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    with patch.object(
        mock_factory,
        "create_retention_engine",
        wraps=mock_factory.create_retention_engine,
    ) as retention_spy:
        core.run()

    assert retention_spy.called


# ── test_parse_preserve_explicit_min_overrides_default ────────────────────


def test_parse_preserve_explicit_min_overrides_default():
    """An explicit preserve_min_str overrides the default ``0h`` value."""
    policy = Core._parse_preserve("24h 7d", preserve_min_str="2h")
    assert policy.preserve_min == "2h"
    assert policy.hourly == 24
    assert policy.daily == 7


# ── test_parse_preserve_none_uses_default_zero_h ──────────────────────────


def test_parse_preserve_none_uses_default_zero_h():
    """When preserve_min_str is None and preserve_str is non-None, default is ``0h``."""
    policy = Core._parse_preserve("24h 7d")
    assert policy.preserve_min == "0h"
    assert policy.hourly == 24
    assert policy.daily == 7


# ── test_evaluate_snapshot_retention_uses_vm_preserve_min ────────────────


def test_evaluate_snapshot_retention_uses_vm_preserve_min(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VMConfig.snapshot_preserve_min is passed through to _parse_preserve."""
    vm = make_vm_config(
        name="testvm",
        snapshot_preserve="24h",
        snapshot_preserve_min="2h",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    with patch.object(
        mock_factory,
        "create_retention_engine",
        wraps=mock_factory.create_retention_engine,
    ) as retention_spy:
        core._evaluate_snapshot_retention(vm)

    assert retention_spy.called
    policy = retention_spy.call_args.args[0]
    assert policy.preserve_min == "2h"


# ── test_evaluate_backup_retention_uses_target_preserve_min ──────────────


def test_evaluate_backup_retention_uses_target_preserve_min(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """TargetConfig.target_preserve_min is passed through to _parse_preserve."""
    target = make_target(target_preserve="48h", target_preserve_min="4h")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    backups = [
        SnapshotInfo(
            name="backup1",
            path=Path("/mnt/backup/backup1.qcow2"),
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=1000,
        ),
    ]

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=backups),
        patch.object(
            mock_factory,
            "create_retention_engine",
            wraps=mock_factory.create_retention_engine,
        ) as retention_spy,
    ):
        core._evaluate_backup_retention(vm, target)

    assert retention_spy.called
    policy = retention_spy.call_args.args[0]
    assert policy.preserve_min == "4h"


# ── F-syntax tests for _parse_preserve ────────────────────────────────────


def test_parse_preserve_f_anchor_single_bucket():
    """``7Fd`` sets anchor_daily=True; other anchors remain False."""
    policy = Core._parse_preserve("24h 7Fd 4w")
    assert policy.anchor_daily is True
    assert policy.anchor_hourly is False
    assert policy.anchor_weekly is False
    assert policy.anchor_monthly is False
    assert policy.anchor_yearly is False
    assert policy.hourly == 24
    assert policy.daily == 7
    assert policy.weekly == 4


def test_parse_preserve_f_anchor_all_buckets():
    """Every bucket with F prefix sets its anchor to True."""
    policy = Core._parse_preserve("24Fh 7Fd 4Fw 12Fm 1Fy")
    assert policy.anchor_hourly is True
    assert policy.anchor_daily is True
    assert policy.anchor_weekly is True
    assert policy.anchor_monthly is True
    assert policy.anchor_yearly is True
    assert policy.hourly == 24
    assert policy.daily == 7
    assert policy.weekly == 4
    assert policy.monthly == 12
    assert policy.yearly == 1


def test_parse_preserve_f_anchor_in_snapshot_preserve_no_error():
    """F-syntax is valid in snapshot_preserve context — no error raised."""
    policy = Core._parse_preserve("24Fh 7Fd")
    assert isinstance(policy, RetentionPolicy)
    assert policy.anchor_hourly is True
    assert policy.anchor_daily is True


def test_parse_preserve_no_f_prefix_anchors_false():
    """Non-F policies leave all anchor_* fields as False (backward compat)."""
    policy = Core._parse_preserve("24h 7d")
    assert policy.anchor_hourly is False
    assert policy.anchor_daily is False
    assert policy.anchor_weekly is False
    assert policy.anchor_monthly is False
    assert policy.anchor_yearly is False


def test_parse_preserve_f_prefix_invalid_bucket_ignored():
    """Token ``7Fx`` is silently ignored because 'x' doesn't match [hdwmy]."""
    policy = Core._parse_preserve("7Fx")
    assert policy.hourly == 0
    assert policy.daily == 0
    assert policy.weekly == 0
    assert policy.monthly == 0
    assert policy.yearly == 0
    assert policy.anchor_hourly is False
    assert policy.anchor_daily is False
    assert policy.anchor_weekly is False
    assert policy.anchor_monthly is False
    assert policy.anchor_yearly is False


# ── test_parse_preserve_all_maps_to_preserve_min_all ──────────────────────


def test_parse_preserve_all_maps_to_preserve_min_all():
    """``_parse_preserve("all", None)`` maps to ``preserve_min="all"`` and zero buckets."""
    policy = Core._parse_preserve("all", None)
    assert policy.preserve_min == "all"
    assert policy.hourly == 0
    assert policy.daily == 0
    assert policy.weekly == 0
    assert policy.monthly == 0
    assert policy.yearly == 0


# ── test_parse_preserve_all_with_explicit_min_overrides ───────────────────


def test_parse_preserve_all_with_explicit_min_overrides():
    """``_parse_preserve("all", "6h")`` — explicit min overrides to ``"6h"``, buckets zero."""
    policy = Core._parse_preserve("all", "6h")
    assert policy.preserve_min == "6h"
    assert policy.hourly == 0
    assert policy.daily == 0
    assert policy.weekly == 0


# ── test_parse_preserve_all_with_explicit_min_all ─────────────────────────


def test_parse_preserve_all_with_explicit_min_all():
    """``_parse_preserve("all", "all")`` — explicit min preserves ``"all"``."""
    policy = Core._parse_preserve("all", "all")
    assert policy.preserve_min == "all"


# ── test_parse_preserve_all_with_explicit_min_zero_h ──────────────────────


def test_parse_preserve_all_with_explicit_min_zero_h():
    """``_parse_preserve("all", "0h")`` — explicit ``"0h"`` override wins (contradictory but allowed)."""
    policy = Core._parse_preserve("all", "0h")
    assert policy.preserve_min == "0h"


# ── test_parse_preserve_none_defaults_to_all ──────────────────────────────


def test_parse_preserve_none_defaults_to_all():
    """``_parse_preserve(None, None)`` defaults to ``preserve_min="all"``."""
    policy = Core._parse_preserve(None, None)
    assert policy.preserve_min == "all"


# ── test_parse_preserve_bucket_string_unaffected ──────────────────────────


def test_parse_preserve_bucket_string_unaffected():
    """``_parse_preserve("24h 7d", None)`` — bucket parsing unchanged (regression guard)."""
    policy = Core._parse_preserve("24h 7d", None)
    assert policy.preserve_min == "0h"
    assert policy.hourly == 24
    assert policy.daily == 7
    assert policy.weekly == 0
    assert policy.monthly == 0
    assert policy.yearly == 0


# ── test_parse_preserve_all_defaults_to_all ───────────────────────────────


def test_parse_preserve_all_defaults_to_all():
    """``_parse_preserve("all")`` with default second arg — preserve_min is ``"all"``, no regex parsing."""
    policy = Core._parse_preserve("all")
    assert policy.preserve_min == "all"
    # Verify no regex parsing was attempted: all bucket counts must be zero.
    assert policy.hourly == 0
    assert policy.daily == 0
    assert policy.weekly == 0
    assert policy.monthly == 0
    assert policy.yearly == 0


# ── test_parse_preserve_all_with_explicit_all ─────────────────────────────


def test_parse_preserve_all_with_explicit_all():
    """``_parse_preserve("all", "all")`` — full RetentionPolicy field set, anchors all False."""
    policy = Core._parse_preserve("all", "all")
    assert policy.preserve_min == "all"
    assert policy.hourly == 0
    assert policy.daily == 0
    assert policy.weekly == 0
    assert policy.monthly == 0
    assert policy.yearly == 0
    assert policy.anchor_hourly is False
    assert policy.anchor_daily is False
    assert policy.anchor_weekly is False
    assert policy.anchor_monthly is False
    assert policy.anchor_yearly is False


# ── test_parse_preserve_all_with_explicit_6h ──────────────────────────────


def test_parse_preserve_all_with_explicit_6h():
    """``_parse_preserve("all", "6h")`` — preserve_min is ``"6h"`` AND all buckets == 0."""
    policy = Core._parse_preserve("all", "6h")
    assert policy.preserve_min == "6h"
    assert policy.hourly == 0
    assert policy.daily == 0
    assert policy.weekly == 0
    assert policy.monthly == 0
    assert policy.yearly == 0
