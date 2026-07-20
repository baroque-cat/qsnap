"""Tests for Core.schedule_summary() — retention simulation and preview.

``schedule_summary`` generates synthetic timestamp distributions and
evaluates retention against them, producing a human-readable string.
It does NOT read from ``IStateManager`` — the simulation is purely
based on configured retention policies.

Post-zstd-change: ``schedule_summary()`` logs only factual data:
base image actual-size (from ``qemu-img info``) and compression_type
(from config).  No size projections (``base_size × 0.3`` formula
removed).
"""

from __future__ import annotations

import json
import logging
from argparse import Namespace

from qsnap.core import Core
from qsnap.models.results import ShellResult
from tests.mocks import MockConfigFacade

# ── test_schedule_summary_empty_state_produces_simulation ────────────────


def test_schedule_summary_empty_state_produces_simulation(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """schedule_summary() with empty state returns a non-empty string
    containing retention info (policy, simulated items, kept/remove counts)
    and factual data (base image actual-size, compression type)."""
    vm = make_vm_config(
        name="testvm",
        targets=[make_target(target_preserve="48h")],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    summary = core.schedule_summary()

    assert summary
    assert "testvm" in summary
    assert "Policy:" in summary
    # Factual data — always present:
    assert "Base image actual-size:" in summary
    assert "Snapshots:" in summary
    assert "Backups" in summary
    assert "Compression: zstd (compress=True)" in summary
    assert "Simulated items:" in summary
    # Projection fields REMOVED (size estimation formula removed):
    assert "Projected FULLs:" not in summary
    assert "Projected incrementals:" not in summary
    assert "Projected total size:" not in summary
    assert "Avg incremental size:" not in summary


# ── test_schedule_summary_logs_info_on_timer ──────────────────────────────


def test_schedule_summary_logs_info_on_timer(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """When --timer flag is set, schedule_summary is logged at INFO level
    via the CLI handler ``_handle_schedule_and_timer``."""
    vm = make_vm_config(
        name="testvm",
        targets=[make_target(target_preserve="48h")],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    args = Namespace(timer=True, print_schedule=False, vm=[])
    caplog.set_level(logging.INFO)

    from qsnap.cli.commands import _handle_schedule_and_timer

    _handle_schedule_and_timer(core, args)

    assert "Schedule summary" in caplog.text


# ── test_schedule_summary_shows_snapshot_and_backup_breakdown ─────────────


def test_schedule_summary_shows_snapshot_and_backup_breakdown(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """schedule_summary output includes both snapshot and backup retention info
    with factual data (no size projections)."""
    vm = make_vm_config(
        name="testvm",
        targets=[make_target(target_preserve="48h")],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    summary = core.schedule_summary()

    assert "Snapshots:" in summary
    assert "Backups" in summary
    assert "Base image actual-size:" in summary
    assert "Compression: zstd (compress=True)" in summary
    # Projection fields REMOVED:
    assert "Projected FULLs:" not in summary
    assert "Projected incrementals:" not in summary
    assert "Projected total size:" not in summary


# ── test_schedule_summary_includes_all_vms ────────────────────────────────


def test_schedule_summary_includes_all_vms(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When vm_filter=None, summary includes all VMs in config."""
    vm1 = make_vm_config(
        name="vm1",
        targets=[make_target(target_preserve="48h")],
        snapshot_preserve="24h",
    )
    vm2 = make_vm_config(
        name="vm2",
        targets=[make_target(target_preserve="48h")],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    summary = core.schedule_summary()

    assert "=== vm1 ===" in summary
    assert "=== vm2 ===" in summary


# ── test_schedule_summary_filters_by_vm_name ──────────────────────────────


def test_schedule_summary_filters_by_vm_name(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When vm_filter is set, summary only includes that VM."""
    vm1 = make_vm_config(
        name="vm1",
        targets=[make_target(target_preserve="48h")],
        snapshot_preserve="24h",
    )
    vm2 = make_vm_config(
        name="vm2",
        targets=[make_target(target_preserve="48h")],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    summary = core.schedule_summary(vm_filter="vm2")

    assert "=== vm2 ===" in summary
    assert "=== vm1 ===" not in summary


# ── test_schedule_summary_includes_base_image_size ────────────────────────


def test_schedule_summary_includes_base_image_size(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """schedule_summary includes real base image actual-size from qemu-img info."""
    base_image = "/var/lib/libvirt/images/testvm.qcow2"
    vm = make_vm_config(
        name="testvm",
        base_image=base_image,
        targets=[make_target(target_preserve="48h")],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])

    # Mock qemu-img info to return a known actual-size
    info_json = json.dumps({"actual-size": 1073741824})
    mock_shell.expect(r"qemu-img info.*--output=json").returns(
        ShellResult(success=True, stdout=info_json, stderr="", returncode=0, error=None)
    )

    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    summary = core.schedule_summary()

    assert "Base image actual-size: 1073741824 B" in summary


# ── test_schedule_summary_includes_avg_incremental_size ───────────────────


def test_schedule_summary_includes_avg_incremental_size(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """schedule_summary no longer logs average incremental size — that
    projection was removed along with the 0.3 compression formula.
    Verify it is absent and factual data (compression type) is present."""
    vm = make_vm_config(
        name="testvm",
        targets=[make_target(target_preserve="48h")],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])

    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    summary = core.schedule_summary()

    # Avg incremental size projection was removed.
    assert "Avg incremental size:" not in summary
    # Factual data still present:
    assert "Base image actual-size:" in summary
    assert "Compression:" in summary


# ── test_schedule_summary_includes_compression_type ───────────────────────


def test_schedule_summary_includes_compression_type(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """schedule_summary includes the compression_type per target,
    in the format 'Compression: {compression_type} (compress={compress})'."""
    vm = make_vm_config(
        name="testvm",
        targets=[make_target(target_preserve="48h", compression_type="zstd")],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    summary = core.schedule_summary()

    # Default target sets compression_type="zstd", compress=True
    assert "Compression: zstd (compress=True)" in summary


def test_schedule_summary_compression_type_zlib(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """schedule_summary reports 'zlib' when target.compression_type='zlib'
    and compress is disabled."""
    vm = make_vm_config(
        name="testvm",
        targets=[
            make_target(
                target_preserve="48h",
                compression_type="zlib",
                compress=False,
            )
        ],
        snapshot_preserve="24h",
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    summary = core.schedule_summary()

    assert "Compression: zlib (compress=False)" in summary
