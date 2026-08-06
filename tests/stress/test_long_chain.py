"""Stress test: 50+ snapshot chain survives blockcommit with the default 48 floor.

This test creates a deep snapshot chain (55 levels) on a real libvirt VM
and verifies that ``blockcommit`` collapses ONLY the snapshots beyond the
newest 48 (``snapshot_preserve_min=48`` dominates the default
``chain_length=24`` under load), without corrupting data or breaking
backing-file references.

Marked ``@pytest.mark.stress`` — requires a libvirt environment with a
disposable test VM.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.shell.subprocess_shell import SubprocessShell
from tests.helpers import snapshot_create
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_state import InMemoryStateManager

#: Number of snapshots in the chain (must exceed the 48 floor).
_CHAIN_DEPTH = 55


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

    state = InMemoryStateManager()
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

    shell.run(["virsh", "destroy", vm_name], timeout=30)
