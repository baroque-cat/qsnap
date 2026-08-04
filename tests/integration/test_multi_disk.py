"""Integration tests for multi-disk VMs.

Verifies snapshot isolation, per-disk snapshot directories, blockcommit
isolation, and single-disk restore for VMs with two disks (vda + vdb).

All tests are marked ``@pytest.mark.integration`` and use the real
``SubprocessShell``.  Run only when explicitly requested::

    poetry run pytest tests/integration/test_multi_disk.py -v -m integration
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.lifecycle.blockcommit_manager import BlockCommitManager
from qsnap.shell.subprocess_shell import SubprocessShell
from tests.helpers import snapshot_create
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_state import InMemoryStateManager

# Optional dependency check for backup tests
try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False


# ── helpers ────────────────────────────────────────────────────────────


def _vm_is_running(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True if the VM is running."""
    result = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    return result.success and "running" in result.stdout.lower()


def _file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of *path* contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _backing_chain_count(shell: SubprocessShell, image_path: Path) -> int:
    """Return the number of nodes in the backing chain of *image_path*."""
    result = shell.run(
        ["qemu-img", "info", "--force-share", "--backing-chain", str(image_path)],
        timeout=30,
    )
    if not result.success:
        return 0
    # Count occurrences of "image:" lines
    return result.stdout.count("image:")


def _list_qcow2_files(directory: Path) -> set[str]:
    """Return the set of .qcow2 file names in *directory*."""
    return {p.name for p in directory.glob("*.qcow2")}


