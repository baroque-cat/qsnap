"""Integration test: real-chain segment blockcommit shrinks by the merge set.

Covers design risk #337 (libvirt ``--delete`` correctness on deep
segments): a REAL 9-layer backing chain is collapsed with a single
``virsh blockcommit --base … --top … --delete`` segment command and the
observable chain delta must equal the merge-set size exactly.

Validates against a disposable test VM (``test_vm`` fixture) what the
mock-based lifecycle suite can only approximate — libvirt's actual
segment-commit + ``--delete`` behavior on a deep chain:

- exactly ONE ``virsh blockcommit`` process is spawned for a prune
  (no per-snapshot loop, design D1);
- the segment command carries ``--base <base> --top <newest removable>
  --delete --verbose --wait``;
- the backing chain shrinks by exactly the merge-set size ``N − L``;
- every intermediate overlay file is deleted, the newest ``L`` floor
  files and the active layer survive;
- the VM keeps running and the disk stays writable.

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  Run only when explicitly requested::

    poetry run pytest tests/integration/test_bulk_blockcommit_real_chain.py -v -m integration
"""

from __future__ import annotations

import json
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
    h: int = 8,
    floor: int = 3,
) -> tuple[Core, VMConfig, JsonStateManager]:
    """Build a Core with hysteresis retention and a JSON-backed state manager."""
    state = JsonStateManager(state_dir)
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=h,
        snapshot_preserve_min=floor,
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


def _get_active_layer(shell: SubprocessShell, vm_name: str) -> str | None:
    """Return the active layer path from ``virsh domblklist``."""
    result = shell.run(["virsh", "domblklist", "--domain", vm_name], timeout=30)
    if not result.success:
        return None
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0].startswith("vd"):
            return parts[1]
    return None


class RecordingShell(IShell):
    """IShell wrapper that delegates to SubprocessShell and records commands."""

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
# Test: segment commit shrinks a real chain by the merge-set size
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_segment_commit_shrinks_real_chain_by_merge_set_size(test_vm):
    """A real 9-layer chain collapses by exactly the merge-set size (6).

    1. Start VM; with hysteresis H=8, L=3 create 9 snapshots
       (N=9 > H=8 → merge set = the oldest N−L = 6 snapshots).
    2. Run ONE ``core.prune`` through a recording shell.
    3. Assert:
       (a) exactly ONE ``virsh blockcommit`` with
           ``--base <base> --top <newest removable> --delete``;
       (b) the backing chain shrank by exactly 6;
       (c) every intermediate (merge-set) file is deleted;
       (d) the newest L=3 floor files AND the active layer survive;
       (e) the VM is still running and the disk is writable
           (``qemu-img check`` passes on the active layer and a fresh
           external snapshot can be created on top of it).
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

    # Build the 9-layer chain: base ← s1 ← … ← s9 (s9 = active layer).
    snaps = _create_snapshots(core, vm_config, 9)
    assert len(snaps) == 9
    merge_set = snaps[:6]  # oldest N − L = 6
    floor_set = snaps[6:]  # newest L = 3
    newest_removable = merge_set[-1]

    chain_len_before = _backing_chain_length(snaps[-1].path, recording)
    assert chain_len_before == 10, f"Expected chain base + 9 overlays (10), got {chain_len_before}"

    # ── ONE prune issues ONE segment blockcommit ──────────────────────
    result = core.prune(vm_name)
    assert result.results[0].success, f"prune failed: {result.results[0].error}"

    # (a) Exactly ONE virsh blockcommit with --base/--top/--delete.
    blockcommit_cmds = [c for c in recording.commands if "blockcommit" in c]
    assert len(blockcommit_cmds) == 1, (
        f"Bulk collapse must run exactly ONE virsh blockcommit, got {len(blockcommit_cmds)}: "
        f"{blockcommit_cmds}"
    )
    cmd = blockcommit_cmds[0]
    assert cmd[0] == "virsh" and cmd[1] == "blockcommit", cmd
    assert "--domain" in cmd and "--path" in cmd and "--base" in cmd, cmd
    assert "--top" in cmd and "--delete" in cmd and "--verbose" in cmd and "--wait" in cmd, cmd
    base_idx = cmd.index("--base")
    assert cmd[base_idx + 1] == str(base_image), (
        f"--base must be the disk's base image, got {cmd[base_idx + 1]}"
    )
    top_idx = cmd.index("--top")
    assert cmd[top_idx + 1] == str(newest_removable.path), (
        f"--top must be the newest removable snapshot, got {cmd[top_idx + 1]}"
    )

    # (b) Chain length shrank by exactly the merge-set size (6).
    remaining = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    assert len(remaining) == 3, f"Expected the floor L=3 to survive, got {len(remaining)}"
    chain_len_after = _backing_chain_length(remaining[-1].path, recording)
    assert chain_len_after is not None, "qemu-img info --backing-chain must succeed"
    assert chain_len_after == 4, f"Expected base + 3 floor overlays, got {chain_len_after}"
    assert chain_len_before - chain_len_after == len(merge_set), (
        f"Chain delta must equal the merge-set size ({len(merge_set)}): "
        f"before={chain_len_before}, after={chain_len_after}"
    )

    # (c) Every intermediate (merge-set) file deleted.
    for sn in merge_set:
        assert not sn.path.exists(), f"Intermediate overlay must be deleted by --delete: {sn.path}"

    # (d) Newest L=3 floor files intact AND the active layer is the
    #     newest floor snapshot (the chain tip was repointed at base).
    for sn in floor_set:
        assert sn.path.exists(), f"Floor overlay must survive: {sn.path}"
    active = _get_active_layer(recording, vm_name)
    assert active is not None, "domblklist should return the active layer"
    assert Path(active).resolve() == floor_set[-1].path.resolve(), (
        f"Active layer should still be the newest snapshot ({floor_set[-1].path}), got {active}"
    )

    # (e) VM still running and the disk writable.
    assert _vm_is_running(recording, vm_name), "VM should still be running"
    check = recording.run(
        ["qemu-img", "check", "--force-share", str(floor_set[-1].path)],
        timeout=60,
    )
    assert check.success, f"qemu-img check failed: {check.error}"
    assert "No errors" in check.stdout, f"Active-layer integrity errors: {check.stdout}"

    # Writable: a fresh external snapshot can be created on top of the
    # (shortened) chain — proves the active layer accepts new overlays.
    extra = core._create_snapshot(vm_config)
    assert len(extra) >= 1, "Snapshot creation returned no results"
    assert extra[0].success, f"Post-collapse snapshot failed: {extra[0].error}"
