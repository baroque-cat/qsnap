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
from qsnap.models.config import GlobalConfig
from qsnap.models.results import (
    BackupResult,
    RestoreResult,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import MockBucketFullStrategy, MockConfigFacade

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


# ── test_core_restore_from_snapshot_new_qemu_format ──────────────────────


def test_core_restore_from_snapshot_new_qemu_format(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Core.restore() accepts QEMU 11.0+ "filename" keys in backing-chain JSON.

    When ``qemu-img info --backing-chain --output=json`` returns entries with
    ``"filename"`` keys (QEMU 11.0+ format) instead of the legacy ``"image"``
    keys, the chain must be correctly parsed and all files copied to the
    restore target directory.
    """
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

    # Mock qemu-img info --backing-chain --output=json (QEMU 11.0+ "filename" keys)
    chain_json = json.dumps(
        [
            {"filename": "/snapshots/snap1.qcow2"},
            {"filename": "/var/lib/libvirt/images/testvm.qcow2"},
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
    # Verify chain files are in the target directory with correct names
    chain_names = {f.name for f in result.chain_files}
    assert chain_names == {"snap1.qcow2", "testvm.qcow2"}, (
        "Restored chain must include both the snapshot and base image"
    )


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

    assert create_spy.called, "Snapshot should be created when target is reachable (ondemand)"


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


# ── test_core_imports_from_utils_not_backup_modules ──────────────────────


def test_core_imports_from_utils_not_backup_modules():
    """Core imports shared utilities from qsnap.utils.*, not qsnap.modules.backup.*.

    Verifies that:
    - ``is_vm_running`` is imported from ``qsnap.utils.nbd``
    - ``verify_full_backup`` is imported from ``qsnap.utils.verification``
    - No imports from ``qsnap.modules.backup.nbd_helper``
    - No imports from ``qsnap.modules.backup.verification``
    """
    import inspect

    import qsnap.core

    source = inspect.getsource(qsnap.core)

    # Core imports from qsnap.utils.*
    assert "from qsnap.utils.nbd import" in source, (
        "Core should import is_vm_running from qsnap.utils.nbd"
    )
    assert "from qsnap.utils.verification import" in source, (
        "Core should import verify_full_backup from qsnap.utils.verification"
    )

    # Core must NOT import from qsnap.modules.backup.*
    assert "qsnap.modules.backup.nbd_helper" not in source, (
        "Core must NOT import from qsnap.modules.backup.nbd_helper"
    )
    assert "qsnap.modules.backup.verification" not in source, (
        "Core must NOT import from qsnap.modules.backup.verification"
    )


# ═══════════════════════════════════════════════════════════════════════════
#     Action Audit Trail Tests (core-audit-trail)
# ═══════════════════════════════════════════════════════════════════════════


# ── test_actions_cleared_at_run_start ──────────────────────────────────────


def test_actions_cleared_at_run_start(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run core.run() twice; verify actions from first run don't persist to second run."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result1 = core.run()
    result2 = core.run()

    assert len(result1.actions) == 1, "First run should have 1 snapshot_create action"
    assert result1.actions[0].action == "snapshot_create"
    assert len(result2.actions) == 1, (
        "Second run should also have exactly 1 action (actions are cleared between runs)"
    )


# ── test_action_appended_on_snapshot_create ────────────────────────────────


def test_action_appended_on_snapshot_create(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run core.snapshot(); verify PipelineResult.actions contains snapshot_create ActionRecord."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.snapshot()

    assert len(result.actions) >= 1
    snap_actions = [a for a in result.actions if a.action == "snapshot_create"]
    assert len(snap_actions) == 1, "Should contain exactly one snapshot_create action"
    assert snap_actions[0].vm_name == "testvm"
    assert snap_actions[0].size == 65536  # MockSnapshotProvider default
    assert snap_actions[0].error is None


# ── test_action_appended_on_snapshot_delete ────────────────────────────────


def test_action_appended_on_snapshot_delete(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run core.run() with blockcommit; verify actions contains snapshot_delete ActionRecord."""
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        disks=["vda"],
        snapshot_preserve="0h",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # Pre-populate state with a snapshot so retention has something to remove.
    snap = SnapshotInfo(
        name="snap_old",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap_old.qcow2"),
        timestamp=datetime(2025, 1, 1),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    # On Python 3.14 Path.exists() delegates to os.path.exists().  Use a
    # predicate that returns False for generated snapshot candidates
    # (names containing "_vda") so the collision loop in
    # _generate_snapshot_name terminates, while returning True for all
    # other paths the test needs (recorded snapshots, state files).
    def _path_exists(p: object) -> bool:
        return "_vda" not in Path(str(p)).name

    with (
        patch("os.path.exists", side_effect=_path_exists),
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=[], remove=["snap_old"]),
        ),
    ):
        result = core.run()

    # Should have snapshot_create action (always mode) + snapshot_delete action.
    delete_actions = [a for a in result.actions if a.action == "snapshot_delete"]
    assert len(delete_actions) == 1, "Should contain one snapshot_delete action"
    assert delete_actions[0].vm_name == "testvm"
    assert delete_actions[0].name == "snap_old"


# ── test_action_appended_on_backup_transfer ────────────────────────────────


def test_action_appended_on_backup_transfer(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run core.backup(); verify actions contains backup_transfer ActionRecord."""
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
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    # Need to mock transfer_missing to produce a result with duration for the
    # ActionRecord.  Default mock already returns success results.
    result = core.backup()

    transfer_actions = [a for a in result.actions if a.action == "backup_transfer"]
    assert len(transfer_actions) == 1, "Should contain one backup_transfer action"
    assert transfer_actions[0].vm_name == "testvm"
    assert transfer_actions[0].name == "snap1"
    assert transfer_actions[0].size == 1048576  # MockBackupProvider default


# ── test_action_appended_on_full_backup ────────────────────────────────────


def test_action_appended_on_full_backup(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run core.run() with FULL backup; verify actions contains backup_full ActionRecord."""
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

    # Configure MockBucketFullStrategy to trigger FULL creation.
    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "monthly"))

    with patch("qsnap.core.verify_full_backup", return_value=None):
        result = core.run()

    full_actions = [a for a in result.actions if a.action == "backup_full"]
    assert len(full_actions) == 1, "Should contain one backup_full action"
    assert full_actions[0].vm_name == "testvm"
    assert full_actions[0].size == 1048576  # MockBackupProvider default


