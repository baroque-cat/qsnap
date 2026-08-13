"""Core hysteresis snapshot retention tests (retention-unit group).

Covers the ``bulk-collapse-blockcommit`` change: grow-to-threshold /
collapse-to-floor retention in ``snapshot_retention_mode = "hysteresis"``
with a SINGLE-RUN UNCAPPED bulk collapse — the persisted
``collapse_in_progress`` phase and the ``max_commits_per_run`` per-run cap
no longer exist.

All tests drive the unit seam ``Core._evaluate_snapshot_retention`` (and
``Core._blockcommit_snapshots`` for the merge-all-in-one-run test) with
``MockVMModuleFactory`` + ``InMemoryStateManager`` and a real
``TimeBasedRetention`` engine where count-based keep/remove decisions are
required — zero real virsh/qemu-img calls (TESTING.md).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from qsnap.core import Core
from qsnap.models.config import GlobalConfig, RetentionPolicy
from qsnap.models.results import SnapshotInfo
from qsnap.retention.time_based import TimeBasedRetention
from tests.mocks import MockConfigFacade

# ── helpers ───────────────────────────────────────────────────────────────


def _hysteresis_vm(make_vm_config, H=72, L=24, **kwargs):
    """A VM with hysteresis mode: chain_length = trigger H, preserve_min = floor L."""
    return make_vm_config(
        name="testvm",
        snapshot_retention_mode="hysteresis",
        snapshot_chain_length=H,
        snapshot_preserve_min=L,
        **kwargs,
    )


def _build_core(mock_factory, mock_state, mock_shell, vm, global_config=None):
    """Build Core with a MockConfigFacade.

    ``global_config`` defaults to ``GlobalConfig()`` — the removed
    ``max_commits_per_run`` option does not exist on the model anymore.
    """
    config = MockConfigFacade(global_config=global_config or GlobalConfig(), vms=[vm])
    return Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )


def _record_snapshots(mock_state, count, start=1, vm="testvm", disk="vda", prefix="snap"):
    """Record ``count`` snapshots named ``{prefix}{n:03d}`` in ascending time order.

    Timestamps continue from the batch's ``start`` index so that multiple
    calls with increasing ``start`` values produce a globally ascending
    chain (no timestamp ties across batches).
    """
    base = datetime(2025, 7, 13, 10, 0)
    for i in range(count):
        n = start + i
        mock_state.record_snapshot(
            vm,
            SnapshotInfo(
                name=f"{prefix}{n:03d}",
                path=Path(f"/tmp/{prefix}{n:03d}.qcow2"),
                timestamp=base + timedelta(hours=(start - 1) + i),
                allocation=1000 * n,
                disk=disk,
            ),
        )


def _names(first, last, prefix="snap"):
    """Snapshot names ``{prefix}{first:03d}`` .. ``{prefix}{last:03d}`` inclusive."""
    return [f"{prefix}{n:03d}" for n in range(first, last + 1)]


def _simulate_commit(mock_state, names, vm="testvm"):
    """Remove committed snapshot names from state, as the blockcommit step would."""
    for name in names:
        assert mock_state.remove_snapshot(vm, name), f"snapshot {name} not in state"


def _state_has_no_collapse_key(mock_state) -> bool:
    """True when no persisted ``collapse_in_progress`` key exists anywhere."""
    return all(
        "collapse_in_progress" not in vm_state
        for vm_state in mock_state._state.values()
        if isinstance(vm_state, dict)
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Hysteresis mode selection
# ═══════════════════════════════════════════════════════════════════════════


def test_default_mode_is_hysteresis_single_run_collapse(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Default ``snapshot_retention_mode`` is hysteresis: grow to H, then
    collapse to L in a SINGLE run — no cap batching, no phase state."""
    # No explicit snapshot_retention_mode → VMConfig default "hysteresis".
    vm = make_vm_config(
        name="testvm",
        snapshot_chain_length=8,
        snapshot_preserve_min=3,
    )
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 8)

    with caplog.at_level(logging.DEBUG, logger="qsnap.core"):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # N == H (8): grow phase — nothing marked, every snapshot kept.
    assert result.remove == []
    assert result.keep == _names(1, 8)
    assert "collapse phase" not in caplog.text
    assert _state_has_no_collapse_key(mock_state)
    assert "[retention] testvm/vda: grow phase (N=8 <= threshold 8) — no commits" in caplog.text

    # Single-run collapse: N=9 > H=8 → all N-L=6 oldest marked at once.
    _record_snapshots(mock_state, 1, start=9)
    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == _names(1, 6)
    assert result.keep == _names(7, 9)
    assert _state_has_no_collapse_key(mock_state)


