"""Integration tests for the D1 fix: ``preserve_all`` retention.

Verifies that ``Core._parse_preserve("all")`` returns ``RetentionPolicy``
with ``preserve_min="all"`` and all bucket counts 0 (the bug
previously returned ``preserve_min="0h"``, causing silent data loss).

Also verifies end-to-end that a pipeline with ``snapshot_preserve="all"``
keeps all snapshots and defers blockcommit on a running VM (D2 fix).

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import GlobalConfig, RetentionPolicy, TargetConfig, VMConfig
from qsnap.shell.subprocess_shell import SubprocessShell
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_state import InMemoryStateManager

# ──────────────────────────────────────────────────────────────────────
# Test 1: _parse_preserve("all") produces correct RetentionPolicy
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_parse_preserve_all_produces_correct_policy():
    """Verify ``Core._parse_preserve("all")`` returns the correct policy.

    The D1 fix changed ``_parse_preserve`` so that ``"all"`` returns
    ``RetentionPolicy(preserve_min="all")`` with all bucket counts 0.
    Previously it returned ``preserve_min="0h"`` — a silent data-loss
    bug that caused retention to delete everything.
    """
    policy = Core._parse_preserve("all")

    assert isinstance(policy, RetentionPolicy), (
        f"Expected RetentionPolicy, got {type(policy).__name__}"
    )
    assert policy.preserve_min == "all", f"Expected preserve_min='all', got {policy.preserve_min!r}"
    assert policy.hourly == 0, f"hourly should be 0, got {policy.hourly}"
    assert policy.daily == 0, f"daily should be 0, got {policy.daily}"
    assert policy.weekly == 0, f"weekly should be 0, got {policy.weekly}"
    assert policy.monthly == 0, f"monthly should be 0, got {policy.monthly}"
    assert policy.yearly == 0, f"yearly should be 0, got {policy.yearly}"
    assert policy.anchor_hourly is False
    assert policy.anchor_daily is False
    assert policy.anchor_weekly is False
    assert policy.anchor_monthly is False
    assert policy.anchor_yearly is False


# ──────────────────────────────────────────────────────────────────────
# Test 2: preserve_all keeps all snapshots — integration
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_preserve_all_keeps_all_backups_integration(test_vm):
    """Verify that ``snapshot_preserve="all"`` keeps all snapshots.

    1. Start the test VM.
    2. Build Core with a VMConfig using ``snapshot_preserve="all"``,
       real shell, ``InMemoryStateManager``, and ``DefaultFactory``.
    3. Create 3 snapshots via ``core._create_snapshot()``.
    4. Evaluate snapshot retention — it MUST keep all 3 and remove none.
    5. Verify all snapshot files still exist on disk.
    6. Verify ``Core._parse_preserve("all").preserve_min == "all"``.

    Additionally, run the pipeline to verify that snapshot preservation
    works end-to-end without errors.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Step 1: Start the VM.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    # Verify VM is running.
    domstate = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    assert domstate.success, f"domstate failed: {domstate.error}"
    assert "running" in domstate.stdout.lower(), f"VM should be running, got: {domstate.stdout!r}"

    # Step 2: Build Core with snapshot_preserve="all" and a target.
    state = InMemoryStateManager()
    target = TargetConfig(
        path=target_dir,
        incremental=True,
        incremental_mode="file-copy",
        compress=False,
        verify="off",
    )
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        snapshot_preserve="all",
        targets=[target],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(timestamp_format="short"),
        vms=[vm_config],
    )
    factory = DefaultFactory(shell=shell, state=state)

    core = Core(
        config=config,
        factory=factory,
        state=state,
        shell=shell,
    )

    # Step 3: Create 3 snapshots via _create_snapshot.
    for i in range(3):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} creation failed: {results[0].error}"
        # Small sleep to ensure distinct timestamps in snapshot names.
        time.sleep(1.1)

    # Verify 3 snapshots recorded in state.
    snapshots_in_state = state.get_snapshots(vm_name)
    assert len(snapshots_in_state) == 3, (
        f"Expected 3 snapshots in state, got {len(snapshots_in_state)}"
    )

    # Verify all snapshot files exist on disk.
    for snap in snapshots_in_state:
        assert snap.path.exists(), f"Snapshot file should exist: {snap.path}"

    # Step 4: Evaluate snapshot retention — "all" should keep everything.
    retention = core._evaluate_snapshot_retention(vm_config)
    assert retention is not None, "Retention result should not be None"
    assert len(retention.keep) == 3, (
        f"Expected 3 snapshots kept, got {len(retention.keep)}: {retention.keep}"
    )
    assert len(retention.remove) == 0, (
        f"Expected 0 snapshots removed, got {len(retention.remove)}: {retention.remove}"
    )

    # Step 5: Double-check parse_preserve is correct.
    policy = Core._parse_preserve("all")
    assert policy.preserve_min == "all"

    # Step 6: Run the full pipeline — with "all" retention, nothing
    # gets removed.  Blockcommit on a running VM gets deferred (D2 fix)
    # so no error should occur.
    pipeline_result = core.run()
    assert pipeline_result.success, (
        f"Pipeline should succeed, got errors: "
        f"{[(r.vm_name, r.error) for r in pipeline_result.results if not r.success]}"
    )

    # All original snapshots should still be present in state.
    # Note: core.run() may create an additional snapshot because
    # snapshot_create defaults to "always", so the count may be >= 3.
    snapshots_after = state.get_snapshots(vm_name)
    assert len(snapshots_after) >= 3, (
        f"At least 3 snapshots should remain in state after run, got {len(snapshots_after)}"
    )
    # The 3 originally created snapshots should all still be present.
    original_names = {s.name for s in snapshots_in_state}
    remaining_names = {s.name for s in snapshots_after}
    assert original_names <= remaining_names, (
        f"Original snapshots {original_names} should all remain, "
        f"but only {remaining_names} are present"
    )
