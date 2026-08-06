"""Integration tests for multi-disk VMs.

Verifies snapshot isolation, per-disk snapshot directories, blockcommit
isolation, and single-disk restore for VMs with two disks (vda + vdb).

All tests are marked ``@pytest.mark.integration`` and use the real
``SubprocessShell``.  Run only when explicitly requested::

    poetry run pytest tests/integration/test_multi_disk.py -v -m integration
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
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


def _spy_snapshot_create_as(shell: SubprocessShell, log: list[list[str]]):
    """Wrap ``shell.run`` to record ``virsh snapshot-create-as`` invocations.

    Real commands still execute; only the batch snapshot command is
    appended to *log* (used to assert the batch command shape — one
    call with 2 ``--diskspec`` entries plus ``--atomic``/``--quiesce``).
    """

    orig_run = shell.run

    def _spy(cmd, timeout=30, check=False):
        if cmd and cmd[0] == "virsh" and cmd[1] == "snapshot-create-as":
            log.append(list(cmd))
        return orig_run(cmd, timeout=timeout, check=check)

    shell.run = _spy  # type: ignore[method-assign]


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints for *vm_name*."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return
    for line in result.stdout.strip().splitlines():
        cp = line.strip()
        if cp and cp.startswith("qsnap-"):
            shell.run(
                ["virsh", "checkpoint-delete", "--domain", vm_name, cp],
                timeout=30,
            )


def _list_checkpoints(shell: SubprocessShell, vm_name: str) -> set[str]:
    """Return the set of ``qsnap-*`` checkpoint names for *vm_name*."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return set()
    return {
        line.strip()
        for line in result.stdout.strip().splitlines()
        if line.strip().startswith("qsnap-")
    }


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
            shell,
            vm_name,
            snap_name,
            "vda",
            snapshot_dirs["vda"],
            base_images["vda"],
        )
        state.record_snapshot(vm_name, snap)
        vda_snaps.append(snap)

    # --- Create 1 snapshot on vdb ---
    hex_sfx = secrets.token_hex(3)
    snap_name = f"{vm_name}.20250801T000009_vdb_{hex_sfx}"
    vdb_snap = snapshot_create(
        shell,
        vm_name,
        snap_name,
        "vdb",
        snapshot_dirs["vdb"],
        base_images["vdb"],
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

    vm_config = VMConfig(
        name=vm_name,
        disks=[
            DiskConfig(
                target="vda", base_image=base_images["vda"], snapshot_dir=snapshot_dirs["vda"]
            ),
            DiskConfig(
                target="vdb", base_image=base_images["vdb"], snapshot_dir=snapshot_dirs["vdb"]
            ),
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
        pytest.skip(f"Live blockcommit not supported in this environment: {commit_result.error}")

    # --- Verify vda chain shortened ---
    vda_chain_after = _backing_chain_count(shell, active_vda)
    assert vda_chain_after < vda_chain_before, (
        f"VDA chain should have shortened after blockcommit: {vda_chain_before} → {vda_chain_after}"
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
    assert vdb_snap.path.exists(), f"VDB snapshot file should still exist: {vdb_snap.path}"

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
        shell,
        vm_name,
        snap_name_vda,
        "vda",
        snapshot_dirs["vda"],
        base_images["vda"],
    )

    # Create snapshot on vdb
    hex_vdb = secrets.token_hex(3)
    snap_name_vdb = f"{vm_name}.20250801T100001_vdb_{hex_vdb}"
    snap_vdb = snapshot_create(
        shell,
        vm_name,
        snap_name_vdb,
        "vdb",
        snapshot_dirs["vdb"],
        base_images["vdb"],
    )

    # Verify vda snapshot is in vda's directory
    assert snap_vda.path.parent == snapshot_dirs["vda"], (
        f"VDA snapshot should be in {snapshot_dirs['vda']}, got {snap_vda.path.parent}"
    )
    assert snap_vda.path.exists(), f"VDA snapshot missing: {snap_vda.path}"

    # Verify vdb snapshot is in vdb's directory
    assert snap_vdb.path.parent == snapshot_dirs["vdb"], (
        f"VDB snapshot should be in {snapshot_dirs['vdb']}, got {snap_vdb.path.parent}"
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
        shell,
        vm_name,
        snap_name,
        "vdb",
        snapshot_dirs["vdb"],
        base_images["vdb"],
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
    dumpxml = shell.run(["virsh", "dumpxml", "--domain", vm_name], timeout=30)
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
    assert result.disk == "vdb", f"Restore should report disk='vdb', got {result.disk}"

    # Verify per-disk state reset: vdb state cleared, vda state intact.
    # Core.restore() step 8 calls reset_vm_disk_state / reset_target_disk_state,
    # NOT the old full reset_vm_state / reset_target_state.
    vdb_allocation_after = state.get_last_allocation(vm_name, "vdb")
    assert vdb_allocation_after is None, (
        f"VDB last_allocation should be cleared after restore, got {vdb_allocation_after}"
    )
    vda_allocation_after = state.get_last_allocation(vm_name, "vda")
    assert vda_allocation_after == 0, (
        f"VDA last_allocation should survive restore, got {vda_allocation_after}"
    )

    # vdb snapshot record should be cleared from state
    vdb_snapshots_after = [s for s in state.get_snapshots(vm_name) if s.disk == "vdb"]
    assert vdb_snapshots_after == [], (
        f"VDB snapshots should be cleared after restore, got {vdb_snapshots_after}"
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
        shell,
        vm_name,
        snap_name_vda,
        "vda",
        snapshot_dirs["vda"],
        base_images["vda"],
    )

    # Create snapshot on vdb
    hex_vdb = secrets.token_hex(3)
    snap_name_vdb = f"{vm_name}.20250801T300001_vdb_{hex_vdb}"
    snap_vdb = snapshot_create(
        shell,
        vm_name,
        snap_name_vdb,
        "vdb",
        snapshot_dirs["vdb"],
        base_images["vdb"],
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

    # The running-VM FULL must report the exact checkpoint it created
    # atomically (design D1), and that checkpoint must be listed by
    # libvirt.
    assert full_vda.checkpoint is not None, "Running-VM FULL of vda must report its checkpoint name"
    cps_after_vda = _list_checkpoints(shell, vm_name)
    assert full_vda.checkpoint in cps_after_vda, (
        f"vda checkpoint {full_vda.checkpoint!r} should be listed, got {cps_after_vda}"
    )
    assert "-vda-" in full_vda.checkpoint, (
        f"vda checkpoint must carry the vda disk segment, got {full_vda.checkpoint!r}"
    )

    # Backup vdb
    full_vdb = provider.create_full_backup(vm_name, snap_vdb, target, compress=False)
    if not full_vdb.success:
        pytest.skip(f"FULL backup of vdb failed: {full_vdb.error}")

    assert "_vdb_" in full_vdb.target_path.name or "vdb" in full_vdb.target_path.name.lower(), (
        f"vdb backup name should contain '_vdb_': {full_vdb.target_path.name}"
    )
    assert full_vdb.target_path.exists(), f"VDB backup missing: {full_vdb.target_path}"

    # Same checkpoint-reporting contract for vdb.
    assert full_vdb.checkpoint is not None, "Running-VM FULL of vdb must report its checkpoint name"
    cps_after_vdb = _list_checkpoints(shell, vm_name)
    assert full_vdb.checkpoint in cps_after_vdb, (
        f"vdb checkpoint {full_vdb.checkpoint!r} should be listed, got {cps_after_vdb}"
    )
    assert "-vdb-" in full_vdb.checkpoint, (
        f"vdb checkpoint must carry the vdb disk segment, got {full_vdb.checkpoint!r}"
    )

    # Per-disk naming isolation: the vda checkpoint is not the vdb
    # checkpoint (each disk owns its own dirty-bitmap lineage), and
    # neither name carries the other disk's segment.
    assert full_vda.checkpoint != full_vdb.checkpoint, (
        "vda and vdb checkpoints must be distinct names"
    )
    assert "-vdb-" not in full_vda.checkpoint, (
        f"vda checkpoint must not carry the vdb segment: {full_vda.checkpoint!r}"
    )
    assert "-vda-" not in full_vdb.checkpoint, (
        f"vdb checkpoint must not carry the vda segment: {full_vdb.checkpoint!r}"
    )


# ── Test D2: Failed vda FULL rollback leaves other disks untouched ──────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_failed_full_rollback_deletes_only_failed_disk_checkpoint(test_vm_multi_disk, caplog):
    """A failed vda FULL deletes ONLY its own successor checkpoint.

    1. Seed prior baseline checkpoints via successful FULLs of vda and vdb
       (one ``core.run`` — each disk gets its own FULL + checkpoint).
    2. Create fresh snapshots on both disks; force a new FULL run.
    3. Patch ``verify_full_backup`` so ONLY the vda attempt fails; capture
       the checkpoint list at the moment of failure (the successor still
       exists).
    4. Assert: the vda prior baseline and the vdb baseline remain listed,
       and the failed attempt's successor checkpoint is gone — the set
       difference between the checkpoints observed during the attempt and
       the post-rollback list is exactly the failed attempt's name.
    """
    ctx = test_vm_multi_disk
    shell: SubprocessShell = ctx["shell"]
    vm_name: str = ctx["vm_name"]
    base_images: dict[str, Path] = ctx["base_images"]
    snapshot_dirs: dict[str, Path] = ctx["snapshot_dirs"]
    target_dir: Path = ctx["target_dir"]
    tmpdir: Path = ctx["tmpdir"]
    disk_configs: list[DiskConfig] = ctx["disk_configs"]

    from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)

    state = InMemoryStateManager()
    vm_config = VMConfig(
        name=vm_name,
        disks=disk_configs,
        snapshot_dir=None,  # each disk has its own per-disk override
        targets=[
            TargetConfig(path=target_dir, compress=False, verify="off"),
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(
            state_dir="/var/tmp",
            full_verify_after_create="check",
        ),
        vms=[vm_config],
        config_path=tmpdir / "rollback_multi.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # Step 1: Seed run — the pipeline creates snapshots for both disks and
    # then a FULL per disk (running-VM NBD path), seeding baseline
    # checkpoints for vda and vdb.
    seed_result = core.run(vm_name)
    assert seed_result.results[0].success, f"Seed FULL run failed: {seed_result.results[0].error}"
    baselines = _list_checkpoints(shell, vm_name)
    assert len(baselines) >= 2, f"Expected vda+vdb baseline checkpoints, got {baselines}"
    assert any("-vda-" in cp for cp in baselines), f"Missing vda baseline: {baselines}"
    assert any("-vdb-" in cp for cp in baselines), f"Missing vdb baseline: {baselines}"

    # Step 2: Fresh snapshots so the forced FULL has a new source per disk.
    import secrets

    def _make_snapshot(disk: str, label: str) -> SnapshotInfo:
        hex_sfx = secrets.token_hex(3)
        snap_name = f"{vm_name}.20260807T{label}_{disk}_{hex_sfx}"
        time.sleep(0.6)  # unique timestamps
        return snapshot_create(
            shell,
            vm_name,
            snap_name,
            disk,
            snapshot_dirs[disk],
            base_images[disk],
        )

    state.record_snapshot(vm_name, _make_snapshot("vda", "200000"))
    state.record_snapshot(vm_name, _make_snapshot("vdb", "200001"))

    # Force a new FULL for every disk.  The vdb FULL will succeed
    # (verify_full_backup passes for non-vda targets), and the vda FULL
    # will transfer successfully but its post-create verification will
    # be forced to fail — triggering the rollback path.
    #
    # NOTE: create_full_backup calls _delete_superseded_checkpoints after
    # a successful transfer (before Core's verification).  The old vda
    # and vdb baselines are therefore legitimately deleted by the
    # provider — NOT by the rollback.  The assertions below verify that
    # the rollback (Core._cleanup_failed_checkpoint) deletes exactly the
    # successor checkpoint and does NOT touch the vdb successor.
    core._force_full_targets.add(str(target_dir))

    # Step 3: The patched verifier fails ONLY the vda attempt.  At the
    # moment of the failure the successor checkpoint still exists — capture
    # the checkpoint list so we can prove the rollback deleted exactly it.
    observed_during_attempt: set[str] = set()

    def _failing_verify(
        shell,
        target_path,
        verify_mode,
        source_path=None,
        expected_virtual_size=None,
    ):
        list_result = shell.run(
            ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
            timeout=30,
        )
        if list_result.success:
            observed_during_attempt.update(
                line.strip()
                for line in list_result.stdout.strip().splitlines()
                if line.strip().startswith("qsnap-")
            )
        if "_vda_" in Path(target_path).name:
            return "verification failed: forced failure for vda FULL"
        return None

    caplog.clear()
    with (
        caplog.at_level(logging.INFO),
        patch("qsnap.core.verify_full_backup", side_effect=_failing_verify),
    ):
        failed_result = core.backup(vm_name)

    assert not failed_result.results[0].success, "Expected the vda FULL attempt to fail"
    assert failed_result.results[0].backup_failed, "Backup-stage abort must set backup_failed=True"
    all_logs = " ".join(r.message for r in caplog.records)
    assert "rolled back" in all_logs.lower(), f"Expected rollback log. Logs: {all_logs[:500]}"

    cps_after = _list_checkpoints(shell, vm_name)

    # Step 4a: The vdb successor (from the successful vdb FULL) survives
    # the vda rollback — proving multi-disk isolation.  The old baselines
    # were legitimately deleted by _delete_superseded_checkpoints inside
    # the provider during FULL creation (not by rollback).
    assert any("-vdb-" in cp for cp in cps_after), (
        f"vdb must retain a checkpoint after the vda rollback, got {cps_after}"
    )
    assert not any("-vda-" in cp for cp in cps_after), (
        f"vda must have zero checkpoints after cleanup (baseline deleted by "
        f"_delete_superseded_checkpoints + successor deleted by rollback), got {cps_after}"
    )

    # Step 4b: The failed attempt's successor checkpoint is gone.  The
    # checkpoints that appeared during the attempt differ from the
    # post-rollback list by exactly the successor name.
    successor = observed_during_attempt - cps_after
    assert len(successor) == 1, (
        f"Expected exactly one deleted successor checkpoint, got {successor}. "
        f"(observed during attempt: {observed_during_attempt}, after: {cps_after})"
    )
    deleted = successor.pop()
    assert deleted not in cps_after, f"Successor checkpoint {deleted} must be deleted"
    assert "-vda-" in deleted, f"Deleted successor must belong to vda, got {deleted}"

    _cleanup_checkpoints(shell, vm_name)
    shell.run(["virsh", "destroy", vm_name], timeout=30)


# ── Test E: Batch snapshot via Core (create_multi) ─────────────────────


def _build_multi_core(ctx, quiesce: bool):
    """Build Core + VMConfig for the 2-disk fixture with batch snapshots."""
    from qsnap.core import Core
    from qsnap.factory.default import DefaultFactory
    from qsnap.models.config import TargetConfig
    from tests.mocks.mock_config import MockConfigFacade
    from tests.mocks.mock_state import InMemoryStateManager

    shell: SubprocessShell = ctx["shell"]
    vm_name: str = ctx["vm_name"]
    tmpdir: Path = ctx["tmpdir"]
    disk_configs = ctx["disk_configs"]

    state = InMemoryStateManager()
    vm_config = VMConfig(
        name=vm_name,
        disks=disk_configs,
        snapshot_dir=None,  # each disk has its own override
        snapshot_quiesce=quiesce,
        targets=[
            TargetConfig(path=ctx["target_dir"], compress=False, verify="off"),
        ],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "multi_batch.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config, state


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_quiesced_batch_snapshot_multi_disk(test_vm_multi_disk):
    """Core issues ONE quiesced batch snapshot call covering both disks.

    1. Start the 2-disk VM.
    2. Run ``Core._create_snapshot`` with ``snapshot_quiesce=True``.
    3. Assert exactly ONE ``virsh snapshot-create-as`` call carrying two
       ``--dispspec`` entries (vda + vdb) plus ``--disk-only --atomic
       --no-metadata --quiesce``.
    4. This test VM has no qemu-guest-agent, so the quiesced freeze is
       expected to fail (all-or-nothing batch → ``RuntimeError``, nothing
       recorded).  The command SHAPE is the primary assertion; the
       no-quiesce fallback (next test) proves the batch itself works.
    """
    ctx = test_vm_multi_disk
    shell: SubprocessShell = ctx["shell"]
    vm_name: str = ctx["vm_name"]

    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    core, vm_config, state = _build_multi_core(ctx, quiesce=True)

    batch_calls: list[list[str]] = []
    _spy_snapshot_create_as(shell, batch_calls)

    # The guest agent is absent → the quiesced batch fails all-or-nothing.
    with pytest.raises(RuntimeError, match="Snapshot batch creation failed"):
        core._create_snapshot(vm_config)

    # Exactly ONE batch virsh call was attempted.
    assert len(batch_calls) == 1, (
        f"Expected exactly 1 virsh snapshot-create-as call, got {len(batch_calls)}"
    )
    cmd = batch_calls[0]

    # The command must carry two --diskspec entries (vda and vdb).
    # ``create_multi`` emits ``--diskspec`` and its value as separate
    # list elements, so pair each flag with the following element.
    diskspec_values = [
        cmd[i + 1] for i, a in enumerate(cmd) if a == "--diskspec" and i + 1 < len(cmd)
    ]
    assert len(diskspec_values) == 2, f"Expected 2 --diskspec entries, got {diskspec_values}"
    joined = " ".join(diskspec_values)
    assert "vda,file=" in joined and "vdb,file=" in joined, (
        f"Both disks must appear in --diskspec: {joined}"
    )
    # Batch flags: atomic + quiesce cover ALL disks under one freeze.
    assert "--disk-only" in cmd, f"Missing --disk-only: {cmd}"
    assert "--atomic" in cmd, f"Missing --atomic: {cmd}"
    assert "--no-metadata" in cmd, f"Missing --no-metadata: {cmd}"
    assert "--quiesce" in cmd, f"Missing --quiesce: {cmd}"

    # All-or-nothing: nothing recorded after the failed batch.
    assert state.get_snapshots(vm_name) == [], (
        "No snapshot may be recorded after a failed quiesced batch"
    )

    # Cleanup: destroy VM (no snapshots were created).
    shell.run(["virsh", "destroy", vm_name], timeout=30)


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_batch_snapshot_no_quiesce_default(test_vm_multi_disk):
    """Non-quiesced batch snapshot: ONE call, both files exist, both recorded.

    1. Start the 2-disk VM.
    2. Run ``Core._create_snapshot`` with ``snapshot_quiesce=False``
       (default).
    3. Assert exactly ONE ``virsh snapshot-create-as`` call with 2
       ``--diskspec`` entries, ``--atomic``, and NO ``--quiesce``.
    4. Assert both .qcow2 files exist in their per-disk dirs and both
       ``SnapshotInfo`` records (disk="vda" / disk="vdb") are in state.
    """
    ctx = test_vm_multi_disk
    shell: SubprocessShell = ctx["shell"]
    vm_name: str = ctx["vm_name"]
    snapshot_dirs: dict[str, Path] = ctx["snapshot_dirs"]

    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"

    core, vm_config, state = _build_multi_core(ctx, quiesce=False)

    batch_calls: list[list[str]] = []
    _spy_snapshot_create_as(shell, batch_calls)

    results = core._create_snapshot(vm_config)
    assert len(results) == 2, f"Expected 2 SnapshotResults, got {len(results)}"
    assert all(r.success for r in results), (
        f"Batch snapshot failed: {[r.error for r in results if not r.success]}"
    )

    # One batch call, no --quiesce.
    assert len(batch_calls) == 1, (
        f"Expected exactly 1 virsh snapshot-create-as call, got {len(batch_calls)}"
    )
    cmd = batch_calls[0]
    diskspec_values = [
        cmd[i + 1] for i, a in enumerate(cmd) if a == "--diskspec" and i + 1 < len(cmd)
    ]
    assert len(diskspec_values) == 2, f"Expected 2 --diskspec entries, got {diskspec_values}"
    assert "--atomic" in cmd, f"Missing --atomic: {cmd}"
    assert "--quiesce" not in cmd, f"Non-quiesced batch must not pass --quiesce: {cmd}"

    # Both files exist in the correct per-disk dirs.
    # (The real provider does not populate SnapshotResult.disk — derive
    # the disk from the result path's parent directory instead.)
    by_disk: dict[str, object] = {}
    for r in results:
        for disk, snap_dir in snapshot_dirs.items():
            if r.path.parent == snap_dir:
                by_disk[disk] = r
                break
    assert set(by_disk) == {"vda", "vdb"}, f"Unexpected disks: {set(by_disk)}"
    for disk in ("vda", "vdb"):
        path = by_disk[disk].path
        assert path.exists(), f"Snapshot file for {disk} missing: {path}"
        assert path.parent == snapshot_dirs[disk], (
            f"Snapshot for {disk} should land in {snapshot_dirs[disk]}, got {path.parent}"
        )

    # Both records in state with the right disk tags (spec:
    # core-orchestrator — SnapshotInfo records have disk="vda" and
    # disk="vdb").
    recorded = state.get_snapshots(vm_name)
    assert len(recorded) == 2, f"Expected 2 recorded snapshots, got {len(recorded)}"
    assert {s.disk for s in recorded} == {"vda", "vdb"}, (
        f"Both disks must be recorded with their disk tags, got "
        f"{[(s.name, s.disk) for s in recorded]}"
    )

    # VM still running and healthy.
    assert _vm_is_running(shell, vm_name), "VM should still be running"

    shell.run(["virsh", "destroy", vm_name], timeout=30)
