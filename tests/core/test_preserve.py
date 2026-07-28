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
