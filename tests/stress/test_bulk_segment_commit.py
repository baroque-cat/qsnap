"""Stress test: single-job bulk segment ``virsh blockcommit`` on a deep chain.

bulk-collapse-blockcommit design D1/D2: the ENTIRE oldest ``N − L`` segment
of a disk collapses with ONE ``virsh blockcommit --base <B> --top <newest
removable> --delete --verbose --wait`` job within a single run — no
per-snapshot loop, no per-run cap, no ``collapse_in_progress`` phase.

This stress test exercises that job on a real deep chain (66 overlays,
H=64, L=48) and verifies:

- exactly ONE ``virsh blockcommit`` process is spawned (single segment
  job, not a per-snapshot loop);
- the command argv carries the documented contract: ``--domain``,
  ``--path vda``, ``--base`` = the disk's base image, ``--top`` = the
  NEWEST removable snapshot path, plus ``--delete --verbose --wait``;
- the chain delta is exactly ``N − L`` = 18 (66 → 48);
- the commit-intent journal is written once and cleared after success
  (the intent journal remains the ONLY crash-recovery record, design D5);
- per-snapshot ``snapshot_delete`` audit rows are still emitted after the
  bulk job (design D9 — audit granularity stays per snapshot);
- the floor files (newest 48) survive, the active layer passes
  ``qemu-img check``, and the VM is still running.

Marked ``@pytest.mark.stress`` — requires a libvirt environment with a
disposable test VM (stress_env, 512M disk).
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
from tests.stress.test_long_chain import (
    _HYST_CHAIN_DEPTH,
    _HYST_FLOOR,
    _HYST_THRESHOLD,
    RecordingShell,
    _qemu_img_check,
    _vm_is_running,
)

#: Expected merge-set size: N − L = 66 − 48.
_HYST_MERGE_SIZE = _HYST_CHAIN_DEPTH - _HYST_FLOOR


@pytest.mark.stress
@pytest.mark.timeout(7200)
def test_bulk_segment_commit_single_job_deep_chain(stress_env):
    """A 66-overlay chain collapses 18 layers with ONE segment blockcommit.

    1. Start the VM and build a 66-overlay chain recorded per-disk as
       ``vda`` (exceeds the H=64 trigger threshold).
    2. Switch to hysteresis (H=64, L=48) with a recording shell around the
       real ``SubprocessShell`` and run ONE ``core.prune``.
    3. Assert exactly ONE ``virsh blockcommit`` command carrying the full
       argv contract: ``--domain <vm> --path vda --base <base> --top
       <newest removable> --delete --verbose --wait``.
    4. Assert the chain delta is exactly 18 (66 → 48), the 18 oldest
       overlay files are gone, the newest 48 survive, the commit-intent
       journal is empty again, exactly 18 ``snapshot_delete`` audit rows
       were emitted, and no blockcommit is left deferred.
    5. The active layer passes ``qemu-img check`` and the VM is still
       running.
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

    # 1. Build the deep chain (66 overlays > H=64) while steady.
    snapshots = []
    for i in range(_HYST_CHAIN_DEPTH):
        hex_sfx = secrets.token_hex(3)
        snap = snapshot_create(
            shell,
            vm_name,
            f"{vm_name}.seg-{i:03d}-{hex_sfx}",
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

    # Recording proxy around the real shell — the segment job's command
    # must be observable (exactly ONE virsh blockcommit).
    recording = RecordingShell(shell)

    # 2. Hysteresis collapse trigger: H=64, L=48.
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
        config_path=tmpdir / "bulk_segment_commit.toml",
    )
    factory = DefaultFactory(shell=recording, state=state)
    core = Core(config=config, factory=factory, state=state, shell=recording)

    # 3. ONE prune run collapses the whole oldest N − L segment.
    result = core.prune(vm_name)
    assert result.results[0].success, f"Prune failed: {result.results[0].error}"

    # Exactly ONE virsh blockcommit process — the single segment job
    # (design D1), never a per-snapshot loop.
    blockcommits = recording.blockcommit_commands()
    assert len(blockcommits) == 1, (
        f"Exactly ONE virsh blockcommit job must collapse the whole segment, "
        f"got {len(blockcommits)}: {blockcommits}"
    )

    # Full argv contract (test-plan note 2 / lifecycle-manager spec):
    # virsh blockcommit --domain <vm> --path vda --base <base>
    # --top <snapshots_to_merge[-1].path> --delete --verbose --wait.
    bc = blockcommits[0]
    assert bc[0] == "virsh" and "blockcommit" in bc, f"not a blockcommit command: {bc}"
    assert "--domain" in bc and bc[bc.index("--domain") + 1] == vm_name, (
        f"--domain must name the VM: {bc}"
    )
    assert "--path" in bc and bc[bc.index("--path") + 1] == "vda", (
        f"--path must name the disk target vda: {bc}"
    )
    assert "--base" in bc and bc[bc.index("--base") + 1] == str(base_image), (
        f"--base must be the disk's base image {base_image}: {bc}"
    )
    newest_removable = sorted(snapshots, key=lambda s: s.timestamp)[_HYST_MERGE_SIZE - 1]
    assert "--top" in bc and bc[bc.index("--top") + 1] == str(newest_removable.path), (
        f"--top must be the NEWEST removable snapshot {newest_removable.path}: {bc}"
    )
    for flag in ("--delete", "--verbose", "--wait"):
        assert flag in bc, f"blockcommit argv must contain {flag}: {bc}"

    # 4. Chain delta exactly N − L = 18: 66 overlays → 48 at the floor.
    surviving = sorted(state.get_snapshots(vm_name), key=lambda s: s.timestamp)
    assert len(surviving) == _HYST_FLOOR, (
        f"Chain must drop to the floor {_HYST_FLOOR} (delta {_HYST_MERGE_SIZE}), "
        f"got {len(surviving)}"
    )
    assert len(surviving) == _HYST_CHAIN_DEPTH - _HYST_MERGE_SIZE, (
        "Chain delta must be exactly the merge-set size (N − L = 18)"
    )

    # Oldest 18 overlay files committed away; newest 48 survive.
    sorted_snaps = sorted(snapshots, key=lambda s: s.timestamp)
    for snap in sorted_snaps[:_HYST_MERGE_SIZE]:
        assert not snap_paths_before[snap.name].exists(), (
            f"Merged overlay file must be deleted by --delete: {snap_paths_before[snap.name]}"
        )
    for snap in sorted_snaps[_HYST_MERGE_SIZE:]:
        assert snap_paths_before[snap.name].exists(), (
            f"Newest {_HYST_FLOOR} floor files must survive: {snap_paths_before[snap.name]}"
        )

    # Intent journal: the ONLY crash-recovery record (design D5) — written
    # once around the bulk job and cleared after success.
    assert state.get_commit_in_progress(vm_name) == [], (
        "Commit-intent journal must be cleared after a successful bulk collapse"
    )

    # Audit granularity stays per snapshot (design D9): one
    # snapshot_delete ActionRecord per merged snapshot.
    delete_actions = [a for a in result.actions if a.action == "snapshot_delete"]
    assert len(delete_actions) == _HYST_MERGE_SIZE, (
        f"Expected {_HYST_MERGE_SIZE} per-snapshot delete audit rows, got {len(delete_actions)}"
    )

    # Nothing deferred by the single-run bulk collapse.
    assert state.get_deferred_operations(vm_name) == [], (
        "No deferred blockcommit entries may remain after the bulk collapse"
    )

    # 5. Chain intact and VM healthy after the live segment commit.
    active = surviving[-1].path
    assert _qemu_img_check(recording, active), (
        f"qemu-img check must pass on the active layer after the segment commit: {active}"
    )
    assert _vm_is_running(recording, vm_name), "VM should still be running"

    shell.run(["virsh", "destroy", vm_name], timeout=30)
