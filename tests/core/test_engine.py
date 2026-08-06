"""Tests for Core orchestrator initialization and run() dispatch.

Covers Core dependency injection, full-pipeline execution for all VMs,
and VM filtering via ``vm_filter``.

RISK (test-plan.md line 131): Core must depend on ``IConfigFacade`` (the
ABC), never on ``ConfigFacade`` directly.  The
``test_core_init_stores_dependencies`` test asserts that the stored config
object is an instance of ``IConfigFacade``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.cli.commands import _format_pipeline_result
from qsnap.cli.errors import EXIT_BACKUP_ABORT, EXIT_SUCCESS
from qsnap.core import Core, PipelineResult, VMRunResult
from qsnap.interfaces.config import IConfigFacade
from qsnap.interfaces.factory import IVMModuleFactory
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import DiskConfig, GlobalConfig
from qsnap.models.results import (
    ActionRecord,
    BackupResult,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import MockConfigFacade

pytestmark = pytest.mark.unit

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
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``core.run()`` with no filter executes the pipeline for every VM.

    Given a config with 2 VMs, ``run()`` should return a ``PipelineResult``
    with 2 ``VMRunResult`` entries, all successful, and the factory's
    ``create_snapshot_provider`` should have been called once per VM.
    """
    # FULL verification is not the subject of this test — disable it so the
    # backup step completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    vm1 = make_vm_config(name="vm1", targets=[make_target()])
    vm2 = make_vm_config(name="vm2", targets=[make_target()])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm1, vm2])

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
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``core.run(vm_filter="vm1")`` processes only the VM whose name matches.

    The filter uses exact name match.  With 2 VMs ("vm1" and "vm2"), only
    "vm1" should appear in the results.
    """
    # FULL verification is not the subject of this test — disable it so the
    # backup step completes (a failure now aborts the VM pipeline).
    global_cfg = make_global_config(full_verify_after_create="off")
    vm1 = make_vm_config(name="vm1", targets=[make_target()])
    vm2 = make_vm_config(name="vm2", targets=[make_target()])
    config = MockConfigFacade(global_config=global_cfg, vms=[vm1, vm2])

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
    """When the generated name collides, ``_N`` suffix is appended."""
    from unittest.mock import patch

    vm = make_vm_config(name="testvm", snapshot_dir=str(tmp_path))
    config = MockConfigFacade(
        global_config=GlobalConfig(),
        vms=[vm],
    )
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with (
        frozen_clock(datetime(2025, 7, 13, 15, 31, 23)),
        patch("qsnap.core.secrets.token_hex", return_value="a1b2c3"),
    ):
        name1 = core._generate_snapshot_name(vm, disk="vda")
        assert name1 == "testvm.20250713T153123_vda_a1b2c3"
        (tmp_path / f"{name1}.qcow2").touch()
        name2 = core._generate_snapshot_name(vm, disk="vda")

    assert name2 == "testvm.20250713T153123_vda_a1b2c3_1"


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
    from unittest.mock import patch

    vm = make_vm_config(name="testvm", snapshot_dir=str(tmp_path))
    config = MockConfigFacade(
        global_config=GlobalConfig(),
        vms=[vm],
    )
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with (
        frozen_clock(datetime(2025, 7, 13, 15, 31, 23)),
        patch("qsnap.core.secrets.token_hex", return_value="a1b2c3"),
    ):
        name1 = core._generate_snapshot_name(vm, disk="vda")
        (tmp_path / f"{name1}.qcow2").touch()
        (tmp_path / f"{name1}_1.qcow2").touch()
        name2 = core._generate_snapshot_name(vm, disk="vda")

    assert name2 == "testvm.20250713T153123_vda_a1b2c3_2"


# ── test_core_snapshot_name_uses_unified_format ────────────────────────────


def test_core_snapshot_name_uses_unified_format(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
):
    """Snapshot name uses ``{vm}.{YYYYMMDDTHHMMSS}_{disk}_{6hex}`` format."""
    vm = make_vm_config(name="testvm", snapshot_dir=str(tmp_path))
    config = MockConfigFacade(
        global_config=GlobalConfig(),
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

    assert name.startswith("testvm.20250713T153123_vda_")
    # Hex suffix is 6 characters.
    hex_part = name.rsplit("_", 1)[-1]
    assert len(hex_part) == 6
    int(hex_part, 16)  # must be valid hex


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

    with patch("qsnap.core.verify_full_backup", return_value=None):
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
        "create_multi",
        wraps=snapshot_provider.create_multi,
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
        "create_multi",
        wraps=snapshot_provider.create_multi,
    ) as create_spy:
        core.run()

    assert not create_spy.called, (
        "Snapshot should NOT be created when no target is reachable (ondemand)"
    )


# ── test_core_create_multi_quiesce_all_disks ──────────────────────────────


def test_core_create_multi_quiesce_all_disks(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Quiesce enabled → ONE create_multi call covering ALL disks.

    ``quiesce=vm_config.snapshot_quiesce`` is passed to the single batch
    call; both disk specs are delivered in one freeze (design D9/D10).
    """
    vm = make_vm_config(
        name="testvm",
        snapshot_quiesce=True,
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
    ) as create_multi_spy:
        core.snapshot()

    create_multi_spy.assert_called_once()
    call = create_multi_spy.call_args
    assert call.kwargs.get("quiesce") is True
    specs = call.args[1]
    assert [s.disk for s in specs] == ["vda", "vdb"]


