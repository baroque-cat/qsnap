"""Tests for Core pipeline step ordering, command isolation, and dry-run mode.

Covers:
- Pipeline step order for ``always`` and ``onchange`` snapshot modes.
- Error isolation between VMs (RISK test-plan.md line 137).
- Command isolation: ``snapshot()``, ``backup()``, ``prune()`` each run
  only their respective steps.
- Dry-run mode (RISK test-plan.md line 138): no state mutation, no shell
  mutation, no snapshot creation.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qsnap.core import Core, PipelineResult
from qsnap.models.config import VMConfig
from qsnap.models.results import ChangeResult, SnapshotInfo
from tests.mocks import MockConfigFacade

# ── test_pipeline_always_mode_creates_snapshot ───────────────────────────


def test_pipeline_always_mode_creates_snapshot(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """In ``always`` mode, a snapshot is created and change detection is skipped.

    The pipeline should call ``snapshot_provider.create()`` and should NOT
    call ``factory.create_change_detector()`` (always mode bypasses change
    detection entirely).
    """
    vm = make_vm_config(name="testvm", snapshot_create="always")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider

    with (
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
        patch.object(
            mock_factory,
            "create_change_detector",
            wraps=mock_factory.create_change_detector,
        ) as cd_spy,
    ):
        result = core.run()

    # Snapshot creation was invoked.
    assert create_spy.called, "Snapshot provider.create() should be called in always mode"

    # Change detection was NOT invoked (always mode skips it).
    assert not cd_spy.called, "create_change_detector() should NOT be called in always mode"

    # Pipeline succeeded.
    assert result.success is True


# ── test_pipeline_onchange_no_changes_skips_snapshot ─────────────────────


def test_pipeline_onchange_no_changes_skips_snapshot(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """In ``onchange`` mode with no detected changes, no snapshot is created.

    The change detector reports ``has_changed=False``, so the pipeline
    should skip snapshot creation.  Retention is still evaluated (but with
    an empty state, it returns None).
    """
    vm = make_vm_config(name="testvm", snapshot_create="onchange")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider
    change_detector = mock_factory._change_detector

    # Configure the change detector to report no changes.
    no_change = ChangeResult(
        has_changed=False,
        last_allocation=1000,
        current_allocation=1000,
    )

    with (
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
        patch.object(
            change_detector,
            "has_changed",
            return_value=no_change,
        ),
    ):
        result = core.run()

    # Snapshot creation was NOT invoked (no changes detected).
    assert not create_spy.called, (
        "Snapshot provider.create() should NOT be called when onchange "
        "detector reports has_changed=False"
    )

    # Pipeline still succeeded (skipping a snapshot is not an error).
    assert result.success is True


# ── test_error_isolation_between_vms ─────────────────────────────────────


def test_error_isolation_between_vms(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """An error processing one VM does NOT prevent other VMs from succeeding.

    RISK (test-plan.md line 137): Core pipeline error isolation must not
    silently swallow errors.  The return value of ``core.run()`` must include
    per-VM status indicating which succeeded and which failed, and the error
    for the failing VM must be captured in the result.
    """
    vm1 = make_vm_config(name="vm1")
    vm2 = make_vm_config(name="vm2")
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Make the factory raise for vm1 but return the normal mock for vm2.
    snapshot_provider = mock_factory._snapshot_provider

    def side_effect(vm_config: VMConfig):
        if vm_config.name == "vm1":
            raise RuntimeError("Simulated failure for vm1")
        return snapshot_provider

    with patch.object(
        mock_factory,
        "create_snapshot_provider",
        side_effect=side_effect,
    ):
        result = core.run()

    # Return value is a PipelineResult with per-VM status.
    assert isinstance(result, PipelineResult)
    assert len(result.results) == 2

    # Build a map for easy lookup.
    result_map = {r.vm_name: r for r in result.results}

    # vm1 failed — error is captured, not silently swallowed.
    assert result_map["vm1"].success is False
    assert result_map["vm1"].error is not None
    assert "Simulated failure" in result_map["vm1"].error

    # vm2 succeeded despite vm1's failure (error isolation).
    assert result_map["vm2"].success is True
    assert result_map["vm2"].error is None

    # Aggregate success is False because at least one VM failed.
    assert result.success is False


# ── test_snapshot_command_skips_backup ───────────────────────────────────


def test_snapshot_command_skips_backup(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``core.snapshot()`` runs only snapshot steps (1-4); backup is skipped.

    The snapshot provider's ``create()`` should be called, but the backup
    provider's ``transfer_missing()`` should NOT be called.
    """
    vm = make_vm_config(name="testvm", targets=[make_target()])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider
    backup_provider = mock_factory._backup_provider

    with (
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
        patch.object(
            backup_provider,
            "transfer_missing",
            wraps=backup_provider.transfer_missing,
        ) as transfer_spy,
    ):
        result = core.snapshot()

    # Snapshot steps were executed.
    assert create_spy.called, "snapshot() should call snapshot_provider.create()"

    # Backup steps were NOT executed.
    assert not transfer_spy.called, "snapshot() should NOT call backup_provider.transfer_missing()"

    # Pipeline succeeded.
    assert result.success is True


