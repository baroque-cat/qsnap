"""Tests for Core orchestrator initialization and run() dispatch.

Covers Core dependency injection, full-pipeline execution for all VMs,
and VM filtering via ``vm_filter``.

RISK (test-plan.md line 131): Core must depend on ``IConfigFacade`` (the
ABC), never on ``ConfigFacade`` directly.  The
``test_core_init_stores_dependencies`` test asserts that the stored config
object is an instance of ``IConfigFacade``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qsnap.core import Core, PipelineResult, VMRunResult
from qsnap.interfaces.config import IConfigFacade
from qsnap.interfaces.factory import IVMModuleFactory
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import GlobalConfig
from qsnap.models.results import SnapshotInfo
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

    (tmp_path / "testvm.20250713T1531.qcow2").touch()

    with frozen_clock(datetime(2025, 7, 13, 15, 31)):
        name = core._generate_snapshot_name(vm)

    assert name == "testvm.20250713T1531_1"


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

    (tmp_path / "testvm.20250713T1531.qcow2").touch()
    (tmp_path / "testvm.20250713T1531_1.qcow2").touch()

    with frozen_clock(datetime(2025, 7, 13, 15, 31)):
        name = core._generate_snapshot_name(vm)

    assert name == "testvm.20250713T1531_2"


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
        name = core._generate_snapshot_name(vm)

    assert name == "testvm.20250713"


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
        name = core._generate_snapshot_name(vm)

    assert name == "testvm.20250713T1531"


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
        name = core._generate_snapshot_name(vm)

    assert name.startswith("testvm.20250713T153123")


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
