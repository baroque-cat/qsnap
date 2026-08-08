"""Tests for count-based FULL backup anchor decision in Core._backup_target().

Covers:
- First backup to target creates FULL unconditionally.
- Incremental count exceeds target_chain_length → FULL triggered.
- target_chain_length is None → no FULL triggered by count.
- Incremental count within chain length → FULL skipped.
- backup_retry_max = 0 → exactly one FULL creation attempt.
- Dry-run mode → logs FULL-would-be-created, no execution.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.results import SnapshotInfo
from tests.mocks import MockConfigFacade


def _record_snap(state, target, vm):
    """Record a snapshot in state and return it."""
    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
        disk="vda",
    )
    state.record_snapshot(vm.name, snap)
    return snap


# ── test_first_backup_creates_full ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_first_backup_creates_full(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """First backup to target with no prior FULLs → creates FULL unconditionally."""
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(mock_state, target, vm)

    # No prior FULLs exist in state.
    assert mock_state.get_full_backups(str(target.path)) == []

    with patch.object(
        mock_factory._backup_provider,
        "run_backup",
        wraps=mock_factory._backup_provider.run_backup,
    ) as full_spy:
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "run_backup should be called on first backup"
    assert full_spy.call_args.kwargs["force_full"] is True, (
        "run_backup should be called with force_full=True when no prior FULL exists"
    )
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 1, "One FULL backup should be recorded after first backup"


# ── test_incremental_count_exceeds_chain_length_triggers_full ──────────────


@pytest.mark.unit
@pytest.mark.mock
def test_incremental_count_exceeds_chain_length_triggers_full(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """target_chain_length=5, incremental_count=6 → FULL triggered."""
    target = make_target(target_chain_length=5)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(mock_state, target, vm)

    # Pre-populate a prior FULL and 6 incremental dependencies
    # (one more than target_chain_length=5).
    full_name = "existing.FULL.daily.qcow2"
    mock_state.record_full_backup(
        str(target.path),
        full_name,
        datetime(2025, 7, 13, 8, 0),
        "vda",
    )
    for i in range(6):
        mock_state.record_incremental_dependency(str(target.path), f"inc{i}.qcow2", full_name)

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            wraps=mock_factory._backup_provider.run_backup,
        ) as full_spy,
        # Patch os.path.exists so the pre-populated FULL is not treated as phantom.
        patch("qsnap.core.os.path.exists", return_value=True),
    ):
        core._backup_target(vm, target, [snap])

    assert full_spy.called, (
        "run_backup should be called when incremental_count (6) > chain_length (5)"
    )
    assert full_spy.call_args.kwargs["force_full"] is True, (
        "run_backup should be called with force_full=True when count exceeds chain_length"
    )


# ── test_target_chain_length_none_no_full_triggered ────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_target_chain_length_none_no_full_triggered(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """target_chain_length=None → no FULL triggered by incremental count."""
    target = make_target(target_chain_length=None)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(mock_state, target, vm)

    # Pre-populate a prior FULL and 10 incremental dependencies.
    full_name = "existing.FULL.daily.qcow2"
    mock_state.record_full_backup(
        str(target.path),
        full_name,
        datetime(2025, 7, 13, 8, 0),
        "vda",
    )
    for i in range(10):
        mock_state.record_incremental_dependency(str(target.path), f"inc{i}.qcow2", full_name)

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            wraps=mock_factory._backup_provider.run_backup,
        ) as full_spy,
        # Patch os.path.exists so the pre-populated FULL is not treated as phantom.
        patch("qsnap.core.os.path.exists", return_value=True),
    ):
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "run_backup should be called when target_chain_length is None"
    assert full_spy.call_args.kwargs["force_full"] is False, (
        "run_backup should NOT be called with force_full=True when target_chain_length is None"
    )


# ── test_incremental_count_within_chain_length_skips_full ──────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_incremental_count_within_chain_length_skips_full(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """target_chain_length=5, incremental_count=3 → FULL NOT triggered."""
    target = make_target(target_chain_length=5)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(mock_state, target, vm)

    # Pre-populate a prior FULL and 3 incremental dependencies
    # (less than target_chain_length=5).
    full_name = "existing.FULL.daily.qcow2"
    mock_state.record_full_backup(
        str(target.path),
        full_name,
        datetime(2025, 7, 13, 8, 0),
        "vda",
    )
    for i in range(3):
        mock_state.record_incremental_dependency(str(target.path), f"inc{i}.qcow2", full_name)

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            wraps=mock_factory._backup_provider.run_backup,
        ) as full_spy,
        # Patch os.path.exists so the pre-populated FULL is not treated as phantom.
        patch("qsnap.core.os.path.exists", return_value=True),
    ):
        core._backup_target(vm, target, [snap])

    assert full_spy.called, "run_backup should be called for the incremental"
    assert full_spy.call_args.kwargs["force_full"] is False, (
        "run_backup should NOT be called with force_full=True when incremental_count "
        "(3) <= chain_length (5)"
    )


# ── test_backup_retry_max_zero_single_full_attempt ─────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_backup_retry_max_zero_single_full_attempt(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """backup_retry_max=0 → FULL creation attempted exactly once (no retry loop).

    Uses _execute_with_retry which, when max_retries <= 0, calls the
    operation exactly once without entering the retry loop.
    """
    target = make_target(backup_retry_max=0)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = _record_snap(mock_state, target, vm)

    # No prior FULLs → first backup triggers FULL creation.
    with patch.object(
        mock_factory._backup_provider,
        "run_backup",
        wraps=mock_factory._backup_provider.run_backup,
    ) as full_spy:
        core._backup_target(vm, target, [snap])

    # run_backup should be called exactly once.
    assert full_spy.call_count == 1, (
        f"run_backup should be called exactly once with backup_retry_max=0, "
        f"got {full_spy.call_count}"
    )


# ── test_dry_run_logs_full_would_be_created ────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_dry_run_logs_full_would_be_created(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Dry-run mode → logs 'Would create FULL backup' without executing."""
    target = make_target(target_chain_length=5)
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    # Enable dry-run mode via the property setter.
    core.dry_run = True

    snap = _record_snap(mock_state, target, vm)

    # Pre-populate a prior FULL and 6 incremental dependencies
    # (incremental_count=6 > chain_length=5 → should trigger FULL).
    full_name = "existing.FULL.daily.qcow2"
    mock_state.record_full_backup(
        str(target.path),
        full_name,
        datetime(2025, 7, 13, 8, 0),
        "vda",
    )
    for i in range(6):
        mock_state.record_incremental_dependency(str(target.path), f"inc{i}.qcow2", full_name)

    caplog.set_level(logging.INFO)

    with (
        patch.object(
            mock_factory._backup_provider,
            "run_backup",
            wraps=mock_factory._backup_provider.run_backup,
        ) as full_spy,
        # Patch os.path.exists so the pre-populated FULL is not treated as phantom.
        patch("qsnap.core.os.path.exists", return_value=True),
    ):
        core._backup_target(vm, target, [snap])

    # run_backup should NOT be called in dry-run mode.
    assert not full_spy.called, "run_backup should NOT be called in dry-run mode"
    # INFO log should contain the dry-run FULL prediction message with
    # per-disk context and VM state.  The default MockShell expectation for
    # qemu-img info --backing-chain returns no actual-size, so the estimate
    # is undecidable and logged as "size unknown".
    expected_fragment = (
        "[dry-run] Would create FULL backup for disk vda (size unknown, method=NBD, VM=running)"
    )
    assert expected_fragment in caplog.text, (
        "Dry-run should log 'Would create FULL backup' with per-disk context "
        "and method/VM-state details"
    )