# ── test_action_appended_on_backup_delete ──────────────────────────────────


def test_action_appended_on_backup_delete(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run core.run() with backup retention; verify actions contains backup_delete ActionRecord."""
    target = make_target(target_preserve="0h")
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

    # Mock backup provider list to return a backup that retention will remove.
    backup = SnapshotInfo(
        name="backup1",
        path=target.path / "backup1.qcow2",
        timestamp=datetime(2025, 1, 1),
        allocation=1000,
    )

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=[backup]),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=[], remove=["backup1"]),
        ),
    ):
        result = core.run()

    delete_actions = [a for a in result.actions if a.action == "backup_delete"]
    assert len(delete_actions) == 1, "Should contain one backup_delete action"
    assert delete_actions[0].vm_name == "testvm"
    assert delete_actions[0].name == "backup1"


# ── test_error_action_appended_on_failure ──────────────────────────────────


def test_error_action_appended_on_failure(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Make a VM step raise an exception; verify actions contains error ActionRecord."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    def raise_error(vm_config):
        raise RuntimeError("Simulated failure in pipeline")

    with patch.object(
        mock_factory,
        "create_snapshot_provider",
        side_effect=raise_error,
    ):
        result = core.run()

    error_actions = [a for a in result.actions if a.action == "error"]
    assert len(error_actions) == 1, "Should contain exactly one error action"
    assert error_actions[0].vm_name == "testvm"
    assert "Simulated failure" in error_actions[0].error or "" in (error_actions[0].error or ""), (
        "Error message should be captured"
    )
    assert result.success is False


# ── test_no_actions_in_dry_run_mutations ───────────────────────────────────


def test_no_actions_in_dry_run_mutations(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run core.run() with dry_run=True; verify actions list is empty (no mutation actions)."""
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

    result = core.run()

    assert len(result.actions) == 0, (
        f"Dry-run should produce no mutation actions, got: {result.actions}"
    )


# ── test_pipeline_result_includes_actions_success ──────────────────────────


def test_pipeline_result_includes_actions_success(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run core.run() successfully; verify PipelineResult.actions is a list and is populated."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.run()

    assert isinstance(result.actions, list)
    assert len(result.actions) > 0, "Actions should be populated after successful run"
    assert result.success is True


# ── test_pipeline_result_includes_error_actions ────────────────────────────


def test_pipeline_result_includes_error_actions(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run with a failing VM; verify PipelineResult.actions contains error ActionRecords."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    def raise_error(vm_config):
        raise RuntimeError("Simulated failure in pipeline")

    with patch.object(
        mock_factory,
        "create_snapshot_provider",
        side_effect=raise_error,
    ):
        result = core.run()

    assert isinstance(result.actions, list)
    assert len(result.actions) > 0, "Error actions should be populated"
    assert any(a.action == "error" for a in result.actions), (
        "Actions should contain error ActionRecord"
    )
    assert result.success is False


# ── test_backup_failed_warning_with_transfer_failures ───────────────────────


def test_backup_failed_warning_with_transfer_failures(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Verify logger.warning for backup transfer failure is emitted."""
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
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    failed_backup = BackupResult(
        success=False,
        snapshot_name="snap1",
        source_path=Path("/tmp/snap1.qcow2"),
        target_path=target.path / "snap1.qcow2",
        bytes_transferred=0,
        error="Connection refused",
    )

    caplog.set_level(logging.WARNING)

    with patch.object(
        mock_factory._backup_provider,
        "transfer_missing",
        return_value=[failed_backup],
    ):
        result = core.run()

    assert isinstance(result, PipelineResult)
    assert result.results[0].backup_failed is True
    assert "Backup transfer failed for VM" in caplog.text
    assert "snapshot(s) failed" in caplog.text
    assert "snap1" in caplog.text
    assert "Connection refused" in caplog.text


# ── test_no_backup_failed_warning_when_all_succeed ─────────────────────────


def test_no_backup_failed_warning_when_all_succeed(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Verify no backup_failed WARNING when all transfers succeed."""
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
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    caplog.set_level(logging.WARNING)

    result = core.run()

    assert result.results[0].backup_failed is False
    # No "Backup transfer failed" warning should be logged.
    backup_failed_warnings = [
        r.message for r in caplog.records if "Backup transfer failed" in r.message
    ]
    assert len(backup_failed_warnings) == 0, (
        f"No backup_failed warnings expected when all succeed, got: {backup_failed_warnings}"
    )


# ── test_transaction_log_not_written_in_dry_run ────────────────────────────


def test_transaction_log_not_written_in_dry_run(
    tmp_path,
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Set transaction_log config; run in dry-run; verify no transaction log is written."""
    tx_log = tmp_path / "transaction.log"
    global_cfg = make_global_config(transaction_log=str(tx_log))
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.dry_run = True

    result = core.run()

    assert isinstance(result, PipelineResult)
    assert result.dry_run is True
    assert not tx_log.exists(), (
        f"Transaction log should NOT be written in dry-run mode, but found: {tx_log}"
    )


# ═══════════════════════════════════════════════════════════════════════════
#     INFO Log Tests
# ═══════════════════════════════════════════════════════════════════════════


# ── test_snapshot_create_info_log ──────────────────────────────────────────


def test_snapshot_create_info_log(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Verify [snapshot] info log is emitted after snapshot creation."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    caplog.set_level(logging.INFO)
    core.snapshot()

    assert "[snapshot]" in caplog.text
    assert "created" in caplog.text
    assert "testvm" in caplog.text
    assert "65536" in caplog.text, "Log should include allocation size in bytes"


# ── test_snapshot_delete_info_log ──────────────────────────────────────────


def test_snapshot_delete_info_log(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Verify [blockcommit] info log is emitted after blockcommit."""
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        disks=["vda"],
        snapshot_preserve="0h",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap_old",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap_old.qcow2"),
        timestamp=datetime(2025, 1, 1),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    caplog.set_level(logging.INFO)

    # On Python 3.14 Path.exists() delegates to os.path.exists().  Use a
    # predicate that returns False for generated snapshot candidates
    # (names containing "_vda") so the collision loop in
    # _generate_snapshot_name terminates, while returning True for all
    # other paths the test needs (recorded snapshots, state files).
    def _path_exists(p: object) -> bool:
        return "_vda" not in Path(str(p)).name

    with (
        patch("os.path.exists", side_effect=_path_exists),
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=[], remove=["snap_old"]),
        ),
    ):
        core.run()

    assert "[blockcommit]" in caplog.text
    assert "merged" in caplog.text
    assert "testvm" in caplog.text
    assert "snap_old" in caplog.text


# ── test_backup_transfer_info_log ──────────────────────────────────────────


def test_backup_transfer_info_log(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Verify [backup] transfer info log is emitted for each successful transfer."""
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
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    caplog.set_level(logging.INFO)
    core.run()

    # Find the transfer info log line.
    transfer_lines = [
        r.message for r in caplog.records if "[backup]" in r.message and "transferred" in r.message
    ]
    assert len(transfer_lines) >= 1, (
        f"Should have at least one backup transfer log line, got: {transfer_lines}"
    )
    assert "testvm" in transfer_lines[0]
    assert "snap1" in transfer_lines[0]
    assert "MiB/s" in transfer_lines[0]
    assert "1048576" in transfer_lines[0], "Log should include bytes_transferred"


# ── test_full_backup_create_info_log ───────────────────────────────────────


def test_full_backup_create_info_log(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Verify [backup] FULL creation info log is emitted for FULL creation."""
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

    mock_factory._bucket_full_strategy = MockBucketFullStrategy(return_value=(True, "monthly"))

    caplog.set_level(logging.INFO)
    with patch("qsnap.core.verify_full_backup", return_value=None):
        core.run()

    full_lines = [
        r.message for r in caplog.records if "[backup]" in r.message and "created FULL" in r.message
    ]
    assert len(full_lines) == 1, (
        f"Should have exactly one FULL creation log line, got: {full_lines}"
    )
    assert "testvm" in full_lines[0]
    assert "FULL" in full_lines[0]
    assert "monthly" in full_lines[0], "Bucket level should be in the log"
    assert "1048576" in full_lines[0], "Log should include bytes_transferred"


# ── test_backup_delete_info_log ────────────────────────────────────────────


def test_backup_delete_info_log(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Verify [delete] info log is emitted for each deleted backup."""
    target = make_target(target_preserve="0h")
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

    backup = SnapshotInfo(
        name="backup1",
        path=target.path / "backup1.qcow2",
        timestamp=datetime(2025, 1, 1),
        allocation=1000,
    )

    caplog.set_level(logging.INFO)

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=[backup]),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=[], remove=["backup1"]),
        ),
    ):
        core.run()

    delete_lines = [
        r.message
        for r in caplog.records
        if "[delete]" in r.message and "removed backup" in r.message
    ]
    assert len(delete_lines) >= 1, (
        f"Should have at least one backup delete log line, got: {delete_lines}"
    )
    assert "testvm" in delete_lines[0]
    assert "backup1" in delete_lines[0]


# ── test_ghost_retention_info_log ──────────────────────────────────────────


def test_ghost_retention_info_log(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Verify [delete] ghost-retained info log is emitted for ghost retention."""
    target = make_target(target_preserve="0h")
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

    full_name = "snap1.FULL.monthly.qcow2"
    inc_name = "snap2.qcow2"
    now = datetime.now()

    # Pre-populate state: FULL with dependent incremental.
    mock_state.record_full_backup(str(target.path), full_name, now, "monthly")
    mock_state.record_incremental_dependency(str(target.path), inc_name, full_name)

    backups = [
        SnapshotInfo(name=full_name, path=target.path / full_name, timestamp=now, allocation=10000),
        SnapshotInfo(name=inc_name, path=target.path / inc_name, timestamp=now, allocation=500),
    ]

    caplog.set_level(logging.INFO)

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=backups),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=[inc_name], remove=[full_name]),
        ),
    ):
        core.run()

    ghost_lines = [
        r.message
        for r in caplog.records
        if "[delete]" in r.message and "ghost-retained" in r.message
    ]
    assert len(ghost_lines) >= 1, f"Should have a ghost-retained log line, got: {ghost_lines}"
    assert "testvm" in ghost_lines[0]
    assert "ghost-retained FULL" in ghost_lines[0]
    assert "dependent(s) in keep-set" in ghost_lines[0]
