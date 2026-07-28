"""Integration tests for chain_length=0 retention (keep everything via count-based).

In the count-based retention model, setting ``chain_length=0`` with
``keep_generations=1`` causes the retention engine to fall back to
``keep_generations`` as the keep count.  ``chain_length=0`` with
``keep_generations=0`` causes all items to be marked for removal.

This replaces the old ``_parse_preserve("all")`` string-based approach.

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
# Test 1: chain_length=0 with keep_generations=1 keeps newest 1
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_chain_length_zero_keeps_one():
    """Verify chain_length=0 falls back to keep_generations=1.

    When chain_length is 0 and keep_generations is 1, the retention
    engine keeps exactly the newest 1 snapshot and removes the rest.
    This is the minimal-count retention — the opposite of "all".
    """
    from datetime import datetime, timedelta

    from qsnap.models.results import RetentionItem
    from qsnap.retention.time_based import TimeBasedRetention

    now = datetime.now()
    items = [
        RetentionItem(name="old", timestamp=now - timedelta(hours=3)),
        RetentionItem(name="mid", timestamp=now - timedelta(hours=2)),
        RetentionItem(name="new", timestamp=now - timedelta(hours=1)),
    ]
    policy = RetentionPolicy(chain_length=0, keep_generations=1)
    engine = TimeBasedRetention()
    result = engine.evaluate(items, policy, now)

    assert set(result.keep) == {"new"}, (
        f"Expected only 'new' kept, got: keep={result.keep}, remove={result.remove}"
    )
    assert "old" in result.remove
    assert "mid" in result.remove


# ──────────────────────────────────────────────────────────────────────
# Test 2: Large chain_length keeps all snapshots — integration
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_large_chain_length_keeps_all_backups_integration(test_vm):
    """Verify that a large ``snapshot_chain_length`` keeps all snapshots.

    1. Start the test VM.
    2. Build Core with a VMConfig using ``snapshot_chain_length=999999``,
       real shell, ``InMemoryStateManager``, and ``DefaultFactory``.
    3. Create 3 snapshots via ``core._create_snapshot()``.
    4. Evaluate snapshot retention — it MUST keep all 3 and remove none.
    5. Verify all snapshot files still exist on disk.

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

    # Step 2: Build Core with a very large chain_length and a target.
    state = InMemoryStateManager()
    target = TargetConfig(
        path=target_dir,
        incremental=True,
        compress=False,
        verify="off",
    )
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=999999,
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
        time.sleep(1.1)

    # Verify 3 snapshots recorded in state.
    snapshots_in_state = state.get_snapshots(vm_name)
    assert len(snapshots_in_state) == 3, (
        f"Expected 3 snapshots in state, got {len(snapshots_in_state)}"
    )

    # Verify all snapshot files exist on disk.
    for snap in snapshots_in_state:
        assert snap.path.exists(), f"Snapshot file should exist: {snap.path}"

    # Step 4: Evaluate snapshot retention — large chain_length should keep everything.
    retention = core._evaluate_snapshot_retention(vm_config)
    assert retention is not None, "Retention result should not be None"
    assert len(retention.keep) == 3, (
        f"Expected 3 snapshots kept, got {len(retention.keep)}: {retention.keep}"
    )
    assert len(retention.remove) == 0, (
        f"Expected 0 snapshots removed, got {len(retention.remove)}: {retention.remove}"
    )

    # Step 5: Run the full pipeline — with large chain_length, nothing
    # gets removed.  Blockcommit on a running VM gets deferred (D2 fix)
    # so no error should occur.
    pipeline_result = core.run()
    assert pipeline_result.success, (
        f"Pipeline should succeed, got errors: "
        f"{[(r.vm_name, r.error) for r in pipeline_result.results if not r.success]}"
    )

    # All original snapshots should still be present in state.
    snapshots_after = state.get_snapshots(vm_name)
    assert len(snapshots_after) >= 3, (
        f"At least 3 snapshots should remain in state after run, got {len(snapshots_after)}"
    )
    original_names = {s.name for s in snapshots_in_state}
    remaining_names = {s.name for s in snapshots_after}
    assert original_names <= remaining_names, (
        f"Original snapshots {original_names} should all remain, "
        f"but only {remaining_names} are present"
    )