# ── test_core_create_multi_no_quiesce_default ─────────────────────────────


def test_core_create_multi_no_quiesce_default(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Quiesce disabled (default) → ONE create_multi call with quiesce=False."""
    vm = make_vm_config(
        name="testvm",
        snapshot_quiesce=False,
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
    ) as create_multi_spy:
        core.snapshot()

    create_multi_spy.assert_called_once()
    call = create_multi_spy.call_args
    assert call.kwargs.get("quiesce") is False
    specs = call.args[1]
    assert [s.disk for s in specs] == ["vda", "vdb"]


# ── test_pipeline_result_space_limited_true ───────────────────────────────


def test_pipeline_result_space_limited_true(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A space-limited run is flagged on PipelineResult.space_limited.

    core-orchestrator scenario "space-limited run flagged": an ENOSPC
    transfer failure suspends the target and sets the flag without
    failing the VM.
    """
    target = make_target(path=str(tmp_path / "backup"))
    target.path.mkdir(parents=True, exist_ok=True)
    (target.path / "testvm.FULL.anchor.qcow2").touch()

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
    mock_state.record_full_backup(
        str(target.path), "testvm.FULL.anchor.qcow2", datetime(2025, 7, 12, 10, 0), "vda"
    )

    failed = BackupResult(
        success=False,
        snapshot_name="snap1",
        source_path=snap.path,
        target_path=target.path / "snap1.qcow2",
        bytes_transferred=0,
        error="No space left on device",
        disk="vda",
    )

    with patch.object(
        mock_factory._backup_provider,
        "transfer_missing",
        return_value=[failed],
    ):
        result = core.run()

    assert result.space_limited is True
    assert result.results[0].success is True
    assert result.results[0].backup_failed is False


# ── test_pipeline_result_space_limited_false ──────────────────────────────


def test_pipeline_result_space_limited_false(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A clean run is NOT flagged space_limited (default False)."""
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

    result = core.run()

    assert result.space_limited is False
    assert result.success is True


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
    vm = make_vm_config(name="testvm")
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
    vm = make_vm_config(name="testvm")
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
    assert snap_actions[0].disk == "vda"
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
        snapshot_chain_length=0,
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
        disk="vda",
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
    assert delete_actions[0].disk == "vda"


# ── test_action_appended_on_backup_transfer ────────────────────────────────


def test_action_appended_on_backup_transfer(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """Run core.backup(); verify actions contains backup_transfer ActionRecord."""
    target_dir = tmp_path / "backup"
    target_dir.mkdir()
    target = make_target(path=str(target_dir))
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

    # Pre-record a FULL whose file actually exists so it survives the
    # phantom filter and this run performs an incremental transfer of snap1
    # (a new FULL would consume the snapshot instead of transferring it).
    (target_dir / "testvm.FULL.daily.qcow2").touch()
    mock_state.record_full_backup(
        str(target.path), "testvm.FULL.daily.qcow2", datetime(2025, 7, 13, 9, 0), "vda"
    )

    # Spy on transfer_missing to verify new kwargs are passed by Core.
    bitmap_provider = mock_factory._bitmap_backup_provider
    with patch.object(
        bitmap_provider,
        "transfer_missing",
        wraps=bitmap_provider.transfer_missing,
    ) as transfer_spy:
        result = core.backup()

    transfer_actions = [a for a in result.actions if a.action == "backup_transfer"]
    assert len(transfer_actions) == 1, "Should contain one backup_transfer action"
    assert transfer_actions[0].vm_name == "testvm"
    assert transfer_actions[0].name == "snap1"
    assert transfer_actions[0].disk == "vda"
    assert transfer_actions[0].size == 1048576  # MockBitmapBackupProvider default

    # Verify Core passes compression_type and stall_timeout to transfer_missing.
    assert transfer_spy.called
    assert transfer_spy.call_args.kwargs["compression_type"] == "zstd"
    assert transfer_spy.call_args.kwargs["stall_timeout"] == 1800


# ── test_action_appended_on_full_backup ────────────────────────────────────


def test_action_appended_on_full_backup(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run core.run() with FULL backup; verify actions contains backup_full ActionRecord."""
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Count-based trigger: no prior FULLs causes first backup to create FULL.

    # Spy on create_full_backup to verify new kwargs are passed by Core.
    bitmap_provider = mock_factory._bitmap_backup_provider
    with (
        patch.object(
            bitmap_provider,
            "create_full_backup",
            wraps=bitmap_provider.create_full_backup,
        ) as full_spy,
        patch("qsnap.core.verify_full_backup", return_value=None),
    ):
        result = core.run()

    full_actions = [a for a in result.actions if a.action == "backup_full"]
    assert len(full_actions) == 1, "Should contain one backup_full action"
    assert full_actions[0].vm_name == "testvm"
    assert full_actions[0].disk == "vda"
    assert full_actions[0].size == 1048576  # MockBitmapBackupProvider default

    # Verify Core passes compression_type and stall_timeout to create_full_backup.
    assert full_spy.called
    assert full_spy.call_args.kwargs["compression_type"] == "zstd"
    assert full_spy.call_args.kwargs["stall_timeout"] == 1800
    # bucket_level is not passed in count-based FULL (Core calls create_full_backup without it).
    assert full_spy.call_args.kwargs["compress"] is True


# ── test_action_appended_on_backup_delete ──────────────────────────────────


def test_action_appended_on_backup_delete(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run core.run() with backup retention; verify actions contains backup_delete ActionRecord."""
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Pre-populate a FULL backup that retention will remove.
    backup = SnapshotInfo(
        name="testvm.FULL.backup1.qcow2",
        path=target.path / "testvm.FULL.backup1.qcow2",
        timestamp=datetime(2025, 1, 1),
        allocation=1000,
        disk="vda",
    )

    # Ensure auto-recovery does not delete backup1 (valid backing chain).
    mock_shell.expect("qemu-img info --backing-chain").returns(
        ShellResult(success=True, stdout="{}", stderr="", returncode=0, error=None)
    )

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=[backup]),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=[], remove=["testvm.FULL.backup1.qcow2"]),
        ),
        patch("qsnap.core.verify_full_backup", return_value=None),
    ):
        result = core.run()

    delete_actions = [a for a in result.actions if a.action == "backup_delete"]
    assert len(delete_actions) == 1, "Should contain one backup_delete action"
    assert delete_actions[0].vm_name == "testvm"
    assert delete_actions[0].name == "testvm.FULL.backup1.qcow2"
    assert delete_actions[0].disk == "vda"


# ── test_error_action_appended_on_failure ──────────────────────────────────


def test_error_action_appended_on_failure(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Make a VM step raise an exception; verify actions contains error ActionRecord."""
    vm = make_vm_config(name="testvm")
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
    assert error_actions[0].disk is None, "VM-level error record should have disk=None"
    assert "Simulated failure" in (error_actions[0].error or ""), "Error message should be captured"
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
    assert len(result.predictions) > 0, (
        f"Dry-run should produce predictions, got: {result.predictions}"
    )
    assert all(isinstance(p, ActionRecord) for p in result.predictions), (
        "All predictions must be ActionRecord instances"
    )
    assert result.dry_run is True, "PipelineResult.dry_run must be True for a dry-run"


# ── test_multi_disk_actions_each_carry_disk ────────────────────────────────


def test_multi_disk_actions_each_carry_disk(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
):
    """2-disk VM; each disk's ActionRecord carries its own disk."""
    disks = [
        DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm_vda.qcow2")),
        DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm_vdb.qcow2")),
    ]
    vm = make_vm_config(name="testvm", disks=disks)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with (
        frozen_clock(datetime(2025, 7, 13, 15, 31, 23)),
        patch("qsnap.core.secrets.token_hex", side_effect=["a1b2c3", "d4e5f6"]),
    ):
        result = core.snapshot()

    snap_actions = [a for a in result.actions if a.action == "snapshot_create"]
    assert len(snap_actions) == 2, (
        f"Should have 2 snapshot_create actions (one per disk), got: {len(snap_actions)}"
    )
    disks_found = {a.disk for a in snap_actions}
    assert disks_found == {"vda", "vdb"}, f"Expected disks vda and vdb, got: {disks_found}"
    # Each action carries its own disk name.
    for action in snap_actions:
        assert action.vm_name == "testvm"
        assert action.disk in {"vda", "vdb"}
        assert action.size == 65536  # MockSnapshotProvider default
        assert action.error is None


# ── test_pipeline_result_includes_actions_success ──────────────────────────


def test_pipeline_result_includes_actions_success(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Run core.run() successfully; verify PipelineResult.actions is a list and is populated."""
    vm = make_vm_config(name="testvm")
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
    vm = make_vm_config(name="testvm")
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
        disk="vda",
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

    # FULL verification is not the subject of this test — let the FULL
    # succeed so the (patched) transfer failure is what gets exercised.
    with (
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch.object(
            mock_factory._backup_provider,
            "transfer_missing",
            return_value=[failed_backup],
        ),
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    caplog.set_level(logging.WARNING)

    with patch("qsnap.core.verify_full_backup", return_value=None):
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
    vm = make_vm_config(name="testvm")
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
    vm = make_vm_config(name="testvm")
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
        snapshot_chain_length=0,
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
        disk="vda",
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
    tmp_path,
):
    """Verify [backup] transfer info log is emitted for each successful transfer."""
    target_dir = tmp_path / "backup"
    target_dir.mkdir()
    target = make_target(path=str(target_dir))
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

    # Pre-record a FULL whose file actually exists so it survives the
    # phantom filter and this run performs an incremental transfer of snap1
    # (a new FULL would consume the snapshot instead of transferring it).
    (target_dir / "testvm.FULL.daily.qcow2").touch()
    mock_state.record_full_backup(
        str(target.path), "testvm.FULL.daily.qcow2", datetime(2025, 7, 13, 9, 0), "vda"
    )

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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # Count-based trigger: no prior FULLs causes first backup to create FULL.

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
    # Count-based log: no bucket level.
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
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    backup = SnapshotInfo(
        name="testvm.FULL.backup1.qcow2",
        path=target.path / "testvm.FULL.backup1.qcow2",
        timestamp=datetime(2025, 1, 1),
        allocation=1000,
        disk="vda",
    )

    # Ensure startup validation does not delete backup1 (valid FULL).
    mock_shell.expect("qemu-img info --backing-chain").returns(
        ShellResult(success=True, stdout="{}", stderr="", returncode=0, error=None)
    )

    caplog.set_level(logging.INFO)

    with (
        patch.object(mock_factory._backup_provider, "list", return_value=[backup]),
        patch.object(
            mock_factory._retention_engine,
            "evaluate",
            return_value=RetentionResult(keep=[], remove=["testvm.FULL.backup1.qcow2"]),
        ),
        patch("qsnap.core.verify_full_backup", return_value=None),
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
    assert "testvm.FULL.backup1" in delete_lines[0]
