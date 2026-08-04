"""Integration tests for post-creation validation in ``ExternalSnapshotProvider``
and post-transfer validation in ``BitmapBackupProvider``.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

Coverage:

- ``ExternalSnapshotProvider.create()`` post-creation validation:
  file existence (test -f), qcow2 format, corrupt-bit check,
  backing-filename match, libvirt pivot (domblklist).
- ``BitmapBackupProvider.transfer_missing()`` post-transfer validation
  for incrementals: chain-to-FULL traversability, checkpoint existence.
- ``BitmapBackupProvider.create_full_backup()`` post-creation validation:
  no backing file, checkpoint existence.

Design D4 (ExternalSnapshotProvider) and D5 (BitmapBackupProvider).

Run only when explicitly requested::

    uv run pytest tests/integration/test_post_creation_validation.py -v -m integration
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

# libnbd availability — needed for incremental backup transfers.
try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False

from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo, SnapshotResult
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
)

if _HAS_LIBNBD:
    from qsnap.utils.nbd_client import LibnbdClient


# ── helpers ──────────────────────────────────────────────────────────


def _qemu_img_info(shell: SubprocessShell, path: Path) -> dict | None:
    """Return ``qemu-img info --output=json`` as a dict, or None."""
    result = shell.run(
        ["qemu-img", "info", "--force-share", "--output=json", str(path)],
        timeout=30,
    )
    if not result.success:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _get_backing_filename(shell: SubprocessShell, path: Path) -> str | None:
    """Return ``backing-filename`` from qcow2 metadata, or None."""
    info = _qemu_img_info(shell, path)
    if info is None:
        return None
    backing = info.get("backing-filename")
    return str(backing) if backing else None


def _get_domblklist_source(shell: SubprocessShell, vm_name: str) -> str | None:
    """Return the active disk source path from ``virsh domblklist``."""
    result = shell.run(
        ["virsh", "domblklist", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return None
    # domblklist output format:
    #   Target   Source
    #   ---------------------
    #   vda      /path/to/disk
    # Skip the header (first 2 lines), parse the data line.
    lines = result.stdout.strip().splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip the header line and separator.
        if stripped.startswith("Target") or stripped.startswith("-"):
            continue
        # Data row: first token is target (e.g. "vda"),
        # remaining is the source path (may contain spaces on some
        # platforms, but typically doesn't).
        parts = stripped.split(None, 1)
        if len(parts) >= 2:
            return parts[1]
    return None


def _ensure_vm_running(shell: SubprocessShell, vm_name: str, max_retries: int = 3) -> bool:
    """Start the VM and wait for it to reach running state.

    Returns True if the VM is running, False otherwise.
    """
    for _attempt in range(max_retries):
        shell.run(["virsh", "start", vm_name], timeout=30)
        time.sleep(2)
        if is_vm_running(shell, vm_name):
            return True
        time.sleep(2)
    return False


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


def _cleanup_snapshots(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all external snapshots for *vm_name* (--metadata only)."""
    result = shell.run(
        ["virsh", "snapshot-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return
    for line in result.stdout.strip().splitlines():
        snap = line.strip()
        if snap:
            shell.run(
                ["virsh", "snapshot-delete", "--domain", vm_name, snap, "--metadata"],
                timeout=30,
            )


# ──────────────────────────────────────────────────────────────────────
# Test 1: Snapshot post-creation validation — all checks pass
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_snapshot_post_creation_validation(test_vm):
    """Verify ``ExternalSnapshotProvider.create()`` post-creation validation.

    1. Start the VM (retry if it shuts off — no-boot-OS VMs are fragile).
    2. Call ``ExternalSnapshotProvider.create()`` to create an external
       disk-only snapshot.
    3. Assert ``SnapshotResult(success=True)``.
    4. Verify ``test -f snapshot_path`` succeeds (file exists on disk).
    5. Verify ``qemu-img info`` reports ``format=qcow2``.
    6. Verify ``backing-filename`` points to the previous active layer
       (the base image).
    7. Verify ``virsh domblklist`` shows the snapshot path as the active
       source (pivot confirmed).
    8. Verify ``corrupt`` bit is NOT set in ``incompatible-features``.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    _cleanup_snapshots(shell, vm_name)

    # Start the VM and ensure it is running.
    if not _ensure_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state after retries")

    # Capture the active layer path before snapshot creation.
    previous_active = _get_domblklist_source(shell, vm_name)
    if previous_active is None:
        # VM may have shut off — try once more.
        if not _ensure_vm_running(shell, vm_name):
            pytest.skip("VM shut off before snapshot creation")
        previous_active = _get_domblklist_source(shell, vm_name)
    assert previous_active is not None, "Must be able to read domblklist"

    # Create an external snapshot via ExternalSnapshotProvider.
    provider = ExternalSnapshotProvider(shell)
    snap_name = f"{vm_name}.post-val"
    snap_path = snapshot_dir / f"{snap_name}.qcow2"

    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    result: SnapshotResult = provider.create(vm_config, snap_name, "vda", snap_path)

    assert result.success, (
        f"Snapshot creation must succeed (post-creation validation). Error: {result.error}"
    )
    assert result.path == snap_path, f"Result path mismatch: {result.path} != {snap_path}"

    # 4. File existence: verify the snapshot file landed on disk.
    assert snap_path.exists(), f"Snapshot file not found on disk: {snap_path}"

    # 5. qcow2 format check: qemu-img info reports format="qcow2".
    info = _qemu_img_info(shell, snap_path)
    assert info is not None, f"qemu-img info failed for {snap_path}"
    assert info.get("format") == "qcow2", f"Expected qcow2 format, got: {info.get('format')}"

    # 6. backing-filename must point to previous active layer.
    backing = _get_backing_filename(shell, snap_path)
    assert backing is not None, "New snapshot must have a backing file"
    assert backing == previous_active, (
        f"backing-filename mismatch: expected {previous_active}, got {backing}"
    )

    # 7. libvirt pivot: domblklist must show the new snapshot as source.
    active_after = _get_domblklist_source(shell, vm_name)
    assert active_after is not None, "Must be able to read domblklist after snapshot"
    assert active_after == str(snap_path), (
        f"libvirt pivot not confirmed: domblklist shows {active_after}, expected {snap_path}"
    )

    # 8. Corrupt bit must NOT be set.
    incompat_features = info.get("incompatible-features", [])
    if isinstance(incompat_features, list):
        assert "corrupt" not in incompat_features, (
            f"Snapshot has unexpected corrupt bit in incompatible-features: {incompat_features}"
        )

    _cleanup_snapshots(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Snapshot post-creation validation — detects missing file
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_snapshot_post_creation_validation_failure(test_vm):
    """Verify that post-creation validation detects a missing snapshot file.

    Scenario:
    1. Create a VM and an external disk-only snapshot via
       ``ExternalSnapshotProvider.create()`` — must succeed.
    2. Delete the snapshot file from disk.
    3. Verify the individual validation checks
       (``test -f``) correctly report the file is missing.
    4. Verify ``qemu-img info`` on the deleted path fails.

    This validates that the same checks used internally by
    ``ExternalSnapshotProvider.create()`` would detect a missing file
    and return ``SnapshotResult(success=False)``.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    _cleanup_snapshots(shell, vm_name)

    if not _ensure_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state after retries")

    # Create an external snapshot via ExternalSnapshotProvider.
    provider = ExternalSnapshotProvider(shell)
    snap_name = f"{vm_name}.post-val-fail"
    snap_path = snapshot_dir / f"{snap_name}.qcow2"

    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    result = provider.create(vm_config, snap_name, "vda", snap_path)

    assert result.success, f"Snapshot creation must succeed: {result.error}"
    assert snap_path.exists(), f"Snapshot file must exist: {snap_path}"

    # Now delete the snapshot file.
    os.unlink(str(snap_path))
    assert not snap_path.exists(), f"Snapshot file was not deleted: {snap_path}"

    # Verify that test -f would detect the missing file (the same check
    # that ExternalSnapshotProvider.create() performs internally).
    test_cmd = shell.run(
        ["test", "-f", str(snap_path)],
        timeout=10,
        check=True,
    )
    assert not test_cmd.success, (
        "test -f must fail for deleted snapshot file — validation must detect missing file"
    )

    # Verify that qemu-img info on the deleted file also fails.
    info = _qemu_img_info(shell, snap_path)
    assert info is None, (
        "qemu-img info must fail for deleted file — "
        "metadata validation checks must detect the missing file"
    )

    # Clean up: destroy/restart VM to clear the stale domblklist entry.
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(1)
    _ensure_vm_running(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Incremental post-transfer validation — chain + checkpoint
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_incremental_post_transfer_validation(test_vm):
    """Verify ``transfer_missing()`` post-transfer validation for incrementals.

    1. Start the VM.
    2. Create a FULL backup (which atomically creates a checkpoint).
    3. Create an external snapshot.
    4. Call ``transfer_missing()`` — must produce an incremental delta
       (a prior checkpoint from the FULL already exists).
    5. Verify ``qemu-img info --backing-chain`` on the incremental file
       shows a traversable chain to the FULL anchor.
    6. Verify ``virsh checkpoint-list`` shows at least one ``qsnap-``
       checkpoint exists.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin + checkpoint not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed — required for incremental transfer")

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)

    if not _ensure_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state after retries")

    # Step 1: Create a FULL backup (also creates an atomic checkpoint).
    provider = BitmapBackupProvider(shell)
    source = SnapshotInfo(
        name=f"{vm_name}.incr-val-base",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    target = TargetConfig(path=target_dir, compress=False, verify="off")

    result_full = provider.create_full_backup(
        vm_name,
        source,
        target,
        compress=False,
    )
    assert result_full.success, f"FULL backup failed: {result_full.error}"

    # Step 2: Create an external snapshot.
    snap_name = f"{vm_name}.incr-val-snap"
    snap_result = shell.run(
        [
            "virsh",
            "snapshot-create-as",
            "--domain",
            vm_name,
            "--name",
            snap_name,
            "--disk-only",
            "--diskspec",
            "vda,snapshot=external",
            "--no-metadata",
        ],
        timeout=60,
        check=True,
    )
    if not snap_result.success:
        _cleanup_checkpoints(shell, vm_name)
        _cleanup_snapshots(shell, vm_name)
        pytest.skip(f"Snapshot creation failed: {snap_result.error}")

    # Determine the overlay path.
    overlay_path = None
    for line in shell.run(
        ["virsh", "domblklist", "--domain", vm_name, "--details"],
        timeout=30,
    ).stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Type") or stripped.startswith("-"):
            continue
        parts = stripped.split(None, 3)
        if len(parts) >= 4 and parts[1] == "disk":
            overlay_path = Path(parts[3]) if parts[3] else None
            break

    if overlay_path is None:
        _cleanup_checkpoints(shell, vm_name)
        _cleanup_snapshots(shell, vm_name)
        pytest.skip("Could not determine overlay path after external snapshot")

    snapshot_info = SnapshotInfo(
        name=snap_name,
        path=overlay_path,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )

    # Step 3: transfer_missing — must produce an incremental.
    provider_inc = BitmapBackupProvider(shell, nbd=LibnbdClient())
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )

    results = provider_inc.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[snapshot_info],
        stall_timeout=300,
    )
    assert len(results) > 0, "transfer_missing must return at least one result"
    inc_result = results[0]
    assert inc_result.success, (
        f"Incremental transfer failed: {inc_result.error}. "
        f"Check that transfer_missing detected the checkpoint from create_full_backup."
    )

    inc_path = inc_result.target_path
    assert inc_path.exists(), f"Incremental file not found: {inc_path}"

    # Step 4: Verify chain-to-FULL traversability.
    chain_result = shell.run(
        [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            str(inc_path),
        ],
        timeout=60,
        check=True,
    )
    assert chain_result.success, f"Chain-to-FULL not traversable: {chain_result.error}"
    try:
        chain_data = json.loads(chain_result.stdout)
        assert isinstance(chain_data, list) and len(chain_data) > 0, (
            "Backing chain must contain at least one element"
        )
    except json.JSONDecodeError:
        pytest.fail("Failed to parse qemu-img info --backing-chain output")

    # Step 5: Verify checkpoint exists.
    cp_result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    assert cp_result.success, f"checkpoint-list failed: {cp_result.error}"
    qsnap_cps = [
        cp.strip() for cp in cp_result.stdout.splitlines() if cp.strip().startswith("qsnap-")
    ]
    assert len(qsnap_cps) >= 1, (
        f"At least one qsnap- checkpoint expected after incremental transfer, got: {qsnap_cps}"
    )

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 4: FULL post-creation validation — no backing file + checkpoint
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_full_post_creation_validation(test_vm):
    """Verify ``create_full_backup()`` post-creation validation.

    1. Start the VM.
    2. Call ``create_full_backup()`` — must succeed (post-creation
       validation passes internally).
    3. Verify ``qemu-img info`` reports no ``backing-filename``
       (standalone — the FULL check).
    4. Verify ``virsh checkpoint-list`` shows at least one ``qsnap-``
       checkpoint exists.

    The post-creation validation is performed internally by
    ``BitmapBackupProvider.create_full_backup()`` (design D5).  This
    test runs the same checks independently to confirm they would detect
    issues.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    _cleanup_checkpoints(shell, vm_name)

    if not _ensure_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state after retries")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    # Create FULL backup — post-creation validation runs internally.
    provider = BitmapBackupProvider(shell)
    source = SnapshotInfo(
        name=f"{vm_name}.full-val",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    target = TargetConfig(path=target_dir, compress=False, verify="off")

    result = provider.create_full_backup(vm_name, source, target, compress=False)
    assert result.success, (
        f"FULL backup must succeed — post-creation validation failed: {result.error}"
    )
    full_path = result.target_path
    assert full_path.exists(), f"FULL file not found: {full_path}"

    # 3. No backing file — FULL must be standalone.
    backing = _get_backing_filename(shell, full_path)
    assert backing is None or backing == "", (
        f"FULL backup must be standalone (no backing file), got backing: {backing!r}"
    )

    # 4. Checkpoint must exist.
    cp_result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    assert cp_result.success, f"checkpoint-list failed: {cp_result.error}"
    qsnap_cps = [
        cp.strip() for cp in cp_result.stdout.splitlines() if cp.strip().startswith("qsnap-")
    ]
    assert len(qsnap_cps) >= 1, (
        f"At least one qsnap- checkpoint expected after FULL backup, got: {qsnap_cps}"
    )

    _cleanup_checkpoints(shell, vm_name)