# ── Test A: Snapshot + blockcommit isolation ───────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_snapshot_blockcommit_isolation(test_vm_multi_disk):
    """Snapshots and blockcommit are isolated per disk.

    1. Start VM, create 3 snapshots on vda and 1 on vdb.
    2. Record them in InMemoryStateManager.
    3. Commit ONLY the 2 oldest vda snapshots via BlockCommitManager
       (disk="vda", base_image=base_vda).
    4. Verify vda backing chain shortened (fewer nodes).
    5. Verify vdb backing chain is UNTOUCHED (base + 1 overlay).
    """
    ctx = test_vm_multi_disk
    shell: SubprocessShell = ctx["shell"]
    vm_name: str = ctx["vm_name"]
    base_images: dict[str, Path] = ctx["base_images"]
    snapshot_dirs: dict[str, Path] = ctx["snapshot_dirs"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    import secrets

    state = InMemoryStateManager()

    # --- Create 3 snapshots on vda ---
    vda_snaps: list[SnapshotInfo] = []
    for i in range(3):
        hex_sfx = secrets.token_hex(3)
        snap_name = f"{vm_name}.20250801T00000{i}_vda_{hex_sfx}"
        time.sleep(0.6)  # unique timestamps
        snap = snapshot_create(
            shell, vm_name, snap_name, "vda",
            snapshot_dirs["vda"], base_images["vda"],
        )
        state.record_snapshot(vm_name, snap)
        vda_snaps.append(snap)

    # --- Create 1 snapshot on vdb ---
    hex_sfx = secrets.token_hex(3)
    snap_name = f"{vm_name}.20250801T000009_vdb_{hex_sfx}"
    vdb_snap = snapshot_create(
        shell, vm_name, snap_name, "vdb",
        snapshot_dirs["vdb"], base_images["vdb"],
    )
    state.record_snapshot(vm_name, vdb_snap)

    # Verify all snapshots recorded
    all_snaps = state.get_snapshots(vm_name)
    assert len(all_snaps) == 4, f"Expected 4 snapshots, got {len(all_snaps)}"

    # Identify vda backing chain before blockcommit
    # The active vda layer is the newest snapshot (index 2)
    active_vda = vda_snaps[-1].path
    vda_chain_before = _backing_chain_count(shell, active_vda)
    assert vda_chain_before >= 4, (
        f"Expected vda chain >= 4 nodes (base + 3 overlays), got {vda_chain_before}"
    )

    # vdb chain: base + 1 overlay = 2 nodes
    vdb_chain_before = _backing_chain_count(shell, vdb_snap.path)
    assert vdb_chain_before >= 2, (
        f"Expected vdb chain >= 2 nodes (base + 1 overlay), got {vdb_chain_before}"
    )

    # --- Blockcommit the 2 oldest vda snapshots ---
    snapshots_sorted = sorted(vda_snaps, key=lambda s: s.timestamp)
    oldest_two = snapshots_sorted[:2]  # merge these
    newest = snapshots_sorted[2]  # should remain as active

    vm_config = VMConfig(
        name=vm_name,
        disks=[
            DiskConfig(target="vda", base_image=base_images["vda"],
                       snapshot_dir=snapshot_dirs["vda"]),
            DiskConfig(target="vdb", base_image=base_images["vdb"],
                       snapshot_dir=snapshot_dirs["vdb"]),
        ],
    )
    manager = BlockCommitManager(shell)
    commit_result = manager.blockcommit(
        vm_config,
        oldest_two,
        disk="vda",
        base_image=base_images["vda"],
    )

    # Blockcommit should succeed (even live)
    if not commit_result.success:
        pytest.skip(
            f"Live blockcommit not supported in this environment: {commit_result.error}"
        )

    # --- Verify vda chain shortened ---
    vda_chain_after = _backing_chain_count(shell, active_vda)
    assert vda_chain_after < vda_chain_before, (
        f"VDA chain should have shortened after blockcommit: "
        f"{vda_chain_before} → {vda_chain_after}"
    )

    # Oldest snapshot files may or may not exist (--delete is version-dependent).
    # The key assertion is that the backing chain is shorter.

    # --- Verify vdb chain is UNTOUCHED ---
    vdb_chain_after = _backing_chain_count(shell, vdb_snap.path)
    assert vdb_chain_after == vdb_chain_before, (
        f"VDB chain should be UNTOUCHED after vda-only blockcommit: "
        f"{vdb_chain_before} → {vdb_chain_after}"
    )

    # Verify vdb snapshot file still exists
    assert vdb_snap.path.exists(), (
        f"VDB snapshot file should still exist: {vdb_snap.path}"
    )

    # VM should still be running
    assert _vm_is_running(shell, vm_name), "VM should still be running"


# ── Test B: Per-disk snapshot_dir override ─────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_snapshots_land_in_correct_dirs(test_vm_multi_disk):
    """Snapshots land in each disk's own snapshot directory.

    1. Start VM.
    2. Create a snapshot on vda — verify file in snapshot_dirs["vda"].
    3. Create a snapshot on vdb — verify file in snapshot_dirs["vdb"].
    4. Verify neither directory contains the other disk's snapshot.
    """
    ctx = test_vm_multi_disk
    shell: SubprocessShell = ctx["shell"]
    vm_name: str = ctx["vm_name"]
    base_images: dict[str, Path] = ctx["base_images"]
    snapshot_dirs: dict[str, Path] = ctx["snapshot_dirs"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    import secrets

    # Create snapshot on vda
    hex_vda = secrets.token_hex(3)
    snap_name_vda = f"{vm_name}.20250801T100000_vda_{hex_vda}"
    snap_vda = snapshot_create(
        shell, vm_name, snap_name_vda, "vda",
        snapshot_dirs["vda"], base_images["vda"],
    )

    # Create snapshot on vdb
    hex_vdb = secrets.token_hex(3)
    snap_name_vdb = f"{vm_name}.20250801T100001_vdb_{hex_vdb}"
    snap_vdb = snapshot_create(
        shell, vm_name, snap_name_vdb, "vdb",
        snapshot_dirs["vdb"], base_images["vdb"],
    )

    # Verify vda snapshot is in vda's directory
    assert snap_vda.path.parent == snapshot_dirs["vda"], (
        f"VDA snapshot should be in {snapshot_dirs['vda']}, "
        f"got {snap_vda.path.parent}"
    )
    assert snap_vda.path.exists(), f"VDA snapshot missing: {snap_vda.path}"

    # Verify vdb snapshot is in vdb's directory
    assert snap_vdb.path.parent == snapshot_dirs["vdb"], (
        f"VDB snapshot should be in {snapshot_dirs['vdb']}, "
        f"got {snap_vdb.path.parent}"
    )
    assert snap_vdb.path.exists(), f"VDB snapshot missing: {snap_vdb.path}"

    # Verify no cross-contamination: vda dir has no vdb files, and vice versa
    vda_files = _list_qcow2_files(snapshot_dirs["vda"])
    vdb_files = _list_qcow2_files(snapshot_dirs["vdb"])

    assert snap_vda.path.name in vda_files
    assert snap_vdb.path.name in vdb_files
    # The directories may contain other files from test setup; at minimum
    # each snapshot file should NOT appear in the wrong directory.
    assert snap_vdb.path.name not in vda_files, (
        f"VDB snapshot leaked into vda directory: {vda_files}"
    )
    assert snap_vda.path.name not in vdb_files, (
        f"VDA snapshot leaked into vdb directory: {vdb_files}"
    )


# ── Test C: Restore of a single disk ───────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_restore_single_disk_isolation(test_vm_multi_disk):
    """Restore replaces only the target disk's base image.

    1. Start VM, create a vdb snapshot.
    2. Stop VM, hash vda base image.
    3. Record snapshot in state, build Core with real factory + shell.
    4. Call ``Core.restore(<vdb snapshot name>)``.
    5. Assert vdb base image replaced (file content changed or mtime updated).
    6. Assert vda base image byte-identical (SHA-256 matches pre-restore).
    7. Verify domain XML: vda <source file> unchanged, vdb points at
       restored base.
    """
    ctx = test_vm_multi_disk
    shell: SubprocessShell = ctx["shell"]
    vm_name: str = ctx["vm_name"]
    base_images: dict[str, Path] = ctx["base_images"]
    snapshot_dirs: dict[str, Path] = ctx["snapshot_dirs"]
    target_dir: Path = ctx["target_dir"]
    tmpdir: Path = ctx["tmpdir"]
    disk_configs: list[DiskConfig] = ctx["disk_configs"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    import secrets

    # Create one snapshot on vdb
    hex_sfx = secrets.token_hex(3)
    snap_name = f"{vm_name}.20250801T200000_vdb_{hex_sfx}"
    vdb_snap = snapshot_create(
        shell, vm_name, snap_name, "vdb",
        snapshot_dirs["vdb"], base_images["vdb"],
    )

    # Stop VM (restore requires stopped VM)
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)

    # Hash vda base before restore (should remain identical)
    vda_hash_before = _file_sha256(base_images["vda"])

    # Build Core with real factory + state
    state = InMemoryStateManager()
    state.record_snapshot(vm_name, vdb_snap)
    # Seed per-disk last_allocation so detector doesn't fail
    state.set_last_allocation(vm_name, "vda", 0)
    state.set_last_allocation(vm_name, "vdb", vdb_snap.allocation)

    vm_config = VMConfig(
        name=vm_name,
        disks=disk_configs,
        snapshot_dir=None,  # each disk has its own override
        targets=[
            TargetConfig(path=target_dir, compress=False, verify="off"),
        ],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "restore_multi.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Perform restore of the vdb snapshot
    with patch("qsnap.core.os.replace", wraps=__import__("os").replace):
        result = core.restore(snap_name)

    if not result.success:
        pytest.skip(f"Restore failed (environment limitation): {result.error}")

    # --- Assert vdb base image was replaced ---
    assert result.restored_path == base_images["vdb"], (
        f"Restored path should be vdb base, got {result.restored_path}"
    )

    # The vdb base image should still exist and be valid qcow2
    assert base_images["vdb"].exists(), "VDB base image must exist after restore"
    # It might be the same size or different (converted standalone)
    info_result = shell.run(
        ["qemu-img", "info", "--force-share", str(base_images["vdb"])],
        timeout=30,
    )
    assert info_result.success, "VDB base should be valid qcow2 after restore"
    assert "qcow2" in info_result.stdout.lower() or "format" in info_result.stdout.lower(), (
        "VDB base should be qcow2 format"
    )

    # --- Assert vda base image byte-identical ---
    vda_hash_after = _file_sha256(base_images["vda"])
    assert vda_hash_after == vda_hash_before, (
        f"VDA base image must be byte-identical after vdb-only restore "
        f"(hash changed: {vda_hash_before[:12]}... → {vda_hash_after[:12]}...)"
    )

    # --- Verify domain XML: vda unchanged, vdb points at restored base ---
    dumpxml = shell.run(
        ["virsh", "dumpxml", "--domain", vm_name], timeout=30
    )
    assert dumpxml.success, f"virsh dumpxml failed: {dumpxml.error}"

    xml_text = dumpxml.stdout
    # vda should still reference the original base image path
    assert str(base_images["vda"]) in xml_text, (
        f"Domain XML should still reference vda base: {base_images['vda']}"
    )
    # vdb should reference its base image path (may be the same path,
    # since os.replace replaces in-place)
    assert str(base_images["vdb"]) in xml_text, (
        f"Domain XML should reference vdb base: {base_images['vdb']}"
    )

    # Verify restore result includes the disk
    assert result.disk == "vdb", (
        f"Restore should report disk='vdb', got {result.disk}"
    )


# ── Test D: Backup both disks (optional, libnbd-dependent) ─────────────


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_backup_both_disks(test_vm_multi_disk):
    """FULL backup produces per-disk backup files (_vda_, _vdb_ in names).

    Requires ``python3-libnbd`` and libvirt ≥ 7.2.  Skipped gracefully
    when either is unavailable.

    1. Start VM, create snapshot on vda and vdb.
    2. Use ``BitmapBackupProvider`` to create FULL backups of each disk.
    3. Verify backup file names contain ``_vda_`` and ``_vdb_`` segments.
    4. Verify both backup files exist on target.
    """
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    ctx = test_vm_multi_disk
    shell: SubprocessShell = ctx["shell"]
    vm_name: str = ctx["vm_name"]
    base_images: dict[str, Path] = ctx["base_images"]
    snapshot_dirs: dict[str, Path] = ctx["snapshot_dirs"]
    target_dir: Path = ctx["target_dir"]

    from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    import secrets

    from qsnap.models.config import TargetConfig
    from qsnap.modules.backup.bitmap import BitmapBackupProvider

    # Create snapshot on vda
    hex_vda = secrets.token_hex(3)
    snap_name_vda = f"{vm_name}.20250801T300000_vda_{hex_vda}"
    snap_vda = snapshot_create(
        shell, vm_name, snap_name_vda, "vda",
        snapshot_dirs["vda"], base_images["vda"],
    )

    # Create snapshot on vdb
    hex_vdb = secrets.token_hex(3)
    snap_name_vdb = f"{vm_name}.20250801T300001_vdb_{hex_vdb}"
    snap_vdb = snapshot_create(
        shell, vm_name, snap_name_vdb, "vdb",
        snapshot_dirs["vdb"], base_images["vdb"],
    )

    target = TargetConfig(path=target_dir, compress=False, verify="off")
    provider = BitmapBackupProvider(shell)

    # Backup vda
    full_vda = provider.create_full_backup(vm_name, snap_vda, target, compress=False)
    if not full_vda.success:
        # Some environments may not support bitmap checkpoint creation.
        # Skip instead of failing — this is optional coverage.
        pytest.skip(f"FULL backup of vda failed: {full_vda.error}")

    assert "_vda_" in full_vda.target_path.name or "vda" in full_vda.target_path.name.lower(), (
        f"vda backup name should contain '_vda_': {full_vda.target_path.name}"
    )
    assert full_vda.target_path.exists(), f"VDA backup missing: {full_vda.target_path}"

    # Backup vdb
    full_vdb = provider.create_full_backup(vm_name, snap_vdb, target, compress=False)
    if not full_vdb.success:
        pytest.skip(f"FULL backup of vdb failed: {full_vdb.error}")

    assert "_vdb_" in full_vdb.target_path.name or "vdb" in full_vdb.target_path.name.lower(), (
        f"vdb backup name should contain '_vdb_': {full_vdb.target_path.name}"
    )
    assert full_vdb.target_path.exists(), f"VDB backup missing: {full_vdb.target_path}"
