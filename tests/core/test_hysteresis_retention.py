"""Core hysteresis snapshot retention tests (retention-unit group).

Covers the ``hysteresis-snapshot-retention`` change: grow-to-threshold /
collapse-to-floor retention in ``snapshot_retention_mode = "hysteresis"``
with a persisted ``collapse_in_progress`` phase and the shared
``max_commits_per_run`` per-run cap.

All tests drive the unit seam ``Core._evaluate_snapshot_retention`` with
``MockVMModuleFactory`` + ``InMemoryStateManager`` and a real
``TimeBasedRetention`` engine where count-based keep/remove decisions are
required — zero real virsh/qemu-img calls (TESTING.md).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

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


def _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=None):
    """Build Core with a MockConfigFacade; ``max_commits_per_run=None`` uses the
    default (12), an explicit int pins the cap (0 = unlimited)."""
    global_cfg = (
        GlobalConfig()
        if max_commits_per_run is None
        else GlobalConfig(max_commits_per_run=max_commits_per_run)
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
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


# ═══════════════════════════════════════════════════════════════════════════
#  Hysteresis mode selection
# ═══════════════════════════════════════════════════════════════════════════


def test_default_hysteresis_mode_no_phase_state_written(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Default ``hysteresis`` mode: grow phase at the threshold, no collapse writes."""
    # No explicit snapshot_retention_mode → resolves to the production
    # default "hysteresis": chain_length is the trigger threshold H, and
    # preserve_min the collapse floor L.
    vm = make_vm_config(
        name="testvm",
        snapshot_chain_length=8,
        snapshot_preserve_min=3,
    )
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=0)
    _record_snapshots(mock_state, 8)

    with caplog.at_level(logging.DEBUG, logger="qsnap.core"):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # N == H (8): grow phase — nothing marked, every snapshot kept.
    assert result.remove == []
    assert result.keep == _names(1, 8)
    # No collapse-phase state is ever written while growing.
    assert mock_state.get_collapse_in_progress("testvm") == []
    assert "collapse phase" not in caplog.text
    assert (
        "[retention] testvm/vda: grow phase (N=8 <= threshold 8) — no commits"
        in caplog.text
    )


