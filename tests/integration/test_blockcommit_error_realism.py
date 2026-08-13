"""Integration tests: error-path realism of ``virsh blockcommit`` classification.

The mock-based suite scripts virsh stderr for every failure mode; these
tests classify what REAL libvirt does when the merge-set ``--top`` is
invalid — without pinning the exact stderr text (design risk #341/#342,
"Exact-string regressions" note in the test plan):

- ``test_invalid_top_nonexistent_path_classified`` — ``--top`` points at
  a file that does not exist on disk: the real manager classifies the
  outcome as qsnap's ``failure`` (never a crash, and never ``unknown``
  unless virsh itself times out), and the chain + VM are untouched.
- ``test_top_equal_active_layer_classified`` — ``--top`` equals the
  active layer of a running VM: virsh rejects it up front; the outcome
  is classified as ``failure``, the chain length is unchanged, and the
  VM keeps running.

Both invoke the REAL ``BlockCommitManager`` (DefaultFactory module, not
Core) so no state manager, intent journal, or orchestration is involved.

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  Run only when explicitly requested::

    poetry run pytest tests/integration/test_blockcommit_error_realism.py -v -m integration
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.models.config import DiskConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.lifecycle.blockcommit_manager import BlockCommitManager
from qsnap.shell.subprocess_shell import SubprocessShell

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


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


def _create_external_snapshots(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    count: int,
) -> list[SnapshotInfo]:
    """Create *count* external disk-only snapshots via ``virsh snapshot-create-as``.

    Returns the created ``SnapshotInfo`` records oldest-first.

    The explicit ``--diskspec`` pins each overlay to *snapshot_dir* (libvirt
    would otherwise auto-place it next to the current disk source), and
    ``--no-metadata`` mirrors production so a mid-test failure never poisons
    the domain with leftover libvirt snapshot metadata.
    """
    snaps: list[SnapshotInfo] = []
    for i in range(count):
        snap_name = f"{vm_name}.err-real-{i + 1}"
        snap_path = snapshot_dir / f"{snap_name}.qcow2"
        result = shell.run(
            [
                "virsh",
                "snapshot-create-as",
                "--domain",
                vm_name,
                "--name",
                snap_name,
                "--diskspec",
                f"vda,file={snap_path},snapshot=external",
                "--disk-only",
                "--atomic",
                "--no-metadata",
            ],
            timeout=60,
        )
        assert result.success, f"snapshot-create-as failed: {result.error}"
        assert snap_path.exists(), f"Snapshot overlay not found: {snap_path}"
        snaps.append(
            SnapshotInfo(
                name=snap_name,
                path=snap_path,
                timestamp=datetime.now(),
                allocation=0,
                disk="vda",
            )
        )
        time.sleep(1.1)
    return snaps


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


def _build_vm_config(vm_name: str, base_image: Path, snapshot_dir: Path) -> VMConfig:
    """Build the minimal VMConfig the manager needs (name + disk)."""
    return VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )


# ──────────────────────────────────────────────────────────────────────
# Test 1: --top points at a nonexistent path
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_invalid_top_nonexistent_path_classified(test_vm):
    """A real ``--top`` pointing at a nonexistent path maps to ``failure``.

    1. Start VM, create 2 real external snapshots (chain: base ← s1 ← s2).
    2. Invoke the real ``BlockCommitManager`` with a merge set whose
       NEWEST element is a ghost ``SnapshotInfo`` whose path does not
       exist (so ``--top`` is the ghost path).
    3. Assert: the outcome classifies as qsnap's ``failure`` (no exact
       stderr text pinned, no crash, and NOT ``unknown``/``success``);
       the chain is untouched (both overlay files still exist, chain
       length unchanged); the VM keeps running.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    _start_vm(shell, vm_name)
    snaps = _create_external_snapshots(shell, vm_name, base_image, snapshot_dir, 2)
    s1, s2 = snaps[0], snaps[1]
    assert s1.path.exists() and s2.path.exists()
    chain_len_before = _backing_chain_length(s2.path, shell)
    assert chain_len_before == 3, f"Expected base + 2 overlays, got {chain_len_before}"

    # A ghost overlay — the file does not exist on disk.
    ghost = SnapshotInfo(
        name=f"{vm_name}.ghost",
        path=snapshot_dir / "does-not-exist.qcow2",
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    assert not ghost.path.exists(), "Test precondition: ghost path must not exist"

    manager = BlockCommitManager(shell)
    result = manager.blockcommit(
        _build_vm_config(vm_name, base_image, snapshot_dir),
        [s1, ghost],  # oldest-first; newest (ghost) becomes --top
        disk="vda",
        base_image=base_image,
    )

    # (a) Classification: definitive failure (never unknown/success/crash).
    assert result.outcome == "failure", (
        "virsh rejects a nonexistent --top up front; the manager MUST "
        f"classify it as 'failure', got outcome={result.outcome!r} error={result.error!r}"
    )
    assert result.success is False

    # (b) Chain untouched: both overlay files still exist.
    assert s1.path.exists(), "s1 must be untouched by the failed commit"
    assert s2.path.exists(), "s2 must be untouched by the failed commit"

    # (c) Chain length unchanged.
    chain_len_after = _backing_chain_length(s2.path, shell)
    assert chain_len_after == chain_len_before, (
        f"Chain must be unchanged: before={chain_len_before}, after={chain_len_after}"
    )

    # (d) VM still running.
    assert _vm_is_running(shell, vm_name), "VM must keep running"


# ──────────────────────────────────────────────────────────────────────
# Test 2: --top equals the active layer
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_top_equal_active_layer_classified(test_vm):
    """A real ``--top`` equal to the active layer maps to ``failure``.

    1. Start VM, create 3 real external snapshots; the newest (s3) is
       the active layer (``virsh domblklist``).
    2. Invoke the real ``BlockCommitManager`` with merge set [s1, s2, s3]
       so ``--top`` = s3.path (the active layer) — virsh rejects this
       up front (no ``--active``/``--pivot`` given).
    3. Assert: classification is ``failure`` (never ``unknown``/crash);
       the chain length is unchanged; all three overlays survive; the
       VM keeps running.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    _start_vm(shell, vm_name)
    snaps = _create_external_snapshots(shell, vm_name, base_image, snapshot_dir, 3)
    s1, s2, s3 = snaps[0], snaps[1], snaps[2]

    # Precondition: the newest overlay really is the active layer.
    active = _get_active_layer(shell, vm_name)
    assert active is not None, "domblklist should return the active layer"
    assert Path(active).resolve() == s3.path.resolve(), (
        f"Test precondition: active layer must be s3 ({s3.path}), got {active}"
    )

    chain_len_before = _backing_chain_length(s3.path, shell)
    assert chain_len_before == 4, f"Expected base + 3 overlays, got {chain_len_before}"

    manager = BlockCommitManager(shell)
    result = manager.blockcommit(
        _build_vm_config(vm_name, base_image, snapshot_dir),
        [s1, s2, s3],  # newest (s3) is the active layer → becomes --top
        disk="vda",
        base_image=base_image,
    )

    # (a) Classification: definitive failure (never unknown/success/crash).
    assert result.outcome == "failure", (
        "virsh rejects an active-layer --top without --active/--pivot; the "
        f"manager MUST classify it as 'failure', got outcome={result.outcome!r} "
        f"error={result.error!r}"
    )
    assert result.success is False

    # (b) Chain length unchanged.
    chain_len_after = _backing_chain_length(s3.path, shell)
    assert chain_len_after == chain_len_before, (
        f"Chain must be unchanged: before={chain_len_before}, after={chain_len_after}"
    )

    # (c) All overlays survive (nothing was committed or deleted).
    for sn in (s1, s2, s3):
        assert sn.path.exists(), f"Overlay must survive the failed commit: {sn.path}"

    # (d) VM still running.
    assert _vm_is_running(shell, vm_name), "VM must keep running"
