"""Tests for Core pipeline step ordering, command isolation, and dry-run mode.

Covers:
- Pipeline step order for ``always`` and ``onchange`` snapshot modes.
- Error isolation between VMs (RISK test-plan.md line 137).
- Command isolation: ``snapshot()``, ``backup()``, ``prune()`` each run
  only their respective steps.
- Dry-run mode (RISK test-plan.md line 138): no state mutation, no shell
  mutation, no snapshot creation.
- Count-based FULL backup decision: incremental_count > target_chain_length.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import BackupAbortError, Core, PipelineResult
from qsnap.models.config import DiskConfig, VMConfig
from qsnap.models.results import (
    BackupResult,
    ChangeResult,
    CommitResult,
    FullBackupInfo,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
    SnapshotResult,
    SnapshotSpec,
)
from tests.mocks import MockConfigFacade

# ── test_pipeline_always_mode_creates_snapshot ───────────────────────────


def test_pipeline_always_mode_creates_snapshot(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """In ``always`` mode, a snapshot is created and change detection is skipped.

    The pipeline should call ``snapshot_provider.create_multi()`` and should
    NOT call ``factory.create_change_detector()`` (always mode bypasses
    change detection entirely).
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
            "create_multi",
            wraps=snapshot_provider.create_multi,
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

    # Snapshot creation was invoked (one batch call per VM).
    assert create_spy.called, "Snapshot provider.create_multi() should be called in always mode"

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
        disk="vda",
    )

    with (
        patch.object(
            snapshot_provider,
            "create_multi",
            wraps=snapshot_provider.create_multi,
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
        "Snapshot provider.create_multi() should NOT be called when onchange "
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

    The snapshot provider's ``create_multi()`` should be called, but the
    backup provider's ``transfer_missing()`` should NOT be called.
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
            "create_multi",
            wraps=snapshot_provider.create_multi,
        ) as create_spy,
        patch.object(
            backup_provider,
            "transfer_missing",
            wraps=backup_provider.transfer_missing,
        ) as transfer_spy,
    ):
        result = core.snapshot()

    # Snapshot steps were executed.
    assert create_spy.called, "snapshot() should call snapshot_provider.create_multi()"

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
    has a target), but the snapshot provider's ``create_multi()`` should
    NOT be called.
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
            "create_multi",
            wraps=snapshot_provider.create_multi,
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
    assert not create_spy.called, "backup() should NOT call snapshot_provider.create_multi()"

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
        disk="vda",
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
            "create_multi",
            wraps=snapshot_provider.create_multi,
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
    assert not create_spy.called, "prune() should NOT call snapshot_provider.create_multi()"

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
            "create_multi",
            wraps=snapshot_provider.create_multi,
        ) as create_spy,
    ):
        result = core.run()

    # IStateManager.set_last_allocation was NEVER called.
    set_alloc_spy.assert_not_called()

    # IStateManager.record_snapshot was NEVER called.
    record_spy.assert_not_called()

    # IShell.run may be called for read-only operations: qemu-img info
    # --force-share for chain-size and allocation estimation (design D5),
    # virsh domstate for deferred drain planning (design D8), and
    # _validate_environment() read-only calls (which, test, virsh dominfo,
    # find).  Verify only read-only shell calls were made.
    read_only_patterns = (
        "qemu-img info",
        "du",
        "test ",
        "which ",
        "virsh dominfo",
        "virsh domstate",
        "find",
        "qemu-nbd",
    )
    for call in shell_spy.call_args_list:
        cmd = call[0][0]  # command list
        cmd_str = " ".join(cmd)
        assert any(p in cmd_str for p in read_only_patterns), (
            f"Unexpected shell call in dry-run: {cmd_str}"
        )

    # Snapshot provider's create_multi() was NOT called (dry-run skips actual
    # mutations).
    create_spy.assert_not_called()

    # Pipeline still "succeeds" — dry-run is not an error, it just skips
    # mutations.
    assert result.success is True

    # Dry-run logs planned actions at INFO level.
    assert "[dry-run]" in caplog.text
    assert "Would create snapshot for disk" in caplog.text

    # Result carries predictions populated by dry-run, actions empty.
    assert len(result.predictions) > 0
    assert result.actions == []

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
    """Explicit sda disk config. Verify the batch spec carries the sda suffix."""
    vm = make_vm_config(
        name="testvm",
        disks=[DiskConfig(target="sda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider
    with patch.object(
        snapshot_provider,
        "create_multi",
        wraps=snapshot_provider.create_multi,
    ) as create_spy:
        core.snapshot()

    assert create_spy.called
    specs = create_spy.call_args.args[1]
    assert len(specs) == 1
    spec = specs[0]
    assert spec.disk == "sda"
    assert "_sda_" in spec.name
    assert "_vda_" not in spec.name


# ── test_create_multi_multi_disk_batch_names ──────────────────────────────


def test_create_multi_multi_disk_batch_names(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Explicit vda+vdb disk configs. ONE create_multi batch call with both specs.

    core-orchestrator scenario "VM with multiple disks (vda, vdb)": the
    whole VM is snapshotted in a single batch; per-disk names carry the
    ``_vda_``/``_vdb_`` segments and both snapshots are recorded.
    """
    vm = make_vm_config(
        name="testvm",
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm-disk2.qcow2")),
        ],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider
    with patch.object(
        snapshot_provider,
        "create_multi",
        wraps=snapshot_provider.create_multi,
    ) as create_spy:
        core.snapshot()

    # ONE batch call per VM — not per disk.
    create_spy.assert_called_once()
    specs = create_spy.call_args.args[1]
    assert [s.disk for s in specs] == ["vda", "vdb"]
    assert all(isinstance(s, SnapshotSpec) for s in specs)
    assert all(s.path.name.endswith(".qcow2") for s in specs)
    snapshot_names = [s.name for s in specs]
    assert any("_vda_" in name for name in snapshot_names)
    assert any("_vdb_" in name for name in snapshot_names)

    # Both disks recorded with per-disk SnapshotInfo.
    snapshots = mock_state.get_snapshots("testvm")
    assert {s.disk for s in snapshots} == {"vda", "vdb"}


# ── test_create_snapshot_explicit_disk_list_overrides_discovery ───────────


def test_create_snapshot_explicit_disk_list_overrides_discovery(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VMConfig.disks=['sda'] explicitly. Verify domblklist is NOT called, snapshot uses sda."""
    vm = make_vm_config(
        name="testvm",
        disks=[DiskConfig(target="sda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
    )
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
            "create_multi",
            wraps=snapshot_provider.create_multi,
        ) as create_spy,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy,
    ):
        core.snapshot()

    # domblklist should NOT be called (explicit disk list overrides discovery)
    shell_spy.assert_not_called()
    # Snapshot should use sda
    assert create_spy.called
    spec = create_spy.call_args.args[1][0]
    assert spec.disk == "sda"
    assert "_sda_" in spec.name


# ── test_create_multi_all_success_all_recorded ────────────────────────────


def test_create_multi_all_success_all_recorded(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Batch success → every disk's snapshot is recorded (post-creation-validation).

    After ``create_multi`` returns all-success, Core records one
    :class:`SnapshotInfo` per disk and audits one ``snapshot_create``
    action per disk (design D10 all-or-nothing success branch).
    """
    vm = make_vm_config(
        name="testvm",
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm-disk2.qcow2")),
        ],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider
    with patch.object(
        snapshot_provider,
        "create_multi",
        wraps=snapshot_provider.create_multi,
    ) as create_spy:
        result = core.snapshot()

    create_spy.assert_called_once()

    snapshots = mock_state.get_snapshots("testvm")
    assert len(snapshots) == 2, "all disks' snapshots must be recorded"
    assert {s.disk for s in snapshots} == {"vda", "vdb"}
    assert all(s.path.name.endswith(".qcow2") for s in snapshots)

    actions = [a for a in result.actions if a.action == "snapshot_create"]
    assert len(actions) == 2, "one snapshot_create action per disk"
    assert {a.disk for a in actions} == {"vda", "vdb"}


# ── test_single_disk_vm_uses_batch_path ───────────────────────────────────


def test_single_disk_vm_uses_batch_path(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Single-disk VM is the degenerate case of the same create_multi path.

    core-orchestrator scenario "single-disk VM uses the same batch path":
    one ``create_multi`` call with a single spec.
    """
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider
    with patch.object(
        snapshot_provider,
        "create_multi",
        wraps=snapshot_provider.create_multi,
    ) as create_spy:
        result = core.snapshot()

    create_spy.assert_called_once()
    specs = create_spy.call_args.args[1]
    assert len(specs) == 1
    assert specs[0].disk == "vda"

    snapshots = mock_state.get_snapshots("testvm")
    assert len(snapshots) == 1
    assert snapshots[0].disk == "vda"
    assert result.results[0].success is True


# ── test_onchange_gate_vm_wide_snapshots_all_disks ────────────────────────


def test_onchange_gate_vm_wide_snapshots_all_disks(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """The onchange gate is VM-wide: ALL disks are snapshotted in one batch.

    core-orchestrator scenario "onchange gate is VM-wide, snapshots cover
    all disks": when any disk changed, one ``create_multi`` call covers
    every configured disk.
    """
    vm = make_vm_config(
        name="testvm",
        snapshot_create="onchange",
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm-disk2.qcow2")),
        ],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # The mock change detector reports changed=True (default) → gate opens.
    snapshot_provider = mock_factory._snapshot_provider
    with patch.object(
        snapshot_provider,
        "create_multi",
        wraps=snapshot_provider.create_multi,
    ) as create_spy:
        result = core.snapshot()

    create_spy.assert_called_once()
    specs = create_spy.call_args.args[1]
    assert [s.disk for s in specs] == ["vda", "vdb"]
    assert result.results[0].success is True


# ── test_vdb_failure_aborts_remaining_steps ───────────────────────────────


def test_vdb_failure_aborts_remaining_steps(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """vdb batch failure → the VM pipeline aborts (VM-level isolation).

    All-or-nothing batch semantics (design D10): when one disk of the
    batch fails, NOTHING is recorded and the remaining steps of the VM
    (backup transfer) never run.
    """
    vm = make_vm_config(
        name="testvm",
        targets=[make_target()],
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm-disk2.qcow2")),
        ],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider
    backup_provider = mock_factory._backup_provider

    def create_multi_side_effect(vm_config, specs, quiesce=False):
        return [
            SnapshotResult(
                success=True,
                name=specs[0].name,
                path=specs[0].path,
                new_allocation=65536,
                error=None,
                disk=specs[0].disk,
            ),
            SnapshotResult(
                success=False,
                name=specs[1].name,
                path=specs[1].path,
                new_allocation=0,
                error="virsh timeout for vdb",
                disk=specs[1].disk,
            ),
        ]

    caplog.set_level(logging.ERROR)
    with (
        patch.object(snapshot_provider, "create_multi", side_effect=create_multi_side_effect),
        patch.object(
            backup_provider,
            "transfer_missing",
            wraps=backup_provider.transfer_missing,
        ) as transfer_spy,
    ):
        result = core.run()

    # VM-level isolation: the vdb failure aborts this VM's pipeline.
    assert result.success is False
    assert len(result.results) == 1
    assert result.results[0].success is False
    assert "virsh timeout for vdb" in (result.results[0].error or "")

    # All-or-nothing: NOTHING is recorded (not even the successful vda).
    snapshots = mock_state.get_snapshots("testvm")
    assert len(snapshots) == 0

    # Remaining steps of the aborted VM never run.
    transfer_spy.assert_not_called()

    # An error ActionRecord was appended to the audit trail.
    error_records = [a for a in result.actions if a.action == "error"]
    assert len(error_records) == 1

    # vdb error was logged
    assert "vdb" in caplog.text
    assert "virsh timeout" in caplog.text


def test_snapshot_failure_on_one_vm_does_not_affect_others(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VM-level isolation: a snapshot failure aborts only the affected VM.

    vm1's snapshot batch fails; vm2 is processed normally and succeeds.
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

    snapshot_provider = mock_factory._snapshot_provider

    def create_multi_side_effect(vm_config, specs, quiesce=False):
        if vm_config.name == "vm1":
            return [
                SnapshotResult(
                    success=False,
                    name=specs[0].name,
                    path=specs[0].path,
                    new_allocation=0,
                    error="virsh failed for vm1",
                    disk=specs[0].disk,
                )
            ]
        return [
            SnapshotResult(
                success=True,
                name=specs[0].name,
                path=specs[0].path,
                new_allocation=65536,
                error=None,
                disk=specs[0].disk,
            )
        ]

    with patch.object(snapshot_provider, "create_multi", side_effect=create_multi_side_effect):
        result = core.snapshot()

    by_name = {r.vm_name: r for r in result.results}
    assert by_name["vm1"].success is False
    assert by_name["vm2"].success is True

    # vm2's snapshot was recorded; vm1 has none (all-or-nothing batch).
    assert len(mock_state.get_snapshots("vm1")) == 0
    assert len(mock_state.get_snapshots("vm2")) == 1


def test_mac_denial_defers_without_aborting(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """MAC denial (AppArmor/SELinux) during blockcommit → deferred, NOT aborted.

    VM-level isolation treats a MAC denial as a deferral (the commit is
    queued for the next VM shutdown), not a failure: no exception is raised
    and the operation is recorded in the deferred queue.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0),
        allocation=1000,
        disk="vda",
    )

    manager = mock_factory._lifecycle_manager
    with (
        patch.object(
            manager,
            "blockcommit",
            return_value=CommitResult(
                success=False, committed_snapshot="", error="blocked by apparmor profile"
            ),
        ),
        patch.object(
            mock_state, "add_deferred_blockcommit", wraps=mock_state.add_deferred_blockcommit
        ) as defer_spy,
    ):
        # Must NOT raise — a MAC denial is deferred, not a failure.
        core._blockcommit_one_disk(vm, "vda", [snap])

    defer_spy.assert_called_once()
    # (vm_name, disk, [snapshot_names], reason)
    assert defer_spy.call_args.args[3] == "apparmor"


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
        disk="vda",
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

    If validation fails, snapshot_provider.create_multi() must NOT be
    called.  This proves validation executes first and short-circuits the
    pipeline.
    """
    from qsnap.models.results import ShellResult

    # Make validation fail by overriding the snapshot_dir check.
    mock_shell._expectations = [e for e in mock_shell._expectations if e.pattern != "test -d"]
    mock_shell.expect("test -d").returns(
        ShellResult(success=False, stdout="", stderr="", returncode=1, error="not found")
    )

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
            core,
            "_validate_environment",
            wraps=core._validate_environment,
        ) as validate_spy,
        patch.object(
            snapshot_provider,
            "create_multi",
            wraps=snapshot_provider.create_multi,
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

    no_change = ChangeResult(
        changed=False,
        last_allocation=1000,
        current_allocation=1000,
        disk="vda",
    )

    with (
        patch.object(
            core,
            "_validate_environment",
            wraps=core._validate_environment,
        ) as validate_spy,
        patch.object(
            snapshot_provider,
            "create_multi",
            wraps=snapshot_provider.create_multi,
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


# ── Count-based FULL backup decision ─────────────────────────────────────


def test_first_backup_creates_full_regardless_of_chain_length(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """First backup to target (no prior FULLs) always creates a FULL.

    Core's count-based decision: when ``all_fulls`` is empty
    (``not all_fulls``), ``should_full = True`` unconditionally.
    No bucket strategy involved.
    """
    # FULL verification is not the subject of this test — disable it so the
    # happy path completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    target = make_target(target_chain_length=5)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # No FULLs in state — first backup creates FULL unconditionally.
    backup_provider = mock_factory._backup_provider

    with patch.object(
        backup_provider,
        "create_full_backup",
        wraps=backup_provider.create_full_backup,
    ) as full_spy:
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "create_full_backup should be called on first backup (no prior FULLs)"


def test_incremental_count_exceeds_chain_length_triggers_full(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Incremental count > target_chain_length triggers a new FULL.

    Core's count-based decision: ``should_full = incremental_count > chain_length``.
    """
    # FULL verification is not the subject of this test — disable it so the
    # happy path completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    target = make_target(target_chain_length=2)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Record a prior FULL with 3 incremental dependencies (exceeds chain_length=2).
    full_name = "testvm.FULL.daily.qcow2"
    mock_state.record_full_backup(
        str(target.path),
        full_name,
        datetime(2025, 7, 13, 2),
        "vda",
    )
    mock_state.record_incremental_dependency(str(target.path), "inc1.qcow2", full_name)
    mock_state.record_incremental_dependency(str(target.path), "inc2.qcow2", full_name)
    mock_state.record_incremental_dependency(str(target.path), "inc3.qcow2", full_name)

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    backup_provider = mock_factory._backup_provider

    with (
        patch("qsnap.core.os.path.exists", return_value=True),
        patch.object(
            backup_provider,
            "create_full_backup",
            wraps=backup_provider.create_full_backup,
        ) as full_spy,
    ):
        core._backup_target(vm, target, [snap])

    assert full_spy.called, (
        "create_full_backup should be called when incremental_count (3) > chain_length (2)"
    )


def test_incremental_count_within_chain_skips_full(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Incremental count <= target_chain_length skips FULL creation.

    Core's count-based decision: ``should_full = incremental_count > chain_length``.
    When ``incremental_count <= chain_length``, no FULL is created.
    """
    target = make_target(target_chain_length=5)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Record a prior FULL with 2 incremental dependencies (within chain_length=5).
    full_name = "testvm.FULL.daily.qcow2"
    mock_state.record_full_backup(
        str(target.path),
        full_name,
        datetime(2025, 7, 13, 2),
        "vda",
    )
    mock_state.record_incremental_dependency(str(target.path), "inc1.qcow2", full_name)
    mock_state.record_incremental_dependency(str(target.path), "inc2.qcow2", full_name)

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    backup_provider = mock_factory._backup_provider

    with (
        patch("qsnap.core.os.path.exists", return_value=True),
        patch.object(
            backup_provider,
            "create_full_backup",
            wraps=backup_provider.create_full_backup,
        ) as full_spy,
    ):
        core._backup_target(vm, target, [snap])

    assert not full_spy.called, (
        "create_full_backup should NOT be called when incremental_count (2) <= chain_length (5)"
    )


def test_dry_run_logs_full_would_be_created(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """In dry-run mode, no FULL is created but a log line announces it.

    Core logs ``[dry-run] Would create FULL backup`` without calling
    ``create_full_backup()``.  Uses count-based chain_length in the log.
    """
    caplog.set_level(logging.INFO)
    target = make_target(target_chain_length=0)
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    backup_provider = mock_factory._backup_provider

    with patch.object(
        backup_provider,
        "create_full_backup",
        wraps=backup_provider.create_full_backup,
    ) as full_spy:
        core._backup_target(vm, target, [snap])

    # No FULL actually created — dry-run skips mutations.
    assert not full_spy.called, "create_full_backup should NOT be called in dry-run"

    # Log line announces the planned action with count-based info and
    # per-disk context (design D9) including chain-size estimate.
    assert "[dry-run]" in caplog.text
    assert "Would create FULL backup" in caplog.text
    assert "for disk" in caplog.text
    assert "chain_length=" in caplog.text


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
            disk="vda",
        ),
    )
    state.record_snapshot(
        vm_name,
        SnapshotInfo(
            name="snap4",
            path=Path("/var/lib/libvirt/snapshots/testvm/snap4.qcow2"),
            timestamp=datetime(2025, 7, 13, 14, 0),
            allocation=4194304,
            disk="vda",
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
                disk="vda",
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

    retention = RetentionResult(
        keep=["snap1", "snap2", "snap3", "snap4", "snap5"],
        remove=["snap6"],
    )
    manager = mock_factory._lifecycle_manager

    # Mock the domain XML refresh after offline commit (best-effort, non-fatal).
    mock_shell.expect("virsh dumpxml").returns(
        ShellResult(
            success=True,
            stdout="<domain><devices><disk><source file='/var/lib/libvirt/snapshots/testvm/snap5.qcow2'/></disk></devices></domain>",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.INFO)
    with (
        patch("os.path.exists", return_value=True),
        patch.object(
            core,
            "_get_chain_length",
            side_effect=[7, 6],
        ),
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

    retention = RetentionResult(
        keep=["snap1", "snap2", "snap6"],
        remove=["snap6"],
    )
    manager = mock_factory._lifecycle_manager

    # Mock the domain XML refresh after offline commit (best-effort, non-fatal).
    mock_shell.expect("virsh dumpxml").returns(
        ShellResult(
            success=True,
            stdout="<domain><devices><disk><source file='/var/lib/libvirt/snapshots/testvm/snap2.qcow2'/></disk></devices></domain>",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.INFO)
    with (
        patch("os.path.exists", return_value=True),
        patch.object(
            core,
            "_get_chain_length",
            side_effect=[7, 3],
        ),
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
    """Post-commit chain length unchanged → CRITICAL log + RuntimeError abort.

    Both pre-commit and post-commit queries return 7 entries.  After
    snap6 is removed from state, the post-commit query hits snap5 but
    still returns 7 entries — simulating that the blockcommit did not
    actually reduce the chain.  VM-level isolation: the suspected
    inconsistent chain aborts the VM pipeline (RuntimeError).
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
        pytest.raises(RuntimeError, match="chain length unchanged"),
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

    # Override conftest default — generic --backing-chain returns failure,
    # so _get_chain_length() returns None for the post-commit measurement.
    mock_shell.expect_first("qemu-img info --force-share --backing-chain").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="permission denied",
            returncode=1,
            error="permission denied",
        )
    )
    # But pre-commit _verify_backing_chain → scan_backing_chain queries
    # snap6_path specifically — override with success so blockcommit proceeds.
    mock_shell.expect_first(
        f"qemu-img info --force-share --backing-chain.*{re.escape(snap6_path)}"
    ).returns(ShellResult(success=True, stdout=chain_7, stderr="", returncode=0, error=None))

    retention = RetentionResult(
        keep=["snap1", "snap2", "snap3", "snap4", "snap5"],
        remove=["snap6"],
    )
    manager = mock_factory._lifecycle_manager

    # Mock the domain XML refresh after offline commit (best-effort, non-fatal).
    mock_shell.expect("virsh dumpxml").returns(
        ShellResult(
            success=True,
            stdout="<domain><devices><disk><source file='/var/lib/libvirt/snapshots/testvm/snap5.qcow2'/></disk></devices></domain>",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

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

    # Override conftest default — qemu-img fails so _get_chain_length() returns None.
    mock_shell.expect_first("qemu-img info --force-share --backing-chain").returns(
        ShellResult(success=False, stdout="", stderr="timeout", returncode=124, error="timeout")
    )

    retention = RetentionResult(
        keep=["snap1", "snap2", "snap3", "snap4", "snap5"],
        remove=["snap6"],
    )
    manager = mock_factory._lifecycle_manager

    # Mock the domain XML refresh after offline commit (best-effort, non-fatal).
    mock_shell.expect("virsh dumpxml").returns(
        ShellResult(
            success=True,
            stdout="<domain><devices><disk><source file='/var/lib/libvirt/snapshots/testvm/snap5.qcow2'/></disk></devices></domain>",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

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

    # State is empty → should query base_image.
    # Use expect_first to override conftest default --backing-chain expectation.
    mock_shell.expect_first(
        f"qemu-img info --force-share --backing-chain.*{re.escape('/var/lib/libvirt/images/testvm.qcow2')}"
    ).returns(ShellResult(success=True, stdout=chain_3, stderr="", returncode=0, error=None))
    length = core._get_chain_length(vm, disk="vda")
    assert length == 3, f"Expected 3 entries when querying base image, got {length}"

    # State has snapshots → should query most recent (snap6)
    _add_snapshots_6_for_chain(mock_state, "testvm")
    snap6_path = "/var/lib/libvirt/snapshots/testvm/snap6.qcow2"
    mock_shell.expect_first(
        f"qemu-img info --force-share --backing-chain.*{re.escape(snap6_path)}"
    ).returns(ShellResult(success=True, stdout=chain_3, stderr="", returncode=0, error=None))
    length = core._get_chain_length(vm, disk="vda")
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
        disk="vda",
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
        disk="vda",
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
        disk="vda",
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
        disk="vda",
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


def test_backup_retry_exhausted_returns_last_error(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When max_retries=1 and error is retryable → last error returned after exhaustion.

    The retry loop calls transfer_missing once (the only allowed attempt),
    the error is retryable so it does not fail early, and since attempt >=
    max_retries (1 >= 1), the loop returns the failed results immediately.
    """
    vm = make_vm_config(name="testvm")
    target = make_target(backup_retry_max=1, backup_retry_base="1s")
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
        disk="vda",
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

    assert transfer_spy.call_count == 1, "with max_retries=1, exactly one call should happen"
    assert len(results) == 1, "should return results"
    assert results[0].success is False, "result should indicate failure"
    assert results[0].error == "Connection refused", (
        "last error should be returned after exhausting retries"
    )


def test_transfer_retries_on_content_comparison_mismatch(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Content comparison mismatch verification error is retryable → retried and succeeds on second attempt."""
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
        disk="vda",
    )

    fail_result = BackupResult(
        success=False,
        snapshot_name="snap1",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "snap1",
        bytes_transferred=0,
        error="verification failed: content comparison mismatch",
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

    assert transfer_spy.call_count >= 2, (
        "content comparison mismatch should be retried at least once"
    )
    assert all(r.success for r in results), "all results should succeed after retry"
    assert "succeeded on retry" in caplog.text


def test_transfer_does_not_retry_format_error(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Format mismatch verification error is NOT retryable → fails immediately (one call)."""
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
        disk="vda",
    )

    fail_result = BackupResult(
        success=False,
        snapshot_name="snap1",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "snap1",
        bytes_transferred=0,
        error="verification failed: expected format qcow2, got raw",
    )

    provider = mock_factory._backup_provider

    with patch.object(
        provider,
        "transfer_missing",
        return_value=[fail_result],
    ) as transfer_spy:
        results = core._transfer_with_retry(provider, vm, target, [snap])

    assert transfer_spy.call_count == 1, (
        "format error is non-retryable → should fail immediately (one call)"
    )
    assert any(not r.success for r in results), "results should indicate failure"
    assert results[0].error == "verification failed: expected format qcow2, got raw", (
        "error should be the format verification error"
    )


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
            disk="vda",
        ),
    )
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap1"], "apparmor")

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
    mock_state.record_full_backup(str(target.path), full_name, now, "vda")
    mock_state.record_incremental_dependency(str(target.path), inc_name, full_name)

    backups = [
        SnapshotInfo(
            name=full_name,
            path=target.path / full_name,
            timestamp=now,
            allocation=10000,
            disk="vda",
        ),
        SnapshotInfo(
            name=inc_name, path=target.path / inc_name, timestamp=now, allocation=500, disk="vda"
        ),
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
        SnapshotInfo(
            name=full_name,
            path=target.path / full_name,
            timestamp=now,
            allocation=10000,
            disk="vda",
        ),
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


def test_dry_run_detects_vm_running_state_for_method(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Dry-run detects VM state: running → VM=running, stopped → VM=stopped.

    Method is always NBD (bitmap-only), but VM state is still reported
    in the dry-run log for informational purposes.
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
    core.dry_run = True

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    caplog.set_level(logging.INFO)

    # Count-based trigger: no prior FULLs → should_full=True.
    # Count-based trigger: no prior FULLs → should_full=True.
    # Default count-based check returns True — no prior FULLs.

    # --- Case A: VM running (default fixture dominfo returns State: running) ---
    core._backup_target(vm, target, [snap])

    assert "method=NBD" in caplog.text, "Running VM should produce method=NBD in dry-run log"
    # Dry-run log carries per-disk context (design D9).
    assert "for disk" in caplog.text, "Dry-run log should include disk context"
    # VM state is reported in dry-run log

    # --- Case B: VM stopped — patch is_vm_running to return False ---
    caplog.clear()
    with patch("qsnap.core.is_vm_running", return_value=False):
        core._backup_target(vm, target, [snap])

    assert "method=NBD" in caplog.text, "Stopped VM should also produce method=NBD (bitmap-only)"
    assert "VM=stopped" in caplog.text
    assert "for disk" in caplog.text, "Dry-run log should include disk context"


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
    target = make_target()
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    caplog.set_level(logging.INFO)

    # Count-based trigger: no prior FULLs → should_full=True.
    # Count-based trigger: no prior FULLs → should_full=True.
    # Default count-based check returns True — no prior FULLs.

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        core._backup_target(vm, target, [snap])

    # Log confirms FULL would be created with per-disk context and
    # chain-size estimate (design D9).
    assert "[dry-run] Would create FULL backup" in caplog.text
    assert "for disk" in caplog.text
    assert "chain_length=" in caplog.text

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


def test_full_creation_works_for_bitmap(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Bitmap backup provider (always used) → create_full_backup() called.

    Verifies that the factory always returns the bitmap backup provider
    and that create_full_backup succeeds without raising NotImplementedError.
    """
    # FULL verification is not the subject of this test — disable it so the
    # happy path completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Count-based trigger: no prior FULLs causes first backup to create FULL.

    bitmap_provider = mock_factory._bitmap_backup_provider

    with patch.object(
        bitmap_provider,
        "create_full_backup",
        wraps=bitmap_provider.create_full_backup,
    ) as full_spy:
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "BitmapBackupProvider.create_full_backup() should be called"


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
        disk="vda",
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
    """Core._deep_check_file() uses --force-share on qemu-img check with 7200s timeout."""
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

    # Timeout changed from 60s to 7200s (design D6)
    assert check_calls[0].kwargs.get("timeout") == 7200, (
        f"qemu-img check timeout should be 7200, got: {check_calls[0].kwargs.get('timeout')}"
    )


# ── test_deep_check_errors_not_just_corruptions ────────────────────────────


def test_deep_check_errors_not_just_corruptions(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``_deep_check_file`` checks the ``errors`` field, not just ``corruptions``.

    When ``errors > 0`` but ``corruptions == 0``, the file is reported as
    ``"warning"`` and added to the broken list.
    """
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
            stdout=json.dumps({"corruptions": 0, "errors": 2, "leaks": 0}),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    broken: list[str] = []
    result = core._deep_check_file(
        Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        "snap1",
        broken,
    )

    assert result == "warning", (
        f"_deep_check_file should return 'warning' when errors>0, got: {result}"
    )
    assert "snap1" in broken, "snap1 should be added to broken list when errors>0"


# ── test_deep_check_timeout_7200_seconds ─────────────────────────────────


def test_deep_check_timeout_7200_seconds(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``_deep_check_file`` passes timeout=7200 (not 60) to qemu-img check."""
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
            stdout=json.dumps({"corruptions": 0, "errors": 0, "leaks": 0}),
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

    timeout_val = check_calls[0].kwargs.get("timeout")
    assert timeout_val == 7200, (
        f"Timeout should be 7200 (2 hours) for large disks, got: {timeout_val}"
    )


# ── test_core_passes_vm_name_to_create_full_backup ───────────────────────


def test_core_passes_vm_name_to_create_full_backup(
    make_vm_config,
    make_target,
    make_global_config,
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
    # FULL verification is not the subject of this test — disable it so the
    # happy path completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    target = make_target()
    vm = make_vm_config(name="3.Projects_opencode", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
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
        disk="vda",
    )
    mock_state.record_snapshot("3.Projects_opencode", snap)

    # Count-based trigger: no prior FULLs causes first backup to create FULL.

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
    """All snapshot files exist → stale guard passes → blockcommit called, D5 cleanup.

    When every snapshot in ``to_merge`` has its file present on disk,
    ``os.path.exists()`` returns True for all.  The stale guard does not
    remove any entries, but after successful blockcommit, design D5
    unconditionally removes committed snapshots from state.
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
        disk="vda",
    )
    snap2 = SnapshotInfo(
        name="snap2",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap2.qcow2"),
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=2000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap1)
    mock_state.record_snapshot("testvm", snap2)

    retention = RetentionResult(keep=[], remove=["snap1", "snap2"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True) as exists_mock,
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(mock_state, "remove_snapshot", wraps=mock_state.remove_snapshot) as remove_spy,
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # All files exist — no staleness detected
    assert exists_mock.called, "os.path.exists should be called to verify files"

    # Design D5 unconditionally removes COMMITTED snapshots from state on
    # success — both snap1 and snap2 are committable (conftest domblklist
    # default returns snap4, not in the remove set).
    assert remove_spy.call_count == 2, (
        f"D5 should remove both committed snapshots, got {remove_spy.call_count}"
    )
    remove_spy_names = [c.args[1] for c in remove_spy.call_args_list]
    assert set(remove_spy_names) == {"snap1", "snap2"}

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
        disk="vda",
    )
    snap_stale = SnapshotInfo(
        name="snap_stale",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap_stale.qcow2"),
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=2000,
        disk="vda",
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
        patch.object(mock_state, "remove_snapshot", wraps=mock_state.remove_snapshot) as remove_spy,
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
    assert merge_names == ["snap_ok"], f"Only snap_ok should be blockcommitted, got: {merge_names}"
    assert "snap_stale" not in merge_names, "stale snapshot must not be blockcommitted"


def test_blockcommit_stale_guard_dry_run_no_state_write(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """L3: dry-run predicts stale-entry removal without writing state.

    Same setup as ``test_blockcommit_stale_guard_one_stale_removed`` but in
    dry-run mode: the stale entry is filtered out of ``to_merge`` and a
    ``[dry-run] Would remove stale state entry`` WARNING is logged, but
    ``remove_snapshot()`` is never called and no blockcommit executes.
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
    core.dry_run = True

    snap_ok = SnapshotInfo(
        name="snap_ok",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap_ok.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
        disk="vda",
    )
    snap_stale = SnapshotInfo(
        name="snap_stale",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap_stale.qcow2"),
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=2000,
        disk="vda",
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
        patch.object(mock_state, "remove_snapshot", wraps=mock_state.remove_snapshot) as remove_spy,
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # No state write in dry-run — the stale entry survives.
    remove_spy.assert_not_called()
    remaining = {s.name for s in mock_state.get_snapshots("testvm")}
    assert "snap_stale" in remaining, "stale entry must remain in state during dry-run"

    # Prediction is logged instead.
    assert "[dry-run] Would remove stale state entry" in caplog.text
    assert "snap_stale" in caplog.text

    # No blockcommit executes in dry-run.
    bc_spy.assert_not_called()


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
        disk="vda",
    )
    snap2 = SnapshotInfo(
        name="snap2",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap2.qcow2"),
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=2000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap1)
    mock_state.record_snapshot("testvm", snap2)

    retention = RetentionResult(keep=[], remove=["snap1", "snap2"])
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.INFO)

    with (
        patch("os.path.exists", return_value=False),
        patch.object(mock_state, "remove_snapshot", wraps=mock_state.remove_snapshot) as remove_spy,
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

    Design D5 unconditionally removes COMMITTED snapshots from state on
    success, so ``remove_snapshot`` is called for snap_stale (stale guard),
    snap_a (D5), and snap_b (D5).
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
        disk="vda",
    )
    snap_stale = SnapshotInfo(
        name="snap_stale",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap_stale.qcow2"),
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=2000,
        disk="vda",
    )
    snap_b = SnapshotInfo(
        name="snap_b",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap_b.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=3000,
        disk="vda",
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
        patch.object(mock_state, "remove_snapshot", wraps=mock_state.remove_snapshot) as remove_spy,
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Stale guard removes snap_stale; D5 removes snap_a and snap_b after
    # successful commit (conftest domblklist default = snap4, not in remove set).
    remove_spy_calls = [c.args[1] for c in remove_spy.call_args_list]
    assert set(remove_spy_calls) == {"snap_stale", "snap_a", "snap_b"}, (
        f"Expected snap_stale (stale guard) + snap_a + snap_b (D5), got: {remove_spy_calls}"
    )

    # WARNING logged for the single stale entry
    assert "snap_stale" in caplog.text
    assert "Stale state entry" in caplog.text

    # Blockcommit called with the two surviving snapshots
    assert bc_spy.called, "blockcommit should proceed despite one stale entry in the middle"
    merge_names = [s.name for s in bc_spy.call_args[0][1]]
    assert set(merge_names) == {"snap_a", "snap_b"}, (
        f"snap_a and snap_b should be blockcommitted, snap_stale skipped; got: {merge_names}"
    )
    assert "snap_stale" not in merge_names, "stale snapshot must be excluded from blockcommit"


# ── Blockcommit VM State Check ───────────────────────────────────────────────


def test_blockcommit_live_commit_when_vm_running(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VM running + lifecycle_mode="virsh" → non-active snapshots committed live.

    The adaptive lifecycle fork (design D2) commits the non-active prefix
    via ``virsh blockcommit`` when the VM is running in virsh mode.
    Only the active layer (reported by domblklist) is deferred.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        lifecycle_mode="virsh",
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

    # Two snapshots: snap1 (older, to be committed) and snap2 (newer,
    # will be the active layer reported by domblklist).
    snap1 = SnapshotInfo(
        name="snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
        disk="vda",
    )
    snap2 = SnapshotInfo(
        name="snap2",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap2.qcow2"),
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=2000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap1)
    mock_state.record_snapshot("testvm", snap2)

    # VM is running.
    mock_shell.expect_first("domstate").returns(
        ShellResult(success=True, stdout="running\n", stderr="", returncode=0, error=None)
    )
    # domblklist returns snap2 as the active layer — snap1 is NOT active.
    domblklist_output = (
        " Target   Source\n"
        "--------------------------------------\n"
        " vda      /var/lib/libvirt/snapshots/testvm/snap2.qcow2\n"
    )
    mock_shell.expect_first("domblklist").returns(
        ShellResult(success=True, stdout=domblklist_output, stderr="", returncode=0, error=None)
    )

    # Remove only snap1 (non-active snapshot).
    retention = RetentionResult(keep=["snap2"], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(
            mock_factory,
            "create_lifecycle_manager",
            wraps=mock_factory.create_lifecycle_manager,
        ) as lifecycle_spy,
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Blockcommit was called for the non-active snapshot.
    assert bc_spy.called, "blockcommit should proceed for non-active snapshots"
    merge_names = [s.name for s in bc_spy.call_args[0][1]]
    assert set(merge_names) == {"snap1"}, "snap1 (non-active) should be committed"

    # Factory was called with mode="virsh" (live commit path).
    lifecycle_spy.assert_called_once_with(mode="virsh")

    # No deferred entry — snap1 is not the active layer.
    deferred = mock_state.get_deferred_operations("testvm")
    assert deferred == [], "No deferred entries expected for non-active snapshot"

    # snap1 removed from state after successful commit (design D5).
    snapshots_after = mock_state.get_snapshots("testvm")
    assert not any(s.name == "snap1" for s in snapshots_after), (
        "snap1 should be removed from state after successful blockcommit"
    )


def test_blockcommit_executes_when_vm_shut_off(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VM shut off → blockcommit proceeds via qemu-img executor.

    The adaptive lifecycle fork (design D2) uses ``qemu-img commit`` on
    shut off even when ``lifecycle_mode="virsh"``.  ``remove_snapshot``
    is called unconditionally on success (design D5).
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        lifecycle_mode="virsh",
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

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # domblklist returns base image (NOT snap1) — snap1 is not the tip.
    domblklist_output = (
        " Target   Source\n"
        "--------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
    )
    mock_shell.expect_first("domblklist").returns(
        ShellResult(success=True, stdout=domblklist_output, stderr="", returncode=0, error=None)
    )

    # VM is shut off (conftest default).  The race guard in the qemu-img
    # path also calls domstate — the conftest default matches both.

    retention = RetentionResult(keep=[], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(
            mock_factory,
            "create_lifecycle_manager",
            wraps=mock_factory.create_lifecycle_manager,
        ) as lifecycle_spy,
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Blockcommit was called (VM is shut off).
    assert bc_spy.called, "blockcommit should proceed when VM is shut off"
    merge_names = [s.name for s in bc_spy.call_args[0][1]]
    assert "snap1" in merge_names, "snap1 should be blockcommitted"

    # Factory was called with mode="qemu-img" (offline executor even with
    # lifecycle_mode="virsh" — the fork overrides it).
    lifecycle_spy.assert_called_once_with(mode="qemu-img")

    # snap1 removed from state after successful commit (design D5).
    snapshots_after = mock_state.get_snapshots("testvm")
    assert not any(s.name == "snap1" for s in snapshots_after), (
        "snap1 should be removed from state after successful blockcommit"
    )

    # No deferred entries.
    deferred = mock_state.get_deferred_operations("testvm")
    assert deferred == [], "No deferred entries expected when VM is shut off"


def test_blockcommit_deferred_when_vm_paused(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """VM paused → blockcommit deferred (paused is NOT "shut off").

    Only ``"shut off"`` allows blockcommit to proceed; any other state
    (including paused) triggers deferral.
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

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Override conftest default — VM is paused.
    mock_shell.expect_first("domstate").returns(
        ShellResult(success=True, stdout="paused\n", stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=[], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.INFO)
    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Blockcommit was NOT called — paused VM → deferred.
    bc_spy.assert_not_called()

    # Deferred entry added with reason "vm_running".
    deferred = mock_state.get_deferred_operations("testvm")
    assert len(deferred) == 1
    assert deferred[0].reason == "vm_running"
    assert deferred[0].snapshots == ["snap1"]

    # INFO log about deferring.
    assert "Deferring blockcommit" in caplog.text


def test_blockcommit_vm_state_check_failure_non_fatal(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """domstate call fails → blockcommit proceeds (non-fatal fallback).

    When the ``virsh domstate`` command itself fails (e.g. domain not
    found), the code falls through and allows blockcommit to proceed
    rather than blocking it.
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

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Override conftest default — domstate fails entirely.
    mock_shell.expect_first("domstate").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: domain not found",
            returncode=1,
            error="domain not found",
        )
    )

    retention = RetentionResult(keep=[], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Blockcommit proceeds despite failed domstate check.
    assert bc_spy.called, "blockcommit should proceed when domstate check fails (non-fatal)"

    # No deferral occurs (domstate failure is non-fatal, not a defer trigger).
    deferred = mock_state.get_deferred_operations("testvm")
    assert len(deferred) == 0, "No deferred entries expected when domstate fails"


def test_deferred_blockcommit_executed_after_vm_shutdown(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Deferred blockcommit executes after VM shutdown via qemu-img executor.

    Per D6: drain uses the qemu-img executor on shut-off regardless of
    configured lifecycle_mode.  Committed snapshots are removed from
    state (design D5) and the deferred queue is empty afterwards.
    """
    vm = make_vm_config(name="testvm")
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
            disk="vda",
        ),
    )
    mock_state.add_deferred_blockcommit("testvm", "vda", ["snap1"], "vm_running")

    # VM is shut off (conftest default for domstate).
    # domblklist returns base image — NOT snap1's path — so snap1 is
    # committable (not the XML-referenced tip).
    domblklist_output = (
        " Target   Source\n"
        "--------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
    )
    mock_shell.expect_first("domblklist").returns(
        ShellResult(success=True, stdout=domblklist_output, stderr="", returncode=0, error=None)
    )

    manager = mock_factory._lifecycle_manager

    with (
        patch.object(
            mock_factory,
            "create_lifecycle_manager",
            wraps=mock_factory.create_lifecycle_manager,
        ) as lifecycle_spy,
        patch.object(
            manager,
            "blockcommit",
            wraps=manager.blockcommit,
        ) as bc_spy,
    ):
        core._check_deferred_operations(vm)

    # Blockcommit was called for the deferred snapshot.
    assert bc_spy.called, "blockcommit should be called for deferred operation"
    merge_names = [s.name for s in bc_spy.call_args[0][1]]
    assert "snap1" in merge_names, "snap1 should be blockcommitted"

    # Factory was called with mode="qemu-img" (offline drain executor per D6).
    lifecycle_spy.assert_called_once_with(mode="qemu-img")

    # snap1 removed from state after successful commit (design D5).
    snapshots_after = mock_state.get_snapshots("testvm")
    assert not any(s.name == "snap1" for s in snapshots_after), (
        "snap1 should be removed from state after successful deferred commit"
    )

    # Deferred queue is empty afterwards.
    assert mock_state.get_deferred_operations("testvm") == []


def test_preserve_all_vm_running_no_blockcommit(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Large chain_length + VM running → blockcommit never reached.

    When no snapshots are removed (remove set empty),
    ``_blockcommit_snapshots`` returns early at the
    empty to_merge guard, never reaching the VM state check.  This proves
    a large snapshot_chain_length + running VM does not trigger a spurious deferral.
    """
    global_cfg = make_global_config()
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
        snapshot_chain_length=999999,
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
        disk="vda",
    )
    snap2 = SnapshotInfo(
        name="snap2",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap2.qcow2"),
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=2000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap1)
    mock_state.record_snapshot("testvm", snap2)

    # Override conftest default — VM is running (but it shouldn't matter).
    mock_shell.expect_first("domstate").returns(
        ShellResult(success=True, stdout="running\n", stderr="", returncode=0, error=None)
    )

    # Retention keeps ALL snapshots — remove set is empty.
    retention = RetentionResult(keep=["snap1", "snap2"], remove=[])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Blockcommit was NOT called (nothing to remove).
    bc_spy.assert_not_called()

    # No deferred entries added — the empty to_merge guard returned before
    # reaching the VM state check.
    deferred = mock_state.get_deferred_operations("testvm")
    assert len(deferred) == 0, "preserve=all should not add deferred entries"


# ── XML Refresh After Offline Commit (Design D8) ────────────────────────────


def test_offline_commit_refreshes_domain_xml(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Offline (qemu-img) commit triggers _refresh_domain_backing_store.

    Design D8: after a successful offline commit, ``_refresh_domain_backing_store``
    strips stale ``<backingStore>`` elements from the domain XML so the VM
    remains bootable.  This test verifies that ``virsh dumpxml`` and
    ``virsh define`` are called, and the INFO "Refreshed domain XML" log
    is emitted.
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

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # domblklist returns the base image (NOT snap1) — snap1 is committable.
    domblklist_output = (
        " Target   Source\n"
        "--------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
    )
    mock_shell.expect_first("domblklist").returns(
        ShellResult(success=True, stdout=domblklist_output, stderr="", returncode=0, error=None)
    )

    # Provide domain XML with <backingStore> elements — the refresh strips them.
    domain_xml = """<domain type="kvm">
  <devices>
    <disk type="file" device="disk">
      <driver name="qemu" type="qcow2"/>
      <source file="/var/lib/libvirt/snapshots/testvm/snap1.qcow2"/>
      <backingStore type="file">
        <format type="qcow2"/>
        <source file="/var/lib/libvirt/images/testvm.qcow2"/>
        <backingStore/>
      </backingStore>
      <target dev="vda" bus="virtio"/>
    </disk>
  </devices>
</domain>"""
    mock_shell.expect("virsh dumpxml").returns(
        ShellResult(success=True, stdout=domain_xml, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh define").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=[], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.INFO)
    with (
        patch("os.path.exists", return_value=True),
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Blockcommit was called and succeeded
    assert bc_spy.called, "blockcommit should proceed when all files exist"

    # virsh define was called after dumpxml (XML refresh)
    define_calls = [
        " ".join(c.args[0])
        for c in shell_spy.call_args_list
        if c.args
        and isinstance(c.args[0], list)
        and "virsh" in c.args[0][0]
        and "define" in " ".join(c.args[0])
    ]
    assert len(define_calls) >= 1, "virsh define should be called to refresh domain XML"

    # "Refreshed domain XML" INFO log was emitted
    assert "Refreshed domain XML" in caplog.text, (
        "Should log INFO about refreshed domain XML after offline commit"
    )

    # Committed snapshots removed from state (D5)
    snapshots_after = mock_state.get_snapshots("testvm")
    assert not any(s.name == "snap1" for s in snapshots_after), (
        "snap1 should be removed from state after successful commit (D5)"
    )


def test_offline_commit_xml_refresh_failure_non_fatal(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """XML refresh failure after offline commit is non-fatal — commit still counted.

    Design D8: ``_refresh_domain_backing_store`` is best-effort.  If
    ``virsh dumpxml`` fails, a WARNING is logged but the blockcommit
    still succeeds and state cleanup still proceeds (D5).
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

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # domblklist returns base image — snap1 is committable
    domblklist_output = (
        " Target   Source\n"
        "--------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
    )
    mock_shell.expect_first("domblklist").returns(
        ShellResult(success=True, stdout=domblklist_output, stderr="", returncode=0, error=None)
    )

    # virsh dumpxml fails — XML refresh will be skipped with a WARNING
    mock_shell.expect("virsh dumpxml").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: domain not found",
            returncode=1,
            error="domain not found",
        )
    )

    retention = RetentionResult(keep=[], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.WARNING)
    with (
        patch("os.path.exists", return_value=True),
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        # Must not raise — XML refresh failure is non-fatal
        core._blockcommit_snapshots(vm, retention)

    # Blockcommit was called and succeeded despite XML refresh failure
    assert bc_spy.called, "blockcommit should proceed even when dumpxml fails"

    # WARNING logged about XML refresh failure
    warning_logs = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Could not refresh domain XML" in r.message for r in warning_logs), (
        f"Expected WARNING about XML refresh failure, got: {[r.message for r in warning_logs]}"
    )

    # Committed snapshot still removed from state (D5 — state cleanup is
    # independent of XML refresh).
    snapshots_after = mock_state.get_snapshots("testvm")
    assert not any(s.name == "snap1" for s in snapshots_after), (
        "snap1 should be removed from state despite XML refresh failure (D5)"
    )


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
            "create_multi",
            wraps=snapshot_provider.create_multi,
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
    assert "Would create snapshot for disk" in caplog.text

    # Predictions are populated, actions remain empty.
    assert result.predictions is not None
    assert len(result.predictions) > 0
    assert result.actions == []

    # No mutations executed
    assert not create_spy.called, "snapshot provider create_multi() must NOT be called in dry-run"
    assert not transfer_spy.called, (
        "backup provider transfer_missing() must NOT be called in dry-run"
    )

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
            "create_multi",
            wraps=snapshot_provider.create_multi,
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
        ) as _alloc_spy,
    ):
        result = core.run()

    assert result.dry_run is True
    # Predictions populated in dry-run mode.
    assert len(result.predictions) > 0
    # No snapshot creation
    assert not create_spy.called
    # No backup transfer
    assert not transfer_spy.called
    # No state mutations
    assert not record_spy.called, "record_snapshot must not be called in dry-run"


# ── test_detect_orphan_checkpoints_uses_factory ──────────────────────────────


def test_detect_orphan_checkpoints_uses_factory(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core._detect_orphan_checkpoints calls self._factory.create_backup_provider().

    The factory routing fix (design D5) ensures Core does NOT directly
    instantiate BitmapBackupProvider.  Instead, it delegates to the
    factory's ``create_backup_provider(vm_config, target)`` and calls
    ``list_checkpoints()`` / ``target_hash()`` on the result.
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

    with (
        patch.object(
            mock_factory,
            "create_backup_provider",
            wraps=mock_factory.create_backup_provider,
        ) as factory_spy,
        patch.object(
            mock_factory._bitmap_backup_provider,
            "list_checkpoints",
            return_value=["qsnap-abc12345-snap1"],
        ),
        patch.object(
            mock_factory._bitmap_backup_provider,
            "target_hash",
            return_value="deadbeef",
        ),
    ):
        orphans = core._detect_orphan_checkpoints(vm)

    # Factory was called with the VM and its first target.
    assert factory_spy.called, "create_backup_provider should be called"
    call_args = factory_spy.call_args
    assert call_args[0][0] is vm, "First arg should be vm_config"
    assert call_args[0][1] is target, "Second arg should be target"

    # The checkpoint is orphaned because its hash ("abc12345") does not
    # match the configured target hash ("deadbeef").
    assert len(orphans) == 1
    assert "qsnap-abc12345-snap1" in orphans


# ── test_resolve_disks_returns_empty_on_failure ──────────────────────────────


def test_resolve_disks_returns_empty_on_failure(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """When virsh domblklist fails, _resolve_disks returns empty list and logs WARNING.

    The disk fallback fix (design D6) removes the ``["vda"]`` default.
    When domblklist fails, ``_resolve_disks()`` returns ``[]`` and a
    WARNING is logged.  The snapshot is skipped because there are no
    disks to snapshot.
    """
    # Override the default domblklist expectation to simulate failure.
    # Remove existing domblklist first, then add failure.
    mock_shell._expectations = [
        e for e in mock_shell._expectations if "domblklist" not in e.pattern
    ]
    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: Domain not found",
            returncode=1,
            error="Domain not found",
        )
    )

    vm = make_vm_config(name="testvm", snapshot_create="always")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    caplog.set_level(logging.WARNING)
    snapshot_provider = mock_factory._snapshot_provider

    with patch.object(
        snapshot_provider,
        "create_multi",
        wraps=snapshot_provider.create_multi,
    ) as create_spy:
        core.run()

    # Snapshot IS created — _resolve_disks uses vm_config.disks,
    # not domblklist, so domblklist failure does not prevent snapshot creation.
    assert create_spy.called, (
        "Snapshot should be created via vm_config.disks, regardless of domblklist failure"
    )


# ── Onchange Backup Gate (independent-target-onchange) ────────────────────


def test_always_mode_backup_gate_bypassed(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """backup_create="always" → gate is bypassed entirely.

    ``_backup_target`` must NOT call ``_should_backup_onchange()``.  The
    ``if target.backup_create == "onchange"`` check is ``False``, so the
    gate code is skipped.
    """
    # FULL verification is not the subject of this test — disable it so the
    # happy path completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    target = make_target(backup_create="always")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
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
        disk="vda",
    )

    with patch.object(
        core,
        "_should_backup_onchange",
        wraps=core._should_backup_onchange,
    ) as gate_spy:
        core._backup_target(vm, target, [snap])

    assert not gate_spy.called, "_should_backup_onchange should NOT be called in always mode"


def test_core_passes_convert_parallel_to_create_full_backup(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core reads target.convert_parallel and passes it to provider.create_full_backup()."""
    # FULL verification is not the subject of this test — disable it so the
    # happy path completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    target = make_target(
        convert_parallel=8,
    )
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Count-based trigger: no prior FULLs causes first backup to create FULL.

    backup_provider = mock_factory._backup_provider

    with patch.object(
        backup_provider,
        "create_full_backup",
        wraps=backup_provider.create_full_backup,
    ) as full_spy:
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "create_full_backup should be called when strategy returns True"
    assert full_spy.call_args.kwargs.get("convert_parallel") == 8, (
        f"convert_parallel should be 8, got: {full_spy.call_args.kwargs.get('convert_parallel')!r}"
    )


def test_core_passes_convert_out_of_order_to_create_full_backup(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core reads target.convert_out_of_order and passes it to provider.create_full_backup()."""
    # FULL verification is not the subject of this test — disable it so the
    # happy path completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    target = make_target(
        convert_out_of_order=False,
    )
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Count-based trigger: no prior FULLs causes first backup to create FULL.

    backup_provider = mock_factory._backup_provider

    with patch.object(
        backup_provider,
        "create_full_backup",
        wraps=backup_provider.create_full_backup,
    ) as full_spy:
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "create_full_backup should be called when strategy returns True"
    assert full_spy.call_args.kwargs.get("convert_out_of_order") is False, (
        f"convert_out_of_order should be False, got: "
        f"{full_spy.call_args.kwargs.get('convert_out_of_order')!r}"
    )


def test_core_passes_convert_parallel_to_transfer_missing(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core reads target.convert_parallel and passes it to provider.transfer_missing()."""
    # FULL verification is not the subject of this test — disable it so the
    # happy path completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    target = make_target(
        convert_parallel=8,
    )
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Default count-based check returns False — no FULL,
    # only transfer_missing is called.

    backup_provider = mock_factory._backup_provider

    with patch.object(
        backup_provider,
        "transfer_missing",
        wraps=backup_provider.transfer_missing,
    ) as transfer_spy:
        core._backup_target(vm, target, [snap])

    assert transfer_spy.called, "transfer_missing should be called"
    assert transfer_spy.call_args.kwargs.get("convert_parallel") == 8, (
        f"convert_parallel should be 8, got: "
        f"{transfer_spy.call_args.kwargs.get('convert_parallel')!r}"
    )


def test_core_passes_convert_out_of_order_to_transfer_missing(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core reads target.convert_out_of_order and passes it to provider.transfer_missing()."""
    # FULL verification is not the subject of this test — disable it so the
    # happy path completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    target = make_target(
        convert_out_of_order=False,
    )
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Default count-based check returns False — no FULL,
    # only transfer_missing is called.

    backup_provider = mock_factory._backup_provider

    with patch.object(
        backup_provider,
        "transfer_missing",
        wraps=backup_provider.transfer_missing,
    ) as transfer_spy:
        core._backup_target(vm, target, [snap])

    assert transfer_spy.called, "transfer_missing should be called"
    assert transfer_spy.call_args.kwargs.get("convert_out_of_order") is False, (
        f"convert_out_of_order should be False, got: "
        f"{transfer_spy.call_args.kwargs.get('convert_out_of_order')!r}"
    )


def test_onchange_skip_runs_retention(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When gate skips transfer, _evaluate_backup_retention is still called."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
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
        disk="vda",
    )

    # Gate: source disk unchanged — set baseline == current_allocation.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 2000000)
    mock_factory.change_detector.current_allocation = 2000000
    mock_factory.change_detector.changed = False

    with patch.object(
        core,
        "_evaluate_backup_retention",
        wraps=core._evaluate_backup_retention,
    ) as retention_spy:
        core._backup_target(vm, target, [snap])

    # Retention evaluation ran even though transfer was skipped.
    assert retention_spy.called, (
        "_evaluate_backup_retention should be called even when transfer is skipped"
    )


def test_gate_skip_retention_still_runs(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When skip_transfer=True, retention evaluation + cleanup still execute.

    The _backup_target method places retention evaluation and cleanup OUTSIDE
    the ``if not skip_transfer:`` block.  This test verifies both
    _evaluate_backup_retention and _cleanup_backups are called.
    """
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
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
        disk="vda",
    )

    # Gate: source disk unchanged — set baseline == current_allocation.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 2000000)
    mock_factory.change_detector.current_allocation = 2000000
    mock_factory.change_detector.changed = False

    with (
        patch.object(
            core, "_evaluate_backup_retention", wraps=core._evaluate_backup_retention
        ) as retention_spy,
        patch.object(core, "_cleanup_backups", wraps=core._cleanup_backups) as cleanup_spy,
    ):
        result = core._backup_target(vm, target, [snap])

    assert result is False  # no backup failure
    assert retention_spy.called, "retention evaluation should run even when skip_transfer=True"
    assert cleanup_spy.called, "cleanup should run even when skip_transfer=True"


def test_onchange_skip_cleans_expired_backups(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When gate skips transfer, retention still runs and deletes expired backups."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
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
        disk="vda",
    )

    # Gate: source disk unchanged — set baseline == current_allocation.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 2000000)
    mock_factory.change_detector.current_allocation = 2000000

    # Set up an expired backup on target — retention engine should mark it
    # for removal.
    old_backup = SnapshotInfo(
        name="testvm.FULL.old_backup.qcow2",
        path=target.path / "testvm.FULL.old_backup.qcow2",
        timestamp=datetime(2020, 1, 1),
        allocation=0,
        disk="vda",
    )

    # Mock provider.list() to show the old backup so retention evaluates it.
    with patch.object(
        mock_factory._backup_provider,
        "list",
        return_value=[old_backup],
    ):
        # Use a retention engine that removes old_backup.
        mock_factory._retention_engine.evaluate = lambda items, policy, now, **kw: RetentionResult(
            keep=[i.name for i in items if i.name != "testvm.FULL.old_backup.qcow2"],
            remove=["testvm.FULL.old_backup.qcow2"],
        )

        with (
            patch.object(
                mock_factory._backup_provider,
                "delete",
                wraps=mock_factory._backup_provider.delete,
            ) as delete_spy,
            patch("qsnap.core.verify_full_backup", return_value=None),
        ):
            core._backup_target(vm, target, [snap])

    # Expired backup was deleted even though transfer was skipped.
    assert delete_spy.called, "Expired backup should be deleted even when transfer is skipped"


# ── New onchange gate tests (independent-target-onchange) ──────────────────


def test_onchange_source_disk_changed_gate_opens(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """current_allocation > baseline → gate opens (proceeds with backup)."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Baseline from prior run is smaller than current allocation.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 1000000)
    mock_factory.change_detector.current_allocation = 3000000

    should_proceed, change_results = core._should_backup_onchange(vm, target)
    assert should_proceed is True
    assert change_results["vda"].current_allocation == 3000000


def test_onchange_source_disk_unchanged_gate_skips(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """current_allocation == baseline → gate skips (disk unchanged)."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_state.set_last_backup_allocation(str(target.path), "vda", 2000000)
    mock_factory.change_detector.current_allocation = 2000000
    mock_factory.change_detector.changed = False

    should_proceed, change_results = core._should_backup_onchange(vm, target)
    assert should_proceed is False


def test_onchange_first_run_no_baseline_gate_opens(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """No baseline (None) → gate opens (first backup)."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # No baseline set — get_last_backup_allocation returns None.
    should_proceed, change_results = core._should_backup_onchange(vm, target)
    assert should_proceed is True


def test_onchange_gate_works_without_snapshots(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """No snapshots in state, disk changed → gate opens."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # No snapshots in state, no baseline → gate opens.
    mock_factory.change_detector.current_allocation = 5000000

    should_proceed, change_results = core._should_backup_onchange(vm, target)
    assert should_proceed is True


def test_onchange_always_snapshot_disk_unchanged_gate_skips(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """snapshot_create="always", disk unchanged → gate skips backup."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm", snapshot_create="always")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_state.set_last_backup_allocation(str(target.path), "vda", 2000000)
    mock_factory.change_detector.current_allocation = 2000000
    mock_factory.change_detector.changed = False

    should_proceed, change_results = core._should_backup_onchange(vm, target)
    assert should_proceed is False


def test_onchange_allocation_size_detector_mode(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """allocation-size mode: changed = current > last (strictly greater)."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm", change_detection_mode="allocation-size")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # current_allocation > baseline → changed
    mock_factory.change_detector.changed = False
    mock_state.set_last_backup_allocation(str(target.path), "vda", 1000000)
    mock_factory.change_detector.current_allocation = 3000000
    should_proceed, _ = core._should_backup_onchange(vm, target)
    assert should_proceed is True

    # current_allocation == baseline → unchanged
    mock_state.set_last_backup_allocation(str(target.path), "vda", 3000000)
    should_proceed, _ = core._should_backup_onchange(vm, target)
    assert should_proceed is False

    # current_allocation < baseline → also unchanged (never shrinks backward)
    mock_state.set_last_backup_allocation(str(target.path), "vda", 5000000)
    should_proceed, _ = core._should_backup_onchange(vm, target)
    assert should_proceed is False


def test_onchange_allocation_map_detector_mode(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """allocation-map mode: changed = current != last."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm", change_detection_mode="allocation-map")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # current_allocation != baseline → changed
    mock_factory.change_detector.changed = False
    mock_state.set_last_backup_allocation(str(target.path), "vda", 1000000)
    mock_factory.change_detector.current_allocation = 9999999
    should_proceed, _ = core._should_backup_onchange(vm, target)
    assert should_proceed is True

    # current_allocation == baseline → unchanged
    mock_state.set_last_backup_allocation(str(target.path), "vda", 9999999)
    should_proceed, _ = core._should_backup_onchange(vm, target)
    assert should_proceed is False


def test_onchange_baseline_updated_after_successful_backup(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """After successful backup, set_last_backup_allocation is called."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
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
        disk="vda",
    )

    # Gate opens (no baseline).
    mock_factory.change_detector.current_allocation = 5000000

    # Mock FULL backup verification to pass.
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

    with patch.object(
        mock_state,
        "set_last_backup_allocation",
        wraps=mock_state.set_last_backup_allocation,
    ) as baseline_spy:
        core._backup_target(vm, target, [snap])

    assert baseline_spy.called, (
        "set_last_backup_allocation should be called after successful backup"
    )


def test_onchange_baseline_not_updated_on_failure(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """After failed backup, set_last_backup_allocation is NOT called.

    VM-level isolation: the definitive transfer failure raises
    BackupAbortError before the baseline update is reached.
    """
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
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
        disk="vda",
    )

    # Gate opens (no baseline).
    mock_factory.change_detector.current_allocation = 5000000

    fail_result = BackupResult(
        success=False,
        snapshot_name="snap1",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "snap1.qcow2",
        bytes_transferred=0,
        error="Connection refused",
    )

    with (
        patch.object(
            mock_state,
            "set_last_backup_allocation",
            wraps=mock_state.set_last_backup_allocation,
        ) as baseline_spy,
        patch.object(
            mock_factory._backup_provider,
            "transfer_missing",
            return_value=[fail_result],
        ),
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    # Baseline was NOT updated because the transfer failed.
    baseline_spy.assert_not_called()


def test_onchange_baseline_not_updated_when_gate_skips(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When gate skips (disk unchanged), set_last_backup_allocation is NOT called."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
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
        disk="vda",
    )

    # Gate: disk unchanged.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 2000000)
    mock_factory.change_detector.current_allocation = 2000000

    with patch.object(
        mock_state,
        "set_last_backup_allocation",
        wraps=mock_state.set_last_backup_allocation,
    ) as baseline_spy:
        core._backup_target(vm, target, [snap])

    baseline_spy.assert_not_called()


def test_onchange_skip_runs_retention_and_cleanup(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When gate skips transfer, retention + cleanup still run."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
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
        disk="vda",
    )

    # Gate: disk unchanged.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 2000000)
    mock_factory.change_detector.current_allocation = 2000000
    mock_factory.change_detector.changed = False

    with (
        patch.object(
            core, "_evaluate_backup_retention", wraps=core._evaluate_backup_retention
        ) as retention_spy,
        patch.object(core, "_cleanup_backups", wraps=core._cleanup_backups) as cleanup_spy,
    ):
        core._backup_target(vm, target, [snap])

    assert retention_spy.called, "retention should run even when gate skips"
    assert cleanup_spy.called, "cleanup should run even when gate skips"


def test_onchange_detector_failure_gate_opens_fail_safe(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Detector reports ``changed=True`` (fail-safe) — gate opens even
    when per-target allocation comparison would skip.

    When the change detector fails to query the source disk, it must
    return ``ChangeResult(changed=True)``.  The backup gate must interpret
    this ``changed=True`` flag as a fail-safe signal and proceed with the
    backup, overriding the per-target baseline comparison.
    """
    # FULL verification is not the subject of this test — disable it so the
    # happy path completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
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
        disk="vda",
    )

    # Simulate a real detector failure: the detector could not query
    # the source disk (virsh/qemu-img error), so it returns
    # changed=True (fail-safe) with current_allocation=0 and
    # last_allocation > 0 (snapshot-side baseline exists).
    # Per-target comparison: 0 > 2000000 → False (would skip).
    # Fail-safe override: changed=True + last_allocation>0 + current_allocation=0 → True.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 2000000)
    mock_factory.change_detector.current_allocation = 0
    mock_factory.change_detector.last_allocation = 1000000
    mock_factory.change_detector.changed = True

    should_proceed, change_results = core._should_backup_onchange(vm, target)
    assert should_proceed is True, (
        "fail-safe: gate must open when change_result.changed=True "
        "even though per-target allocation comparison returns False"
    )
    assert change_results["vda"].changed is True

    # Also verify through _backup_target that transfer proceeds.
    with patch.object(
        mock_factory._backup_provider,
        "transfer_missing",
        wraps=mock_factory._backup_provider.transfer_missing,
    ) as transfer_spy:
        core._backup_target(vm, target, [snap])

    assert transfer_spy.called, "transfer_missing should be called when fail-safe opens the gate"


def test_onchange_gate_uses_detector_not_snapshot_names(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Verify provider.list is NOT called for gate decision."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Baseline exists.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 1000000)
    mock_factory.change_detector.current_allocation = 3000000

    with patch.object(
        mock_factory._backup_provider,
        "list",
        wraps=mock_factory._backup_provider.list,
    ) as list_spy:
        should_proceed, _ = core._should_backup_onchange(vm, target)

    assert should_proceed is True
    assert not list_spy.called, "provider.list() should NOT be called by the onchange gate"


def test_onchange_transfer_skipped_but_retention_cleans_expired_backups(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Gate skips transfer, but retention runs and deletes expired backups."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
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
        disk="vda",
    )

    # Gate: disk unchanged.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 2000000)
    mock_factory.change_detector.current_allocation = 2000000

    old_backup = SnapshotInfo(
        name="testvm.FULL.old_backup.qcow2",
        path=target.path / "testvm.FULL.old_backup.qcow2",
        timestamp=datetime(2020, 1, 1),
        allocation=0,
        disk="vda",
    )

    with patch.object(
        mock_factory._backup_provider,
        "list",
        return_value=[old_backup],
    ):
        mock_factory._retention_engine.evaluate = lambda items, policy, now, **kw: RetentionResult(
            keep=[i.name for i in items if i.name != "testvm.FULL.old_backup.qcow2"],
            remove=["testvm.FULL.old_backup.qcow2"],
        )

        with (
            patch.object(
                mock_factory._backup_provider,
                "delete",
                wraps=mock_factory._backup_provider.delete,
            ) as delete_spy,
            patch("qsnap.core.verify_full_backup", return_value=None),
        ):
            core._backup_target(vm, target, [snap])

    assert delete_spy.called, "Expired backup should be deleted even when transfer is skipped"


def test_onchange_mode_switch_detects_changed(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Switching change_detection_mode mid-lifecycle → type mismatch → gate opens."""
    target = make_target(backup_create="onchange")
    # Run in allocation-map mode now, but baseline was stored as allocation-size.
    vm = make_vm_config(name="testvm", change_detection_mode="allocation-map")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Baseline set as if from allocation-size mode.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 1000000)
    # Current allocation for allocation-map mode is different.
    mock_factory.change_detector.current_allocation = 9999999

    should_proceed, _ = core._should_backup_onchange(vm, target)
    assert should_proceed is True, "mode switch should cause gate to open (type mismatch → changed)"


def test_onchange_gate_works_vm_shut_off(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """VM shut off — detector queries base image, gate works normally."""
    target = make_target(backup_create="onchange")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # No baseline → gate opens (detector works even when VM is shut off).
    mock_factory.change_detector.current_allocation = 5000000

    should_proceed, change_results = core._should_backup_onchange(vm, target)
    assert should_proceed is True
    assert change_results["vda"].current_allocation == 5000000


# ═══════════════════════════════════════════════════════════════════════════
# RUNTIME TESTS — _validate_state_at_startup + phantom cascade
# ═══════════════════════════════════════════════════════════════════════════


def test_startup_validation_cleans_phantom_fulls(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Phantom FULL (in state but not on disk) is removed by startup validation."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    phantom_path = Path("/nonexistent/phantom.FULL.monthly.qcow2")
    full_info = FullBackupInfo(
        name="phantom.FULL.monthly.qcow2",
        path=phantom_path,
        timestamp=datetime.now(),
        disk="vda",
    )
    mock_state._full_backups[str(target.path)] = [full_info]

    with patch.object(
        mock_state,
        "remove_all_incremental_dependencies",
        wraps=mock_state.remove_all_incremental_dependencies,
    ) as cascade_spy:
        core._validate_state_at_startup(vm)

    # Phantom FULL removed from state.
    remaining = mock_state.get_full_backups(str(target.path))
    assert len(remaining) == 0, f"Phantom FULL should be removed, got {remaining}"
    # remove_all_incremental_dependencies was called for cascade cleanup.
    assert cascade_spy.called, "Cascade dep cleanup should be called for phantom FULL"


def test_startup_validation_clears_baseline_after_phantom(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """After removing the only phantom FULL, stale baseline is cleared."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    phantom_path = Path("/nonexistent/phantom.FULL.monthly.qcow2")
    full_info = FullBackupInfo(
        name="phantom.FULL.monthly.qcow2",
        path=phantom_path,
        timestamp=datetime.now(),
        disk="vda",
    )
    mock_state._full_backups[str(target.path)] = [full_info]
    # Set a stale baseline.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 99999)

    core._validate_state_at_startup(vm)

    # Baseline cleared since no FULLs remain.
    assert mock_state.get_last_backup_allocation(str(target.path), "vda") is None, (
        "Stale baseline should be cleared after phantom FULL removal"
    )


def test_startup_validation_clears_baseline_no_fulls(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When there are no FULLs in state at all, stale baseline is cleared."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # No FULLs in state, but a stale baseline exists.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 99999)

    core._validate_state_at_startup(vm)

    # Baseline cleared.
    assert mock_state.get_last_backup_allocation(str(target.path), "vda") is None, (
        "Stale baseline should be cleared when no FULLs exist"
    )


def test_startup_validation_non_fatal_on_corrupt_state(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Corrupt state (exception from state manager) is non-fatal, logs warning."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Make get_full_backups raise an exception.
    with patch.object(
        mock_state,
        "get_full_backups",
        side_effect=RuntimeError("corrupt state"),
    ):
        caplog.set_level(logging.WARNING)
        # Should NOT raise.
        core._validate_state_at_startup(vm)

    # Warning was logged.
    warning_messages = [r.message for r in caplog.records]
    assert any("corrupt state" in msg for msg in warning_messages), (
        f"Expected corrupt state warning, got: {warning_messages}"
    )


def test_startup_validation_runs_for_standalone_backup(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """_execute_backup_steps calls _validate_state_at_startup before target loop."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with patch.object(
        core,
        "_validate_state_at_startup",
        wraps=core._validate_state_at_startup,
    ) as validate_spy:
        core._execute_backup_steps(vm)

    assert validate_spy.called, (
        "_validate_state_at_startup should be called by _execute_backup_steps"
    )


def test_startup_validation_no_checkpoint_deletion(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """_validate_state_at_startup does NOT auto-delete orphan checkpoints."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with patch.object(
        core,
        "_detect_orphan_checkpoints",
        wraps=core._detect_orphan_checkpoints,
    ) as detect_spy:
        core._validate_state_at_startup(vm)

    # _detect_orphan_checkpoints was NOT called (startup validation does not
    # auto-delete checkpoints — only qsnap reconcile does).
    assert not detect_spy.called, (
        "_detect_orphan_checkpoints should NOT be called during startup validation"
    )


def test_phantom_full_cascade_dep_cleanup(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """In _backup_target, phantom FULL removal triggers cascade dep cleanup."""
    # FULL verification is not the subject of this test — disable it so the
    # happy path completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Set up a phantom FULL in state (file doesn't exist on disk).
    phantom_path = Path("/nonexistent/phantom.FULL.monthly.qcow2")
    full_info = FullBackupInfo(
        name="phantom.FULL.monthly.qcow2",
        path=phantom_path,
        timestamp=datetime.now(),
        disk="vda",
    )
    mock_state._full_backups[str(target.path)] = [full_info]

    with patch.object(
        mock_state,
        "remove_all_incremental_dependencies",
        wraps=mock_state.remove_all_incremental_dependencies,
    ) as cascade_spy:
        core._backup_target(vm, target, [snap])

    # remove_all_incremental_dependencies was called after remove_full_backup.
    assert cascade_spy.called, (
        "remove_all_incremental_dependencies should be called after phantom FULL removal"
    )


def test_phantom_last_full_clears_baseline(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """When the last phantom FULL is removed, baseline is cleared."""
    # FULL verification is not the subject of this test — disable it so the
    # happy path completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Set up a phantom FULL (file doesn't exist).
    phantom_path = Path("/nonexistent/phantom.FULL.monthly.qcow2")
    full_info = FullBackupInfo(
        name="phantom.FULL.monthly.qcow2",
        path=phantom_path,
        timestamp=datetime.now(),
        disk="vda",
    )
    mock_state._full_backups[str(target.path)] = [full_info]
    # Set a stale baseline.
    mock_state.set_last_backup_allocation(str(target.path), "vda", 99999)

    core._backup_target(vm, target, [snap])

    # Baseline cleared since no FULLs remain.
    assert mock_state.get_last_backup_allocation(str(target.path), "vda") is None, (
        "Baseline should be cleared when last phantom FULL is removed"
    )


def test_phantom_full_keeps_baseline_with_remaining(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """When a phantom FULL is removed but other valid FULLs remain, baseline is NOT cleared."""
    target = make_target()
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
        timestamp=datetime.now(),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Create a valid FULL file on disk.
    valid_full_path = tmp_path / "valid.FULL.monthly.qcow2"
    valid_full_path.write_text("")

    valid_full = FullBackupInfo(
        name="valid.FULL.monthly.qcow2",
        path=valid_full_path,
        timestamp=datetime.now(),
        disk="vda",
    )

    # Phantom FULL (file doesn't exist).
    phantom_path = Path("/nonexistent/phantom.FULL.monthly.qcow2")
    phantom_full = FullBackupInfo(
        name="phantom.FULL.monthly.qcow2",
        path=phantom_path,
        timestamp=datetime.now(),
        disk="vda",
    )

    mock_state._full_backups[str(target.path)] = [valid_full, phantom_full]
    mock_state.set_last_backup_allocation(str(target.path), "vda", 99999)

    core._backup_target(vm, target, [snap])

    # Phantom FULL removed but baseline is still set because valid FULL remains.
    all_fulls = mock_state.get_full_backups(str(target.path))
    assert len(all_fulls) == 1, "Phantom FULL should be removed"
    assert all_fulls[0].name == "valid.FULL.monthly.qcow2"
    assert mock_state.get_last_backup_allocation(str(target.path), "vda") == 99999, (
        "Baseline should NOT be cleared when valid FULLs remain"
    )


def test_pipeline_calls_startup_validation_before_steps(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """_execute_pipeline calls _validate_state_at_startup before _execute_snapshot_steps."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    call_order = []
    original_validate = core._validate_state_at_startup

    def _track_validate(vm_config):
        call_order.append("validate_state")
        original_validate(vm_config)

    def _track_snapshot(vm_config):
        call_order.append("snapshot_steps")
        return False

    with (
        patch.object(core, "_validate_state_at_startup", side_effect=_track_validate),
        patch.object(core, "_execute_snapshot_steps", side_effect=_track_snapshot),
        patch.object(core, "_execute_backup_steps", return_value=False),
    ):
        core._execute_pipeline(vm)

    validate_idx = call_order.index("validate_state") if "validate_state" in call_order else -1
    snapshot_idx = call_order.index("snapshot_steps") if "snapshot_steps" in call_order else -1
    assert validate_idx != -1, "validate_state_at_startup not called"
    assert snapshot_idx != -1, "execute_snapshot_steps not called"
    assert validate_idx < snapshot_idx, (
        f"validate_state_at_startup ({validate_idx}) must be called before "
        f"execute_snapshot_steps ({snapshot_idx})"
    )


def test_standalone_backup_calls_startup_validation(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """_execute_backup_steps calls _validate_state_at_startup."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with patch.object(
        core,
        "_validate_state_at_startup",
        wraps=core._validate_state_at_startup,
    ) as validate_spy:
        core._execute_backup_steps(vm)

    assert validate_spy.called, (
        "_validate_state_at_startup should be called by _execute_backup_steps"
    )


# ═══════════════════════════════════════════════════════════════════════════
#     Per-Chain Retention (G1)
# ═══════════════════════════════════════════════════════════════════════════


def test_per_chain_retention_keeps_entire_chain(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Single chain entirely kept by retention engine.

    Engine returns keep=[chain_id]. Verify all chain members in keep list.
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

    full_name = "testvm.FULL.monthly.qcow2"
    inc_name = "testvm.T0008.qcow2"
    now = datetime.now()

    mock_state.record_full_backup(str(target.path), full_name, now, "vda")
    mock_state.record_incremental_dependency(str(target.path), inc_name, full_name)

    # Mock _resolve_chain_full_anchor for inc
    mock_shell.expect_first(rf"qemu-img info.*--output=json.*{inc_name}").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "backing-filename": str(target.path / full_name),
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    backups = [
        SnapshotInfo(
            name=full_name,
            path=target.path / full_name,
            timestamp=now,
            allocation=10000,
            disk="vda",
        ),
        SnapshotInfo(
            name=inc_name, path=target.path / inc_name, timestamp=now, allocation=500, disk="vda"
        ),
    ]

    # Mock backup provider list
    with (
        patch.object(mock_factory._backup_provider, "list", return_value=backups),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=[full_name], remove=[]),
        ),
    ):
        _, result = core._evaluate_backup_retention(vm, target)

    assert result is not None
    assert full_name in result.keep
    assert inc_name in result.keep


def test_per_chain_retention_removes_entire_old_chain(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Old chain entirely removed. Engine returns remove=[chain_id].

    Verify all chain members in remove list.
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

    full_name = "testvm.FULL.monthly.qcow2"
    inc_name = "testvm.T0008.qcow2"
    now = datetime.now()

    mock_state.record_full_backup(str(target.path), full_name, now, "vda")
    mock_state.record_incremental_dependency(str(target.path), inc_name, full_name)

    # Mock _resolve_chain_full_anchor for inc
    mock_shell.expect_first(rf"qemu-img info.*--output=json.*{inc_name}").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "backing-filename": str(target.path / full_name),
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    backups = [
        SnapshotInfo(
            name=full_name,
            path=target.path / full_name,
            timestamp=now,
            allocation=10000,
            disk="vda",
        ),
        SnapshotInfo(
            name=inc_name, path=target.path / inc_name, timestamp=now, allocation=500, disk="vda"
        ),
    ]

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=backups),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=[], remove=[full_name, Path(full_name).stem]),
        ),
    ):
        _, result = core._evaluate_backup_retention(vm, target)

    assert result is not None
    assert full_name in result.remove
    assert inc_name in result.remove


def test_per_chain_no_middle_deletion(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Engine returns remove=[middle_chain_id], keep=[newer, older].

    Only middle chain's members are removed; others kept.
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

    middle_full = "testvm.FULL.daily.qcow2"
    middle_inc = "testvm.T0008.qcow2"
    newer_full = "testvm.FULL.weekly.qcow2"
    newer_inc = "testvm.T0141.qcow2"
    older_full = "testvm.FULL.yearly.qcow2"
    now = datetime.now()

    for fn in [middle_full, newer_full, older_full]:
        mock_state.record_full_backup(str(target.path), fn, now, "vda")
    mock_state.record_incremental_dependency(str(target.path), middle_inc, middle_full)
    mock_state.record_incremental_dependency(str(target.path), newer_inc, newer_full)

    # Mock _resolve_chain_full_anchor for incs
    for inc_name, fn in [(middle_inc, middle_full), (newer_inc, newer_full)]:
        mock_shell.expect_first(rf"qemu-img info.*--output=json.*{inc_name}").returns(
            ShellResult(
                success=True,
                stdout=json.dumps(
                    {
                        "format": "qcow2",
                        "backing-filename": str(target.path / fn),
                    }
                ),
                stderr="",
                returncode=0,
                error=None,
            )
        )

    backups = [
        SnapshotInfo(
            name=middle_full,
            path=target.path / middle_full,
            timestamp=now,
            allocation=10000,
            disk="vda",
        ),
        SnapshotInfo(
            name=middle_inc,
            path=target.path / middle_inc,
            timestamp=now,
            allocation=500,
            disk="vda",
        ),
        SnapshotInfo(
            name=newer_full,
            path=target.path / newer_full,
            timestamp=now,
            allocation=10000,
            disk="vda",
        ),
        SnapshotInfo(
            name=newer_inc, path=target.path / newer_inc, timestamp=now, allocation=500, disk="vda"
        ),
        SnapshotInfo(
            name=older_full,
            path=target.path / older_full,
            timestamp=now,
            allocation=10000,
            disk="vda",
        ),
    ]

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=backups),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(
                keep=[newer_full, older_full, Path(newer_full).stem],
                remove=[middle_full, Path(middle_full).stem],
            ),
        ),
    ):
        _, result = core._evaluate_backup_retention(vm, target)

    assert result is not None
    # Middle chain removed
    assert middle_full in result.remove
    assert middle_inc in result.remove
    # Newer and older chains kept
    assert newer_full in result.keep
    assert newer_inc in result.keep
    assert older_full in result.keep


def test_group_backups_by_chain_correct_full(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Incrementals grouped to correct FULL via _group_backups_by_chain.

    Mock qemu-img info to return backing-filename pointing to FULL.
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

    full_name = "testvm.FULL.monthly.qcow2"
    inc_name = "testvm.T0008.qcow2"
    now = datetime.now()
    full_path = target.path / full_name
    inc_path = target.path / inc_name

    # Mock _resolve_chain_full_anchor: inc → backing = FULL
    mock_shell.expect_first(rf"qemu-img info.*--output=json.*{inc_name}").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1048576,
                    "backing-filename": str(full_path),
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    backups = [
        SnapshotInfo(name=full_name, path=full_path, timestamp=now, allocation=10000, disk="vda"),
        SnapshotInfo(name=inc_name, path=inc_path, timestamp=now, allocation=500, disk="vda"),
    ]

    chains = core._group_backups_by_chain(backups)
    assert full_name in chains, "FULL should be grouped under its own name"
    # Incrementals are grouped under the stem of the FULL anchor
    anchor_stem = Path(full_name).stem
    assert anchor_stem in chains, "inc should be grouped under FULL anchor stem"
    assert inc_name in [b.name for b in chains[anchor_stem]], (
        "inc should be grouped with FULL anchor"
    )


def test_group_backups_by_chain_orphan_from_broken_chain(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Broken-chain incremental classified as orphan.

    Mock qemu-img info to fail → _resolve_chain_full_anchor returns None.
    Verify orphan classification under '__orphan__'.
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

    inc_name = "testvm.T0008.qcow2"
    now = datetime.now()
    inc_path = target.path / inc_name

    # qemu-img info --output=json fails → _resolve_chain_full_anchor returns None
    mock_shell.expect_first(rf"qemu-img info.*--output=json.*{inc_name}").returns(
        ShellResult(success=False, stdout="", stderr="error", returncode=1, error="failed")
    )

    backups = [
        SnapshotInfo(name=inc_name, path=inc_path, timestamp=now, allocation=500, disk="vda"),
    ]

    chains = core._group_backups_by_chain(backups)
    assert "__orphan__" in chains
    assert inc_name in [b.name for b in chains["__orphan__"]]


def test_per_chain_cleanup_entire_chain_deleted(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Entire chain deleted atomically via per-chain cleanup.

    Verify remove_full_backup() + remove_all_incremental_dependencies() called.
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
    now = datetime.now()
    mock_state.record_full_backup(str(target.path), full_name, now, "vda")

    backups = [
        SnapshotInfo(
            name=full_name,
            path=target.path / full_name,
            timestamp=now,
            allocation=10000,
            disk="vda",
        ),
    ]
    retention = RetentionResult(keep=[], remove=[full_name])

    with (
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch.object(
            mock_state,
            "remove_full_backup",
            wraps=mock_state.remove_full_backup,
        ) as remove_full_spy,
        patch.object(
            mock_state,
            "remove_all_incremental_dependencies",
            wraps=mock_state.remove_all_incremental_dependencies,
        ) as remove_all_spy,
    ):
        core._cleanup_backups(vm, target, backups, retention)

    assert remove_full_spy.called, "remove_full_backup should be called"
    assert remove_all_spy.called, "remove_all_incremental_dependencies should be called"


def test_per_chain_cleanup_no_ghost_retention(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """No ghost-retention: FULL in remove with inc in keep → FULL still deleted.

    In per-chain mode, the entire chain goes. When retention says remove
    the chain, all members (including the FULL) are deleted regardless of
    any keep-set incrementals.
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

    mock_state.record_full_backup(str(target.path), full_name, now, "vda")
    mock_state.record_incremental_dependency(str(target.path), inc_name, full_name)

    backups = [
        SnapshotInfo(
            name=full_name,
            path=target.path / full_name,
            timestamp=now,
            allocation=10000,
            disk="vda",
        ),
        SnapshotInfo(
            name=inc_name, path=target.path / inc_name, timestamp=now, allocation=500, disk="vda"
        ),
    ]
    # inc in keep, FULL in remove → per-chain: FULL still deleted (no ghost-retention)
    retention = RetentionResult(keep=[inc_name], remove=[full_name])

    backup_provider = mock_factory._backup_provider
    with (
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch.object(backup_provider, "delete", wraps=backup_provider.delete) as delete_spy,
    ):
        core._cleanup_backups(vm, target, backups, retention)

    deleted_names = [call.args[0].name for call in delete_spy.call_args_list]
    assert full_name in deleted_names, (
        "FULL should be deleted in per-chain mode (no ghost-retention)"
    )


def test_per_chain_cleanup_incremental_state_cleaned(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When incremental deleted, remove_incremental_dependency() called with correct anchor."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    full_name = "testvm.FULL.monthly.qcow2"
    inc_name = "testvm.T0008.qcow2"
    now = datetime.now()
    full_path = target.path / full_name
    inc_path = target.path / inc_name

    mock_state.record_full_backup(str(target.path), full_name, now, "vda")
    mock_state.record_incremental_dependency(str(target.path), inc_name, full_name)

    # _resolve_chain_full_anchor: inc → backing = FULL
    mock_shell.expect_first(rf"qemu-img info.*--output=json.*{inc_name}").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "backing-filename": str(full_path),
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    backups = [
        SnapshotInfo(name=full_name, path=full_path, timestamp=now, allocation=10000, disk="vda"),
        SnapshotInfo(name=inc_name, path=inc_path, timestamp=now, allocation=500, disk="vda"),
    ]
    retention = RetentionResult(keep=[], remove=[full_name, inc_name])

    with (
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch.object(
            mock_state,
            "remove_incremental_dependency",
            wraps=mock_state.remove_incremental_dependency,
        ) as remove_spy,
    ):
        core._cleanup_backups(vm, target, backups, retention)

    assert remove_spy.called, "remove_incremental_dependency should be called"
    assert remove_spy.call_args[0] == (str(target.path), inc_name, Path(full_name).stem), (
        f"remove_incremental_dependency called with wrong args: {remove_spy.call_args[0]}"
    )


def test_per_chain_post_cleanup_verification_pass(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """After cleanup, all keep-set chains verified intact.

    Mock qemu-img info --backing-chain succeeds for all keep-set non-FULLs.
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

    full_name = "testvm.FULL.monthly.qcow2"
    inc_name = "testvm.T0008.qcow2"
    now = datetime.now()
    full_path = target.path / full_name
    inc_path = target.path / inc_name

    mock_state.record_full_backup(str(target.path), full_name, now, "vda")
    mock_state.record_incremental_dependency(str(target.path), inc_name, full_name)

    backups = [
        SnapshotInfo(name=full_name, path=full_path, timestamp=now, allocation=10000, disk="vda"),
        SnapshotInfo(name=inc_name, path=inc_path, timestamp=now, allocation=500, disk="vda"),
    ]
    # FULL removed, inc kept → inc will be post-cleanup verified
    retention = RetentionResult(keep=[full_name, inc_name], remove=[])

    # Post-cleanup verification: qemu-img info --backing-chain on inc succeeds
    mock_shell.expect_first(r"qemu-img.*--backing-chain").returns(
        ShellResult(success=True, stdout="[]", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.CRITICAL)
    with (
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch("qsnap.core.os.path.exists", return_value=True),
    ):
        core._cleanup_backups(vm, target, backups, retention)

    # No CRITICAL log — chain is intact
    critical_logs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert not critical_logs, f"Expected no CRITICAL log, got: {critical_logs}"


def test_per_chain_post_cleanup_verification_fail(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """After cleanup, a keep-set chain is broken → CRITICAL log.

    Mock qemu-img info --backing-chain fails for a kept incremental.
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

    full_name = "testvm.FULL.monthly.qcow2"
    inc_name = "testvm.T0008.qcow2"
    now = datetime.now()
    full_path = target.path / full_name
    inc_path = target.path / inc_name

    mock_state.record_full_backup(str(target.path), full_name, now, "vda")
    mock_state.record_incremental_dependency(str(target.path), inc_name, full_name)

    backups = [
        SnapshotInfo(name=full_name, path=full_path, timestamp=now, allocation=10000, disk="vda"),
        SnapshotInfo(name=inc_name, path=inc_path, timestamp=now, allocation=500, disk="vda"),
    ]
    retention = RetentionResult(keep=[full_name, inc_name], remove=[full_name])

    # Post-cleanup verification fails on inc
    mock_shell.expect_first(r"qemu-img.*--backing-chain").returns(
        ShellResult(success=False, stdout="", stderr="broken", returncode=1, error="broken")
    )

    caplog.set_level(logging.CRITICAL)
    with (
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch("qsnap.core.os.path.exists", return_value=True),
    ):
        core._cleanup_backups(vm, target, backups, retention)

    critical_logs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical_logs, "Expected CRITICAL log for broken post-cleanup chain"
    assert any("post-cleanup verification FAILED" in r.message for r in critical_logs), (
        "CRITICAL log should mention post-cleanup verification"
    )


# ═══════════════════════════════════════════════════════════════════════════
#     Oldest-Prefix Snapshot Retention
# ═══════════════════════════════════════════════════════════════════════════


def test_snapshot_oldest_prefix_contiguous_removed(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Contiguous oldest prefix removed.

    Snapshots sorted by timestamp, contiguous remove items from oldest kept in remove.
    """
    vm = make_vm_config(name="testvm", snapshot_chain_length=0)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    timestamps = [
        datetime(2025, 7, 13, 8, 0),
        datetime(2025, 7, 13, 9, 0),
        datetime(2025, 7, 13, 10, 0),
        datetime(2025, 7, 13, 11, 0),
    ]
    for i, ts in enumerate(timestamps):
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=f"snap{i + 1}",
                path=Path(f"/tmp/snap{i + 1}.qcow2"),
                timestamp=ts,
                allocation=1000,
                disk="vda",
            ),
        )

    # Engine returns: remove oldest 2, keep newest 2
    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        return_value=RetentionResult(
            keep=["snap3", "snap4"],
            remove=["snap1", "snap2"],
        ),
    ):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # snap1 and snap2 form contiguous oldest prefix → kept in remove
    assert "snap1" in result.remove
    assert "snap2" in result.remove
    # snap3 and snap4 are keep
    assert "snap3" in result.keep
    assert "snap4" in result.keep


def test_snapshot_oldest_prefix_middle_moved_to_keep(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Middle snapshots in remove list moved to keep (gap fillers).

    Engine removes snap2 and snap1 but keeps snap3 and snap4.
    snap1 (oldest remove) stays in remove; snap2 (non-prefix after keep) moves to keep.
    """
    vm = make_vm_config(name="testvm", snapshot_chain_length=0)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    timestamps = [
        datetime(2025, 7, 13, 8, 0),
        datetime(2025, 7, 13, 9, 0),
        datetime(2025, 7, 13, 10, 0),
        datetime(2025, 7, 13, 11, 0),
    ]
    for i, ts in enumerate(timestamps):
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=f"snap{i + 1}",
                path=Path(f"/tmp/snap{i + 1}.qcow2"),
                timestamp=ts,
                allocation=1000,
                disk="vda",
            ),
        )

    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        return_value=RetentionResult(
            keep=["snap2", "snap4"],
            remove=["snap1", "snap3"],
        ),
    ):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # snap1 is the oldest prefix → kept in remove
    assert "snap1" in result.remove
    # snap3 is non-prefix (after snap2 keep) → moved to keep
    assert "snap3" in result.keep
    assert "snap3" not in result.remove


def test_snapshot_oldest_prefix_mixed(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Mixed prefix and gap fillers.

    Engine removes snap1, snap3, snap4. Keep snap2, snap5.
    snap1 (contiguous from oldest) stays in remove.
    snap3, snap4 (non-contiguous after keep snap2) moved to keep.
    """
    vm = make_vm_config(name="testvm", snapshot_chain_length=0)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    timestamps = [
        datetime(2025, 7, 13, 8, 0),
        datetime(2025, 7, 13, 9, 0),
        datetime(2025, 7, 13, 10, 0),
        datetime(2025, 7, 13, 11, 0),
        datetime(2025, 7, 13, 12, 0),
    ]
    for i, ts in enumerate(timestamps):
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=f"snap{i + 1}",
                path=Path(f"/tmp/snap{i + 1}.qcow2"),
                timestamp=ts,
                allocation=1000,
                disk="vda",
            ),
        )

    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        return_value=RetentionResult(
            keep=["snap2", "snap5"],
            remove=["snap1", "snap3", "snap4"],
        ),
    ):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # snap1 is contiguous oldest prefix → remove
    assert "snap1" in result.remove
    # snap3 and snap4 are non-prefix → moved to keep
    assert "snap3" in result.keep
    assert "snap4" in result.keep
    assert "snap3" not in result.remove
    assert "snap4" not in result.remove


def test_blockcommit_receives_oldest_prefix(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Blockcommit processes only contiguous prefix.

    Verify to_merge list only contains oldest prefix items.
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

    # Record 4 snapshots
    base_path = "/var/lib/libvirt/snapshots/testvm"
    timestamps = [
        datetime(2025, 7, 13, 8, 0),
        datetime(2025, 7, 13, 9, 0),
        datetime(2025, 7, 13, 10, 0),
        datetime(2025, 7, 13, 11, 0),
    ]
    for i, ts in enumerate(timestamps):
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=f"snap{i + 1}",
                path=Path(f"{base_path}/snap{i + 1}.qcow2"),
                timestamp=ts,
                allocation=1000,
                disk="vda",
            ),
        )

    # Oldest-prefix: snap1 removed, snap2-snap4 kept
    retention = RetentionResult(
        keep=["snap2", "snap3", "snap4"],
        remove=["snap1"],
    )
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    assert bc_spy.called, "blockcommit should be called"
    merge_names = [s.name for s in bc_spy.call_args[0][1]]
    assert "snap1" in merge_names, "contiguous oldest prefix should be committed"
    assert "snap2" not in merge_names, "non-remove items should NOT be committed"


# ═══════════════════════════════════════════════════════════════════════════
#     Multi-Disk Retention Grouping
# ═══════════════════════════════════════════════════════════════════════════


def test_multidisk_retention_groups_by_disk(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Retention evaluates each disk's chain independently.

    vda has 3 snapshots, vdb has 4.  ``_evaluate_disk_retention`` must be
    called exactly twice — once per disk — with only that disk's
    snapshots.  The final keep/remove is the union of both disks.
    """
    vda_base = Path("/var/lib/libvirt/images/testvm_vda.qcow2")
    vdb_base = Path("/var/lib/libvirt/images/testvm_vdb.qcow2")
    disks = [
        DiskConfig(target="vda", base_image=vda_base),
        DiskConfig(target="vdb", base_image=vdb_base),
    ]
    vm = make_vm_config(name="testvm", disks=disks)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    # vda: 3 snapshots
    for i in range(3):
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=f"vda_snap{i + 1}",
                path=Path(f"/tmp/vda_snap{i + 1}.qcow2"),
                timestamp=base + timedelta(hours=i),
                allocation=1000,
                disk="vda",
            ),
        )
    # vdb: 4 snapshots
    for i in range(4):
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=f"vdb_snap{i + 1}",
                path=Path(f"/tmp/vdb_snap{i + 1}.qcow2"),
                timestamp=base + timedelta(hours=i + 10),
                allocation=1000,
                disk="vdb",
            ),
        )

    with patch.object(
        core, "_evaluate_disk_retention", wraps=core._evaluate_disk_retention
    ) as disk_retention_spy:
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None

    # Called exactly twice (once per disk)
    assert disk_retention_spy.call_count == 2, (
        f"Expected 2 _evaluate_disk_retention calls, got {disk_retention_spy.call_count}"
    )

    # Each call received only one disk's snapshots
    vda_call = None
    vdb_call = None
    for call in disk_retention_spy.call_args_list:
        snaps = call[0][0]
        names = {s.name for s in snaps}
        if "vda_snap1" in names:
            vda_call = names
        else:
            vdb_call = names

    assert vda_call == {"vda_snap1", "vda_snap2", "vda_snap3"}
    assert vdb_call == {"vdb_snap1", "vdb_snap2", "vdb_snap3", "vdb_snap4"}

    # Final result is the union of both disks (default MockRetentionEngine keeps all)
    assert len(result.keep) == 7
    assert len(result.remove) == 0


def test_multidisk_retention_per_disk_independence(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Removal on vda does not affect vdb's keep list.

    Engine removes the oldest of each disk independently; verify
    per-disk isolation.
    """
    vda_base = Path("/var/lib/libvirt/images/testvm_vda.qcow2")
    vdb_base = Path("/var/lib/libvirt/images/testvm_vdb.qcow2")
    disks = [
        DiskConfig(target="vda", base_image=vda_base),
        DiskConfig(target="vdb", base_image=vdb_base),
    ]
    vm = make_vm_config(name="testvm", disks=disks, snapshot_chain_length=0)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    # vda: 3 snapshots
    for i in range(3):
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=f"vda_snap{i + 1}",
                path=Path(f"/tmp/vda_snap{i + 1}.qcow2"),
                timestamp=base + timedelta(hours=i),
                allocation=1000,
                disk="vda",
            ),
        )
    # vdb: 4 snapshots
    for i in range(4):
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=f"vdb_snap{i + 1}",
                path=Path(f"/tmp/vdb_snap{i + 1}.qcow2"),
                timestamp=base + timedelta(hours=i + 10),
                allocation=1000,
                disk="vdb",
            ),
        )

    # Engine removes the oldest of each call's items, keeps the rest
    def _custom_evaluate(items, policy, now):
        names = [item.name for item in items]
        return RetentionResult(keep=names[1:], remove=names[:1])

    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        side_effect=_custom_evaluate,
    ):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # vda: oldest removed → vda_snap1 removed, vda_snap2/3 kept
    assert "vda_snap1" in result.remove
    assert "vda_snap2" in result.keep
    assert "vda_snap3" in result.keep

    # vdb: oldest removed → vdb_snap1 removed, vdb_snap2/3/4 kept
    assert "vdb_snap1" in result.remove
    assert "vdb_snap2" in result.keep
    assert "vdb_snap3" in result.keep
    assert "vdb_snap4" in result.keep

    # Per-disk independence: removal count is 1 per disk, not mixed
    assert len(result.keep) == 5  # 2 from vda + 3 from vdb
    assert len(result.remove) == 2  # 1 from each disk


# ═══════════════════════════════════════════════════════════════════════════
#     Multi-Disk Blockcommit Dispatch
# ═══════════════════════════════════════════════════════════════════════════


def test_multidisk_blockcommit_dispatches_per_disk(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Blockcommit dispatches per-disk with correct base_image.

    Remove-set contains snapshots of both disks → two blockcommit calls,
    each with its own disk's base_image and only that disk's snapshots.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vda_base = Path("/var/lib/libvirt/images/testvm_vda.qcow2")
    vdb_base = Path("/var/lib/libvirt/images/testvm_vdb.qcow2")
    disks = [
        DiskConfig(target="vda", base_image=vda_base),
        DiskConfig(target="vdb", base_image=vdb_base),
    ]
    vm = make_vm_config(
        name="testvm",
        disks=disks,
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Record snapshots for both disks
    base = datetime(2025, 7, 13, 10, 0)
    for i in range(2):
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=f"vda_snap{i + 1}",
                path=Path(f"/tmp/vda_snap{i + 1}.qcow2"),
                timestamp=base + timedelta(hours=i),
                allocation=1000,
                disk="vda",
            ),
        )
    for i in range(2):
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=f"vdb_snap{i + 1}",
                path=Path(f"/tmp/vdb_snap{i + 1}.qcow2"),
                timestamp=base + timedelta(hours=i + 10),
                allocation=1000,
                disk="vdb",
            ),
        )

    # Remove-set contains snapshots from BOTH disks
    retention = RetentionResult(
        keep=[],
        remove=["vda_snap1", "vda_snap2", "vdb_snap1", "vdb_snap2"],
    )
    manager = mock_factory._lifecycle_manager

    # Override domblklist to show tips NOT in the candidate set
    # (all snapshots committable for both disks)
    mock_shell.expect_first("domblklist").returns(
        ShellResult(
            success=True,
            stdout=(
                "Target   Source\n"
                "--------------------------------\n"
                "vda   /tmp/vda_tip.qcow2\n"
                "vdb   /tmp/vdb_tip.qcow2\n"
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        patch("os.path.exists", return_value=True),
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    assert bc_spy.call_count == 2, (
        f"Expected 2 blockcommit calls (one per disk), got {bc_spy.call_count}"
    )

    # Collect per-disk call details
    calls_by_disk: dict[str, dict] = {}
    for call in bc_spy.call_args_list:
        disk = call.kwargs["disk"]
        calls_by_disk[disk] = {
            "snapshots": call[0][1],  # positional arg: snapshots_to_merge
            "base_image": call.kwargs["base_image"],
        }

    assert "vda" in calls_by_disk
    assert "vdb" in calls_by_disk

    # vda call: only vda snapshots, correct base_image
    vda_snap_names = {s.name for s in calls_by_disk["vda"]["snapshots"]}
    assert vda_snap_names == {"vda_snap1", "vda_snap2"}
    assert calls_by_disk["vda"]["base_image"] == vda_base

    # vdb call: only vdb snapshots, correct base_image
    vdb_snap_names = {s.name for s in calls_by_disk["vdb"]["snapshots"]}
    assert vdb_snap_names == {"vdb_snap1", "vdb_snap2"}
    assert calls_by_disk["vdb"]["base_image"] == vdb_base


# _execute_with_retry tests
# ═══════════════════════════════════════════════════════════════════════════


# ── test_execute_with_retry_max_retries_zero ─────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_execute_with_retry_max_retries_zero(
    mock_shell,
    mock_state,
    mock_factory,
    make_vm_config,
    make_target,
):
    """When ``backup_retry_max=0``, the operation is called exactly once — no retry.

    ``_execute_with_retry`` checks ``max_retries <= 0`` and returns
    ``operation()`` immediately.
    """
    from types import SimpleNamespace

    target = make_target(backup_retry_max=0, backup_retry_base="2s")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    call_count = 0

    def operation():
        nonlocal call_count
        call_count += 1
        return SimpleNamespace(success=True, error=None)

    result = core._execute_with_retry(operation, target)

    assert call_count == 1, (
        f"Operation should be called exactly once with max_retries=0, got {call_count}"
    )
    assert result.success is True


# ── test_execute_with_retry_transient_error_retried ──────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_execute_with_retry_transient_error_retried(
    mock_shell,
    mock_state,
    mock_factory,
    make_vm_config,
    make_target,
):
    """First call fails with retryable error, second succeeds → returns success.

    ``is_retryable("Connection refused")`` returns ``True``, so the
    operation is retried with exponential backoff.  ``time.sleep`` is
    patched to avoid actual delays.
    """
    from types import SimpleNamespace

    target = make_target(backup_retry_max=3, backup_retry_base="2s")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    call_count = 0

    def operation():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return SimpleNamespace(success=False, error="Connection refused")
        return SimpleNamespace(success=True, error=None)

    with patch("time.sleep"):
        result = core._execute_with_retry(operation, target)

    assert call_count == 2, (
        f"Operation should be called twice (first failed, second succeeded), got {call_count}"
    )
    assert result.success is True


# ── test_execute_with_retry_non_retryable_immediate ──────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_execute_with_retry_non_retryable_immediate(
    mock_shell,
    mock_state,
    mock_factory,
    make_vm_config,
    make_target,
):
    """First call fails with non-retryable error → returns immediately, no retry.

    ``is_retryable("No space left on device")`` returns ``False``, so
    the result is returned immediately without further attempts.
    """
    from types import SimpleNamespace

    target = make_target(backup_retry_max=3, backup_retry_base="2s")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    call_count = 0

    def operation():
        nonlocal call_count
        call_count += 1
        return SimpleNamespace(success=False, error="No space left on device")

    with patch("time.sleep"):
        result = core._execute_with_retry(operation, target)

    assert call_count == 1, (
        f"Operation should be called exactly once (non-retryable), got {call_count}"
    )
    assert result.success is False
    assert result.error == "No space left on device"


# ── test_execute_with_retry_all_attempts_exhausted ───────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_execute_with_retry_all_attempts_exhausted(
    mock_shell,
    mock_state,
    mock_factory,
    make_vm_config,
    make_target,
):
    """All attempts fail with retryable errors → returns last failure.

    ``backup_retry_max=3``, all 3 attempts fail with ``"Connection refused"``.
    The last result is returned with ``success=False``.
    """
    from types import SimpleNamespace

    target = make_target(backup_retry_max=3, backup_retry_base="2s")
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    call_count = 0

    def operation():
        nonlocal call_count
        call_count += 1
        return SimpleNamespace(success=False, error=f"Connection refused (attempt {call_count})")

    with patch("time.sleep"):
        result = core._execute_with_retry(operation, target)

    assert call_count == 3, (
        f"Operation should be called exactly {target.backup_retry_max} times, got {call_count}"
    )
    assert result.success is False
    assert "attempt 3" in result.error


# ── test_full_creation_retried_via_execute_with_retry ────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_full_creation_retried_via_execute_with_retry(
    mock_shell,
    mock_state,
    mock_factory,
    make_vm_config,
    make_target,
):
    """FULL creation fails transiently on first attempt, retried, succeeds on second.

    ``create_full_backup()`` is mocked to fail with ``"Connection refused"``
    on the first call and succeed on the second.  ``_execute_with_retry``
    detects the retryable error and retries via exponential backoff.
    Verification is mocked to pass.
    """
    target = make_target(backup_retry_max=2, backup_retry_base="0s")
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    full_calls = 0

    def full_side_effect(*args, **kwargs):
        nonlocal full_calls
        full_calls += 1
        if full_calls == 1:
            return BackupResult(
                success=False,
                snapshot_name="snap1",
                source_path=Path("/tmp/snap1.qcow2"),
                target_path=target.path / "testvm.FULL.qcow2",
                bytes_transferred=0,
                error="Connection refused",
            )
        return BackupResult(
            success=True,
            snapshot_name="snap1",
            source_path=Path("/tmp/snap1.qcow2"),
            target_path=target.path / "testvm.FULL.qcow2",
            bytes_transferred=1048576,
            error=None,
        )

    with (
        patch.object(
            mock_factory._backup_provider,
            "create_full_backup",
            side_effect=full_side_effect,
        ),
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch("time.sleep"),
    ):
        core._backup_target(vm, target, [snap])

    assert full_calls == 2, (
        f"create_full_backup should be called twice (first failed, retried), got {full_calls}"
    )


# ── test_full_creation_not_retried_non_transient ─────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_full_creation_not_retried_non_transient(
    mock_shell,
    mock_state,
    mock_factory,
    make_vm_config,
    make_target,
):
    """FULL creation fails with a non-space, non-retryable error → NOT retried.

    ``is_retryable("EACCES: permission denied")`` returns ``False``, so
    ``_execute_with_retry`` returns the failure immediately.
    ``create_full_backup()`` is called exactly once, and the definitive
    failure raises ``BackupAbortError`` (VM-level isolation).

    Note: space errors no longer raise — they suspend the target
    (see tests/core/test_enospc_isolation.py).
    """

    target = make_target(backup_retry_max=3, backup_retry_base="0s")
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    full_calls = 0

    def full_side_effect(*args, **kwargs):
        nonlocal full_calls
        full_calls += 1
        return BackupResult(
            success=False,
            snapshot_name="snap1",
            source_path=Path("/tmp/snap1.qcow2"),
            target_path=target.path / "testvm.FULL.qcow2",
            bytes_transferred=0,
            error="EACCES: permission denied",
        )

    with (
        patch.object(
            mock_factory._backup_provider,
            "create_full_backup",
            side_effect=full_side_effect,
        ),
        patch("time.sleep"),
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    assert full_calls == 1, (
        f"create_full_backup should be called exactly once (non-retryable), got {full_calls}"
    )


# ── test_incremental_transfer_uses_execute_with_retry ────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_incremental_transfer_uses_execute_with_retry(
    mock_shell,
    mock_state,
    mock_factory,
    make_vm_config,
    make_target,
):
    """``transfer_missing`` fails transiently on first call, retried, succeeds on second.

    ``_transfer_with_retry()`` delegates its retry loop to
    ``_execute_with_retry()``.  The mock ``transfer_missing`` fails with
    ``"Connection refused"`` on the first attempt and succeeds on the
    second.  ``time.sleep`` is patched to avoid actual delays.
    """
    target = make_target(backup_retry_max=2, backup_retry_base="0s")
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
        disk="vda",
    )

    transfer_calls = 0

    def transfer_side_effect(*args, **kwargs):
        nonlocal transfer_calls
        transfer_calls += 1
        if transfer_calls == 1:
            return [
                BackupResult(
                    success=False,
                    snapshot_name="snap1",
                    source_path=Path("/tmp/snap1.qcow2"),
                    target_path=target.path / "snap1.qcow2",
                    bytes_transferred=0,
                    error="Connection refused",
                )
            ]
        return [
            BackupResult(
                success=True,
                snapshot_name="snap1",
                source_path=Path("/tmp/snap1.qcow2"),
                target_path=target.path / "snap1.qcow2",
                bytes_transferred=1048576,
                error=None,
            )
        ]

    with (
        patch.object(
            mock_factory._backup_provider,
            "transfer_missing",
            side_effect=transfer_side_effect,
        ),
        patch("time.sleep"),
    ):
        results = core._transfer_with_retry(
            mock_factory._backup_provider,
            vm,
            target,
            [snap],
        )

    assert transfer_calls == 2, (
        f"transfer_missing should be called twice (first failed, retried), got {transfer_calls}"
    )
    assert results[0].success is True


def test_chain_verify_intact_chain_proceeds(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Intact chain → pre-commit verification via scan_backing_chain passes → blockcommit called.

    Alias test that verifies the full flow from chain-integrity-verification spec:
    _verify_backing_chain() → scan_backing_chain() → intact → blockcommit proceeds.
    This complements test_chain_verify_intact_chain_blockcommit_proceeds.
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

    intact_json = _load_fixture("backing_chain_intact.json")
    # Override conftest default with intact chain fixture for this test
    mock_shell.expect_first("qemu-img info --force-share --backing-chain").returns(
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


def test_chain_verify_missing_file_aborts_vm_pipeline(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Missing file in chain → pre-commit verification fails → RuntimeError abort.

    VM-level isolation: a broken backing chain aborts the VM pipeline
    immediately.  No partial blockcommit or auto-rebase is attempted;
    the operator must repair the chain (``qsnap check --deep``).
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
    mock_shell.expect_first("qemu-img info --force-share --backing-chain").returns(
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
    manager = mock_factory._lifecycle_manager

    caplog.set_level(logging.CRITICAL)
    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
        pytest.raises(RuntimeError, match="chain verification failed"),
    ):
        core._blockcommit_snapshots(vm, retention)

    # No blockcommit is attempted on a broken chain.
    assert not bc_spy.called, "blockcommit must not run on a broken chain"
    # CRITICAL log carries the remediation hint.
    critical_logs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical_logs, "Expected CRITICAL log for broken chain"
    assert "qsnap check --deep" in critical_logs[0].message


# ── test_batch_failure_records_nothing_aborts_vm ──────────────────────────


def test_batch_failure_records_nothing_aborts_vm(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """A batch failure records NOTHING and aborts the VM (all-or-nothing).

    Replaces the obsolete partial-recording test: under ``create_multi``
    batch semantics (design D10) a failed disk rejects the whole batch —
    the successful vda disk is NOT recorded either.
    """
    vm = make_vm_config(
        name="testvm",
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm-disk2.qcow2")),
        ],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider

    def create_multi_side_effect(vm_config, specs, quiesce=False):
        return [
            SnapshotResult(
                success=True,
                name=specs[0].name,
                path=specs[0].path,
                new_allocation=65536,
                error=None,
                disk=specs[0].disk,
            ),
            SnapshotResult(
                success=False,
                name=specs[1].name,
                path=specs[1].path,
                new_allocation=0,
                error="snapshot-create-as failed for vdb",
                disk=specs[1].disk,
            ),
        ]

    caplog.set_level(logging.ERROR)
    with patch.object(snapshot_provider, "create_multi", side_effect=create_multi_side_effect):
        result = core.snapshot()

    # VM-level isolation: the vdb failure aborts this VM's pipeline.
    assert result.success is False

    # All-or-nothing: NOTHING is recorded — not even the successful vda.
    snapshots = mock_state.get_snapshots("testvm")
    assert len(snapshots) == 0

    # vdb failure logged.
    assert "vdb" in caplog.text
    assert "snapshot-create-as failed" in caplog.text


# ── GAP-5: Pipeline skips blockcommit when snapshot failed ─────────────────


def test_pipeline_skips_blockcommit_when_snapshot_creation_fails(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """When a snapshot creation fails, blockcommit is NOT called for that disk.

    The failed disk has no snapshot recorded in state → no snapshot to
    commit → blockcommit does not run for the failed disk.
    """
    vm = make_vm_config(
        name="testvm",
        snapshot_chain_length=1,  # triggers commit after 1 snapshot
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with a snapshot so retention triggers commit.
    old_snap = SnapshotInfo(
        name="testvm.20250101T000000_vda_abc123",
        path=Path("/tmp/testvm.20250101T000000_vda_abc123.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", old_snap)

    snapshot_provider = mock_factory._snapshot_provider

    # New snapshot creation FAILS for vda (whole batch rejected).
    def create_multi_side_effect(vm_config, specs, quiesce=False):
        return [
            SnapshotResult(
                success=False,
                name=specs[0].name,
                path=specs[0].path,
                new_allocation=0,
                error="snapshot-create-as failed",
                disk=specs[0].disk,
            )
        ]

    lifecycle = mock_factory._lifecycle_manager
    with (
        patch.object(snapshot_provider, "create_multi", side_effect=create_multi_side_effect),
        patch.object(lifecycle, "blockcommit", wraps=lifecycle.blockcommit) as bc_spy,
        patch("os.path.exists", return_value=False),  # no collision files
    ):
        caplog.set_level(logging.ERROR)
        core.run("testvm")

    # Blockcommit must NOT be called — no new snapshot was created.
    assert not bc_spy.called, "blockcommit should be skipped when snapshot creation fails"


# ── GAP-6: Pipeline skips retention when backup transfer fails ────────────


def test_pipeline_skips_retention_when_backup_transfer_fails(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """BackupResult(success=False) → BackupAbortError, retention/cleanup never reached.

    VM-level isolation: when FULL backup creation fails definitively,
    Core raises BackupAbortError.  The abort itself preserves old
    generations — retention evaluation and cleanup are never reached.
    """
    target = make_target(target_keep_generations=1)
    vm = make_vm_config(
        name="testvm",
        targets=[target],
        snapshot_chain_length=99,
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Record a current snapshot in state.
    snap = SnapshotInfo(
        name="testvm.20250102T000000_vda_abc123",
        path=Path("/tmp/testvm.20250102T000000_vda_abc123.qcow2"),
        timestamp=datetime(2025, 1, 2, 0, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # NO prior FULL in state → _should_create_full returns True →
    # create_full_backup IS called.  Mock it to fail.
    backup_provider = mock_factory._backup_provider

    fail_result = BackupResult(
        success=False,
        snapshot_name="snap1",
        source_path=snap.path,
        target_path=target.path / "bogus.qcow2",
        bytes_transferred=0,
        error="Connection refused — NBD transfer failed",
    )

    with (
        patch("qsnap.core.os.path.exists", return_value=True),
        patch.object(backup_provider, "create_full_backup", return_value=fail_result),
        patch.object(core, "_cleanup_backups") as cleanup_spy,
    ):
        caplog.set_level(logging.CRITICAL)
        with pytest.raises(BackupAbortError):
            core._backup_target(vm, target, [snap])

    # _cleanup_backups must NOT be called — the abort precedes retention.
    assert not cleanup_spy.called, "_cleanup_backups should be skipped when backup transfer fails"
