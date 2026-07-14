"""Tests for Core.schedule_summary() — retention simulation and preview.

``schedule_summary`` generates synthetic timestamp distributions and
evaluates retention against them, producing a human-readable string.
It does NOT read from ``IStateManager`` — the simulation is purely
based on configured retention policies.
"""

from __future__ import annotations

import logging
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qsnap.core import Core
from qsnap.models.results import SnapshotInfo
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
    containing retention info (policy, simulated items, kept/remove counts)."""
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
    """schedule_summary output includes both snapshot and backup retention info."""
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