def test_hysteresis_mode_interprets_chain_length_as_threshold_floor(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Hysteresis: chain_length is the trigger H, preserve_min is the floor L.

    With H=72, L=24 and N=73 the engine is invoked with effective keep-count
    L (24) — NOT H — and N - L = 49 snapshots are marked (pre-cap).
    """
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=0)
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
    # Oldest N - L = 49 marked for commit (pre-cap, cap pinned off).
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
    """N == H (72): grow phase — empty remove set, no phase, no blockcommit."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=0)
    _record_snapshots(mock_state, 72)

    with caplog.at_level(logging.DEBUG, logger="qsnap.core"):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == []
    assert result.keep == _names(1, 72)
    assert mock_state.get_collapse_in_progress("testvm") == []
    # No blockcommit command was issued during evaluation.
    assert mock_shell.call_history == []
    assert (
        "[retention] testvm/vda: grow phase (N=72 <= threshold 72) — no commits"
        in caplog.text
    )


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
                _record_snapshots(mock_state, added, start=len(mock_state.get_snapshots("testvm")) + 1)
            n = len(mock_state.get_snapshots("testvm"))
            result = core._evaluate_snapshot_retention(vm)
            assert result is not None
            assert result.remove == [], f"run with N={n} must not commit"
            assert result.keep == _names(1, n)
            assert mock_state.get_collapse_in_progress("testvm") == []
            assert "collapse phase" not in caplog.text
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
    assert mock_state.get_collapse_in_progress("testvm") == []
    assert (
        "[dry-run] testvm/vda: grow phase (N=72 <= threshold 72) — no commits"
        in caplog.text
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Collapse trigger and floor
# ═══════════════════════════════════════════════════════════════════════════


def test_trigger_marks_oldest_n_minus_l_before_cap(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """N=73 > H=72, cap off: the oldest N - L = 49 are marked, newest 24 kept."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=0)
    _record_snapshots(mock_state, 73)

    result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == _names(1, 49)
    assert result.keep == _names(50, 73)
    assert mock_state.get_collapse_in_progress("testvm") == ["vda"]


def test_floor_snapshots_never_in_remove_set(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """The newest L snapshots are never marked, even when the cap truncates."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=12)
    _record_snapshots(mock_state, 73)

    result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == _names(1, 12)
    floor_snaps = set(_names(50, 73))
    assert set(result.remove).isdisjoint(floor_snaps)
    assert floor_snaps.issubset(set(result.keep))


# ═══════════════════════════════════════════════════════════════════════════
#  Persisted collapse phase
# ═══════════════════════════════════════════════════════════════════════════


def test_phase_persists_after_capped_run_continues_next_run(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A capped run keeps the phase; the next run continues collapsing below H."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=12)
    _record_snapshots(mock_state, 100)

    # Run 1: trigger fires, 12 oldest marked, phase persisted.
    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == _names(1, 12)
    assert mock_state.get_collapse_in_progress("testvm") == ["vda"]
    _simulate_commit(mock_state, result.remove)  # N → 88

    # Run 2: phase active → collapse continues.
    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == _names(13, 24)
    assert mock_state.get_collapse_in_progress("testvm") == ["vda"]
    _simulate_commit(mock_state, result.remove)  # N → 76

    # Run 3.
    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == _names(25, 36)
    _simulate_commit(mock_state, result.remove)  # N → 64

    # Run 4: N=64 is BELOW the H=72 trigger, but the persisted phase keeps
    # collapsing without requiring N > H.
    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert len(mock_state.get_snapshots("testvm")) == 64
    assert result.remove == _names(37, 48)
    assert mock_state.get_collapse_in_progress("testvm") == ["vda"]


def test_phase_cleared_when_floor_reached(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """After the chain converges to the floor, the phase is cleared."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=0)
    _record_snapshots(mock_state, 100)

    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == _names(1, 76)
    assert mock_state.get_collapse_in_progress("testvm") == ["vda"]
    _simulate_commit(mock_state, result.remove)  # N → 24 (floor)

    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == []
    assert result.keep == _names(77, 100)
    assert mock_state.get_collapse_in_progress("testvm") == []
    assert mock_shell.call_history == []  # nothing committed at/below the floor


def test_phase_persisted_before_first_blockcommit(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """The phase marker is written during evaluation — before any commit command.

    Evaluation precedes the blockcommit step (design D2); the marker must be
    durable before the first ``virsh blockcommit`` is invoked.  Asserting the
    shell saw zero commands while the phase is already persisted pins that
    ordering at the unit seam.
    """
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 73)
    assert mock_shell.call_history == []

    with patch.object(
        mock_state,
        "set_collapse_in_progress",
        wraps=mock_state.set_collapse_in_progress,
    ) as set_spy:
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    set_spy.assert_called_once_with("testvm", "vda")
    assert mock_state.get_collapse_in_progress("testvm") == ["vda"]
    # No shell command (no blockcommit) was issued while the marker was set.
    assert mock_shell.call_history == []


def test_defensive_phase_clear_on_external_shrink(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Phase active but N <= L (external shrink): phase cleared, nothing marked."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    mock_state.set_collapse_in_progress("testvm", "vda")
    _record_snapshots(mock_state, 20)  # below the floor (operator restore / healing)

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == []
    assert result.keep == _names(1, 20)
    assert mock_state.get_collapse_in_progress("testvm") == []
    assert (
        "[retention] testvm/vda: collapse phase complete (N=20, floor=24)"
        in caplog.text
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Per-run commit cap
# ═══════════════════════════════════════════════════════════════════════════


def test_cap_truncates_collapse_keeps_oldest(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Cap 12 over a 49-item remove set keeps the 12 OLDEST snapshots."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=12)
    _record_snapshots(mock_state, 73)

    result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == _names(1, 12)  # oldest prefix, capped
    assert result.keep == _names(50, 73)  # newest floor entries untouched


def test_cap_zero_unlimited(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """max_commits_per_run = 0 → all 49 marked in the same run."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=0)
    _record_snapshots(mock_state, 73)

    result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == _names(1, 49)
    assert result.keep == _names(50, 73)


def test_cap_never_breaks_floor(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Truncation keeps the floor invariant: the newest L are never marked."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=12)
    _record_snapshots(mock_state, 100)

    result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == _names(1, 12)
    floor_snaps = set(_names(77, 100))
    assert set(result.remove).isdisjoint(floor_snaps)
    assert floor_snaps.issubset(set(result.keep))


# ═══════════════════════════════════════════════════════════════════════════
#  Hysteresis observability
# ═══════════════════════════════════════════════════════════════════════════


def test_trigger_logs_collapse_start_info(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """The trigger emits the collapse-started INFO line naming vm, disk, counts."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 73)

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        core._evaluate_snapshot_retention(vm)

    assert (
        "[retention] testvm/vda: collapse phase started (N=73, merging 49, floor=24)"
        in caplog.text
    )
    assert "collapse phase active" not in caplog.text
    assert "collapse phase complete" not in caplog.text


# ═══════════════════════════════════════════════════════════════════════════
#  Mode branching and engine contract
# ═══════════════════════════════════════════════════════════════════════════


def test_hysteresis_uses_threshold_floor_phase_not_steady_rule(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Hysteresis does NOT apply the steady count-based rule mid-band.

    With H=72, L=24 and N=50 the steady rule (chain_length=24) would remove
    26; hysteresis instead grows (empty remove) until the phase is active,
    then collapses to the floor.
    """
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=0)
    _record_snapshots(mock_state, 50)

    with caplog.at_level(logging.DEBUG, logger="qsnap.core"):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == []  # steady rule would remove 26 here
    assert result.keep == _names(1, 50)
    assert (
        "[retention] testvm/vda: grow phase (N=50 <= threshold 72) — no commits"
        in caplog.text
    )

    # Phase active → collapse to the floor despite N (50) being below H (72).
    mock_state.set_collapse_in_progress("testvm", "vda")
    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == _names(1, 26)
    assert result.keep == _names(27, 50)


def test_hysteresis_collapse_respects_floor(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Uncapped collapse marks N - L oldest; the newest L stay put."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=0)
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
    """Steady mode (cap off) reproduces the pre-change count-based result."""
    vm = make_vm_config(
        name="testvm",
        snapshot_retention_mode="steady",
        snapshot_chain_length=24,
        snapshot_preserve_min=0,
    )
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=0)
    _record_snapshots(mock_state, 73)

    result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # Legacy: keep the newest chain_length=24, remove the oldest 49 excess.
    assert result.remove == _names(1, 49)
    assert result.keep == _names(50, 73)
    # Steady mode never touches the collapse phase.
    assert mock_state.get_collapse_in_progress("testvm") == []


def test_collapse_evaluation_engine_floor_and_cap(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Collapse evaluates via the pure engine with keep-count L, then caps."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=12)
    _record_snapshots(mock_state, 73)

    with patch.object(
        mock_factory,
        "create_retention_engine",
        wraps=mock_factory.create_retention_engine,
    ) as engine_spy:
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    # Outer VM policy + the collapse branch's floor policy.
    assert engine_spy.call_count == 2
    floor_policy = engine_spy.call_args_list[-1].args[0]
    assert isinstance(floor_policy, RetentionPolicy)
    assert floor_policy.chain_length == 24  # effective keep-count = floor L
    assert floor_policy.preserve_min == 24
    # Final remove set is the 12 oldest (cap applied after floor trim).
    assert result.remove == _names(1, 12)
    # Phase persisted for the commit step.
    assert mock_state.get_collapse_in_progress("testvm") == ["vda"]


def test_below_threshold_inactive_phase_no_phase_write(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """N < H with no phase: empty remove, no marker written, grow log only."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    _record_snapshots(mock_state, 50)

    with caplog.at_level(logging.DEBUG, logger="qsnap.core"):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == []
    assert result.keep == _names(1, 50)
    assert mock_state.get_collapse_in_progress("testvm") == []
    assert "collapse phase" not in caplog.text
    assert (
        "[retention] testvm/vda: grow phase (N=50 <= threshold 72) — no commits"
        in caplog.text
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Collapse phase completion handling
# ═══════════════════════════════════════════════════════════════════════════


def test_collapse_complete_info_logged_at_floor(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Floor reached: phase cleared and the collapse-complete INFO line emitted."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm)
    mock_state.set_collapse_in_progress("testvm", "vda")
    _record_snapshots(mock_state, 24)

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == []
    assert result.keep == _names(1, 24)
    assert mock_state.get_collapse_in_progress("testvm") == []
    assert (
        "[retention] testvm/vda: collapse phase complete (N=24, floor=24)"
        in caplog.text
    )


def test_cap_reached_keeps_phase_logs_continuation(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Cap reached: phase stays and the continuation INFO line names the counts."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=12)
    mock_state.set_collapse_in_progress("testvm", "vda")
    _record_snapshots(mock_state, 100)

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == _names(1, 12)
    assert mock_state.get_collapse_in_progress("testvm") == ["vda"]
    assert (
        "[retention] testvm/vda: collapse phase active "
        "(N=100, committing 12 of 76, floor=24)" in caplog.text
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Risk coverage: migration, crash windows, deferred commits, matrix
# ═══════════════════════════════════════════════════════════════════════════


def test_migration_deep_chain_converges_over_capped_runs(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Migration from a deep chain: 74 snaps, H=72, L=24, cap=12 → floor in 5 runs.

    Per-run cap is 12, progress is monotonic, the phase is held until the
    floor, and the chain converges to N == L (50 merges total).
    """
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=12)
    _record_snapshots(mock_state, 74)

    expected_counts = [12, 12, 12, 12, 2]
    committed_total = 0
    for run, expected in enumerate(expected_counts, start=1):
        result = core._evaluate_snapshot_retention(vm)
        assert result is not None
        assert len(result.remove) == expected, (
            f"run {run}: expected {expected} commits, got {len(result.remove)}"
        )
        # The remove set is always the OLDEST remaining prefix.
        remaining = mock_state.get_snapshots("testvm")
        assert result.remove == [s.name for s in remaining[:expected]]
        _simulate_commit(mock_state, result.remove)
        committed_total += expected
        n_after = len(mock_state.get_snapshots("testvm"))
        if n_after > 24:
            assert mock_state.get_collapse_in_progress("testvm") == ["vda"], (
                f"run {run}: phase must persist while N={n_after} > floor"
            )

    assert committed_total == 50
    assert len(mock_state.get_snapshots("testvm")) == 24  # converged to the floor

    # Floor reached: the next evaluation defensively clears the phase.
    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == []
    assert mock_state.get_collapse_in_progress("testvm") == []


def test_phase_resumes_after_crash_between_set_and_commit(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A crash after the marker write but before commit resumes next run.

    With the phase persisted and N=50 (< H=72), the collapse continues to the
    floor on the next run — the phase drives evaluation, not N > H.
    """
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=0)
    _record_snapshots(mock_state, 50)
    # Crash window: marker written, blockcommit never executed, state unchanged.
    mock_state.set_collapse_in_progress("testvm", "vda")

    result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    assert result.remove == _names(1, 26)  # N - L = 26, cap pinned off
    assert mock_state.get_collapse_in_progress("testvm") == ["vda"]
    assert mock_shell.call_history == []  # evaluation alone performs no commits


def test_phase_remains_after_deferred_commit(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Deferred/failed commits leave the phase intact for retry by the next run."""
    vm = _hysteresis_vm(make_vm_config, H=72, L=24)
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=0)
    mock_state.set_collapse_in_progress("testvm", "vda")
    _record_snapshots(mock_state, 50)

    # Run 1: collapse scheduled, but the commit is deferred (state unchanged).
    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == _names(1, 26)
    assert mock_state.get_collapse_in_progress("testvm") == ["vda"]

    # Run 2: nothing was committed; the phase is still active and retries.
    result = core._evaluate_snapshot_retention(vm)
    assert result is not None
    assert result.remove == _names(1, 26)
    assert mock_state.get_collapse_in_progress("testvm") == ["vda"]


@pytest.mark.parametrize("mode", ["steady", "hysteresis"])
@pytest.mark.parametrize("phase_active", [False, True])
@pytest.mark.parametrize("cap", [0, 12])
def test_hysteresis_mode_phase_cap_matrix(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    mode,
    phase_active,
    cap,
):
    """Mode × phase × cap is orthogonal: steady never reads the phase, hysteresis
    collapses when the phase is active (even below H), and the cap truncates."""
    vm = make_vm_config(
        name="testvm",
        snapshot_retention_mode=mode,
        snapshot_chain_length=72,  # steady keep-count / hysteresis threshold H
        snapshot_preserve_min=24,  # steady floor / hysteresis floor L
    )
    mock_factory._retention_engine = TimeBasedRetention(RetentionPolicy())
    core = _build_core(mock_factory, mock_state, mock_shell, vm, max_commits_per_run=cap)
    _record_snapshots(mock_state, 50)  # N=50: below H=72, above L=24
    if phase_active:
        mock_state.set_collapse_in_progress("testvm", "vda")

    result = core._evaluate_snapshot_retention(vm)

    assert result is not None
    if mode == "steady":
        # Steady keeps the newest 72 of 50 → nothing removed; phase untouched.
        assert result.remove == []
        assert result.keep == _names(1, 50)
        assert mock_state.get_collapse_in_progress("testvm") == (["vda"] if phase_active else [])
    elif phase_active:
        # Hysteresis + active phase → collapse to the floor, then capped.
        # The engine keeps exactly the newest L=24 floor entries; the cap
        # truncates the remove list to the oldest entries, so the middle
        # postponed items appear in neither list.
        expected_remove = min(50 - 24, cap) if cap > 0 else 26
        assert result.remove == _names(1, expected_remove)
        assert result.keep == _names(27, 50)
        assert len(result.keep) == 24
        assert mock_state.get_collapse_in_progress("testvm") == ["vda"]
    else:
        # Hysteresis + inactive phase + N <= H → grow, no commits, no marker.
        assert result.remove == []
        assert result.keep == _names(1, 50)
        assert mock_state.get_collapse_in_progress("testvm") == []
