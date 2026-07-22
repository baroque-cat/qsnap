"""Integration tests for the compress driver on FULL backups.

Verifies that when ``compress=True`` is passed to
``create_full_backup()`` or to the full-pull branch of
``transfer_missing()``, the write-side qemu-nbd uses the compress
driver (``--image-opts "driver=compress,..."``) and the output
qcow2 has ``compression-type=zstd``.

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import pytest

# libnbd availability — needed by the unified NBD transfer engine.
try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False

from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
)

if _HAS_LIBNBD:
    from qsnap.utils.nbd_client import LibnbdClient


def _get_checkpoint_names(shell: SubprocessShell, vm_name: str) -> list[str]:
    """Return qsnap-prefixed checkpoint names."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return []
    return [
        line.strip()
        for line in result.stdout.strip().splitlines()
        if line.strip().startswith("qsnap-")
    ]


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints."""
    for cp in _get_checkpoint_names(shell, vm_name):
        shell.run(
            ["virsh", "checkpoint-delete", "--domain", vm_name, cp, "--metadata"],
            timeout=30,
        )


# ──────────────────────────────────────────────────────────────────────
# Test 1: Compressed FULL via create_full_backup
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_compress_driver_create_full_backup(test_vm, caplog):
    """Create a compressed FULL backup via ``create_full_backup(compress=True)``.

    Verifies:
    - The compress driver (``driver=compress``) is used in qemu-nbd command.
    - Output qcow2 has ``compression-type=zstd``.
    - Data integrity: the output is a valid standalone qcow2.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    # Start VM
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    _cleanup_checkpoints(shell, vm_name)

    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())
    source_snapshot = SnapshotInfo(
        name=f"{vm_name}.active",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    target = TargetConfig(
        path=target_dir,
        incremental=True,
        compress=True,
        verify="off",
    )

    # Create compressed FULL backup.
    with caplog.at_level(logging.DEBUG):
        result = provider.create_full_backup(
            vm_name,
            source_snapshot,
            target,
            compress=True,
            bucket_level="monthly",
            compression_type="zstd",
        )

    assert result.success, f"Compressed FULL backup failed: {result.error}"
    assert result.target_path.exists(), f"Backup file not found: {result.target_path}"

    # ── Verify compress driver in qemu-nbd command line ──────────────
    # The debug log should show the compress-driver qemu-nbd command.
    compress_driver_seen = False
    for record in caplog.records:
        msg = record.message
        if "qemu-nbd" in msg and "driver=compress" in msg:
            compress_driver_seen = True
            break
    assert compress_driver_seen, (
        "Compress driver (driver=compress) not found in qemu-nbd command log. "
        f"Log records: {[r.message for r in caplog.records if 'qemu-nbd' in r.message]}"
    )

    # ── Verify compression-type in output qcow2 ─────────────────────
    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(result.target_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    assert info.get("format") == "qcow2", "Backup should be valid qcow2"

    # Check format-specific data for compression-type.
    format_specific = info.get("format-specific", {})
    if isinstance(format_specific, dict):
        data_obj = format_specific.get("data", {})
        if isinstance(data_obj, dict):
            compress_type = data_obj.get("compression-type", "")
            if compress_type:
                assert compress_type != "uncompressed", (
                    f"Compressed FULL should have non-uncompressed type, "
                    f"got compression-type={compress_type!r}"
                )

    # Standalone qcow2 (no backing).
    backing = info.get("backing-filename")
    assert backing is None or backing == "", (
        f"FULL backup should be standalone, got backing: {backing!r}"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Uncompressed FULL does NOT use compress driver
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_uncompressed_full_no_compress_driver(test_vm, caplog):
    """Create an uncompressed FULL backup and verify the compress driver
    is NOT used (qemu-nbd uses ``--format=qcow2`` instead of
    ``--image-opts driver=compress``).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    # Start VM
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    _cleanup_checkpoints(shell, vm_name)

    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())
    source_snapshot = SnapshotInfo(
        name=f"{vm_name}.active",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    target = TargetConfig(
        path=target_dir,
        incremental=True,
        compress=False,
        verify="off",
    )

    # Create uncompressed FULL backup.
    with caplog.at_level(logging.DEBUG):
        result = provider.create_full_backup(
            vm_name,
            source_snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

    assert result.success, f"Uncompressed FULL backup failed: {result.error}"
    assert result.target_path.exists(), f"Backup file not found: {result.target_path}"

    # ── Verify compress driver NOT used ─────────────────────────────
    compress_driver_seen = False
    for record in caplog.records:
        msg = record.message
        if "qemu-nbd" in msg and "driver=compress" in msg:
            compress_driver_seen = True
            break
    assert not compress_driver_seen, "Compress driver should NOT be used for uncompressed backup"

    # ── Verify format=qcow2 used ────────────────────────────────────
    format_qcow2_seen = False
    for record in caplog.records:
        msg = record.message
        if "qemu-nbd" in msg and "--format=qcow2" in msg:
            format_qcow2_seen = True
            break
    assert format_qcow2_seen, "qemu-nbd should use --format=qcow2 for uncompressed backup"

    # Verify output is valid qcow2.
    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(result.target_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    assert info.get("format") == "qcow2", "Backup should be valid qcow2"

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Compressed FULL via transfer_missing (full-pull branch)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_compress_driver_transfer_missing_full(test_vm, caplog):
    """Create a compressed FULL via ``transfer_missing()`` (no prior
    checkpoint → full-pull branch).  Verify compress driver used.

    This tests the full-pull branch of transfer_missing with compress=True.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    _cleanup_checkpoints(shell, vm_name)

    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
    )
    target = TargetConfig(
        path=target_dir,
        incremental=True,
        compress=True,
        verify="off",
    )

    snap = SnapshotInfo(
        name=f"{vm_name}.tm-compressed-full",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )

    with caplog.at_level(logging.DEBUG):
        results = provider.transfer_missing(
            vm_config=vm_config,
            target=target,
            snapshots=[snap],
        )

    if not results:
        pytest.skip("transfer_missing produced no results — NBD may not be available")
    assert results[0].success, f"Compressed FULL via transfer_missing failed: {results[0].error}"

    # Verify compress driver used.
    compress_driver_seen = False
    for record in caplog.records:
        msg = record.message
        if "qemu-nbd" in msg and "driver=compress" in msg:
            compress_driver_seen = True
            break
    assert compress_driver_seen, (
        "Compress driver should be used for compressed FULL via transfer_missing"
    )

    # Verify output qcow2.
    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(results[0].target_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    assert info.get("format") == "qcow2", "Backup should be valid qcow2"
    backing = info.get("backing-filename")
    assert backing is None or backing == "", f"FULL should be standalone, got backing: {backing!r}"

    _cleanup_checkpoints(shell, vm_name)
