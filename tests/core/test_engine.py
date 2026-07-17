"""Tests for Core orchestrator initialization and run() dispatch.

Covers Core dependency injection, full-pipeline execution for all VMs,
and VM filtering via ``vm_filter``.

RISK (test-plan.md line 131): Core must depend on ``IConfigFacade`` (the
ABC), never on ``ConfigFacade`` directly.  The
``test_core_init_stores_dependencies`` test asserts that the stored config
object is an instance of ``IConfigFacade``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qsnap.cli.commands import _format_pipeline_result
from qsnap.cli.errors import EXIT_BACKUP_ABORT, EXIT_SUCCESS
from qsnap.core import Core, PipelineResult, VMRunResult
from qsnap.interfaces.config import IConfigFacade
from qsnap.interfaces.factory import IVMModuleFactory
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import GlobalConfig, RetentionPolicy
from qsnap.models.results import BackupResult, FullBackupInfo, RestoreResult, ShellResult, SnapshotInfo
from tests.mocks import MockConfigFacade

# ── test_core_init_stores_dependencies ───────────────────────────────────


def test_core_init_stores_dependencies(
    mock_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core stores all four DI dependencies and config is an IConfigFacade.

    RISK (test-plan.md line 131): Core must hold an ``IConfigFacade``, not a
    ``ConfigFacade`` directly.  This ensures Core is decoupled from the
    concrete config implementation and uses only the ABC methods
    (``get_global``, ``get_vms``, ``get_vm``).
    """
    core = Core(
        config=mock_config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # All four dependencies are stored as private attributes.
    assert core._config is mock_config
    assert core._factory is mock_factory
    assert core._state is mock_state
    assert core._shell is mock_shell

    # RISK TEST: config must be used ONLY via IConfigFacade methods.
    # Assert that the stored config is an IConfigFacade instance, not a
    # concrete ConfigFacade.  This guards against tight coupling between
    # Core and ConfigFacade's specific dataclass shapes.
    assert isinstance(core._config, IConfigFacade)

    # Verify the other dependencies are also their ABC types.
    assert isinstance(core._factory, IVMModuleFactory)
    assert isinstance(core._state, IStateManager)
    assert isinstance(core._shell, IShell)

    # dry_run defaults to False.
    assert core.dry_run is False

    # Preserve flags default to False.
    assert core.preserve_snapshots is False
    assert core.preserve_backups is False


# ── test_core_run_all_vms ─────────────────────────────────────────────────


def test_core_run_all_vms(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``core.run()`` with no filter executes the pipeline for every VM.

    Given a config with 2 VMs, ``run()`` should return a ``PipelineResult``
    with 2 ``VMRunResult`` entries, all successful, and the factory's
    ``create_snapshot_provider`` should have been called once per VM.
    """
    vm1 = make_vm_config(name="vm1", targets=[make_target()])
    vm2 = make_vm_config(name="vm2", targets=[make_target()])
    config = MockConfigFacade(vms=[vm1, vm2])

    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Spy on the factory's create_snapshot_provider to verify it is called
    # once per VM (always mode creates a snapshot for every VM).
    with patch.object(
        mock_factory,
        "create_snapshot_provider",
        wraps=mock_factory.create_snapshot_provider,
    ) as spy:
        result = core.run()

    # Return value is a PipelineResult with 2 per-VM results.
    assert isinstance(result, PipelineResult)
    assert len(result.results) == 2

    # Both VMs succeeded.
    assert result.success is True
    for r in result.results:
        assert isinstance(r, VMRunResult)
        assert r.success is True
        assert r.error is None

    # VM names match the configured VMs.
    vm_names = {r.vm_name for r in result.results}
    assert vm_names == {"vm1", "vm2"}

    # The factory's create_snapshot_provider was called once per VM.
    assert spy.call_count == 2


# ── test_core_run_with_filter ─────────────────────────────────────────────


def test_core_run_with_filter(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``core.run(vm_filter="vm1")`` processes only the VM whose name matches.

    The filter uses exact name match.  With 2 VMs ("vm1" and "vm2"), only
    "vm1" should appear in the results.
    """
    vm1 = make_vm_config(name="vm1", targets=[make_target()])
    vm2 = make_vm_config(name="vm2", targets=[make_target()])
    config = MockConfigFacade(vms=[vm1, vm2])

    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.run(vm_filter="vm1")

    assert isinstance(result, PipelineResult)
    assert len(result.results) == 1
    assert result.results[0].vm_name == "vm1"
    assert result.results[0].success is True


# ── test_generate_snapshot_name_appends_collision_suffix ──────────────────


def test_generate_snapshot_name_appends_collision_suffix(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
):
    """When the timestamp-based name collides, ``_N`` suffix is appended."""
    vm = make_vm_config(name="testvm", snapshot_dir=str(tmp_path))
    config = MockConfigFacade(
        global_config=GlobalConfig(timestamp_format="long"),
        vms=[vm],
    )
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    (tmp_path / "testvm.20250713T1531_vda.qcow2").touch()

    with frozen_clock(datetime(2025, 7, 13, 15, 31)):
        name = core._generate_snapshot_name(vm, disk="vda")

    assert name == "testvm.20250713T1531_vda_1"


# ── test_generate_snapshot_name_collision_increments_suffix ───────────────


def test_generate_snapshot_name_collision_increments_suffix(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
):
    """When both the base name and ``_1`` exist, ``_2`` is used."""
    vm = make_vm_config(name="testvm", snapshot_dir=str(tmp_path))
    config = MockConfigFacade(
        global_config=GlobalConfig(timestamp_format="long"),
        vms=[vm],
    )
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    (tmp_path / "testvm.20250713T1531_vda.qcow2").touch()
    (tmp_path / "testvm.20250713T1531_vda_1.qcow2").touch()

    with frozen_clock(datetime(2025, 7, 13, 15, 31)):
        name = core._generate_snapshot_name(vm, disk="vda")

    assert name == "testvm.20250713T1531_vda_2"


# ── test_core_uses_config_timestamp_format_for_snapshot_name ──────────────


def test_core_uses_config_timestamp_format_for_snapshot_name(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
):
    """``short`` timestamp format produces a date-only snapshot name."""
    vm = make_vm_config(name="testvm", snapshot_dir=str(tmp_path))
    config = MockConfigFacade(
        global_config=GlobalConfig(timestamp_format="short"),
        vms=[vm],
    )
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with frozen_clock(datetime(2025, 7, 13, 15, 31)):
        name = core._generate_snapshot_name(vm, disk="vda")

    assert name == "testvm.20250713_vda"


# ── test_core_timestamp_format_long_produces_long_name ────────────────────


def test_core_timestamp_format_long_produces_long_name(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
):
    """``long`` timestamp format produces a date+hour+minute snapshot name."""
    vm = make_vm_config(name="testvm", snapshot_dir=str(tmp_path))
    config = MockConfigFacade(
        global_config=GlobalConfig(timestamp_format="long"),
        vms=[vm],
    )
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with frozen_clock(datetime(2025, 7, 13, 15, 31)):
        name = core._generate_snapshot_name(vm, disk="vda")

    assert name == "testvm.20250713T1531_vda"


# ── test_core_timestamp_format_long_iso_produces_iso_name ─────────────────


def test_core_timestamp_format_long_iso_produces_iso_name(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
):
    """``long-iso`` timestamp format produces an ISO 8601 snapshot name with seconds."""
    vm = make_vm_config(name="testvm", snapshot_dir=str(tmp_path))
    config = MockConfigFacade(
        global_config=GlobalConfig(timestamp_format="long-iso"),
        vms=[vm],
    )
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with frozen_clock(datetime(2025, 7, 13, 15, 31, 23)):
        name = core._generate_snapshot_name(vm, disk="vda")

    assert name.startswith("testvm.20250713T153123")
    assert name.endswith("_vda")


# ── test_core_passes_preserve_day_of_week_to_retention_engine ─────────────


def test_core_passes_preserve_day_of_week_to_retention_engine(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core passes ``preserve_day_of_week`` from GlobalConfig to the retention engine."""
    vm = make_vm_config(
        name="testvm",
        targets=[make_target()],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(preserve_day_of_week="tuesday"),
        vms=[vm],
    )
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
        core.run()

    assert eval_spy.called
    assert eval_spy.call_args.kwargs["preserve_day_of_week"] == "tuesday"


# ── test_core_restore_from_snapshot_returns_restore_result ────────────────


def test_core_restore_from_snapshot_returns_restore_result(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core.restore() finds snapshot in state manager, copies chain, returns success."""
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
        path=Path("/snapshots/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    # Mock qemu-img info --backing-chain --output=json (top-to-base order)
    chain_json = json.dumps(
        [
            {"image": "/snapshots/snap1.qcow2"},
            {"image": "/var/lib/libvirt/images/testvm.qcow2"},
        ]
    )
    mock_shell.expect("backing-chain").returns(
        ShellResult(success=True, stdout=chain_json, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("cp").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rebase").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    result = core.restore("snap1", tmp_path)

    assert isinstance(result, RestoreResult)
    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.restored_path == tmp_path
    assert len(result.chain_files) == 2
    assert result.error is None


# ── test_core_restore_from_backup_returns_restore_result ──────────────────


def test_core_restore_from_backup_returns_restore_result(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core.restore() finds backup in target directory, resolves chain through FULL anchors, returns success."""
    vm = make_vm_config(name="testvm", targets=[make_target()])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    backup = SnapshotInfo(
        name="backup1",
        path=Path("/mnt/backup/backup1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )

    # Chain with FULL anchor: backup1 → full.FULL.monthly.qcow2 → base
    chain_json = json.dumps(
        [
            {
                "image": "/mnt/backup/backup1.qcow2",
                "backing-filename": "/mnt/backup/full.FULL.monthly.qcow2",
            },
            {
                "image": "/mnt/backup/full.FULL.monthly.qcow2",
                "backing-filename": "/var/lib/libvirt/images/testvm.qcow2",
            },
            {"image": "/var/lib/libvirt/images/testvm.qcow2"},
        ]
    )
    mock_shell.expect("backing-chain").returns(
        ShellResult(success=True, stdout=chain_json, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("cp").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rebase").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with patch.object(mock_factory._backup_provider, "list", return_value=[backup]):
        result = core.restore("backup1", tmp_path)

    assert isinstance(result, RestoreResult)
    assert result.success is True
    assert result.snapshot_name == "backup1"
    assert len(result.chain_files) == 3, "Chain with FULL anchor should have 3 files"
    assert result.error is None


# ── test_core_restore_from_bitmap_backup_standalone_file ──────────────────


def test_core_restore_from_bitmap_backup_standalone_file(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Restore from bitmap backup (no backing chain, single file)."""
    vm = make_vm_config(name="testvm", targets=[make_target()])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    backup = SnapshotInfo(
        name="bitmap_backup",
        path=Path("/mnt/backup/bitmap_backup.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )

    # Single file — no backing chain
    chain_json = json.dumps([{"image": "/mnt/backup/bitmap_backup.qcow2"}])
    mock_shell.expect("backing-chain").returns(
        ShellResult(success=True, stdout=chain_json, stderr="", returncode=0, error=None)
    )
    mock_shell.expect("cp").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    with patch.object(mock_factory._backup_provider, "list", return_value=[backup]):
        result = core.restore("bitmap_backup", tmp_path)

    assert isinstance(result, RestoreResult)
    assert result.success is True
    assert len(result.chain_files) == 1
    assert result.chain_files[0] == tmp_path / "bitmap_backup.qcow2"
    assert result.error is None


# ── test_pipeline_backup_abort_returns_exit_code_10 ───────────────────────


def test_pipeline_backup_abort_returns_exit_code_10(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When backup fails, VMRunResult.backup_failed=True and exit code is 10."""
    vm = make_vm_config(name="testvm", targets=[make_target()])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    failed_backup = BackupResult(
        success=False,
        snapshot_name="snap1",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=Path("/mnt/backup/snap1.qcow2"),
        bytes_transferred=0,
        error="transfer failed",
    )

    with patch.object(
        mock_factory._backup_provider,
        "transfer_missing",
        return_value=[failed_backup],
    ):
        result = core.run()

    assert len(result.results) == 1
    assert result.results[0].backup_failed is True

    exit_code = _format_pipeline_result(result)
    assert exit_code == EXIT_BACKUP_ABORT


# ── test_pipeline_all_backups_succeed_exit_code_not_10 ────────────────────


def test_pipeline_all_backups_succeed_exit_code_not_10(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When all backups succeed, exit code is NOT 10 (should be 0)."""
    vm = make_vm_config(name="testvm", targets=[make_target()])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.run()

    assert len(result.results) == 1
    assert result.results[0].backup_failed is False

    exit_code = _format_pipeline_result(result)
    assert exit_code != EXIT_BACKUP_ABORT
    assert exit_code == EXIT_SUCCESS


# ── test_ondemand_snapshot_created_when_target_reachable ──────────────────


def test_ondemand_snapshot_created_when_target_reachable(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When snapshot_create='ondemand' and target path exists, snapshot IS created."""
    vm = make_vm_config(
        name="testvm",
        snapshot_create="ondemand",
        targets=[make_target(path=str(tmp_path))],
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
        "create",
        wraps=snapshot_provider.create,
    ) as create_spy:
        core.run()

    assert create_spy.called, (
        "Snapshot should be created when target is reachable (ondemand)"
    )


# ── test_ondemand_snapshot_skipped_when_no_target_reachable ────────────────


def test_ondemand_snapshot_skipped_when_no_target_reachable(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When snapshot_create='ondemand' and no target path exists, snapshot is NOT created."""
    vm = make_vm_config(
        name="testvm",
        snapshot_create="ondemand",
        targets=[make_target(path="/nonexistent/path/does/not/exist")],
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
        "create",
        wraps=snapshot_provider.create,
    ) as create_spy:
        core.run()

    assert not create_spy.called, (
        "Snapshot should NOT be created when no target is reachable (ondemand)"
    )


# ── test_core_passes_quiesce_true_to_snapshot_provider ────────────────────


def test_core_passes_quiesce_true_to_snapshot_provider(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core passes ``quiesce=True`` to ``snapshot_provider.create()``.

    When ``VMConfig.snapshot_quiesce`` is ``True``, the ``create()`` call
    must receive ``quiesce=True`` as a keyword argument.
    """
    vm = make_vm_config(
        name="testvm",
        snapshot_quiesce=True,
        disks=["vda"],
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
        "create",
        wraps=snapshot_provider.create,
    ) as create_spy:
        core.snapshot()

    assert create_spy.called
    assert create_spy.call_args.kwargs.get("quiesce") is True


# ── test_core_passes_quiesce_false_to_snapshot_provider ───────────────────


def test_core_passes_quiesce_false_to_snapshot_provider(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core passes ``quiesce=False`` to ``snapshot_provider.create()``.

    When ``VMConfig.snapshot_quiesce`` is ``False`` (the default), the
    ``create()`` call must receive ``quiesce=False``.
    """
    vm = make_vm_config(
        name="testvm",
        snapshot_quiesce=False,
        disks=["vda"],
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
        "create",
        wraps=snapshot_provider.create,
    ) as create_spy:
        core.snapshot()

    assert create_spy.called
    assert create_spy.call_args.kwargs.get("quiesce") is False


# ── Size Estimation Tests ──────────────────────────────────────────────────


def test_size_estimation_logged_during_normal_run(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Size estimation is logged during normal pipeline run."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Mock qemu-img info to return actual-size
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 1048576}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("du").returns(
        ShellResult(
            success=True,
            stdout="524288 /mnt/backup/testvm\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    caplog.set_level(logging.INFO)
    core.run()

    assert "Size estimate" in caplog.text, (
        "Size estimation log message should appear during normal run"
    )
    assert "base=1048576" in caplog.text, (
        "Base image actual-size should be logged"
    )


def test_size_estimation_logged_during_dry_run(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Size estimation is logged during dry-run.

    When dry-run mode is active and a FULL backup would be created,
    the output includes '[dry-run] FULL backup would be created'.
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

    # Record a snapshot so _log_size_estimate has snapshots to evaluate
    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot(vm.name, snap)

    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 1048576}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("du").returns(
        ShellResult(
            success=True,
            stdout="524288 /mnt/backup/testvm\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    caplog.set_level(logging.INFO)

    with patch.object(
        Core, "_should_create_bucket_full", return_value=(True, "weekly")
    ):
        core._log_size_estimate(vm, target)

    assert "Size estimate" in caplog.text, (
        "Size estimation should be logged during dry-run"
    )
    # New assertion: dry-run FULL would-be-created indicator
    assert "[dry-run] FULL backup would be created" in caplog.text, (
        "Dry-run size estimation should indicate FULL would be created"
    )
    assert "bucket=weekly" in caplog.text, (
        "Bucket level should be indicated in dry-run log"
    )


def test_size_estimation_no_state_history(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Size estimation with no state history uses 0 for avg incremental.

    Call ``_log_size_estimate`` directly so the pipeline does not
    create any snapshots that would populate state.
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

    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 2097152}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("du").returns(
        ShellResult(success=True, stdout="0 /mnt\n", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.INFO)
    core._log_size_estimate(vm, target)

    assert "avg_inc=0" in caplog.text, (
        "With no state history, avg_inc should be 0"
    )
    assert "base=2097152" in caplog.text, (
        "Base image actual-size should be logged even with no history"
    )


def test_estimate_method_for_specific_vm(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core.estimate() for a specific VM returns a report with size projections."""
    target = make_target()
    vm1 = make_vm_config(name="vm1", targets=[target])
    vm2 = make_vm_config(name="vm2", targets=[target])
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 1000000}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("du").returns(
        ShellResult(success=True, stdout="500000 /mnt\n", stderr="", returncode=0, error=None)
    )

    result = core.estimate(vm_filter="vm1")

    assert "=== vm1 ===" in result
    assert "=== vm2 ===" not in result, "filtered VM should not appear"
    assert "Projected FULLs:" in result
    assert "Projected total size:" in result
    assert "Current target size:" in result


def test_estimate_method_for_all_vms(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core.estimate() without filter returns report for all VMs."""
    target = make_target()
    vm1 = make_vm_config(name="vm1", targets=[target])
    vm2 = make_vm_config(name="vm2", targets=[target])
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 1000000}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("du").returns(
        ShellResult(success=True, stdout="500000 /mnt\n", stderr="", returncode=0, error=None)
    )

    result = core.estimate()

    assert "=== vm1 ===" in result
    assert "=== vm2 ===" in result
    assert "Projected FULLs:" in result
    assert "Current target size:" in result


def test_compressed_full_projection_30_percent(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Compressed FULL projection is base_size × 0.3 (compress=True)."""
    target = make_target(compress=True)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 1000000}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("du").returns(
        ShellResult(success=True, stdout="0 /mnt\n", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.INFO)
    core.run()

    assert "base=1000000" in caplog.text
    # full_size = int(1000000 * 0.3) = 300000
    assert "full(compressed=True)=300000" in caplog.text, (
        "Compressed FULL should be ~30% of base size"
    )


def test_uncompressed_full_projection_100_percent(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Uncompressed FULL projection is base_size × 1.0 (compress=False)."""
    target = make_target(compress=False)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 1000000}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("du").returns(
        ShellResult(success=True, stdout="0 /mnt\n", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.INFO)
    core.run()

    assert "base=1000000" in caplog.text
    assert "full(compressed=False)=1000000" in caplog.text, (
        "Uncompressed FULL should be 100% of base size"
    )


def test_incremental_size_rolling_average_from_state(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Average incremental size is computed from state snapshot history.

    Call ``_log_size_estimate`` directly so the pipeline does not create
    any additional snapshots that would skew the average.
    """
    target = make_target(compress=False)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with snapshots having known allocation sizes
    now = datetime.now()
    mock_state.record_snapshot(
        "testvm",
        SnapshotInfo(
            name="snap1",
            path=Path("/tmp/snap1.qcow2"),
            timestamp=now,
            allocation=100000,
        ),
    )
    mock_state.record_snapshot(
        "testvm",
        SnapshotInfo(
            name="snap2",
            path=Path("/tmp/snap2.qcow2"),
            timestamp=now,
            allocation=200000,
        ),
    )
    mock_state.record_snapshot(
        "testvm",
        SnapshotInfo(
            name="snap3",
            path=Path("/tmp/snap3.qcow2"),
            timestamp=now,
            allocation=300000,
        ),
    )

    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 1000000}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("du").returns(
        ShellResult(success=True, stdout="0 /mnt\n", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.INFO)
    core._log_size_estimate(vm, target)

    # avg_inc_size = (100000 + 200000 + 300000) // 3 = 200000
    assert "avg_inc=200000" in caplog.text, (
        "Average incremental size should be computed from state history"
    )


# ── Size Estimation: --force-share on Base Image ────────────────────────────


def test_size_estimation_uses_force_share_on_base_image(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """qemu-img info on base image includes --force-share.

    When base image is locked as backing file by a running VM,
    ``_log_size_estimate`` uses ``--force-share`` to avoid lock conflicts
    (design D5, bug U).
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

    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"actual-size": 1048576}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("du").returns(
        ShellResult(
            success=True,
            stdout="0 /mnt\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        core._log_size_estimate(vm, target)

    # Find the qemu-img info call for the base image
    info_calls = [
        c for c in shell_spy.call_args_list
        if c.args and isinstance(c.args[0], list)
        and "qemu-img" in c.args[0][0] and "info" in " ".join(c.args[0])
        and str(vm.base_image) in " ".join(c.args[0])
    ]
    assert len(info_calls) >= 1, (
        "qemu-img info should be called for base image in size estimation"
    )
    for call in info_calls:
        cmd_str = " ".join(call.args[0])
        assert "--force-share" in cmd_str, (
            f"qemu-img info on base image must include --force-share, got: {cmd_str}"
        )
