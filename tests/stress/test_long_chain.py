"""Stress test: 50+ snapshot chain survives blockcommit with the default 48 floor.

This test creates a deep snapshot chain (55 levels) on a real libvirt VM
and verifies that ``blockcommit`` collapses ONLY the snapshots beyond the
newest 48 (``snapshot_preserve_min=48`` dominates the default
``chain_length=24`` under load), without corrupting data or breaking
backing-file references.

It then extends the surviving chain past the old hard-coded 64-iteration
broken-file walk cap (design D8 of blockcommit-recovery): after removing
a mid-chain overlay, the dynamic ``max(64, measured+2)`` walk bound must
still identify the missing file.

A second test (:func:`test_hysteresis_long_chain_capped_collapse`) proves
the hysteresis-snapshot-retention migration path: a deep chain switched to
hysteresis (H=64, L=48, cap=4) collapses over several capped prune cycles
with the ``collapse_in_progress`` phase persisting between cycles until the
chain reaches the floor (design "Migration Plan" step 2).

Marked ``@pytest.mark.stress`` — requires a libvirt environment with a
disposable test VM.
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import CommitIntent
from qsnap.shell.subprocess_shell import SubprocessShell
from tests.helpers import snapshot_create
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_state import InMemoryStateManager

#: Number of snapshots in the chain (must exceed the 48 floor).
_CHAIN_DEPTH = 55

#: Extra snapshots layered on top after the floor prune.  The surviving
#: chain is 48 overlays deep; adding this many more pushes the deepest
#: remaining overlay to depth 66 from the active layer — beyond the old
#: fixed 64-iteration walk cap.
_EXTRA_DEPTH = 18

# ── Hysteresis long-chain collapse (hysteresis-snapshot-retention) ───────
#: Deep-chain depth for the hysteresis migration proof — a "55+" chain that
#: exceeds the H=64 trigger threshold (66 > 64), mirroring the production
#: migration scenario (design "Migration Plan" step 2: N≈74 → capped runs).
_HYST_CHAIN_DEPTH = 66
#: Hysteresis trigger threshold H (``snapshot_chain_length``).
_HYST_THRESHOLD = 64
#: Hysteresis collapse floor L (``snapshot_preserve_min``).
_HYST_FLOOR = 48
#: Per-run commit cap (``GlobalConfig.max_commits_per_run``).
_HYST_CAP = 4
#: Upper bound of prune cycles to attempt: ceil((N−L)/cap)+1 headroom.
_HYST_MAX_CYCLES = (
    (_HYST_CHAIN_DEPTH - _HYST_FLOOR + _HYST_CAP - 1) // _HYST_CAP + 1
)


class _IntentJournalState(InMemoryStateManager):
    """InMemoryStateManager plus the commit-intent journal.

    Local stand-in while ``tests/mocks/mock_state.py`` (mocks-contracts
    group) lands the three ``IStateManager`` intent-journal methods in
    the same change; a thin pass-through once the upstream mock lands.
    """

    def set_commit_in_progress(
        self,
        vm_name: str,
        disk: str,
        snapshots: list[str],
        base: str,
        started_ts: str,
    ) -> None:
        if vm_name not in self._state:
            self._state[vm_name] = {}
        records = self._state[vm_name].setdefault("commit_in_progress", [])
        records[:] = [r for r in records if r.disk != disk]
        records.append(
            CommitIntent(
                disk=disk,
                snapshots=list(snapshots),
                base=base,
                started_ts=started_ts,
            )
        )

    def get_commit_in_progress(self, vm_name: str) -> list[CommitIntent]:
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return []
        return list(vm_state.get("commit_in_progress", []))

    def clear_commit_in_progress(self, vm_name: str, disk: str) -> None:
        vm_state = self._state.get(vm_name)
        if vm_state is None:
            return
        records = vm_state.get("commit_in_progress", [])
        vm_state["commit_in_progress"] = [r for r in records if r.disk != disk]


def _vm_is_running(shell: SubprocessShell, vm_name: str) -> bool:
    result = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    return result.success and "running" in result.stdout.lower()


def _qemu_img_check(shell: SubprocessShell, path: Path) -> bool:
    """Return True when ``qemu-img check`` reports no errors on *path*."""
    result = shell.run(
        ["qemu-img", "check", "--force-share", str(path)],
        timeout=120,
    )
    return result.success and "No errors were found" in result.stdout


@pytest.mark.stress
@pytest.mark.timeout(7200)
def test_long_chain_default_preserve_min_48(stress_env):
    """55 snapshots with the default 48 floor: only the oldest 7 commit.

    1. Start the VM and create 55 external snapshots (recorded with
       ``disk="vda"`` — the per-disk attribution the config facade
       resolves; see fault-tolerance-hardening D13).
    2. Build Core with the facade-resolved defaults (chain_length=24,
       preserve_min=48) and evaluate retention: exactly 7 oldest
       snapshots are removable (55 − 48).
    3. Run ``core.prune()`` → the 7 oldest are blockcommitted (files
       deleted), the newest 48 survive, the deferred queue stays empty.
    4. The active layer's backing chain is intact (``qemu-img check``
       passes) and the VM is still running.
    5. Depth-fix proof (blockcommit-recovery D8): layer 18 more
       snapshots on top (chain = 66 overlays + base), delete the deepest
       remaining overlay (66 layers below the active layer — beyond the
       old fixed 64-iteration walk cap), and assert the broken-file walk
       still identifies the missing file via the dynamic
       ``max(64, measured+2)`` bound; ``check`` reports the chain broken.
    """
    shell: SubprocessShell = stress_env["shell"]
    vm_name: str = stress_env["vm_name"]
    base_image: Path = stress_env["base_image"]
    snapshot_dir: Path = stress_env["snapshot_dir"]
    target_dir: Path = stress_env["target_dir"]
    tmpdir: Path = stress_env["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    state = _IntentJournalState()
    snapshots = []
    for i in range(_CHAIN_DEPTH):
        hex_sfx = secrets.token_hex(3)
        snap = snapshot_create(
            shell,
            vm_name,
            f"{vm_name}.chain-{i:03d}-{hex_sfx}",
            "vda",
            snapshot_dir,
            base_image,
        )
        state.record_snapshot(vm_name, snap)
        snapshots.append(snap)
        time.sleep(0.35)  # unique timestamps

    assert len(state.get_snapshots(vm_name)) == _CHAIN_DEPTH, (
        f"Expected {_CHAIN_DEPTH} snapshots, got {len(state.get_snapshots(vm_name))}"
    )
    snap_paths_before = {s.name: s.path for s in snapshots}

    # Core with facade-resolved defaults: chain_length=24, preserve_min=48.
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=24,
        snapshot_preserve_min=48,
        lifecycle_mode="virsh",
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
            )
        ],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "long_chain.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Retention: the floor caps the remove list at 55 − 48 = 7 oldest.
    retention = core._evaluate_snapshot_retention(vm_config)
    assert retention is not None, "Retention result should not be None"
    assert len(retention.remove) == _CHAIN_DEPTH - 48, (
        f"Expected exactly {_CHAIN_DEPTH - 48} removable snapshots "
        f"(55 − 48 floor), got {len(retention.remove)}"
    )
    sorted_snaps = sorted(snapshots, key=lambda s: s.timestamp)
    removed_names = {s.name for s in sorted_snaps[: _CHAIN_DEPTH - 48]}
    assert set(retention.remove) == removed_names, (
        "Only the OLDEST 7 snapshots may be removed (floor dominates chain_length)"
    )

    # Prune → blockcommit of exactly the 7 oldest.
    result = core.prune(vm_name)
    assert result.results[0].success, f"Prune failed: {result.results[0].error}"

    for snap in sorted_snaps[: _CHAIN_DEPTH - 48]:
        assert not snap_paths_before[snap.name].exists(), (
            f"Oldest snapshot beyond the 48 floor should be committed: "
            f"{snap_paths_before[snap.name]}"
        )
    for snap in sorted_snaps[_CHAIN_DEPTH - 48 :]:
        assert snap_paths_before[snap.name].exists(), (
            f"Newest 48 snapshots must survive the floor: {snap_paths_before[snap.name]}"
        )

    # Deferred queue empty (nothing beyond the floor was deferred).
    assert state.get_deferred_operations(vm_name) == [], (
        "No deferred blockcommit entries may remain after the floor prune"
    )

    # Chain intact: qemu-img check passes on the active layer.
    active = sorted_snaps[-1].path
    assert _qemu_img_check(shell, active), (
        f"qemu-img check must pass on the active layer after blockcommit: {active}"
    )

    # VM still running after the live blockcommit.
    assert _vm_is_running(shell, vm_name), "VM should still be running"

    # ──────────────────────────────────────────────────────────────────
    # Depth-fix proof (blockcommit-recovery design D8): the broken-file
    # walk must identify a missing file beyond the old hard-coded 64
    # iteration cap on a REAL chain.
    #
    # After the floor prune, 48 overlays remain.  Layering 18 more
    # snapshots on top pushes the DEEPEST remaining overlay (the former
    # s7, now repointed at the base by the prune) to depth 66 from the
    # active layer — deeper than 64.  Deleting that file makes the
    # ``qemu-img info --backing-chain`` scan fail, so the walk runs with
    # the dynamic bound ``max(64, measured + 2)`` (measured from the 66
    # state records ⇒ bound 76).  With the old fixed cap of 64 the walk
    # would exhaust its iterations and return ``None``.
    # ──────────────────────────────────────────────────────────────────
    for i in range(_CHAIN_DEPTH, _CHAIN_DEPTH + _EXTRA_DEPTH):
        hex_sfx = secrets.token_hex(3)
        snap = snapshot_create(
            shell,
            vm_name,
            f"{vm_name}.chain-{i:03d}-{hex_sfx}",
            "vda",
            snapshot_dir,
            base_image,
        )
        state.record_snapshot(vm_name, snap)
        time.sleep(0.35)

    deep_snaps = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    assert len(deep_snaps) == _CHAIN_DEPTH + _EXTRA_DEPTH - 7, (
        f"Expected {_CHAIN_DEPTH + _EXTRA_DEPTH - 7} snapshots in state, got {len(deep_snaps)}"
    )

    # The oldest remaining overlay sits at depth 66 from the active layer.
    deepest = deep_snaps[0]
    deepest_path = deepest.path
    assert deepest_path.exists(), f"Deepest overlay must exist before truncation: {deepest_path}"
    active_top = deep_snaps[-1].path
    assert _qemu_img_check(shell, active_top), (
        "Chain must be healthy before the deliberate truncation"
    )

    # Deliberately truncate the chain: remove the oldest remaining overlay.
    rm_result = shell.run(["rm", "-f", str(deepest_path)], timeout=10, check=True)
    assert rm_result.success and not deepest_path.exists(), (
        f"Deepest overlay must be removed: {deepest_path}"
    )

    # (a) ``check`` surfaces the broken chain end-to-end (read-only, VM
    #     still running — matches the plan's "run `check`").
    check_results = core.check(vm_name)
    check_result = check_results[vm_name]
    assert check_result.status == "broken", (
        f"check must report the truncated chain as broken, got {check_result.status!r}"
    )
    assert len(check_result.broken_snapshots) > 0, "check must report at least one broken snapshot"

    # (b) The dynamic-bound walk identifies the missing file.  The VM is
    #     shut off first: the walk's per-file ``qemu-img info`` call does
    #     not pass ``--force-share`` and therefore cannot read the active
    #     layer of a running VM (known source limitation — tracked in the
    #     integration-stress QA report).
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)
    verify = core._verify_backing_chain(vm_config, "vda")
    assert verify.success is False, "A truncated chain must fail verification"
    assert verify.broken_file is not None, (
        "broken_file must be identified even beyond 64 layers (dynamic walk bound)"
    )
    assert os.path.realpath(verify.broken_file) == os.path.realpath(str(deepest_path)), (
        f"Walk must identify the deleted overlay {deepest_path}, got {verify.broken_file}"
    )

    shell.run(["virsh", "destroy", vm_name], timeout=30)


@pytest.mark.stress
@pytest.mark.timeout(7200)
def test_hysteresis_long_chain_capped_collapse(stress_env):
    """Hysteresis migration-from-deep-chain proof: capped multi-cycle collapse.

    This is the stress counterpart of the design's "Migration Plan" step 2:
    a host with a deep chain flips ONE VM to ``hysteresis`` and watches the
    collapse converge to the floor over several capped runs.

    1. Build a 66-overlay chain (the "55+" depth) while steady, recorded
       per-disk as ``vda``.
    2. Switch to hysteresis with H=64 (trigger threshold), L=48 (collapse
       floor) and ``max_commits_per_run=4`` (per-run cap).
    3. Run up to ``ceil((N−L)/cap)+1`` prune cycles; assert each cycle
       commits exactly ``min(cap, N−L)`` snapshots (≤ 4), the
       ``collapse_in_progress`` phase persists with ``vda`` while
       ``N > L`` and clears at the floor.
    4. The final chain sits at the floor (48 overlays), every committed
       overlay file is gone, the newest 48 survive, ``qemu-img check``
       passes on the active layer, and the VM is still running.
    """
    shell: SubprocessShell = stress_env["shell"]
    vm_name: str = stress_env["vm_name"]
    base_image: Path = stress_env["base_image"]
    snapshot_dir: Path = stress_env["snapshot_dir"]
    target_dir: Path = stress_env["target_dir"]
    tmpdir: Path = stress_env["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    state = _IntentJournalState()

    # 1. Build the deep chain (55+, exceeding the H=64 trigger) while
    #    steady — no collapse phase is ever written during construction.
    snapshots = []
    for i in range(_HYST_CHAIN_DEPTH):
        hex_sfx = secrets.token_hex(3)
        snap = snapshot_create(
            shell,
            vm_name,
            f"{vm_name}.hyst-{i:03d}-{hex_sfx}",
            "vda",
            snapshot_dir,
            base_image,
        )
        state.record_snapshot(vm_name, snap)
        snapshots.append(snap)
        time.sleep(0.35)  # unique timestamps

    assert len(state.get_snapshots(vm_name)) == _HYST_CHAIN_DEPTH, (
        f"Expected {_HYST_CHAIN_DEPTH} snapshots, got {len(state.get_snapshots(vm_name))}"
    )
    snap_paths_before = {s.name: s.path for s in snapshots}

    # 2. Switch to hysteresis H=64, L=48, cap=4.
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=_HYST_THRESHOLD,
        snapshot_preserve_min=_HYST_FLOOR,
        snapshot_retention_mode="hysteresis",
        lifecycle_mode="virsh",
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
            )
        ],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        global_config=GlobalConfig(max_commits_per_run=_HYST_CAP),
        config_path=tmpdir / "hysteresis_long_chain.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # No collapse phase before the first trigger fires.
    assert state.get_collapse_in_progress(vm_name) == [], (
        "No collapse phase may exist before the first capped prune"
    )

    # 3. Capped collapse cycles: each commits min(cap, N−L) oldest
    #    snapshots; the phase persists between cycles until the floor.
    committed_total = 0
    for cycle in range(1, _HYST_MAX_CYCLES + 1):
        n_before = len(state.get_snapshots(vm_name))
        if n_before <= _HYST_FLOOR:
            break
        result = core.prune(vm_name)
        assert result.success, (
            f"cycle {cycle}: prune failed: "
            f"{result.results[0].error if result.results else 'no VM result'}"
        )
        committed = [a for a in result.actions if a.action == "snapshot_delete"]
        expected = min(_HYST_CAP, n_before - _HYST_FLOOR)
        assert len(committed) == expected, (
            f"cycle {cycle}: expected exactly {expected} commit(s) "
            f"(cap {_HYST_CAP}, N={n_before}, L={_HYST_FLOOR}), got {len(committed)}"
        )
        assert len(committed) <= _HYST_CAP, (
            f"cycle {cycle}: per-run cap violated: {len(committed)} > {_HYST_CAP}"
        )
        committed_total += len(committed)

        n_after = len(state.get_snapshots(vm_name))
        phase = state.get_collapse_in_progress(vm_name)
        if n_after > _HYST_FLOOR:
            assert "vda" in phase, (
                f"cycle {cycle}: collapse phase must persist while "
                f"N={n_after} > L={_HYST_FLOOR}, phase={phase}"
            )
        else:
            assert "vda" not in phase, (
                f"cycle {cycle}: collapse phase must clear at the floor "
                f"(N={n_after}), phase={phase}"
            )

    # 4. Final chain sits at the floor L=48.
    surviving = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    assert len(surviving) == _HYST_FLOOR, (
        f"Final chain must sit at the floor {_HYST_FLOOR}, got {len(surviving)}"
    )
    assert committed_total == _HYST_CHAIN_DEPTH - _HYST_FLOOR, (
        f"Total committed must be {_HYST_CHAIN_DEPTH - _HYST_FLOOR} "
        f"(N−L), got {committed_total}"
    )

    for snap in snapshots[: _HYST_CHAIN_DEPTH - _HYST_FLOOR]:
        assert not snap_paths_before[snap.name].exists(), (
            f"Collapsed overlay beyond the floor should be committed: "
            f"{snap_paths_before[snap.name]}"
        )
    for snap in snapshots[_HYST_CHAIN_DEPTH - _HYST_FLOOR :]:
        assert snap_paths_before[snap.name].exists(), (
            f"Newest {_HYST_FLOOR} snapshots must survive the collapse: "
            f"{snap_paths_before[snap.name]}"
        )

    # 5. Chain intact and VM healthy after the multi-cycle collapse.
    active = surviving[-1].path
    assert _qemu_img_check(shell, active), (
        f"qemu-img check must pass on the active layer after the collapse: {active}"
    )
    assert _vm_is_running(shell, vm_name), "VM should still be running"
    assert state.get_deferred_operations(vm_name) == [], (
        "No deferred blockcommit entries may remain after the collapse"
    )
    assert state.get_collapse_in_progress(vm_name) == [], (
        "Collapse phase must be cleared once the floor is reached"
    )

    shell.run(["virsh", "destroy", vm_name], timeout=30)