def test_hysteresis_mode_interprets_chain_length_as_threshold_floor(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Hysteresis: chain_length is the trigger H, preserve_min is the floor L.

    With H=72, L=24 and N=73 the engine is invoked with effective keep-count
    L (24) — NOT H — and the full N - L = 49 snapshots are marked (uncapped).
    """
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 73)

    with patch.object(
        mock_factory,
        "create_retention_engine",
        wraps=mock_factory.create_retention_engine,
    ) as engine_spy:
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # _evaluate_snapshot_retention first builds the VM-level policy, then the
    # hysteresis collapse branch re-invokes the factory with the floor policy.
    assert engine_spy.call_count == 2
    first_policy = engine_spy.call_args_list[0].args[0]
    floor_policy = engine_spy.call_args_list[1].args[0]
    assert isinstance(first_policy, RetentionPolicy)
    assert first_policy.chain_length == 72  # the outer VM policy (H)
    # The engine used for the decision gets keep-count = L (the floor), not H.
    assert isinstance(floor_policy, RetentionPolicy)
    assert floor_policy.chain_length == 24  # L, not the H=72 threshold
    assert floor_policy.preserve_min == 24  # floor trim passes L through
    # The FULL oldest N - L = 49 are marked — no cap truncation.
    assert result.remove == _names(1, 49)
    assert result.keep == _names(50, 73)


# ═══════════════════════════════════════════════════════════════════════════
#  Grow phase below the trigger threshold
# ═══════════════════════════════════════════════════════════════════════════


def test_chain_at_threshold_commits_nothing(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """N == H (72): grow phase — empty remove set, no blockcommit."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 72)

    with caplog.at_level(logging.DEBUG, logger="qsnap.core"):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == []
    assert result.keep == _names(1, 72)
    # No blockcommit command was issued during evaluation.
    assert mock_shell.call_history == []
    assert _state_has_no_collapse_key(mock_state)
    assert "[retention] testvm/vda: grow phase (N=72 <= threshold 72) — no commits" in caplog.text


def test_growth_phase_accumulates_without_commits(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Successive runs from N=30 to N=72: nothing is ever committed."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 30)

    with caplog.at_level(logging.DEBUG, logger="qsnap.core"):
        for added in (0, 20, 22):  # N = 30 → 50 → 72
            if added:
                _record_snapshots(
                    mock_state, added, start=len(mock_state.get_snapshots("testvm")) + 1
                )
            n = len(mock_state.get_snapshots("testvm"))
            result = core._evaluate_snapshot_retention(vm)
            assert result is not None
            assert result.remove == [], f"run with N={n} must not commit"
            assert result.keep == _names(1, n)
            assert "collapse phase" not in caplog.text
            assert _state_has_no_collapse_key(mock_state)
            assert (
                f"[retention] testvm/vda: grow phase (N={n} <= threshold 72) — no commits"
                in caplog.text
            )

    # Dry-run grow prediction: INFO log, and still no phase write.
    core.dry_run = True
    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == []
    assert _state_has_no_collapse_key(mock_state)
    assert "[dry-run] testvm/vda: grow phase (N=72 <= threshold 72) — no commits" in caplog.text


# ═══════════════════════════════════════════════════════════════════════════
#  Collapse trigger and floor
# ═══════════════════════════════════════════════════════════════════════════


def test_trigger_marks_all_oldest_n_minus_l(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """N=73 > H=72: the FULL oldest N - L = 49 are marked, newest 24 kept."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 73)

    result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == _names(1, 49)
    assert result.keep == _names(50, 73)
    assert _state_has_no_collapse_key(mock_state)


def test_trigger_collapse_merges_all_49_in_one_run(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """N=73 > H=72: ``_blockcommit_snapshots`` drives ONE lifecycle-manager
    call with the FULL 49-item merge set — a single bulk blockcommit."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(
        mock_factory,
        mock_state,
        mock_shell,
        vm,
        global_config=GlobalConfig(
            chain_verify_before_commit=False,
            chain_verify_after_commit=False,
        ),
    )
    _record_snapshots(mock_state, 73)

    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == _names(1, 49)

    with (
        patch("os.path.exists", return_value=True),
        patch.object(
            mock_factory._lifecycle_manager,
            "blockcommit",
            wraps=mock_factory._lifecycle_manager.blockcommit,
        ) as manager_spy,
        patch.object(core, "_refresh_domain_backing_store"),
    ):
        core._blockcommit_snapshots(vm, result)

    # Exactly ONE manager call carrying the whole uncapped merge set.
    assert manager_spy.call_count == 1, (
        f"the collapse must be a single bulk blockcommit, "
        f"got {manager_spy.call_count} manager calls"
    )
    args, kwargs = manager_spy.call_args
    merged = args[1]
    assert [s.name for s in merged] == _names(1, 49), (
        f"the single manager call must merge the full oldest N-L=49 set, "
        f"got {len(merged)} snapshots"
    )
    assert kwargs["disk"] == "vda"
    # The collapse converged to the floor: the newest 24 snapshots survive.
    assert [s.name for s in mock_state.get_snapshots("testvm")] == _names(50, 73)


def test_floor_snapshots_never_in_remove_set(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """The newest L snapshots are never marked — the remove set is the FULL
    N-L oldest set with the floor untouched."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 73)

    result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == _names(1, 49)  # full uncapped N - L set
    floor_snaps = set(_names(50, 73))
    assert set(result.remove).isdisjoint(floor_snaps)
    assert floor_snaps.issubset(set(result.keep))


def test_deferred_collapse_retriggers_naturally_without_phase(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A deferred collapse leaves N > H; the next run re-marks the identical
    oldest N - L set — no persisted phase is read or written."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 73)

    # Run 1: the trigger marks 49, but the commit is deferred — the
    # snapshot state is untouched (N stays 73 > H=72).
    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == _names(1, 49)
    assert len(mock_state.get_snapshots("testvm")) == 73
    assert _state_has_no_collapse_key(mock_state)

    # Run 2: N > H still holds → the identical oldest N - L set is marked
    # again, purely from the trigger condition.
    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == _names(1, 49)
    assert result.keep == _names(50, 73)
    assert _state_has_no_collapse_key(mock_state)


# ═══════════════════════════════════════════════════════════════════════════
#  Hysteresis observability
# ═══════════════════════════════════════════════════════════════════════════


def test_trigger_logs_collapse_initiation_info_line(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """The trigger emits the collapse-initiation INFO line naming vm, disk,
    merge count (49), current count (73), and floor (24)."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 73)

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        core._evaluate_snapshot_retention(vm)

    assert (
        "[retention] testvm/vda: collapse triggered (N=73, merging 49, floor=24) "
        "— single bulk blockcommit" in caplog.text
    )
    assert "collapse phase" not in caplog.text


# ═══════════════════════════════════════════════════════════════════════════
#  Mode branching and engine contract
# ═══════════════════════════════════════════════════════════════════════════


def test_hysteresis_uses_threshold_floor_not_steady_rule(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Hysteresis does NOT apply the steady count-based rule mid-band.

    With H=72, L=24 and N=50 the steady rule (chain_length=24) would remove
    26; hysteresis instead grows (empty remove).  Above H the FULL N - L
    collapse fires — the steady rule never applies.
    """
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 50)

    with caplog.at_level(logging.DEBUG, logger="qsnap.core"):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == []  # steady rule would remove 26 here
    assert result.keep == _names(1, 50)
    assert "[retention] testvm/vda: grow phase (N=50 <= threshold 72) — no commits" in caplog.text

    # Above the threshold (N=73 > H=72): the FULL N - L = 49 collapse fires.
    _record_snapshots(mock_state, 23, start=51)
    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == _names(1, 49)
    assert result.keep == _names(50, 73)
    assert _state_has_no_collapse_key(mock_state)


def test_hysteresis_collapse_respects_floor(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Uncapped collapse marks N - L oldest; the newest L stay put."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 100)

    result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == _names(1, 76)
    assert result.keep == _names(77, 100)


def test_steady_mode_branch_identical_to_legacy(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Steady mode reproduces the pre-change count-based result — the shared
    cap is gone but the keep/remove decision is unchanged."""
    vm = make_vm_config(
        name="testvm",
        snapshot_retention_mode="steady",
        snapshot_chain_length=24,
        snapshot_preserve_min=0,
    )
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 73)

    result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # Legacy: keep the newest chain_length=24, remove the oldest 49 excess.
    assert result.remove == _names(1, 49)
    assert result.keep == _names(50, 73)
    # Steady mode never touches the (removed) collapse phase.
    assert _state_has_no_collapse_key(mock_state)


def test_collapse_writes_no_phase_state(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """The collapse evaluates via the engine with keep-count L, produces the
    FULL N-L remove set, and never writes a ``collapse_in_progress`` key."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 73)

    from copy import deepcopy

    state_before = deepcopy(mock_state._state)

    with patch.object(
        mock_factory,
        "create_retention_engine",
        wraps=mock_factory.create_retention_engine,
    ) as engine_spy:
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # The engine invoked for the decision uses keep-count = L (24).
    floor_policy = engine_spy.call_args_list[-1].args[0]
    assert isinstance(floor_policy, RetentionPolicy)
    assert floor_policy.chain_length == 24
    assert floor_policy.preserve_min == 24
    # The full uncapped N - L = 49 set is marked.
    assert result.remove == _names(1, 49)
    # Evaluation is read-only with respect to the collapse: the state is
    # byte-identical and no phase key ever appears.
    assert deepcopy(mock_state._state) == state_before
    assert _state_has_no_collapse_key(mock_state)


def test_below_threshold_remove_set_empty(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """N < H: empty remove, grow log only — no phase marker, no commits."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 50)

    with caplog.at_level(logging.DEBUG, logger="qsnap.core"):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == []
    assert result.keep == _names(1, 50)
    assert "collapse phase" not in caplog.text
    assert _state_has_no_collapse_key(mock_state)
    assert "[retention] testvm/vda: grow phase (N=50 <= threshold 72) — no commits" in caplog.text