# ── test_backup_command_skips_snapshot ───────────────────────────────────


def test_backup_command_skips_snapshot(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``core.backup()`` runs only backup steps (5); snapshot creation is skipped.

    The backup provider's ``transfer_missing()`` should be called (the VM
    has a target), but the snapshot provider's ``create()`` should NOT be
    called.
    """
    vm = make_vm_config(name="testvm", targets=[make_target()])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider
    backup_provider = mock_factory._backup_provider

    with (
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
        patch.object(
            backup_provider,
            "transfer_missing",
            wraps=backup_provider.transfer_missing,
        ) as transfer_spy,
    ):
        result = core.backup()

    # Backup steps were executed.
    assert transfer_spy.called, "backup() should call backup_provider.transfer_missing()"

    # Snapshot creation was NOT executed.
    assert not create_spy.called, "backup() should NOT call snapshot_provider.create()"

    # Pipeline succeeded.
    assert result.success is True


# ── test_prune_command_only_retention ────────────────────────────────────


def test_prune_command_only_retention(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``core.prune()`` runs only retention + lifecycle; no snapshot or backup transfer.

    Retention evaluation should be called (the state is pre-populated with a
    snapshot so the retention engine is invoked).  Snapshot creation and
    backup transfer should NOT be called.
    """
    vm = make_vm_config(name="testvm", targets=[make_target()])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with a recorded snapshot so that retention
    # evaluation is actually triggered (otherwise get_snapshots returns []
    # and the retention engine is never created).
    info = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime.now(),
        allocation=1000,
    )
    mock_state.record_snapshot(vm.name, info)

    snapshot_provider = mock_factory._snapshot_provider
    backup_provider = mock_factory._backup_provider

    with (
        patch.object(
            mock_factory,
            "create_retention_engine",
            wraps=mock_factory.create_retention_engine,
        ) as retention_spy,
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
        patch.object(
            backup_provider,
            "transfer_missing",
            wraps=backup_provider.transfer_missing,
        ) as transfer_spy,
    ):
        result = core.prune()

    # Retention evaluation was called.
    assert retention_spy.called, "prune() should call create_retention_engine()"

    # Snapshot creation was NOT called.
    assert not create_spy.called, "prune() should NOT call snapshot_provider.create()"

    # Backup transfer was NOT called.
    assert not transfer_spy.called, "prune() should NOT call backup_provider.transfer_missing()"

    # Pipeline succeeded.
    assert result.success is True


# ── test_dry_run_logs_no_mutation ────────────────────────────────────────


def test_dry_run_logs_no_mutation(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """In dry-run mode, no mutation occurs: no state writes, no shell calls,
    no snapshot creation.  Planned actions are logged at INFO level.

    RISK (test-plan.md line 138): Dry-run mode must not mutate state via
    ``IStateManager.set_last_allocation`` or ``record_snapshot``, and
    ``IShell.run`` must never be called with mutating commands.
    Each planned action is logged at INFO level.
    """
    caplog.set_level(logging.INFO)
    vm = make_vm_config(name="testvm", targets=[make_target()])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.dry_run = True

    snapshot_provider = mock_factory._snapshot_provider

    with (
        patch.object(
            mock_state,
            "set_last_allocation",
            wraps=mock_state.set_last_allocation,
        ) as set_alloc_spy,
        patch.object(
            mock_state,
            "record_snapshot",
            wraps=mock_state.record_snapshot,
        ) as record_spy,
        patch.object(
            mock_shell,
            "run",
            wraps=mock_shell.run,
        ) as shell_spy,
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
    ):
        result = core.run()

    # IStateManager.set_last_allocation was NEVER called.
    set_alloc_spy.assert_not_called()

    # IStateManager.record_snapshot was NEVER called.
    record_spy.assert_not_called()

    # IShell.run was NEVER called (no mutating virsh/qemu-img commands).
    shell_spy.assert_not_called()

    # Snapshot provider's create() was NOT called (dry-run skips actual
    # mutations).
    create_spy.assert_not_called()

    # Pipeline still "succeeds" — dry-run is not an error, it just skips
    # mutations.
    assert result.success is True

    # Dry-run logs planned actions at INFO level.
    assert "[dry-run]" in caplog.text
