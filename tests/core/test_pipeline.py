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

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core, PipelineResult
from qsnap.models.config import VMConfig
from qsnap.models.results import (
    BackupResult,
    ChangeResult,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
    SnapshotResult,
)
from tests.mocks import MockBucketFullStrategy, MockConfigFacade

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
        patch.object(
            core,
            "_check_deferred_thresholds",
            wraps=core._check_deferred_thresholds,
        ) as deferred_spy,
    ):
        result = core.run()

    # Snapshot creation was invoked.
    assert create_spy.called, "Snapshot provider.create() should be called in always mode"

    # Change detection was NOT invoked (always mode skips it).
    assert not cd_spy.called, "create_change_detector() should NOT be called in always mode"

    # Deferred threshold check was called after pipeline.
    assert deferred_spy.called, "_check_deferred_thresholds() should be called after pipeline"

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

    The change detector reports ``changed=False``, so the pipeline
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
        changed=False,
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
        patch.object(
            core,
            "_check_deferred_thresholds",
            wraps=core._check_deferred_thresholds,
        ) as deferred_spy,
    ):
        result = core.run()

    # Snapshot creation was NOT invoked (no changes detected).
    assert not create_spy.called, (
        "Snapshot provider.create() should NOT be called when onchange "
        "detector reports changed=False"
    )

    # Deferred threshold check runs even when no snapshot created.
    assert deferred_spy.called, (
        "_check_deferred_thresholds() should run even when no snapshot created"
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

    # IShell.run may be called for read-only operations (_log_size_estimate
    # runs even in dry-run mode to provide size projections via qemu-img info
    # and du -sb).  In dry-run mode, _validate_environment() also runs
    # (design D6) making read-only validation calls (test, which, virsh
    # dominfo, find).  Verify only read-only shell calls were made.
    read_only_patterns = (
        "qemu-img info",
        "du",
        "test ",
        "which ",
        "virsh dominfo",
        "find",
    )
    for call in shell_spy.call_args_list:
        cmd = call[0][0]  # command list
        cmd_str = " ".join(cmd)
        assert any(p in cmd_str for p in read_only_patterns), (
            f"Unexpected shell call in dry-run: {cmd_str}"
        )

    # Snapshot provider's create() was NOT called (dry-run skips actual
    # mutations).
    create_spy.assert_not_called()

    # Pipeline still "succeeds" — dry-run is not an error, it just skips
    # mutations.
    assert result.success is True

    # Dry-run logs planned actions at INFO level.
    assert "[dry-run]" in caplog.text

    # Verify --force-share is used on qemu-img info read-only calls
    qemu_img_calls = [
        c
        for c in shell_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and "qemu-img" in " ".join(c.args[0])
    ]
    for call in qemu_img_calls:
        cmd_str = " ".join(call.args[0])
        if "info" in cmd_str:
            assert "--force-share" in cmd_str, (
                f"qemu-img info must use --force-share in dry-run, got: {cmd_str}"
            )


# ── test_create_snapshot_single_disk_sda_not_vda ─────────────────────────


def test_create_snapshot_single_disk_sda_not_vda(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Mock domblklist returning 'sda' disk. Verify snapshot name has _sda suffix."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    domblklist_output = (
        " Target   Source\n"
        "--------------------------------------\n"
        " sda      /var/lib/libvirt/images/testvm.qcow2\n"
    )
    mock_shell.expect("domblklist").returns(
        ShellResult(
            success=True,
            stdout=domblklist_output,
            stderr="",
            returncode=0,
            error=None,
        )
    )

    snapshot_provider = mock_factory._snapshot_provider
    with patch.object(
        snapshot_provider,
        "create",
        wraps=snapshot_provider.create,
    ) as create_spy:
        core.snapshot()

    assert create_spy.called
    snapshot_name = create_spy.call_args.args[1]
    disk = create_spy.call_args.args[2]
    assert disk == "sda"
    assert snapshot_name.endswith("_sda")
    assert not snapshot_name.endswith("_vda")


# ── test_create_snapshot_multi_disk_vda_vdb_creates_two_with_suffix ───────


def test_create_snapshot_multi_disk_vda_vdb_creates_two_with_suffix(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Mock domblklist returning vda and vdb. Verify two snapshots with _vda and _vdb."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    domblklist_output = (
        " Target   Source\n"
        "--------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
        " vdb      /var/lib/libvirt/images/testvm-disk2.qcow2\n"
    )
    mock_shell.expect("domblklist").returns(
        ShellResult(
            success=True,
            stdout=domblklist_output,
            stderr="",
            returncode=0,
            error=None,
        )
    )

    snapshot_provider = mock_factory._snapshot_provider
    with patch.object(
        snapshot_provider,
        "create",
        wraps=snapshot_provider.create,
    ) as create_spy:
        core.snapshot()

    assert create_spy.call_count == 2
    disk_names = [call.args[2] for call in create_spy.call_args_list]
    assert set(disk_names) == {"vda", "vdb"}
    snapshot_names = [call.args[1] for call in create_spy.call_args_list]
    assert any(name.endswith("_vda") for name in snapshot_names)
    assert any(name.endswith("_vdb") for name in snapshot_names)


# ── test_create_snapshot_explicit_disk_list_overrides_discovery ───────────


def test_create_snapshot_explicit_disk_list_overrides_discovery(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VMConfig.disks=['sda'] explicitly. Verify domblklist is NOT called, snapshot uses sda."""
    vm = make_vm_config(name="testvm", disks=["sda"])
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
        patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy,
    ):
        core.snapshot()

    # domblklist should NOT be called (explicit disk list overrides discovery)
    shell_spy.assert_not_called()
    # Snapshot should use sda
    assert create_spy.called
    assert create_spy.call_args.args[2] == "sda"
    assert create_spy.call_args.args[1].endswith("_sda")


# ── test_multi_disk_vda_succeeds_vdb_fails_continues_pipeline ─────────────


def test_multi_disk_vda_succeeds_vdb_fails_continues_pipeline(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """vda snapshot succeeds, vdb fails. Verify vda result recorded, vdb error logged, pipeline continues."""
    vm = make_vm_config(name="testvm", disks=["vda", "vdb"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider

    def create_side_effect(vm_config, snapshot_name, disk, snapshot_path, **kwargs):
        if disk == "vda":
            return SnapshotResult(
                success=True,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=65536,
                error=None,
            )
        return SnapshotResult(
            success=False,
            name=snapshot_name,
            path=snapshot_path,
            new_allocation=0,
            error="virsh timeout for vdb",
        )

    caplog.set_level(logging.ERROR)
    with (
        patch.object(snapshot_provider, "create", side_effect=create_side_effect),
    ):
        result = core.snapshot()

    # Pipeline succeeded (partial failure is not a pipeline failure)
    assert result.success is True

    # vda snapshot was recorded in state (vdb was not)
    snapshots = mock_state.get_snapshots("testvm")
    assert len(snapshots) == 1
    assert snapshots[0].name.endswith("_vda")

    # vdb error was logged
    assert "vdb" in caplog.text
    assert "virsh timeout" in caplog.text


# ── test_metadata_verification_failure_marks_backup_failed ────────────────


def test_metadata_verification_failure_marks_backup_failed(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """transfer_missing returns BackupResult(success=False, error="verification
    failed") → backup_failed=True.

    When the backup provider's transfer_missing returns a failed result with
    a verification error, the pipeline must set ``backup_failed=True`` on the
    VMRunResult so the CLI can exit with EXIT_BACKUP_ABORT.
    """
    vm = make_vm_config(name="testvm", targets=[make_target()])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state so transfer_missing has a snapshot to transfer.
    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime.now(),
        allocation=1000,
    )
    mock_state.record_snapshot(vm.name, snap)

    failed_backup = BackupResult(
        success=False,
        snapshot_name="snap1",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=Path("/mnt/backup/snap1.qcow2"),
        bytes_transferred=0,
        error="verification failed",
    )

    with patch.object(
        mock_factory._backup_provider,
        "transfer_missing",
        return_value=[failed_backup],
    ):
        result = core.run()

    assert len(result.results) == 1
    assert result.results[0].backup_failed is True


# ── test_pipeline_always_mode_validation_first ────────────────────────────


def test_pipeline_always_mode_validation_first(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Verify validation runs before snapshot creation in always mode.

    If validation fails, snapshot_provider.create() must NOT be called.
    This proves validation executes first and short-circuits the pipeline.
    """
    from qsnap.models.results import ShellResult

    # Make validation fail by overriding the snapshot_dir check.
    mock_shell._expectations = [e for e in mock_shell._expectations if e.pattern != "test -d"]
    mock_shell.expect("test -d").returns(
        ShellResult(success=False, stdout="", stderr="", returncode=1, error="not found")
    )

    vm = make_vm_config(name="testvm", snapshot_create="always", disks=["vda"])
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
            core,
            "_validate_environment",
            wraps=core._validate_environment,
        ) as validate_spy,
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
    ):
        result = core.run()

    # Validation was called and failed (pipeline stopped).
    assert validate_spy.called

    # Snapshot creation was NOT called (validation stopped the pipeline).
    assert not create_spy.called

    # Pipeline failed.
    assert result.results[0].success is False


# ── test_pipeline_onchange_no_changes_validation_first ───────────────────


def test_pipeline_onchange_no_changes_validation_first(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Verify validation runs even when no changes are detected (onchange mode).

    In onchange mode with no detected changes, the snapshot is skipped, but
    environment validation must still execute.  This proves validation runs
    before change detection.
    """
    vm = make_vm_config(name="testvm", snapshot_create="onchange", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider
    change_detector = mock_factory._change_detector

    no_change = ChangeResult(
        changed=False,
        last_allocation=1000,
        current_allocation=1000,
    )

    with (
        patch.object(
            core,
            "_validate_environment",
            wraps=core._validate_environment,
        ) as validate_spy,
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

    # Validation was called (runs before change detection).
    assert validate_spy.called

    # Snapshot was NOT created (no changes detected).
    assert not create_spy.called

    # Pipeline succeeded (skipping a snapshot is not an error).
    assert result.success is True


# ── test_first_backup_creates_full_via_strategy ─────────────────────────────


def test_first_backup_creates_full_via_strategy(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """First backup to target triggers FULL via MockBucketFullStrategy.

    Configuring MockBucketFullStrategy to return (True, "monthly") should
    cause ``_backup_target`` to call ``create_full_backup`` with that bucket_level.
    """
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
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

    # Configure MockBucketFullStrategy to return (True, "monthly")
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(
        return_value=(True, "monthly")
    )

    backup_provider = mock_factory._backup_provider

    with patch.object(
        backup_provider,
        "create_full_backup",
        wraps=backup_provider.create_full_backup,
    ) as full_spy:
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "create_full_backup should be called when strategy returns True"
    assert full_spy.call_args.kwargs.get("bucket_level") == "monthly", (
        "bucket_level should be 'monthly' as configured in MockBucketFullStrategy"
    )


# ── test_backup_target_passes_full_list_to_strategy ───────────────────────


def test_backup_target_passes_full_list_to_strategy(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """_backup_target passes the full list of FULLs to the bucket strategy.

    Verifies that state.get_full_backups() (list, not single) is used,
    and the list is passed through to the strategy via the factory mock.
    """
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Record 2 FULL backups (same day as snapshot to avoid triggering new FULL)
    mock_state.record_full_backup(
        str(target.path),
        "full1.FULL.daily.qcow2",
        datetime(2025, 7, 13, 2),
        "daily",
    )
    mock_state.record_full_backup(
        str(target.path),
        "full2.FULL.daily.qcow2",
        datetime(2025, 7, 13, 5),
        "daily",
    )

    # Snapshot on the same day → no new FULL triggered by strategy default
    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    strategy = mock_factory._bucket_full_strategy

    with patch(
        "qsnap.core.os.path.exists", return_value=True
    ):
        core._backup_target(vm, target, [snap])

    # Verify the strategy was called
    assert len(strategy.calls) > 0, "Bucket full strategy should be called during _backup_target"
    # Verify it received the correct full list
    all_fulls_arg = strategy.calls[0]["all_fulls"]
    assert isinstance(all_fulls_arg, list), (
        "all_fulls should be a list, not a single FullBackupInfo or None"
    )
    assert len(all_fulls_arg) == 2, (
        "all_fulls should contain all 2 FULLs from state.get_full_backups()"
    )


# ── test_core_delegates_bucket_decision_to_strategy ───────────────────────


def test_core_delegates_bucket_decision_to_strategy(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core delegates bucket FULL decision to the strategy via factory.

    Verifies that ``factory.create_bucket_full_strategy()`` is called and
    the strategy's ``should_create_full()`` is invoked with correct arguments
    when ``_backup_target`` runs.
    """
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
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

    with (
        patch.object(
            mock_factory,
            "create_bucket_full_strategy",
            wraps=mock_factory.create_bucket_full_strategy,
        ) as create_spy,
    ):
        core._backup_target(vm, target, [snap])

    # Factory was called to create the strategy
    assert create_spy.called, "factory.create_bucket_full_strategy() should be called"

    # Strategy's should_create_full was invoked
    strategy = mock_factory._bucket_full_strategy
    assert len(strategy.calls) > 0, "strategy.should_create_full() should be called"

    # Verify the call includes expected arguments
    call_args = strategy.calls[0]
    assert isinstance(call_args["target"], type(target)), "target should be passed"
    assert isinstance(call_args["snapshot_ts"], datetime), "snapshot_ts should be a datetime"
    assert isinstance(call_args["all_fulls"], list), "all_fulls should be a list"


# ── Chain Integrity Verification (pre-commit) ──────────────────────────────


def _load_fixture(filename: str) -> str:
    """Load a JSON fixture file from tests/fixtures/shell_outputs/."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "shell_outputs" / filename
    with open(fixture_path, encoding="utf-8") as fh:
        return fh.read()


def _add_snapshots_for_chain(state, vm_name: str) -> None:
    """Add snapshots matching the intact backing-chain fixture to state."""
    state.record_snapshot(
        vm_name,
        SnapshotInfo(
            name="snap1",
            path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
            timestamp=datetime(2025, 7, 13, 8, 0),
            allocation=1048576,
        ),
    )
    state.record_snapshot(
        vm_name,
        SnapshotInfo(
            name="snap4",
            path=Path("/var/lib/libvirt/snapshots/testvm/snap4.qcow2"),
            timestamp=datetime(2025, 7, 13, 14, 0),
            allocation=4194304,
        ),
    )


def _add_snapshots_6_for_chain(state, vm_name: str) -> None:
    """Add 6 snapshots (snap1-snap6) to state for chain verification tests."""
    base_path = "/var/lib/libvirt/snapshots/testvm"
    timestamps = [
        datetime(2025, 7, 13, 8, 0),  # snap1
        datetime(2025, 7, 13, 9, 0),  # snap2
        datetime(2025, 7, 13, 10, 0),  # snap3
        datetime(2025, 7, 13, 11, 0),  # snap4
        datetime(2025, 7, 13, 12, 0),  # snap5
        datetime(2025, 7, 13, 13, 0),  # snap6
    ]
    for i, ts in enumerate(timestamps, start=1):
        state.record_snapshot(
            vm_name,
            SnapshotInfo(
                name=f"snap{i}",
                path=Path(f"{base_path}/snap{i}.qcow2"),
                timestamp=ts,
                allocation=1048576 * i,
            ),
        )


_OK = ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


def test_chain_verify_intact_chain_blockcommit_proceeds(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Intact chain → pre-commit verification passes → blockcommit called.

    Set ``chain_verify_before_commit=True`` and ``chain_verify_after_commit=False``
    so the test focuses on pre-commit behaviour.  The chain is intact so
    verification passes and blockcommit proceeds.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=True,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_for_chain(mock_state, "testvm")

    # qemu-img info --backing-chain returns the intact chain fixture
    intact_json = _load_fixture("backing_chain_intact.json")
    mock_shell.expect("qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout=intact_json, stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=["snap4"], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    assert bc_spy.called, "blockcommit should proceed when chain is intact"
    assert mock_state.get_deferred_operations("testvm") == []


def test_chain_verify_intact_chain_new_qemu_format_blockcommit_proceeds(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Intact chain (QEMU 11.0+ ``filename`` keys) → verification passes → blockcommit called.

    The backing chain fixture uses ``"filename"`` keys with nested ``"children"``
    arrays (QEMU 11.0+ format).  All 5 files exist, all are qcow2, and
    references are consistent.  The ``_verify_backing_chain`` method must
    correctly parse both legacy ``"image"`` (QEMU < 11.0) and modern
    ``"filename"`` (QEMU 11.0+) keys.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=True,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_for_chain(mock_state, "testvm")

    # qemu-img info --backing-chain returns the new-format intact chain fixture
    intact_new_json = _load_fixture("backing_chain_intact_new.json")
    mock_shell.expect("qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout=intact_new_json, stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=["snap4"], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    assert bc_spy.called, "blockcommit should proceed when chain is intact (new QEMU format)"
    assert mock_state.get_deferred_operations("testvm") == []


def test_chain_verify_missing_file_blockcommit_skipped(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Broken chain (missing file) → pre-commit verification fails → blockcommit skipped.

    The broken-chain fixture references a MISSING_FILE.qcow2.  ``test -f``
    is pre-configured to return success, so we replace it with a specific
    failure for the missing file followed by a generic success for all
    other files.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=True,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_for_chain(mock_state, "testvm")

    # Broken chain fixture: MISSING_FILE.qcow2 is referenced but missing
    broken_json = _load_fixture("backing_chain_broken.json")
    mock_shell.expect("qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout=broken_json, stderr="", returncode=0, error=None)
    )

    # Replace generic test -f with specific MISSING_FILE failure + generic success
    mock_shell._expectations = [e for e in mock_shell._expectations if e.pattern != "test -f"]
    mock_shell.expect("test -f.*MISSING_FILE").returns(
        ShellResult(success=False, stdout="", stderr="", returncode=1, error="not found")
    )
    mock_shell.expect("test -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=["snap4"], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    with patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy:
        core._blockcommit_snapshots(vm, retention)

    assert not bc_spy.called, "blockcommit should be skipped when chain is broken"


def test_chain_verify_non_qcow2_blockcommit_skipped(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Chain entry with ``format: raw`` → pre-commit verification fails → blockcommit skipped."""
    global_cfg = make_global_config(
        chain_verify_before_commit=True,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_for_chain(mock_state, "testvm")

    # Chain with a non-qcow2 format entry
    raw_chain = json.dumps(
        [
            {"image": "/var/lib/libvirt/images/testvm.qcow2", "format": "qcow2"},
            {"image": "/var/lib/libvirt/snapshots/testvm/snap1.qcow2", "format": "raw"},
        ]
    )
    mock_shell.expect("qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout=raw_chain, stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=["snap4"], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    with patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy:
        core._blockcommit_snapshots(vm, retention)

    assert not bc_spy.called, "blockcommit should be skipped when non-qcow2 format detected"


def test_chain_verify_cyclic_reference_blockcommit_skipped(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Chain with a cycle (same file appears twice) → verification fails → blockcommit skipped."""
    global_cfg = make_global_config(
        chain_verify_before_commit=True,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_for_chain(mock_state, "testvm")

    # Chain where snap1 appears twice (cycle)
    cyclic_chain = json.dumps(
        [
            {"image": "/var/lib/libvirt/images/testvm.qcow2", "format": "qcow2"},
            {"image": "/var/lib/libvirt/snapshots/testvm/snap1.qcow2", "format": "qcow2"},
            {"image": "/var/lib/libvirt/snapshots/testvm/snap1.qcow2", "format": "qcow2"},
        ]
    )
    mock_shell.expect("qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout=cyclic_chain, stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=["snap4"], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    with patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy:
        core._blockcommit_snapshots(vm, retention)

    assert not bc_spy.called, "blockcommit should be skipped when cyclic reference detected"


def test_chain_verify_broken_chain_does_not_defer(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Broken chain must NOT produce deferred operations — it needs operator intervention."""
    global_cfg = make_global_config(
        chain_verify_before_commit=True,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_for_chain(mock_state, "testvm")

    # Broken chain
    broken_json = _load_fixture("backing_chain_broken.json")
    mock_shell.expect("qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout=broken_json, stderr="", returncode=0, error=None)
    )

    # MISSING_FILE → test -f fails
    mock_shell._expectations = [e for e in mock_shell._expectations if e.pattern != "test -f"]
    mock_shell.expect("test -f.*MISSING_FILE").returns(
        ShellResult(success=False, stdout="", stderr="", returncode=1, error="not found")
    )
    mock_shell.expect("test -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=["snap4"], remove=["snap1"])

    core._blockcommit_snapshots(vm, retention)

    # Broken chains must never be deferred — operator must fix the chain.
    assert mock_state.get_deferred_operations("testvm") == []


def test_chain_verify_inconsistent_backing_filename_blockcommit_skipped(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Backing-filename mismatch → pre-commit verification fails → blockcommit skipped."""
    global_cfg = make_global_config(
        chain_verify_before_commit=True,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_for_chain(mock_state, "testvm")

    # Chain where snap4's backing-filename does NOT match the next entry.
    inconsistent_chain = json.dumps(
        [
            {
                "image": "/var/lib/libvirt/snapshots/testvm/snap4.qcow2",
                "format": "qcow2",
                "backing-filename": "/var/lib/libvirt/snapshots/testvm/WRONG.qcow2",
            },
            {
                "image": "/var/lib/libvirt/snapshots/testvm/snap3.qcow2",
                "format": "qcow2",
                "backing-filename": "/var/lib/libvirt/snapshots/testvm/snap2.qcow2",
            },
            {
                "image": "/var/lib/libvirt/images/testvm.qcow2",
                "format": "qcow2",
            },
        ]
    )
    mock_shell.expect("qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout=inconsistent_chain, stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=["snap4"], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    with patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy:
        core._blockcommit_snapshots(vm, retention)

    assert not bc_spy.called, (
        "blockcommit should be skipped when backing-filename mismatch detected"
    )


# ── Chain Integrity Verification (post-commit) ─────────────────────────────


def test_post_commit_chain_shortened_as_expected(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Post-commit verification: chain shortened from 7→6 → passes silently.

    Retention removes snap6.  Pre-commit queries snap6 (7 entries),
    blockcommit succeeds, post-commit queries snap5 (6 entries).
    Verifies that 6 < 7 and no CRITICAL log is emitted.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=True,
        chain_verify_after_commit=True,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_6_for_chain(mock_state, "testvm")

    chain_7 = _load_fixture("backing_chain_7_entries.json")
    chain_6 = _load_fixture("backing_chain_6_entries.json")
    snap6_path = "/var/lib/libvirt/snapshots/testvm/snap6.qcow2"
    snap5_path = "/var/lib/libvirt/snapshots/testvm/snap5.qcow2"

    # Pre-commit queries snap6 → 7 entries
    mock_shell.expect(f"qemu-img info.*{snap6_path}").returns(
        ShellResult(success=True, stdout=chain_7, stderr="", returncode=0, error=None)
    )
    # Post-commit queries snap5 → 6 entries
    mock_shell.expect(f"qemu-img info.*{snap5_path}").returns(
        ShellResult(success=True, stdout=chain_6, stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(
        keep=["snap1", "snap2", "snap3", "snap4", "snap5"],
        remove=["snap6"],
    )
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.INFO)
    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    assert bc_spy.called, "blockcommit should proceed"
    # Verify no CRITICAL log — chain shortened as expected
    critical_logs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert not critical_logs, f"Expected no CRITICAL log, got: {[r.message for r in critical_logs]}"
    assert "Post-commit chain verification passed" in caplog.text


def test_post_commit_chain_shortened_intermediate_removal(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Post-commit passes even when virsh --delete removed intermediate snapshots.

    snap3--snap5 are removed from state before blockcommit (simulating
    ``virsh --delete`` removing them from disk).  After snap6 is merged,
    the most recent surviving snapshot is snap2 → 3 entries.
    3 < 7 → passes.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=True,
        chain_verify_after_commit=True,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_6_for_chain(mock_state, "testvm")
    # Simulate virsh --delete removing snap3, snap4, snap5 from disk
    mock_state.remove_snapshot("testvm", "snap3")
    mock_state.remove_snapshot("testvm", "snap4")
    mock_state.remove_snapshot("testvm", "snap5")

    chain_7 = _load_fixture("backing_chain_7_entries.json")
    chain_3 = _load_fixture("backing_chain_3_entries.json")
    snap6_path = "/var/lib/libvirt/snapshots/testvm/snap6.qcow2"
    snap2_path = "/var/lib/libvirt/snapshots/testvm/snap2.qcow2"

    # Pre-commit queries snap6 → 7 entries
    mock_shell.expect(f"qemu-img info.*{snap6_path}").returns(
        ShellResult(success=True, stdout=chain_7, stderr="", returncode=0, error=None)
    )
    # Post-commit queries snap2 → 3 entries
    mock_shell.expect(f"qemu-img info.*{snap2_path}").returns(
        ShellResult(success=True, stdout=chain_3, stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(
        keep=["snap1", "snap2", "snap6"],
        remove=["snap6"],
    )
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.INFO)
    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    assert bc_spy.called, "blockcommit should proceed"
    critical_logs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert not critical_logs, f"Expected no CRITICAL log, got: {[r.message for r in critical_logs]}"
    assert "Post-commit chain verification passed" in caplog.text


def test_post_commit_chain_length_unchanged_critical(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Post-commit chain length unchanged → CRITICAL log (silent blockcommit failure).

    Both pre-commit and post-commit queries return 7 entries.  After
    snap6 is removed from state, the post-commit query hits snap5 but
    still returns 7 entries — simulating that the blockcommit did not
    actually reduce the chain.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=True,
        chain_verify_after_commit=True,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_6_for_chain(mock_state, "testvm")

    chain_7 = _load_fixture("backing_chain_7_entries.json")

    # Single generic expectation — matches both pre‑commit and post‑commit
    # qemu-img calls, returning 7 entries each time.
    mock_shell.expect("qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout=chain_7, stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(
        keep=["snap1", "snap2", "snap3", "snap4", "snap5"],
        remove=["snap6"],
    )
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.CRITICAL)
    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    assert bc_spy.called, "blockcommit should be attempted"
    critical_logs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical_logs, "Expected CRITICAL log for unchanged chain length"
    assert "chain length unchanged" in critical_logs[0].message
    assert "snap6.qcow2" in critical_logs[0].message


def test_post_commit_measurement_fails_graceful(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Post-commit measurement fails → WARNING logged but verification passes.

    Blockcommit succeeds but the post-commit qemu-img call fails.
    chain_length_after is None → WARNING is logged, "passed" also logged.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=True,
        chain_verify_after_commit=True,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_6_for_chain(mock_state, "testvm")

    chain_7 = _load_fixture("backing_chain_7_entries.json")
    snap6_path = "/var/lib/libvirt/snapshots/testvm/snap6.qcow2"
    snap5_path = "/var/lib/libvirt/snapshots/testvm/snap5.qcow2"

    # Pre-commit: snap6 → 7 entries
    mock_shell.expect(f"qemu-img info.*{snap6_path}").returns(
        ShellResult(success=True, stdout=chain_7, stderr="", returncode=0, error=None)
    )
    # Post-commit: snap5 path → failure
    mock_shell.expect(f"qemu-img info.*{snap5_path}").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="permission denied",
            returncode=1,
            error="permission denied",
        )
    )

    retention = RetentionResult(
        keep=["snap1", "snap2", "snap3", "snap4", "snap5"],
        remove=["snap6"],
    )
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.INFO)
    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    assert bc_spy.called, "blockcommit should proceed"
    # WARNING logged — measurement failed but blockcommit itself succeeded
    warning_logs = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_logs, "Expected WARNING log for failed post-commit measurement"
    assert any("blockcommit itself succeeded" in r.message for r in warning_logs), (
        f"Expected 'blockcommit itself succeeded' in WARNING, got: "
        f"{[r.message for r in warning_logs]}"
    )
    # "passed" is NOT logged when chain_length_after is None (indent fix 2.2.1)
    assert "Post-commit chain verification passed" not in caplog.text


def test_post_commit_skipped_when_pre_commit_unavailable(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """When pre-commit chain_length is None, post-commit verification is skipped.

    Qemu-img fails for the pre-commit measurement → chain_length_before = None.
    The code logs an INFO message and skips the post-commit check entirely.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=True,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_6_for_chain(mock_state, "testvm")

    # Qemu-img fails — chain_length_before will be None
    mock_shell.expect("qemu-img info.*--backing-chain").returns(
        ShellResult(success=False, stdout="", stderr="timeout", returncode=124, error="timeout")
    )

    retention = RetentionResult(
        keep=["snap1", "snap2", "snap3", "snap4", "snap5"],
        remove=["snap6"],
    )
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.INFO)
    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    assert bc_spy.called, "blockcommit should proceed despite measurement failure"
    assert "Pre-commit chain length unavailable" in caplog.text
    # "passed" is NOT logged when chain_length_before is None (indent fix 2.2.1)
    assert "Post-commit chain verification passed" not in caplog.text


def test_get_chain_length_no_use_base_image_param(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """_get_chain_length() no longer accepts use_base_image parameter.

    - State empty → queries vm_config.base_image.
    - State has snapshots → queries most recent snapshot path.
    - Passing use_base_image=True raises TypeError.
    """
    global_cfg = make_global_config()
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    chain_3 = _load_fixture("backing_chain_3_entries.json")

    # State is empty → should query base_image
    mock_shell.expect("qemu-img info.*/var/lib/libvirt/images/testvm.qcow2").returns(
        ShellResult(success=True, stdout=chain_3, stderr="", returncode=0, error=None)
    )
    length = core._get_chain_length(vm)
    assert length == 3, f"Expected 3 entries when querying base image, got {length}"

    # State has snapshots → should query most recent (snap2)
    _add_snapshots_6_for_chain(mock_state, "testvm")
    snap6_path = "/var/lib/libvirt/snapshots/testvm/snap6.qcow2"
    mock_shell.expect(f"qemu-img info.*{snap6_path}").returns(
        ShellResult(success=True, stdout=chain_3, stderr="", returncode=0, error=None)
    )
    length = core._get_chain_length(vm)
    assert length == 3, f"Expected 3 entries when querying most recent snapshot, got {length}"

    # use_base_image param no longer accepted
    with pytest.raises(TypeError, match="use_base_image"):
        core._get_chain_length(vm, use_base_image=True)  # type: ignore[call-arg]


# ── Chain Verify Disabled ──────────────────────────────────────────────────


def test_chain_verify_disabled_skips_pre_commit_check(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """With chain_verify_before_commit=False, _verify_backing_chain is NOT called.

    The blockcommit proceeds without the pre-commit integrity check even
    though _get_chain_length (used for post-commit comparison) still runs.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    _add_snapshots_for_chain(mock_state, "testvm")

    # Provide a broken chain — but verification is disabled so it shouldn't matter
    broken_json = _load_fixture("backing_chain_broken.json")
    mock_shell.expect("qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout=broken_json, stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=["snap4"], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(core, "_verify_backing_chain") as verify_spy,
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    assert not verify_spy.called, "_verify_backing_chain should NOT be called when disabled"
    assert bc_spy.called, "blockcommit should proceed when verify is disabled"


# ── Backup Retry ───────────────────────────────────────────────────────────


def test_backup_retry_transient_error_retried_successfully(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Transient error on first attempt → retried → succeeds on second attempt."""
    vm = make_vm_config(name="testvm")
    target = make_target(backup_retry_max=3, backup_retry_base="1s")
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
        timestamp=datetime.now(),
        allocation=1000,
    )

    fail_result = BackupResult(
        success=False,
        snapshot_name="snap1",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "snap1",
        bytes_transferred=0,
        error="Connection refused",
    )
    success_result = BackupResult(
        success=True,
        snapshot_name="snap1",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "snap1",
        bytes_transferred=1048576,
        error=None,
    )

    provider = mock_factory._backup_provider
    caplog.set_level(logging.INFO)

    with (
        patch("qsnap.core.time.sleep"),
        patch.object(
            provider,
            "transfer_missing",
            side_effect=[[fail_result], [success_result]],
        ) as transfer_spy,
    ):
        results = core._transfer_with_retry(provider, vm, target, [snap])

    assert transfer_spy.call_count == 2, "transfer_missing should be retried once"
    assert all(r.success for r in results), "all results should succeed after retry"
    assert "succeeded on retry" in caplog.text


def test_backup_retry_all_retries_exhausted(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """All retries exhausted → return failed results after max_retries attempts."""
    vm = make_vm_config(name="testvm")
    target = make_target(backup_retry_max=2, backup_retry_base="1s")
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
        timestamp=datetime.now(),
        allocation=1000,
    )

    fail_result = BackupResult(
        success=False,
        snapshot_name="snap1",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "snap1",
        bytes_transferred=0,
        error="Connection refused",
    )

    provider = mock_factory._backup_provider

    with (
        patch("qsnap.core.time.sleep"),
        patch.object(
            provider,
            "transfer_missing",
            return_value=[fail_result],
        ) as transfer_spy,
    ):
        results = core._transfer_with_retry(provider, vm, target, [snap])

    assert transfer_spy.call_count == 2, "transfer_missing should be called max_retries times"
    assert any(not r.success for r in results), "results should indicate failure"


def test_backup_retry_non_retryable_fails_immediately(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Non-retryable error → fail immediately (one call only)."""
    vm = make_vm_config(name="testvm")
    target = make_target(backup_retry_max=3, backup_retry_base="1s")
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
        timestamp=datetime.now(),
        allocation=1000,
    )

    fail_result = BackupResult(
        success=False,
        snapshot_name="snap1",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "snap1",
        bytes_transferred=0,
        error="No space left on device",
    )

    provider = mock_factory._backup_provider

    with patch.object(
        provider,
        "transfer_missing",
        return_value=[fail_result],
    ) as transfer_spy:
        results = core._transfer_with_retry(provider, vm, target, [snap])

    assert transfer_spy.call_count == 1, "non-retryable error should fail immediately (one call)"
    assert any(not r.success for r in results), "results should indicate failure"


def test_backup_retry_disabled_when_max_zero(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """retry_max=0 → retry is disabled, single call only."""
    vm = make_vm_config(name="testvm")
    target = make_target(backup_retry_max=0, backup_retry_base="1s")
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
        timestamp=datetime.now(),
        allocation=1000,
    )

    fail_result = BackupResult(
        success=False,
        snapshot_name="snap1",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "snap1",
        bytes_transferred=0,
        error="Connection refused",
    )

    provider = mock_factory._backup_provider

    with patch.object(
        provider,
        "transfer_missing",
        return_value=[fail_result],
    ) as transfer_spy:
        results = core._transfer_with_retry(provider, vm, target, [snap])

    assert transfer_spy.call_count == 1, "retry disabled (max=0) → only one call"
    assert any(not r.success for r in results), "results should indicate failure"


# ── Deferred Blockcommit with deep_verify ──────────────────────────────────


def test_deferred_blockcommit_passes_deep_verify_true(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When ``blockcommit_deep_verify=True``, deferred blockcommit passes it."""
    vm = make_vm_config(
        name="testvm",
        disks=["vda"],
        blockcommit_deep_verify=True,
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with a deferred blockcommit and matching snapshot.
    mock_state.record_snapshot(
        "testvm",
        SnapshotInfo(
            name="snap1",
            path=Path("/tmp/snap1.qcow2"),
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=1000,
        ),
    )
    mock_state.add_deferred_blockcommit("testvm", ["snap1"], "apparmor")

    # VM is shut off → deferred commit should execute.
    mock_shell.expect("domstate").returns(
        ShellResult(success=True, stdout="shut off", stderr="", returncode=0, error=None)
    )

    manager = mock_factory._lifecycle_manager

    with patch.object(
        manager,
        "blockcommit",
        wraps=manager.blockcommit,
    ) as bc_spy:
        core._check_deferred_operations(vm)

    assert bc_spy.called, "blockcommit should be called for deferred operation"
    # Verify deep_verify=True was passed to blockcommit
    call_kwargs = bc_spy.call_args.kwargs
    assert call_kwargs.get("deep_verify") is True, (
        "deep_verify=True should be passed to blockcommit"
    )


# ── Cascade Deletion: Ghost Retention ──────────────────────────────────────


def test_full_kept_due_to_active_dependent(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """FULL backup is NOT deleted when it has dependent incrementals in keep-set (ghost retention).

    When retention says to remove a FULL backup but one of its dependent
    incrementals is in the keep-set, the FULL is ghost-retained (skipped).
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    full_name = "snap1.FULL.monthly.qcow2"
    inc_name = "snap2.qcow2"
    now = datetime.now()

    # Pre-populate state: FULL with dependent incremental
    mock_state.record_full_backup(str(target.path), full_name, now, "monthly")
    mock_state.record_incremental_dependency(str(target.path), inc_name, full_name)

    backups = [
        SnapshotInfo(name=full_name, path=target.path / full_name, timestamp=now, allocation=10000),
        SnapshotInfo(name=inc_name, path=target.path / inc_name, timestamp=now, allocation=500),
    ]
    # Retention keeps the incremental, removes the FULL
    retention = RetentionResult(keep=[inc_name], remove=[full_name])

    backup_provider = mock_factory._backup_provider
    with patch.object(backup_provider, "delete", wraps=backup_provider.delete) as delete_spy:
        core._cleanup_backups(vm, target, backups, retention)

    # FULL should NOT be deleted (ghost retention)
    deleted_names = [call.args[0].name for call in delete_spy.call_args_list]
    assert full_name not in deleted_names, (
        "FULL with active dependent should be ghost-retained (not deleted)"
    )


# ── test_full_deleted_when_no_active_dependents ───────────────────────────


def test_full_deleted_when_no_active_dependents(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """FULL backup IS deleted when no dependent incrementals are in keep-set."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    full_name = "snap1.FULL.monthly.qcow2"
    now = datetime.now()

    # FULL with no dependents
    backups = [
        SnapshotInfo(name=full_name, path=target.path / full_name, timestamp=now, allocation=10000),
    ]
    retention = RetentionResult(keep=[], remove=[full_name])

    # Provide expectations for verify_full_backup (M1 + M2)
    mock_shell.expect("qemu-img info.*--output=json").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 10000}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("qemu-img check.*--output=json").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"errors": 0, "leaks": 0}),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    backup_provider = mock_factory._backup_provider
    with patch.object(backup_provider, "delete", wraps=backup_provider.delete) as delete_spy:
        core._cleanup_backups(vm, target, backups, retention)

    # FULL should be deleted
    deleted_names = [call.args[0].name for call in delete_spy.call_args_list]
    assert full_name in deleted_names, "FULL with no active dependents should be deleted"


# ── test_orphaned_incrementals_cascade_deleted ────────────────────────────


def test_orphaned_incrementals_cascade_deleted(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Orphaned incrementals are cascade-deleted after their FULL anchor is removed."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    full_name = "snap1.FULL.monthly.qcow2"
    inc1 = "snap2.qcow2"
    inc2 = "snap3.qcow2"
    now = datetime.now()

    # FULL with two dependent incrementals
    mock_state.record_full_backup(str(target.path), full_name, now, "monthly")
    mock_state.record_incremental_dependency(str(target.path), inc1, full_name)
    mock_state.record_incremental_dependency(str(target.path), inc2, full_name)

    backups = [
        SnapshotInfo(name=full_name, path=target.path / full_name, timestamp=now, allocation=10000),
        SnapshotInfo(name=inc1, path=target.path / inc1, timestamp=now, allocation=500),
        SnapshotInfo(name=inc2, path=target.path / inc2, timestamp=now, allocation=600),
    ]
    # All are removed
    retention = RetentionResult(keep=[], remove=[full_name, inc1, inc2])

    # Provide expectations for verify_full_backup (M1 + M2)
    mock_shell.expect("qemu-img info.*--output=json").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 10000}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("qemu-img check.*--output=json").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"errors": 0, "leaks": 0}),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    backup_provider = mock_factory._backup_provider
    with patch.object(backup_provider, "delete", wraps=backup_provider.delete) as delete_spy:
        core._cleanup_backups(vm, target, backups, retention)

    deleted_names = [call.args[0].name for call in delete_spy.call_args_list]
    assert full_name in deleted_names, "FULL should be deleted"
    assert inc1 in deleted_names, "orphaned incremental should be cascade-deleted"
    assert inc2 in deleted_names, "orphaned incremental should be cascade-deleted"


# ── test_kept_incremental_rebased_to_new_anchor ───────────────────────────


def test_kept_incremental_rebased_to_new_anchor(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Kept incremental is NOT cascade-deleted when its FULL anchor is removed.

    When the FULL anchor is deleted but the dependent incremental is in
    the keep-set, the incremental is preserved (not cascade-deleted).
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    full_name = "snap1.FULL.monthly.qcow2"
    inc1 = "snap2.qcow2"
    inc2 = "snap3.qcow2"
    now = datetime.now()

    # FULL with two dependents
    mock_state.record_full_backup(str(target.path), full_name, now, "monthly")
    mock_state.record_incremental_dependency(str(target.path), inc1, full_name)
    mock_state.record_incremental_dependency(str(target.path), inc2, full_name)

    backups = [
        SnapshotInfo(name=full_name, path=target.path / full_name, timestamp=now, allocation=10000),
        SnapshotInfo(name=inc1, path=target.path / inc1, timestamp=now, allocation=500),
        SnapshotInfo(name=inc2, path=target.path / inc2, timestamp=now, allocation=600),
    ]
    # FULL removed, inc1 kept, inc2 removed
    retention = RetentionResult(keep=[inc1], remove=[full_name, inc2])

    backup_provider = mock_factory._backup_provider
    with patch.object(backup_provider, "delete", wraps=backup_provider.delete) as delete_spy:
        core._cleanup_backups(vm, target, backups, retention)

    deleted_names = [call.args[0].name for call in delete_spy.call_args_list]
    # FULL has dependents in keep-set (inc1) → ghost-retained
    assert full_name not in deleted_names, "FULL should be ghost-retained (inc1 is in keep-set)"
    # inc1 is in keep-set → NOT deleted
    assert inc1 not in deleted_names, "kept incremental should NOT be cascade-deleted"


# ── test_core_post_processes_retention_for_dependencies ───────────────────


def test_core_post_processes_retention_for_dependencies(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core post-processes retention result for dependencies.

    Even when the retention engine says to remove a FULL, Core checks
    for dependent incrementals and overrides the deletion when active
    dependents exist.
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    full_name = "snap1.FULL.monthly.qcow2"
    inc1 = "snap2.qcow2"
    now = datetime.now()

    # FULL with one dependent incremental in keep-set
    mock_state.record_full_backup(str(target.path), full_name, now, "monthly")
    mock_state.record_incremental_dependency(str(target.path), inc1, full_name)

    backups = [
        SnapshotInfo(name=full_name, path=target.path / full_name, timestamp=now, allocation=10000),
        SnapshotInfo(name=inc1, path=target.path / inc1, timestamp=now, allocation=500),
    ]
    # Retention engine says: remove FULL, keep incremental
    retention = RetentionResult(keep=[inc1], remove=[full_name])

    backup_provider = mock_factory._backup_provider
    with patch.object(backup_provider, "delete", wraps=backup_provider.delete) as delete_spy:
        core._cleanup_backups(vm, target, backups, retention)

    # Even though retention says remove FULL, Core post-processes and skips it
    # due to active dependent (inc1 in keep-set)
    deleted_names = [call.args[0].name for call in delete_spy.call_args_list]
    assert full_name not in deleted_names, (
        "Core should post-process retention and skip FULL with active dependents"
    )
    assert inc1 not in deleted_names, "inc1 is in keep-set and should not be deleted"


# ── Dry-Run FULL Backup Tests ──────────────────────────────────────────────


def test_dry_run_logs_full_would_be_created(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Dry-run mode: MockBucketFullStrategy returns True → INFO log.

    Verifies the dry-run log includes bucket, method (NBD for running VM),
    and VM state.  Also verifies create_full_backup() is NOT called.
    """
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.dry_run = True

    # Record a snapshot so _backup_target has something to process.
    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    # Configure MockBucketFullStrategy to return (True, "weekly")
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(
        return_value=(True, "weekly")
    )

    backup_provider = mock_factory._backup_provider

    caplog.set_level(logging.INFO)

    with patch.object(
        backup_provider,
        "create_full_backup",
        wraps=backup_provider.create_full_backup,
    ) as full_spy:
        core._backup_target(vm, target, [snap])

    # The dry-run log includes the creation spec.
    assert "[dry-run] Would create FULL backup" in caplog.text
    assert "bucket=weekly" in caplog.text
    assert "method=NBD" in caplog.text, "Running VM should use NBD method"
    assert "VM=running" in caplog.text

    # create_full_backup() was NOT actually called.
    assert not full_spy.called, "create_full_backup() must NOT be called in dry-run mode"


def test_dry_run_detects_vm_running_state_for_method(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Dry-run detects VM state: running → method=NBD, stopped → method=direct convert."""
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.dry_run = True

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    caplog.set_level(logging.INFO)

    # Configure MockBucketFullStrategy to return (True, "weekly")
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(
        return_value=(True, "weekly")
    )

    # --- Case A: VM running (default fixture dominfo returns State: running) ---
    core._backup_target(vm, target, [snap])

    assert "method=NBD" in caplog.text, "Running VM should produce method=NBD in dry-run log"
    assert "VM=running" in caplog.text

    # --- Case B: VM stopped — patch is_vm_running to return False ---
    caplog.clear()
    with patch("qsnap.core.is_vm_running", return_value=False):
        core._backup_target(vm, target, [snap])

    assert "method=direct convert" in caplog.text, (
        "Stopped VM should produce method=direct convert in dry-run log"
    )
    assert "VM=stopped" in caplog.text


def test_dry_run_logs_full_would_be_created_without_executing(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Dry-run with should_full=True → log indicates FULL would be created,
    but no virsh backup-begin or qemu-img convert is executed."""
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.dry_run = True

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    caplog.set_level(logging.INFO)

    # Configure MockBucketFullStrategy to return (True, "weekly")
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(
        return_value=(True, "weekly")
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        core._backup_target(vm, target, [snap])

    # Log confirms FULL would be created.
    assert "[dry-run] Would create FULL backup" in caplog.text

    # No virsh backup-begin or qemu-img convert was executed.
    mutating_cmds = [
        c
        for c in shell_spy.call_args_list
        if c.args
        and isinstance(c.args[0], list)
        and any(m in " ".join(c.args[0]) for m in ("backup-begin", "qemu-img convert"))
    ]
    assert len(mutating_cmds) == 0, (
        f"No mutating commands should be executed in dry-run, got: {mutating_cmds}"
    )


# ── Bitmap Target FULL Backup Test ──────────────────────────────────────────


def test_full_creation_works_for_file_copy_and_bitmap(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Bitmap-mode target with weekly trigger → BitmapBackupProvider.create_full_backup() called.

    Verifies that the factory returns the bitmap provider when incremental_mode="bitmap"
    and that create_full_backup succeeds without raising NotImplementedError.
    """
    target = make_target(
        target_preserve="7d",
        incremental_mode="bitmap",
    )
    vm = make_vm_config(name="testvm", targets=[target])
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

    # Configure MockBucketFullStrategy to return (True, "daily")
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(
        return_value=(True, "daily")
    )

    bitmap_provider = mock_factory._bitmap_backup_provider

    with patch.object(
        bitmap_provider,
        "create_full_backup",
        wraps=bitmap_provider.create_full_backup,
    ) as full_spy:
        core._backup_target(vm, target, [snap])

    assert full_spy.called, (
        "BitmapBackupProvider.create_full_backup() should be called for bitmap target"
    )


# ── Check Integrity: --force-share on Active Layer ──────────────────────────


def test_check_integrity_uses_force_share_on_active_layer(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core.check_integrity() uses --force-share on qemu-img info --backing-chain.

    Verifies that the non-deep check path includes --force-share in the
    qemu-img info command used to verify backing chains on active layers.
    """
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Record a snapshot so check() iterates over it.
    snap = SnapshotInfo(
        name="snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    # Override qemu-img info expectations — the fixture doesn't set one,
    # so MockShell will return a failure by default for unconfigured commands.
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps([{"image": str(snap.path), "format": "qcow2"}]),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        core.check()

    # Find the qemu-img info call for snap1
    info_calls = [
        c
        for c in shell_spy.call_args_list
        if c.args
        and isinstance(c.args[0], list)
        and "qemu-img" in c.args[0][0]
        and "info" in " ".join(c.args[0])
    ]
    assert len(info_calls) >= 1, "qemu-img info should be called"

    # Every qemu-img info --backing-chain call must include --force-share
    backing_chain_calls = [c for c in info_calls if "--backing-chain" in " ".join(c.args[0])]
    for call in backing_chain_calls:
        cmd_str = " ".join(call.args[0])
        assert "--force-share" in cmd_str, (
            f"qemu-img info --backing-chain must include --force-share, got: {cmd_str}"
        )


def test_deep_check_uses_force_share_on_active_layer(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core._deep_check_file() uses --force-share on qemu-img check."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"corruptions": 0}),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        core._deep_check_file(
            Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
            "snap1",
            [],
        )

    check_calls = [
        c
        for c in shell_spy.call_args_list
        if c.args
        and isinstance(c.args[0], list)
        and "qemu-img" in c.args[0][0]
        and "check" in " ".join(c.args[0])
    ]
    assert len(check_calls) == 1, "qemu-img check should be called exactly once"
    cmd_str = " ".join(check_calls[0].args[0])
    assert "--force-share" in cmd_str, f"qemu-img check must include --force-share, got: {cmd_str}"


# ── test_core_passes_vm_name_to_create_full_backup ───────────────────────


def test_core_passes_vm_name_to_create_full_backup(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core passes vm_config.name to create_full_backup as first positional arg.

    Verifies that the full, untruncated VM name (e.g. ``"3.Projects_opencode"``)
    is passed as ``vm_name`` to ``IBackupProvider.create_full_backup()``,
    not extracted from the snapshot filename.  This is critical for VMs with
    dotted names where filename-based extraction would truncate to ``"3"``.
    """
    target = make_target(target_preserve="7d")
    vm = make_vm_config(name="3.Projects_opencode", targets=[target])
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
    mock_state.record_snapshot("3.Projects_opencode", snap)

    # Configure MockBucketFullStrategy to return (True, "monthly")
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(
        return_value=(True, "monthly")
    )

    backup_provider = mock_factory._backup_provider

    with patch.object(
        backup_provider,
        "create_full_backup",
        wraps=backup_provider.create_full_backup,
    ) as full_spy:
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "create_full_backup should be called when FULL is triggered"
    assert full_spy.call_args.args[0] == "3.Projects_opencode", (
        f"vm_name should be '3.Projects_opencode' (full dotted name), "
        f"got: {full_spy.call_args.args[0]!r}"
    )
    assert full_spy.call_args.args[0] == vm.name, (
        f"vm_name should equal vm_config.name, got: {full_spy.call_args.args[0]!r}"
    )


# ── Stale State Self-Healing in _blockcommit_snapshots ──────────────────────


def test_blockcommit_stale_guard_all_exist_proceeds(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """All snapshot files exist → stale guard passes → blockcommit called.

    When every snapshot in ``to_merge`` has its file present on disk,
    ``os.path.exists()`` returns True for all, no entries are removed
    from state, and the lifecycle manager's ``blockcommit()`` is called
    with the complete to_merge list.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Record snapshots in state
    snap1 = SnapshotInfo(
        name="snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
    )
    snap2 = SnapshotInfo(
        name="snap2",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap2.qcow2"),
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=2000,
    )
    mock_state.record_snapshot("testvm", snap1)
    mock_state.record_snapshot("testvm", snap2)

    retention = RetentionResult(keep=[], remove=["snap1", "snap2"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True) as exists_mock,
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(
            mock_state, "remove_snapshot", wraps=mock_state.remove_snapshot
        ) as remove_spy,
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # All files exist — no staleness detected
    assert exists_mock.called, "os.path.exists should be called to verify files"

    # No entries removed from state (all files exist)
    remove_spy.assert_not_called()

    # Blockcommit called with both snapshots
    assert bc_spy.called, "blockcommit should proceed when all files exist"
    merge_names = [s.name for s in bc_spy.call_args[0][1]]
    assert set(merge_names) == {"snap1", "snap2"}


def test_blockcommit_stale_guard_one_stale_removed(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """One stale snapshot → removed from state and to_merge; remaining blockcommitted.

    When one snapshot file no longer exists on disk (e.g. it was already
    blockcommitted by a prior run), the stale entry is removed from state,
    a WARNING is logged, and blockcommit proceeds with only the remaining
    valid snapshot.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap_ok = SnapshotInfo(
        name="snap_ok",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap_ok.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
    )
    snap_stale = SnapshotInfo(
        name="snap_stale",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap_stale.qcow2"),
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=2000,
    )
    mock_state.record_snapshot("testvm", snap_ok)
    mock_state.record_snapshot("testvm", snap_stale)

    retention = RetentionResult(keep=[], remove=["snap_ok", "snap_stale"])
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.WARNING)

    def path_exists(path_str):
        return "snap_stale" not in path_str

    with (
        patch("os.path.exists", side_effect=path_exists),
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(
            mock_state, "remove_snapshot", wraps=mock_state.remove_snapshot
        ) as remove_spy,
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Stale snapshot removed from state
    remove_spy_calls = [c.args for c in remove_spy.call_args_list]
    assert ("testvm", "snap_stale") in remove_spy_calls, (
        "remove_snapshot should be called for the stale entry"
    )

    # WARNING logged for stale state entry
    assert "snap_stale" in caplog.text, "stale snapshot name should appear in WARNING log"
    assert "Stale state entry" in caplog.text, "WARNING should mention stale state entry"

    # Blockcommit called, but only with the surviving snapshot
    assert bc_spy.called, "blockcommit should proceed with surviving snapshots"
    merge_names = [s.name for s in bc_spy.call_args[0][1]]
    assert merge_names == ["snap_ok"], (
        f"Only snap_ok should be blockcommitted, got: {merge_names}"
    )
    assert "snap_stale" not in merge_names, "stale snapshot must not be blockcommitted"


def test_blockcommit_stale_guard_all_stale_skipped(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """All snapshots stale → to_merge becomes empty → blockcommit skipped.

    When every snapshot in ``to_merge`` has already been removed from disk,
    ``to_merge`` becomes empty after filtering.  The method logs an INFO
    message and returns early without calling any lifecycle operations.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap1 = SnapshotInfo(
        name="snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
    )
    snap2 = SnapshotInfo(
        name="snap2",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap2.qcow2"),
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=2000,
    )
    mock_state.record_snapshot("testvm", snap1)
    mock_state.record_snapshot("testvm", snap2)

    retention = RetentionResult(keep=[], remove=["snap1", "snap2"])
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.INFO)

    with (
        patch("os.path.exists", return_value=False),
        patch.object(
            mock_state, "remove_snapshot", wraps=mock_state.remove_snapshot
        ) as remove_spy,
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Both stale entries removed from state
    assert remove_spy.call_count == 2, (
        f"Both stale snapshots should be removed from state, got {remove_spy.call_count}"
    )

    # Blockcommit NOT called (to_merge became empty)
    bc_spy.assert_not_called()

    # INFO log: skipping blockcommit
    assert "All snapshots in to_merge were stale" in caplog.text, (
        "Should log INFO about all snaps being stale"
    )
    assert "skipping blockcommit" in caplog.text


def test_blockcommit_stale_guard_no_short_circuit(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """One stale snapshot doesn't block blockcommit of subsequent valid snapshots.

    When multiple snapshots are in ``to_merge`` and one in the middle is
    stale, the stale entry is removed from state and skipped, but the
    remaining valid snapshots still proceed to blockcommit.  This verifies
    that a single stale entry does NOT short-circuit the entire operation.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap_a = SnapshotInfo(
        name="snap_a",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap_a.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
    )
    snap_stale = SnapshotInfo(
        name="snap_stale",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap_stale.qcow2"),
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=2000,
    )
    snap_b = SnapshotInfo(
        name="snap_b",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap_b.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=3000,
    )
    mock_state.record_snapshot("testvm", snap_a)
    mock_state.record_snapshot("testvm", snap_stale)
    mock_state.record_snapshot("testvm", snap_b)

    retention = RetentionResult(keep=[], remove=["snap_a", "snap_stale", "snap_b"])
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.WARNING)

    def path_exists(path_str):
        # Only the middle snapshot is stale
        return "snap_stale" not in path_str

    with (
        patch("os.path.exists", side_effect=path_exists),
        patch.object(core, "_get_chain_length", return_value=4),
        patch.object(
            mock_state, "remove_snapshot", wraps=mock_state.remove_snapshot
        ) as remove_spy,
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Only snap_stale removed from state
    remove_spy_calls = [c.args[1] for c in remove_spy.call_args_list]
    assert remove_spy_calls == ["snap_stale"], (
        f"Only snap_stale should be removed from state, got: {remove_spy_calls}"
    )

    # WARNING logged for the single stale entry
    assert "snap_stale" in caplog.text
    assert "Stale state entry" in caplog.text

    # Blockcommit called with the two surviving snapshots
    assert bc_spy.called, (
        "blockcommit should proceed despite one stale entry in the middle"
    )
    merge_names = [s.name for s in bc_spy.call_args[0][1]]
    assert set(merge_names) == {"snap_a", "snap_b"}, (
        f"snap_a and snap_b should be blockcommitted, snap_stale skipped; got: {merge_names}"
    )
    assert "snap_stale" not in merge_names, "stale snapshot must be excluded from blockcommit"


# ── Dry-Run Pipeline Result Tests ───────────────────────────────────────────


def test_dry_run_logs_planned_actions(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Dry-run mode: PipelineResult.dry_run is True and planned actions are logged.

    Verifies that running in dry-run mode:
    - Returns a PipelineResult with dry_run=True.
    - Logs ``[dry-run] Would create snapshot for VM`` at INFO level.
    - No mutations (snapshot creation, backup transfer) are executed.
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
        result = core.run()

    # PipelineResult.dry_run is True
    assert result.dry_run is True

    # Planned actions are logged
    assert "[dry-run]" in caplog.text
    assert "Would create snapshot for VM" in caplog.text

    # No mutations executed
    assert not create_spy.called, "snapshot provider create() must NOT be called in dry-run"
    assert not transfer_spy.called, "backup provider transfer_missing() must NOT be called in dry-run"

    # Pipeline still "succeeds" in dry-run
    assert result.success is True


def test_dry_run_activated_from_cli(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Setting core.dry_run=True causes the pipeline to skip all mutations.

    Verifies that when dry_run is enabled on the Core instance:
    - ``_create_snapshot()`` returns early before any shell calls.
    - ``_backup_target()`` skips transfer_missing().
    - No state mutations (record_snapshot, set_last_allocation) occur.
    """
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
        patch.object(
            mock_state,
            "record_snapshot",
            wraps=mock_state.record_snapshot,
        ) as record_spy,
        patch.object(
            mock_state,
            "set_last_allocation",
            wraps=mock_state.set_last_allocation,
        ) as alloc_spy,
    ):
        result = core.run()

    assert result.dry_run is True
    # No snapshot creation
    assert not create_spy.called
    # No backup transfer
    assert not transfer_spy.called
    # No state mutations
    assert not record_spy.called, "record_snapshot must not be called in dry-run"
    assert not alloc_spy.called, "set_last_allocation must not be called in dry-run"
    assert result.success is True
