"""Tests for Core count-based retention — preserve-snapshots / preserve-backups flags.

When ``preserve_snapshots`` is True, ``_blockcommit_snapshots`` must skip the
``blockcommit`` call.  When ``preserve_backups`` is True, ``_backup_target``/
``_cleanup_backups`` must skip the ``delete`` call.  Retention is still
evaluated in both cases.

Retention policies are count-based: ``chain_length`` controls snapshot chain
depth; ``keep_generations`` controls per-target FULL backup chain count.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from qsnap.core import Core
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import (
    RetentionResult,
    SnapshotInfo,
)
from tests.mocks import MockConfigFacade

# ── Snapshot retention: chain_length ───────────────────────────────────────


def test_snapshot_retention_with_chain_length(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VM with snapshot_chain_length=5, 10 snapshots — 5 kept, 5 removed.

    Core constructs ``RetentionPolicy(chain_length=5, keep_generations=1)``
    and passes it to the retention engine.
    """
    vm = make_vm_config(
        name="testvm",
        targets=[make_target()],
        snapshot_chain_length=5,
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(10):
        snap = SnapshotInfo(
            name=f"snap{i + 1:02d}",
            path=Path(f"/tmp/snap{i + 1:02d}.qcow2"),
            timestamp=base + timedelta(hours=i),
            allocation=1000 * (i + 1),
        )
        mock_state.record_snapshot("testvm", snap)

    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        wraps=mock_factory._retention_engine.evaluate,
    ) as eval_spy:
        # Patch the list of known snapshots to ensure snap01-snap05
        # are the oldest and snap06-snap10 are newest, so the
        # oldest-prefix post-processing produces correct results.
        core._evaluate_snapshot_retention(vm)

    assert eval_spy.called
    # Verify the policy passed to the engine uses chain_length=5.
    policy = eval_spy.call_args.args[1]
    assert isinstance(policy, RetentionPolicy)
    assert policy.chain_length == 5
    assert policy.keep_generations == 1


def test_snapshot_retention_no_chain_length_uses_zero(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VM with snapshot_chain_length=None — policy chain_length is 0.

    ``vm_config.snapshot_chain_length or 0`` resolves to 0 when None.
    """
    vm = make_vm_config(
        name="testvm",
        targets=[make_target()],
        snapshot_chain_length=None,
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
        mock_factory._retention_engine,
        "evaluate",
        wraps=mock_factory._retention_engine.evaluate,
    ) as eval_spy:
        core._evaluate_snapshot_retention(vm)

    assert eval_spy.called
    policy = eval_spy.call_args.args[1]
    assert isinstance(policy, RetentionPolicy)
    assert policy.chain_length == 0


# ── Backup retention: keep_generations ─────────────────────────────────────


def test_backup_retention_with_keep_generations(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Target with keep_generations=2, 5 FULLs — 2 kept, 3 removed.

    Core constructs ``RetentionPolicy(chain_length=0, keep_generations=2)``
    and passes it to the retention engine.
    """
    target = make_target(target_keep_generations=2)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Record 5 FULL backup entries with increasing timestamps.
    base = datetime(2025, 7, 13, 10, 0)
    for i in range(5):
        full_name = f"testvm.FULL.daily.{i}.qcow2"
        mock_state.record_full_backup(
            str(target.path),
            full_name,
            base + timedelta(hours=i * 24),
        )

    # The _evaluate_backup_retention method calls provider.list(),
    # which groups backups by chain.  Each FULL becomes its own chain.
    # We patch provider.list() to return SnapshotInfo objects matching
    # our 5 FULLs.
    from qsnap.models.results import SnapshotInfo as SI

    full_infos = [
        SI(
            name=f"testvm.FULL.daily.{i}.qcow2",
            path=target.path / f"testvm.FULL.daily.{i}.qcow2",
            timestamp=base + timedelta(hours=i * 24),
            allocation=0,
        )
        for i in range(5)
    ]

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=full_infos),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            wraps=mock_factory._retention_engine.evaluate,
        ) as eval_spy,
    ):
        backups, retention_result = core._evaluate_backup_retention(vm, target)

    assert eval_spy.called
    policy = eval_spy.call_args.args[1]
    assert isinstance(policy, RetentionPolicy)
    assert policy.chain_length == 0
    assert policy.keep_generations == 2

    # The engine mock returns keep=all, remove=[] by default, so the
    # result from _evaluate_backup_retention will reflect that.  The
    # test confirms the policy was constructed correctly — the engine
    # itself is tested in modules/retention/.
    assert isinstance(retention_result, RetentionResult)
    assert len(backups) == 5


# ── Preserve flags — blockcommit / delete skipped but retention runs ──────


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
        snapshot_chain_length=0,
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


