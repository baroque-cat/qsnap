"""Integration tests for hysteresis snapshot retention on REAL libvirt.

Validates against a disposable test VM (``test_vm`` fixture) what the
mock-based core suite can only approximate:

- ``test_hysteresis_single_run_bulk_collapse_real_chain`` — end-to-end
  grow-to-threshold / collapse-to-floor with the single-shot bulk
  blockcommit (design D1) on a real backing chain: exactly ONE
  ``virsh blockcommit`` segment command per prune, the chain shrinks by
  exactly ``N − L`` in one run, the floor ``L`` files survive, and the
  VM keeps running.
- ``test_hysteresis_default_mode_no_phase_below_threshold_real_chain`` —
  the default ``"hysteresis"`` mode below the trigger threshold grows:
  no blockcommit happens and all snapshot files survive.  (The removed
  ``collapse_in_progress`` phase key is no longer part of the state
  schema at all — nothing to assert about it.)
- ``test_hysteresis_dry_run_zero_mutation_real_chain`` — dry-run on a
  deep chain: state-file and snapshot-file bytes are identical
  before/after, no ``virsh blockcommit`` executes, no lifecycle manager
  is created, and the predicted blockcommit batch names exactly the
  FULL uncapped ``N − L`` oldest snapshots.

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
    steady_chain_length: int = 4,
) -> tuple[Core, VMConfig, JsonStateManager]:
    """Build a Core instance with a JSON-backed state manager.

    The collapse is a single uncapped bulk blockcommit — there is no
    per-run commit cap and no persisted ``collapse_in_progress`` phase,
    so ``GlobalConfig`` carries no ``max_commits_per_run``.

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
        global_config=GlobalConfig(),
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

    Used to assert that a prune runs exactly ONE ``virsh blockcommit``
    (and that dry-run mode never issues one at all).
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
# Test 1: Single-run bulk collapse converges to the floor
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_hysteresis_single_run_bulk_collapse_real_chain(test_vm, caplog):
    """A single bulk blockcommit collapses the full ``N − L`` segment.

    1. Start VM, create 9 snapshots (N=9 > H=8) with H=8, L=3.
    2. Run ``core.prune`` ONCE through a recording shell.
    3. Assert: exactly ONE ``virsh blockcommit`` segment command (with
       ``--top`` = the newest removable snapshot); the chain shrank by
       exactly ``N − L = 6``; the oldest 6 overlay files are deleted;
       the newest L=3 floor files survive; the VM keeps running; the
       intent/success log lines use the ``collapsing``/``collapsed``
       wording (design D9).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]
    state_dir = tmpdir / "state"

    recording = RecordingShell(shell)
    core, vm_config, state = _build_core(
        recording,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        state_dir,
        h=8,
        floor=3,
    )
    _start_vm(recording, vm_name)

    # Grow past the threshold: N = 9 > H = 8.
    snaps = _create_snapshots(core, vm_config, 9)
    assert len(snaps) == 9
    assert len(state.get_snapshots(vm_name)) == 9

    # Chain length before: base + 9 overlays = 10.
    chain_len_before = _backing_chain_length(snaps[-1].path, recording)
    assert chain_len_before == 10, f"Expected chain base + 9 overlays, got {chain_len_before}"

    # ── ONE prune collapses the whole segment ─────────────────────────
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result = core.prune(vm_name)
    assert result.results[0].success, f"prune failed: {result.results[0].error}"

    # (a) Exactly ONE virsh blockcommit segment command.
    blockcommit_cmds = [c for c in recording.commands if "blockcommit" in c]
    assert len(blockcommit_cmds) == 1, (
        f"Bulk collapse must run exactly ONE virsh blockcommit, got {len(blockcommit_cmds)}: "
        f"{blockcommit_cmds}"
    )
    cmd = blockcommit_cmds[0]
    assert "--base" in cmd and "--top" in cmd and "--delete" in cmd, (
        f"Segment command must carry --base/--top/--delete: {cmd}"
    )
    top_idx = cmd.index("--top")
    assert cmd[top_idx + 1] == str(snaps[5].path), (
        f"--top must be the newest removable snapshot (snap #6), got {cmd[top_idx + 1]}"
    )

    # (b) Chain shrank by exactly N − L = 6.
    remaining = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    assert len(remaining) == 3, f"Expected the floor L=3 to survive, got {len(remaining)}"
    chain_len_after = _backing_chain_length(remaining[-1].path, recording)
    assert chain_len_after is not None, "qemu-img info --backing-chain must succeed"
    assert chain_len_before - chain_len_after == 6, (
        f"Chain must shrink by exactly N-L=6: before={chain_len_before}, after={chain_len_after}"
    )

    # (c) The merge-set files are deleted; the floor files survive.
    for sn in snaps[:6]:
        assert not sn.path.exists(), f"Merged snapshot file must be deleted by --delete: {sn.path}"
    for sn in snaps[6:]:
        assert sn.path.exists(), f"Floor snapshot file must survive: {sn.path}"

    # (d) The audit trail records one snapshot_delete per merged snapshot.
    assert _committed_count(result) == 6, (
        f"Expected 6 snapshot_delete action records, got {_committed_count(result)}"
    )

    # (e) VM still running.
    assert _vm_is_running(recording, vm_name), "VM should still be running"

    # (f) Observability wording (design D9): intent + success lines.
    commit_lines = [r.message for r in caplog.records if "[blockcommit]" in r.message]
    assert any("collapsing 6 snapshot(s)" in m and "mode=virsh" in m for m in commit_lines), (
        f"Expected 'collapsing 6 snapshot(s)' intent line, got: {commit_lines}"
    )
    assert any("collapsed 6 snapshot(s)" in m for m in commit_lines), (
        f"Expected 'collapsed 6 snapshot(s)' success line, got: {commit_lines}"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 2: Default mode (hysteresis) below the threshold grows
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_hysteresis_default_mode_no_phase_below_threshold_real_chain(test_vm):
    """Default ``"hysteresis"`` mode below the trigger threshold grows.

    1. Start VM, create 5 snapshots with ``snapshot_chain_length=8`` and
       ``snapshot_preserve_min=3``; NO explicit ``snapshot_retention_mode``
       is set, so the production default ``"hysteresis"`` applies.
    2. N=5 is below the threshold H=8 → ``core.prune`` runs in the grow
       phase: NO blockcommit happens and all 5 snapshots survive.
    3. The removed ``collapse_in_progress`` phase key is not part of the
       state schema anymore — the assertion surface is the absence of
       any commit, not the absence of a phase marker.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]
    state_dir = tmpdir / "state"

    # Default mode is "hysteresis"; H=8, L=3, N=5 → grow phase, no commits.
    recording = RecordingShell(shell)
    core, vm_config, state = _build_core(
        recording,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        state_dir,
        mode=None,
        h=8,
        floor=3,
    )
    _start_vm(recording, vm_name)
    assert vm_config.snapshot_retention_mode == "hysteresis", (
        "Default retention mode must be hysteresis"
    )

    _create_snapshots(core, vm_config, 5)
    assert len(state.get_snapshots(vm_name)) == 5

    result = core.prune(vm_name)
    assert result.results[0].success, f"prune failed: {result.results[0].error}"

    # Grow phase below H: NO blockcommit happens, all 5 snapshots remain.
    assert _committed_count(result) == 0, "Below the hysteresis threshold nothing may be committed"
    assert len(state.get_snapshots(vm_name)) == 5

    # No virsh blockcommit command was ever issued.
    blockcommit_cmds = [c for c in recording.commands if "blockcommit" in c]
    assert blockcommit_cmds == [], f"Grow phase must not execute blockcommit: {blockcommit_cmds}"

    # All 5 snapshot overlay files are still on disk.
    assert len(list(snapshot_dir.glob("*.qcow2"))) == 5, (
        "Grow phase must leave every snapshot file on disk"
    )

    # Chain intact: base + 5 snapshots.
    snaps = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    tip = snaps[-1].path
    chain_len = _backing_chain_length(tip, recording)
    assert chain_len == len(snaps) + 1, (
        f"Chain must be base + {len(snaps)} snapshots, got {chain_len}"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 3: Dry-run on a deep chain is zero-mutation and names the FULL set
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_hysteresis_dry_run_zero_mutation_real_chain(test_vm):
    """Dry-run on a deep hysteresis chain mutates nothing.

    1. Start VM, create 9 snapshots (H=8, L=3, no cap) — the chain sits
       above the trigger threshold.
    2. Capture the exact bytes of the state file and every snapshot file.
    3. Rebuild Core on the same state dir with a recording shell and
       ``dry_run = True``; run ``core.prune``.
    4. Assert: state-file bytes identical; every snapshot file
       byte-identical; no ``virsh blockcommit`` command; no lifecycle
       manager created; ``result.actions == []``; the predicted
       blockcommit batch names exactly the FULL uncapped ``N − L = 6``
       oldest snapshots.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]
    state_dir = tmpdir / "state"

    core, vm_config, state = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, state_dir, h=8, floor=3
    )
    _start_vm(shell, vm_name)
    _create_snapshots(core, vm_config, 9)
    snaps = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    assert len(snaps) == 9

    # Capture the exact bytes of the state file and every snapshot file.
    state_file = _state_file(state_dir, vm_name)
    state_bytes_before = state_file.read_bytes()
    snap_bytes_before = {p.name: p.read_bytes() for p in snapshot_dir.glob("*.qcow2")}
    assert len(snap_bytes_before) == 9

    # Rebuild Core on the same state dir with a recording shell.
    recording = RecordingShell(shell)
    core2, _, _ = _build_core(
        recording, vm_name, base_image, snapshot_dir, target_dir, state_dir, h=8, floor=3
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
    # ``prune`` (not ``run``) so no simulated snapshot is added: N stays
    # 9 and the predicted batch is exactly the oldest N − L = 6.
    result2 = core2.prune(vm_name)

    # (a) Zero executed actions.
    assert result2.dry_run is True, f"Expected dry_run=True, got {result2.dry_run}"
    assert result2.actions == [], f"Expected no executed actions, got {result2.actions}"

    # (b) State file byte-identical (dry-run writes nothing).
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

    # (f) Predicted blockcommit batch == the FULL uncapped N − L set.
    preds = [p for p in result2.predictions if p.action == "blockcommit"]
    assert len(preds) == 1, f"Expected one blockcommit prediction per disk, got {len(preds)}"
    expected_names = [s.name for s in snaps[:6]]
    for name in expected_names:
        assert name in preds[0].name, (
            f"Predicted blockcommit must name the full oldest N-L=6 set; "
            f"{name!r} missing from {preds[0].name!r}"
        )
    newest_names = [s.name for s in snaps[6:]]
    for name in newest_names:
        assert name not in preds[0].name, (
            f"Prediction must NOT name a floor snapshot; {name!r} found in {preds[0].name!r}"
        )
