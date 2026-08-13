"""Integration tests for hysteresis snapshot retention on REAL libvirt.

Validates against a disposable test VM (``test_vm`` fixture) what the
mock-based core suite can only approximate:

- ``test_hysteresis_multi_run_collapse_real_chain`` — end-to-end
  grow-to-threshold / collapse-to-floor with the per-run commit cap on a
  real backing chain: exactly ``cap`` commits per run, the persisted
  ``collapse_in_progress`` phase observable in the JSON state file after
  each capped run, convergence to the floor ``L`` after
  ``ceil((N-L)/cap)`` runs, backing-chain integrity, and re-trigger after
  the chain grows past ``H`` again.
- ``test_hysteresis_default_mode_no_phase_below_threshold_real_chain`` —
  the default ``"hysteresis"`` mode below the trigger threshold grows:
  no blockcommit happens and the state file NEVER contains the
  ``collapse_in_progress`` key.
- ``test_hysteresis_dry_run_zero_mutation_real_chain`` — dry-run with a
  persisted phase + deep chain: state-file and snapshot-file bytes are
  identical before/after, no ``virsh blockcommit`` executes, no lifecycle
  manager is created, and the predicted blockcommit batch names exactly
  the oldest ``min(N-L, cap)`` snapshots.

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  Run only when explicitly requested::

    poetry run pytest tests/integration/test_hysteresis_retention.py -v -m integration
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.interfaces.shell import IShell
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import ShellResult, SnapshotInfo
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.state.json_manager import JsonStateManager
from tests.mocks.mock_config import MockConfigFacade

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _build_core(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    target_dir: Path,
    state_dir: Path,
    *,
    mode: str | None = "hysteresis",
    h: int = 8,
    floor: int = 3,
    cap: int = 2,
    steady_chain_length: int = 4,
) -> tuple[Core, VMConfig, JsonStateManager]:
    """Build a Core instance with a JSON-backed state manager.

    The JSON state manager persists ``collapse_in_progress`` on disk so
    the tests can assert the phase marker in the state file itself.

    ``mode=None`` builds the VM WITHOUT an explicit
    ``snapshot_retention_mode`` so the production default
    (``"hysteresis"``) applies.
    """
    state = JsonStateManager(state_dir)
    if mode is None or mode == "hysteresis":
        vm_kwargs: dict[str, object] = {
            "name": vm_name,
            "disks": [DiskConfig(target="vda", base_image=base_image)],
            "snapshot_dir": snapshot_dir,
            "snapshot_chain_length": h,
            "snapshot_preserve_min": floor,
            "lifecycle_mode": "virsh",
            "targets": [
                TargetConfig(
                    path=target_dir,
                    compress=False,
                    verify="off",
                )
            ],
        }
        if mode == "hysteresis":
            vm_kwargs["snapshot_retention_mode"] = "hysteresis"
        vm_config = VMConfig(**vm_kwargs)
    else:
        vm_config = VMConfig(
            name=vm_name,
            disks=[DiskConfig(target="vda", base_image=base_image)],
            snapshot_dir=snapshot_dir,
            snapshot_chain_length=steady_chain_length,
            snapshot_preserve_min=None,
            snapshot_retention_mode="steady",
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
        global_config=GlobalConfig(max_commits_per_run=cap),
        vms=[vm_config],
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config, state


def _start_vm(shell: SubprocessShell, vm_name: str) -> None:
    """Start the test VM or skip when libvirt is not usable."""
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"


def _vm_is_running(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True if the VM is running."""
    result = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    return result.success and "running" in result.stdout.lower()


def _create_snapshots(core: Core, vm_config: VMConfig, count: int) -> list[SnapshotInfo]:
    """Create *count* external snapshots via Core, return oldest-first."""
    for i in range(count):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(1.1)
    return sorted(core._state.get_snapshots(vm_config.name), key=lambda s: s.timestamp)


def _state_file(state_dir: Path, vm_name: str) -> Path:
    """Return the JSON state file path for *vm_name*."""
    return state_dir / f"{vm_name}.json"


def _collapse_phase_from_file(state_dir: Path, vm_name: str) -> list[str]:
    """Return the ``collapse_in_progress`` list straight from the state file."""
    path = _state_file(state_dir, vm_name)
    data = json.loads(path.read_text())
    return list(data.get("collapse_in_progress", []))