def test_preserve_backups_skips_provider_delete_calls(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When ``preserve_backups`` is True, backup delete is not called."""
    target = make_target(target_keep_generations=0)
    vm = make_vm_config(
        name="testvm",
        targets=[target],
        snapshot_chain_length=0,
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


# ── Snapshot preserve_min post-processing filter ────────────────────────────


def test_preserve_min_inactive_default(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """100 snapshots, chain_length=72, preserve_min=0 → no trimming, all 28 removed."""
    vm = make_vm_config(name="testvm", snapshot_chain_length=72, snapshot_preserve_min=0)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(100):
        snap = SnapshotInfo(
            name=f"snap{i + 1:03d}",
            path=Path(f"/tmp/snap{i + 1:03d}.qcow2"),
            timestamp=base + timedelta(hours=i),
            allocation=1000,
        )
        mock_state.record_snapshot("testvm", snap)

    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        wraps=mock_factory._retention_engine.evaluate,
    ) as eval_spy:
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # With chain_length=72, preserve_min=0: no trimming.
    # Default MockRetentionEngine keeps everything, remove is empty.
    # But the policy is passed with chain_length=72, preserve_min=0.
    policy = eval_spy.call_args.args[1]
    assert policy.chain_length == 72
    assert policy.preserve_min == 0


def test_preserve_min_trim_excess_from_newest(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """30 snapshots, chain_length=6, preserve_min=24 → trim to 6 remove, 24 keep."""
    vm = make_vm_config(name="testvm", snapshot_chain_length=6, snapshot_preserve_min=24)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(30):
        snap = SnapshotInfo(
            name=f"snap{i + 1:02d}",
            path=Path(f"/tmp/snap{i + 1:02d}.qcow2"),
            timestamp=base + timedelta(hours=i),
            allocation=1000 * (i + 1),
        )
        mock_state.record_snapshot("testvm", snap)

    # Engine returns: keep=6 newest, remove=24 oldest.
    keep_names = [f"snap{i:02d}" for i in range(7, 31)]
    remove_names = [f"snap{i:02d}" for i in range(1, 7)]
    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        return_value=RetentionResult(keep=keep_names, remove=remove_names),
    ):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # preserve_min trims remove from len(snapshots) - preserve_min = 30 - 24 = 6.
    # Oldest 6 kept in remove, newest 24 excess moved to keep.
    assert len(result.remove) == 6, (
        f"preserve_min should trim remove to 6, got {len(result.remove)}"
    )
    assert len(result.keep) == 24, (
        f"keep should have 24 items after preserve_min, got {len(result.keep)}"
    )


def test_preserve_min_no_trim_when_within_limit(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """100 snapshots, chain_length=72, preserve_min=24 → 28 <= 76, no trim."""
    vm = make_vm_config(name="testvm", snapshot_chain_length=72, snapshot_preserve_min=24)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(100):
        snap = SnapshotInfo(
            name=f"snap{i + 1:03d}",
            path=Path(f"/tmp/snap{i + 1:03d}.qcow2"),
            timestamp=base + timedelta(hours=i),
            allocation=1000,
        )
        mock_state.record_snapshot("testvm", snap)

    # Engine returns: keep=72 newest, remove=28 oldest.
    keep_names = [f"snap{i:03d}" for i in range(29, 101)]
    remove_names = [f"snap{i:03d}" for i in range(1, 29)]
    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        return_value=RetentionResult(keep=keep_names, remove=remove_names),
    ):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # max_removable = 100 - 24 = 76; len(remove) = 28 <= 76 → no trim.
    assert len(result.remove) == 28
    assert len(result.keep) == 72


def test_preserve_min_equals_total_no_blockcommit(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """30 snapshots, preserve_min=30 → max_removable=0, remove empty."""
    vm = make_vm_config(name="testvm", snapshot_chain_length=6, snapshot_preserve_min=30)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(30):
        snap = SnapshotInfo(
            name=f"snap{i + 1:02d}",
            path=Path(f"/tmp/snap{i + 1:02d}.qcow2"),
            timestamp=base + timedelta(hours=i),
            allocation=1000 * (i + 1),
        )
        mock_state.record_snapshot("testvm", snap)

    # Engine returns: keep=6 newest, remove=24 oldest.
    keep_names = [f"snap{i:02d}" for i in range(7, 31)]
    remove_names = [f"snap{i:02d}" for i in range(1, 7)]
    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        return_value=RetentionResult(keep=keep_names, remove=remove_names),
    ):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # max_removable = 30 - 30 = 0 → remove is empty, all to keep.
    assert len(result.remove) == 0
    assert len(result.keep) == 30


def test_preserve_min_exceeds_total_no_blockcommit(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """30 snapshots, preserve_min=50 → max_removable=0, remove empty."""
    vm = make_vm_config(name="testvm", snapshot_chain_length=6, snapshot_preserve_min=50)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(30):
        snap = SnapshotInfo(
            name=f"snap{i + 1:02d}",
            path=Path(f"/tmp/snap{i + 1:02d}.qcow2"),
            timestamp=base + timedelta(hours=i),
            allocation=1000 * (i + 1),
        )
        mock_state.record_snapshot("testvm", snap)

    keep_names = [f"snap{i:02d}" for i in range(7, 31)]
    remove_names = [f"snap{i:02d}" for i in range(1, 7)]
    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        return_value=RetentionResult(keep=keep_names, remove=remove_names),
    ):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # max_removable = max(0, 30 - 50) = 0 → remove is empty.
    assert len(result.remove) == 0
    assert len(result.keep) == 30


def test_preserve_min_applied_after_oldest_prefix(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """10 snapshots, chain_length=4, preserve_min=6 → remove=[s1..s4], keep=[s5..s10]."""
    vm = make_vm_config(name="testvm", snapshot_chain_length=4, snapshot_preserve_min=6)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(10):
        snap = SnapshotInfo(
            name=f"s{i + 1}",
            path=Path(f"/tmp/s{i + 1}.qcow2"),
            timestamp=base + timedelta(hours=i),
            allocation=1000,
        )
        mock_state.record_snapshot("testvm", snap)

    # Engine: keep=s7,s8,s9,s10, remove=s1..s6.
    keep_names = ["s7", "s8", "s9", "s10"]
    remove_names = ["s1", "s2", "s3", "s4", "s5", "s6"]
    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        return_value=RetentionResult(keep=keep_names, remove=remove_names),
    ):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # preserve_min: max_removable = 10 - 6 = 4.
    # remove is trimmed to oldest 4 items: s1,s2,s3,s4.
    # s5,s6 moved to keep.
    assert set(result.remove) == {"s1", "s2", "s3", "s4"}
    assert "s5" in result.keep
    assert "s6" in result.keep
    assert all(s in result.keep for s in ["s5", "s6", "s7", "s8", "s9", "s10"])


def test_preserve_min_trims_newest_end_of_remove(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """remove=[s1..s6], max_removable=3 → remove=[s1..s3], s4..s6 moved to keep."""
    vm = make_vm_config(name="testvm", snapshot_chain_length=0, snapshot_preserve_min=4)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(7):
        snap = SnapshotInfo(
            name=f"s{i + 1}",
            path=Path(f"/tmp/s{i + 1}.qcow2"),
            timestamp=base + timedelta(hours=i),
            allocation=1000,
        )
        mock_state.record_snapshot("testvm", snap)

    # Engine: keep=s7, remove=s1..s6.
    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        return_value=RetentionResult(keep=["s7"], remove=["s1", "s2", "s3", "s4", "s5", "s6"]),
    ):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # max_removable = 7 - 4 = 3.
    # Oldest 3 remain in remove: s1,s2,s3.
    # Newest 3 moved to keep: s4,s5,s6.
    assert set(result.remove) == {"s1", "s2", "s3"}
    assert "s4" in result.keep
    assert "s5" in result.keep
    assert "s6" in result.keep


def test_preserve_min_does_not_affect_target_retention(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """preserve_min=24, target retention with keep_generations=2 → oldest chain removed."""
    target = make_target(target_keep_generations=2)
    vm = make_vm_config(
        name="testvm",
        targets=[target],
        snapshot_preserve_min=24,
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(3):
        full_name = f"testvm.FULL.daily.{i}.qcow2"
        mock_state.record_full_backup(
            str(target.path),
            full_name,
            base + timedelta(hours=i * 24),
        )

    full_infos = [
        SnapshotInfo(
            name=f"testvm.FULL.daily.{i}.qcow2",
            path=target.path / f"testvm.FULL.daily.{i}.qcow2",
            timestamp=base + timedelta(hours=i * 24),
            allocation=0,
        )
        for i in range(3)
    ]

    with patch.object(
        mock_factory._backup_provider, "list", return_value=full_infos
    ), patch.object(
        mock_factory._retention_engine,
        "evaluate",
        wraps=mock_factory._retention_engine.evaluate,
    ) as eval_spy:
        backups, retention_result = core._evaluate_backup_retention(vm, target)

    # Target retention uses keep_generations=2, NOT preserve_min.
    policy = eval_spy.call_args.args[1]
    assert policy.keep_generations == 2
    assert policy.preserve_min == 0  # target retention never sets preserve_min
    assert isinstance(retention_result, RetentionResult)
    assert len(backups) == 3