def _committed_count(result) -> int:
    """Count the snapshot_delete actions (one per committed snapshot)."""
    return len([a for a in result.actions if a.action == "snapshot_delete"])


def _backing_chain_length(tip_path: Path, shell: SubprocessShell) -> int | None:
    """Return the backing-chain length from *tip_path* (base image included)."""
    result = shell.run(
        [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            str(tip_path),
        ],
        timeout=30,
    )
    if not result.success:
        return None
    chain = json.loads(result.stdout)
    return len(chain) if isinstance(chain, list) else None


class RecordingShell(IShell):
    """IShell wrapper that delegates to SubprocessShell and records commands.

    Used to assert that dry-run mode never issues ``virsh blockcommit``.
    """

    def __init__(self, delegate: SubprocessShell) -> None:
        self._delegate = delegate
        self._commands: list[list[str]] = []

    @property
    def commands(self) -> list[list[str]]:
        """The list of recorded command lists (in execution order)."""
        return list(self._commands)

    def run(self, cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        self._commands.append(list(cmd))
        return self._delegate.run(cmd, timeout, check)

    def run_with_stall_detection(
        self,
        cmd: list[str],
        output_file: Path | None = None,
        stall_timeout: int = 1800,
        check: bool = False,
    ) -> ShellResult:
        self._commands.append(list(cmd))
        return self._delegate.run_with_stall_detection(cmd, output_file, stall_timeout, check)

    def run_with_heartbeat(
        self,
        cmd: list[str],
        timeout: int,
        heartbeat_seconds: int,
        on_heartbeat: Callable[[int], None],
        check: bool = False,
    ) -> ShellResult:
        self._commands.append(list(cmd))
        return self._delegate.run_with_heartbeat(
            cmd, timeout, heartbeat_seconds, on_heartbeat, check
        )


# ──────────────────────────────────────────────────────────────────────
# Test 1: Multi-run capped collapse converges to the floor
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_hysteresis_multi_run_collapse_real_chain(test_vm, caplog):
    """End-to-end hysteresis collapse on a real VM with cap=2.

    1. Start VM, create 9 snapshots (N=9 > H=8) with H=8, L=3, cap=2.
    2. Run ``core.prune`` three times: each run commits exactly 2
       (the cap), the state file's ``collapse_in_progress`` contains
       ``vda`` after each capped run, and the chain converges to the
       floor N==3 after ``ceil((9-3)/2) == 3`` further runs.
    3. Verify backing-chain integrity with ``qemu-img check --force-share``
       and ``qemu-img info --backing-chain``.
    4. Grow the chain past H again and assert the collapse re-triggers.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]
    state_dir = tmpdir / "state"

    core, vm_config, state = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, state_dir, h=8, floor=3, cap=2
    )
    _start_vm(shell, vm_name)

    # Grow past the threshold: N = 9 > H = 8.
    snaps = _create_snapshots(core, vm_config, 9)
    assert len(snaps) == 9
    assert len(state.get_snapshots(vm_name)) == 9

    # ── Run 1: capped collapse 9 → 7 ─────────────────────────────────
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result = core.prune(vm_name)
    assert result.results[0].success, f"prune run 1 failed: {result.results[0].error}"
    committed = _committed_count(result)
    assert committed <= 2, f"Cap violated: {committed} commits in one run"
    assert committed == 2, f"Expected exactly cap=2 commits, got {committed}"
    assert len(state.get_snapshots(vm_name)) == 7
    phase = _collapse_phase_from_file(state_dir, vm_name)
    assert "vda" in phase, f"collapse_in_progress must contain vda after capped run, got {phase}"
    assert any("collapse phase started" in r.message for r in caplog.records), (
        "Trigger must log the collapse start"
    )

    # ── Run 2: capped collapse 7 → 5 ─────────────────────────────────
    result = core.prune(vm_name)
    assert result.results[0].success, f"prune run 2 failed: {result.results[0].error}"
    assert _committed_count(result) == 2
    assert len(state.get_snapshots(vm_name)) == 5
    phase = _collapse_phase_from_file(state_dir, vm_name)
    assert "vda" in phase, f"Phase must persist through capped run, got {phase}"

    # ── Run 3: collapse completes at the floor 5 → 3 ─────────────────
    result = core.prune(vm_name)
    assert result.results[0].success, f"prune run 3 failed: {result.results[0].error}"
    assert _committed_count(result) == 2
    assert len(state.get_snapshots(vm_name)) == 3
    assert any(
        "collapse phase complete" in r.message for r in caplog.records
    ) or _collapse_phase_from_file(state_dir, vm_name) == [], (
        "Phase must be cleared once the floor is reached"
    )

    # ── Backing-chain integrity ──────────────────────────────────────
    snaps = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    assert len(snaps) == 3
    tip = snaps[-1].path
    assert tip.exists(), f"Tip snapshot must exist: {tip}"
    check = shell.run(["qemu-img", "check", "--force-share", str(tip)], timeout=60)
    assert check.success, f"qemu-img check failed: {check.error}"
    assert "No errors" in check.stdout, f"Chain integrity errors: {check.stdout}"

    # NOTE: the backing chain includes the base image, so its length is
    # state_count + 1 (the test-plan formula "length == state count"
    # omits the base entry).
    chain_len = _backing_chain_length(tip, shell)
    assert chain_len is not None, "qemu-img info --backing-chain must succeed"
    assert chain_len == len(snaps) + 1, (
        f"Backing chain length must equal state count + base image: "
        f"chain={chain_len}, state={len(snaps)}"
    )
    for sn in snaps:
        assert sn.path.exists(), f"Floor snapshot file must exist: {sn.path}"

    # ── Grow again past H → collapse re-triggers ─────────────────────
    _create_snapshots(core, vm_config, 6)
    assert len(state.get_snapshots(vm_name)) == 9, "Chain must regrow to N=9"
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result = core.prune(vm_name)
    assert result.results[0].success
    assert _committed_count(result) == 2
    assert len(state.get_snapshots(vm_name)) == 7
    phase = _collapse_phase_from_file(state_dir, vm_name)
    assert "vda" in phase, f"Collapse must re-trigger after regrowth, got {phase}"
    assert any("collapse phase started" in r.message for r in caplog.records), (
        "Re-trigger must log the collapse start again"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 2: Default mode (hysteresis) below the threshold writes no phase
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_hysteresis_default_mode_no_phase_below_threshold_real_chain(test_vm):
    """Default ``"hysteresis"`` mode below the trigger threshold writes no phase.

    1. Start VM, create 5 snapshots with ``snapshot_chain_length=8`` and
       ``snapshot_preserve_min=3``; NO explicit ``snapshot_retention_mode``
       is set, so the production default ``"hysteresis"`` applies.
    2. N=5 is below the threshold H=8 → ``core.prune`` runs in the grow
       phase: NO blockcommit happens and all 5 snapshots survive.
    3. The state file NEVER contains the ``collapse_in_progress`` key
       (the hysteresis phase is only written once the chain crosses H).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]
    state_dir = tmpdir / "state"

    # Default mode is "hysteresis"; H=8, L=3, N=5 → grow phase, no commits.
    # max_commits_per_run=12 → the cap cannot be the reason nothing commits.
    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        state_dir,
        mode=None,
        h=8,
        floor=3,
        cap=12,
    )
    _start_vm(shell, vm_name)
    assert vm_config.snapshot_retention_mode == "hysteresis", (
        "Default retention mode must be hysteresis"
    )

    _create_snapshots(core, vm_config, 5)
    assert len(state.get_snapshots(vm_name)) == 5

    state_file = _state_file(state_dir, vm_name)
    raw_before = json.loads(state_file.read_text())
    assert "collapse_in_progress" not in raw_before, (
        "State file must not contain the phase key before pruning in the grow phase"
    )

    result = core.prune(vm_name)
    assert result.results[0].success, f"prune failed: {result.results[0].error}"

    # Grow phase below H: NO blockcommit happens, all 5 snapshots remain.
    assert _committed_count(result) == 0, (
        "Below the hysteresis threshold nothing may be committed"
    )
    assert len(state.get_snapshots(vm_name)) == 5

    raw_after = json.loads(state_file.read_text())
    assert "collapse_in_progress" not in raw_after, (
        "State file must NEVER contain collapse_in_progress below the threshold"
    )

    # All 5 snapshot overlay files are still on disk.
    assert len(list(snapshot_dir.glob("*.qcow2"))) == 5, (
        "Grow phase must leave every snapshot file on disk"
    )

    # Chain intact: base + 5 snapshots.
    snaps = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    tip = snaps[-1].path
    chain_len = _backing_chain_length(tip, shell)
    assert chain_len == len(snaps) + 1, (
        f"Chain must be base + {len(snaps)} snapshots, got {chain_len}"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 3: Dry-run with a persisted phase is zero-mutation
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_hysteresis_dry_run_zero_mutation_real_chain(test_vm):
    """Dry-run with a persisted collapse phase mutates nothing.

    1. Start VM, create 9 snapshots; run one REAL capped prune so the
       phase is persisted (``collapse_in_progress == ["vda"]``) and
       N = 7.
    2. Rebuild Core with a recording shell and ``dry_run = True``.
    3. Assert: state-file bytes identical; every snapshot file
       byte-identical; no ``virsh blockcommit`` command; no lifecycle
       manager created; ``result.actions == []``; the predicted
       blockcommit batch names exactly the oldest ``min(N-L, cap)``
       snapshots; the phase key is read but never written/cleared.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]
    state_dir = tmpdir / "state"

    core, vm_config, state = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, state_dir, h=8, floor=3, cap=2
    )
    _start_vm(shell, vm_name)
    _create_snapshots(core, vm_config, 9)

    # One REAL capped run: N=7, phase persisted in the JSON state file.
    result = core.prune(vm_name)
    assert result.results[0].success, f"real prune failed: {result.results[0].error}"
    snaps = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    assert len(snaps) == 7
    assert "vda" in _collapse_phase_from_file(state_dir, vm_name)

    # Capture the exact bytes of the state file and every snapshot file.
    state_file = _state_file(state_dir, vm_name)
    state_bytes_before = state_file.read_bytes()
    snap_bytes_before = {p.name: p.read_bytes() for p in snapshot_dir.glob("*.qcow2")}
    assert len(snap_bytes_before) == 7

    # Rebuild Core on the same state dir with a recording shell.
    recording = RecordingShell(shell)
    core2, vm_config2, state2 = _build_core(
        recording, vm_name, base_image, snapshot_dir, target_dir, state_dir, h=8, floor=3, cap=2
    )

    # Spy on the lifecycle manager: dry-run must never create one.
    lifecycle_calls: list[str] = []
    factory = core2._factory
    orig_create_lifecycle = factory.create_lifecycle_manager

    def _spy_create_lifecycle(mode: str = "virsh"):
        lifecycle_calls.append(mode)
        return orig_create_lifecycle(mode)

    factory.create_lifecycle_manager = _spy_create_lifecycle  # type: ignore[method-assign]

    core2.dry_run = True
    result2 = core2.run(vm_name)

    # (a) Zero executed actions.
    assert result2.dry_run is True, f"Expected dry_run=True, got {result2.dry_run}"
    assert result2.actions == [], f"Expected no executed actions, got {result2.actions}"

    # (b) State file byte-identical (phase read but never written/cleared).
    assert state_file.read_bytes() == state_bytes_before, (
        "State file must be byte-identical after a dry-run"
    )

    # (c) Snapshot files byte-identical (no blockcommit executed).
    snap_bytes_after = {p.name: p.read_bytes() for p in snapshot_dir.glob("*.qcow2")}
    assert snap_bytes_after == snap_bytes_before, (
        "Snapshot files must be byte-identical after a dry-run"
    )

    # (d) No virsh blockcommit command was issued.
    blockcommit_cmds = [c for c in recording.commands if "blockcommit" in c]
    assert blockcommit_cmds == [], f"Dry-run must not execute blockcommit: {blockcommit_cmds}"

    # (e) No lifecycle manager was created.
    assert lifecycle_calls == [], (
        f"Dry-run must not create a lifecycle manager, got {lifecycle_calls}"
    )

    # (f) Predicted blockcommit batch == exactly min(N-L, cap) oldest.
    preds = [p for p in result2.predictions if p.action == "blockcommit"]
    assert len(preds) == 1, f"Expected one blockcommit prediction per disk, got {len(preds)}"
    expected_count = min(len(snaps) - 3, 2)
    assert expected_count == 2, "Sanity: min(N-L, cap) must be 2 for this chain"
    expected_names = [s.name for s in snaps[:expected_count]]
    for name in expected_names:
        assert name in preds[0].name, (
            f"Predicted blockcommit must name the oldest {expected_count} snapshots; "
            f"{name!r} missing from {preds[0].name!r}"
        )

    # (g) The phase is unchanged after the dry-run.
    assert "vda" in _collapse_phase_from_file(state_dir, vm_name), (
        "Dry-run must not clear the persisted collapse phase"
    )
    assert "vda" in state2.get_collapse_in_progress(vm_name)
