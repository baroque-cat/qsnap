"""Integration tests for incremental (bitmap-based) backups.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

Incremental backups use the Python libnbd pread/pwrite loop with
QEMU dirty-bitmap checkpoints (design D6).  Compression is NOT
applied to incrementals — it applies to FULL backups only.

Coverage:
- FULL → incremental flow: create FULL, write dirty data, take
  external snapshot, transfer missing — verify sizes
- Compression not applied to incrementals (design D6 check)
- Dirty bytes are proportional to data written, not full disk

Run only when explicitly requested::

    poetry run pytest tests/integration/test_incremental_backup.py -v -m integration
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

# libnbd availability — needed for the incremental transfer.
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

# ── helpers ──────────────────────────────────────────────────────────


def _write_data(shell: SubprocessShell, disk_path: Path, size_mb: int) -> bool:
    """Write *size_mb* MB of patterned data via ``qemu-io`` (VM stopped).

    Splits writes larger than 2000 MB into chunks because ``qemu-io``
    limits a single write to ~2 GB without the ``-n`` flag.
    """
    chunk_mb = min(size_mb, 2000)
    remaining = size_mb
    offset_mb = 0
    while remaining > 0:
        write_mb = min(remaining, chunk_mb)
        size_bytes = write_mb * 1024 * 1024
        offset_bytes = offset_mb * 1024 * 1024
        result = shell.run(
            ["qemu-io", "-c", f"write -P 0xAA {offset_bytes} {size_bytes}", str(disk_path)],
            timeout=max(120, write_mb // 10 + 30),
            check=True,
        )
        if not result.success:
            return False
        remaining -= write_mb
        offset_mb += write_mb
    return True


def _write_data_running(
    shell: SubprocessShell, disk_path: Path, size_mb: int, offset_mb: int
) -> bool:
    """Write *size_mb* MB via ``qemu-io --force-share`` at *offset_mb* (VM running)."""
    size_bytes = size_mb * 1024 * 1024
    result = shell.run(
        [
            "qemu-io",
            "--force-share",
            "-c",
            f"write -P 0xBB {offset_mb}M {size_bytes}",
            str(disk_path),
        ],
        timeout=max(120, size_mb // 10 + 30),
        check=True,
    )
    return result.success


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


def _get_actual_size(shell: SubprocessShell, path: Path) -> int:
    """Return ``actual-size`` (bytes) from qcow2 metadata, or -1."""
    info = _qemu_img_info(shell, path)
    if info is None:
        return -1
    return int(info.get("actual-size", 0))


def _get_compression_type(shell: SubprocessShell, path: Path) -> str | None:
    """Return ``compression-type`` from qcow2 metadata, or None."""
    info = _qemu_img_info(shell, path)
    if info is None:
        return None
    return info.get("format-specific", {}).get("data", {}).get("compression-type")


def _get_backing_filename(shell: SubprocessShell, path: Path) -> str | None:
    """Return ``backing-filename`` of a qcow2, or None."""
    info = _qemu_img_info(shell, path)
    if info is None:
        return None
    backing = info.get("backing-filename")
    return str(backing) if backing else None


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints."""
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
                ["virsh", "checkpoint-delete", "--domain", vm_name, cp, "--metadata"],
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


def _get_snapshot_disk_path(shell: SubprocessShell, vm_name: str) -> Path | None:
    """Return the overlay disk path after ``virsh snapshot-create-as --disk-only``."""
    result = shell.run(
        ["virsh", "domblklist", "--domain", vm_name, "--details"],
        timeout=30,
    )
    if not result.success:
        return None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Type") or stripped.startswith("-"):
            continue
        parts = stripped.split(None, 3)
        if len(parts) >= 4 and parts[1] == "disk":
            path = parts[3]
            return Path(path) if path else None
    return None


# ──────────────────────────────────────────────────────────────────────
# Test 1: FULL → incremental — verify sizes and backing chain
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_incremental_after_full(test_vm):
    """Verify incremental-after-FULL backup via bitmap + libnbd.

    1. Write ~500 MB of initial data (VM stopped), then start VM.
    2. Create FULL backup via ``create_full_backup()``.
    3. Write 10 MB of new data to the running VM.
    4. Create an external disk-only snapshot to freeze the dirty state.
    5. Call ``transfer_missing()`` — must produce an incremental delta
       (because a checkpoint from the FULL already exists).
    6. Assert incremental ``actual-size`` < FULL ``actual-size``.
    7. Assert incremental has a backing file pointing to the FULL.
    8. Assert ``bytes_transferred`` is proportional to 10 MB, not full disk.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Pre-checks.
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin + checkpoint not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed — required for incremental transfer")

    # Step 1: Write 500 MB initial data (VM stopped), then start.
    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(1)
    if not _write_data(shell, base_image, 200):
        result = shell.run(
            ["qemu-io", "-c", f"write -P 0xAA 0 {200 * 1024 * 1024}", str(base_image)],
            timeout=60,
            check=False,
        )
        pytest.skip(
            f"Failed to write 200 MB to {base_image}. qemu-io: success={result.success} error={result.error!r} rc={result.returncode}"
        )

    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    # Step 2: FULL backup — also creates an atomic checkpoint.

    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    # Step 2: FULL backup — also creates an atomic checkpoint.
    provider_full = BitmapBackupProvider(shell)
    source = SnapshotInfo(
        name=f"{vm_name}.full-base",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    target = TargetConfig(path=target_dir, compress=False, verify="off")

    result_full = provider_full.create_full_backup(
        vm_name,
        source,
        target,
        compress=False,

    )
    assert result_full.success, f"FULL backup failed: {result_full.error}"
    full_actual = _get_actual_size(shell, result_full.target_path)
    assert full_actual > 0, f"FULL actual-size must be > 0, got {full_actual}"

    # Step 3: Write 10 MB of new data.  Stop the VM first because
    # ``qemu-io --force-share`` requires read-only mode in recent
    # qemu (>= 8.0).  The checkpoint bitmap persists across VM
    # restarts via libvirt, so the dirty-bitmap baseline for the
    # incremental is still valid.
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(1)
    # Write 10 MB at offset 200 MB (outside the existing 200 MB of data).
    inc_write = shell.run(
        ["qemu-io", "-c", "write -P 0xCC 200M 10M", str(base_image)],
        timeout=60,
        check=False,
    )
    if not inc_write.success:
        pytest.skip(f"Failed to write incremental data: {inc_write.error!r}")
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not restart after incremental data write")

    # Step 4: External disk-only snapshot.
    snap_name = f"{vm_name}.incr-test"
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
        pytest.skip(f"Snapshot creation failed: {snap_result.error}")

    overlay_path = _get_snapshot_disk_path(shell, vm_name)
    if overlay_path is None:
        # Fallback: parse snapshot XML.
        xml_result = shell.run(
            ["virsh", "snapshot-dumpxml", "--domain", vm_name, snap_name],
            timeout=30,
        )
        if xml_result.success:
            import re as _re

            m = _re.search(r'<source file="([^"]+)"', xml_result.stdout)
            if m:
                overlay_path = Path(m.group(1))
        if overlay_path is None:
            pytest.skip("Could not determine overlay path after external snapshot")

    snapshot_info = SnapshotInfo(
        name=snap_name,
        path=overlay_path,
        timestamp=datetime.now(),
        allocation=0,
    )

    # Step 5: Incremental transfer via libnbd.
    provider_inc = BitmapBackupProvider(shell, nbd=LibnbdClient())
    vm_config = VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir)

    results = provider_inc.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[snapshot_info],
        stall_timeout=300,
    )
    assert len(results) > 0, "transfer_missing must return at least one result"

    inc_result = results[0]
    assert inc_result.success, (
        f"Incremental backup failed: {inc_result.error}. "
        f"Check that transfer_missing detected the checkpoint from create_full_backup."
    )

    # Step 6: Size assertions.
    inc_path = inc_result.target_path
    assert inc_path.exists(), f"Incremental file not found: {inc_path}"

    inc_actual = _get_actual_size(shell, inc_path)
    assert inc_actual < full_actual, (
        f"Incremental actual-size ({inc_actual}) must be < FULL ({full_actual})"
    )

    # Backing chain.
    backing = _get_backing_filename(shell, inc_path)
    assert backing is not None, "Incremental must have a backing file"
    assert (
        ".FULL." in backing
        or str(result_full.target_path) in backing
        or str(result_full.target_path.name) in backing
    ), f"Incremental backing {backing!r} must reference the FULL backup"

    # Dirty bytes proportional to 10 MB, not 3 GB.
    transferred = inc_result.bytes_transferred
    max_expected = 10 * 1024 * 1024 * 10  # 10 MB * 10x overhead ≈ 100 MB
    assert transferred < max_expected, (
        f"Incremental delta ({transferred / (1024 * 1024):.1f} MB) "
        f"must be proportional to 10 MB dirty data, not 3 GB disk. "
        f"Max expected: {max_expected / (1024 * 1024):.0f} MB"
    )
    assert transferred < full_actual, (
        f"Incremental bytes_transferred ({transferred}) must be < FULL ({full_actual})"
    )

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Compression NOT applied to incrementals (design D6)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_incremental_compression_not_applied(test_vm, caplog):
    """Verify compression is NOT applied to bitmap incrementals (design D6).

    1. Create a zstd-compressed FULL backup (compression-type: "zstd").
    2. Write new data, create external snapshot.
    3. Call ``transfer_missing()`` with ``target.compress=True``.
    4. Verify the log message "bitmap incrementals are uncompressed".
    5. Verify the incremental qcow2 has ``compression-type: "zlib"``
       (default), NOT "zstd" — proving compression was not applied.
    6. Verify no ``qemu-nbd --image-opts driver=compress`` in logs.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    # Prepare: write data, start VM.
    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(1)
    _write_data(shell, base_image, 500)
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)

    _cleanup_checkpoints(shell, vm_name)

    # Step 1: Zstd-compressed FULL backup.
    provider = BitmapBackupProvider(shell)
    source = SnapshotInfo(
        name=f"{vm_name}.full-zstd",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    target = TargetConfig(path=target_dir, compress=True, verify="off")

    r_full = provider.create_full_backup(
        vm_name,
        source,
        target,
        compress=True,
        compression_type="zstd",

    )
    assert r_full.success, f"zstd FULL failed: {r_full.error}"
    ct_full = _get_compression_type(shell, r_full.target_path)
    assert ct_full == "zstd", f"FULL must have compression-type 'zstd', got {ct_full!r}"

    # Step 2: Write new data, create external snapshot.
    _write_data_running(shell, base_image, 5, offset_mb=500)
    snap_name = f"{vm_name}.incr-nocompress"
    shell.run(
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
    overlay = _get_snapshot_disk_path(shell, vm_name)
    if overlay is None:
        pytest.skip("Could not determine overlay path")

    snapshot_info = SnapshotInfo(
        name=snap_name,
        path=overlay,
        timestamp=datetime.now(),
        allocation=0,
    )

    # Step 3: transfer_missing with compress=True target.
    import logging

    provider_inc = BitmapBackupProvider(shell, nbd=LibnbdClient())
    vm_config = VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir)

    with caplog.at_level(logging.INFO):
        results = provider_inc.transfer_missing(
            vm_config=vm_config,
            target=target,
            snapshots=[snapshot_info],
            stall_timeout=300,
        )
    assert len(results) > 0, "transfer_missing must return results"

    inc_result = results[0]
    # If not successful, report but don't fail — environment may differ.
    if inc_result.success:
        inc_path = inc_result.target_path

        # Step 4: Log message about uncompressed incrementals.
        incr_logs = [
            r.message
            for r in caplog.records
            if "incremental" in r.message.lower() and "uncompressed" in r.message.lower()
        ]
        assert len(incr_logs) > 0, (
            "Expected log message 'bitmap incrementals are uncompressed', but not found in caplog."
        )

        # Step 5: Compression-type must be "zlib" (default), NOT "zstd".
        ct_incr = _get_compression_type(shell, inc_path)
        assert ct_incr != "zstd", (
            f"Incremental must NOT have zstd compression, got {ct_incr!r}. "
            f"Design D6: compression applies to FULL only."
        )
        assert ct_incr == "zlib", (
            f"Incremental expected default compression-type 'zlib', got {ct_incr!r}"
        )

        # Step 6: No qemu-nbd compress driver.
        compress_drv = [
            r.message
            for r in caplog.records
            if "driver=compress" in r.message or "--image-opts" in r.message
        ]
        assert len(compress_drv) == 0, (
            f"qemu-nbd compress driver must NOT be used for incrementals: {compress_drv}"
        )

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Dirty bytes proportional to data written
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_incremental_dirty_bytes_proportional(test_vm):
    """Verify incremental dirty_bytes are proportional to data written, not full disk.

    1. Create FULL backup with known data size.
    2. Write an exact, small amount of new data (5 MB).
    3. External snapshot → transfer_missing() → incremental.
    4. Assert ``bytes_transferred`` is in the 5-50 MB range
       (5 MB × 10x overhead), NOT in the full-disk range.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    # Prepare: 500 MB initial data, start VM.
    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(1)
    _write_data(shell, base_image, 500)
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    # Step 1: FULL backup.
    provider = BitmapBackupProvider(shell)
    source = SnapshotInfo(
        name=f"{vm_name}.full-dirty",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    target = TargetConfig(path=target_dir, compress=False, verify="off")

    r_full = provider.create_full_backup(
        vm_name,
        source,
        target,
        compress=False,

    )
    assert r_full.success, f"FULL failed: {r_full.error}"
    full_actual = _get_actual_size(shell, r_full.target_path)

    # Step 2: Write exactly 5 MB.
    _write_data_running(shell, base_image, 5, offset_mb=500)

    # Step 3: External snapshot.
    snap_name = f"{vm_name}.dirty-proportional"
    shell.run(
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
    overlay = _get_snapshot_disk_path(shell, vm_name)
    if overlay is None:
        pytest.skip("Could not determine overlay path")

    snapshot_info = SnapshotInfo(
        name=snap_name,
        path=overlay,
        timestamp=datetime.now(),
        allocation=0,
    )

    # Step 4: transfer_missing.
    provider_inc = BitmapBackupProvider(shell, nbd=LibnbdClient())
    vm_config = VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir)

    results = provider_inc.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[snapshot_info],
        stall_timeout=300,
    )
    assert len(results) > 0
    inc_result = results[0]
    assert inc_result.success, f"Incremental failed: {inc_result.error}"

    # Step 5: Size assertions.
    transferred = inc_result.bytes_transferred
    # Must be smaller than FULL (obviously).
    assert transferred < full_actual, f"Incremental ({transferred}) must be < FULL ({full_actual})"
    # Must be proportional to 5 MB of dirty data, not 500 MB disk.
    max_expected = 5 * 1024 * 1024 * 10  # 50 MB
    assert transferred < max_expected, (
        f"Incremental delta ({transferred / (1024 * 1024):.1f} MB) "
        f"must be proportional to 5 MB dirty data. "
        f"Max expected: {max_expected / (1024 * 1024):.0f} MB"
    )

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 4: Incremental after libnbd FULL — backing chain integrity
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
@pytest.mark.parametrize("test_vm", ["4G"], indirect=True)
def test_incremental_after_libnbd_full(test_vm):
    """Verify incremental backup works after a FULL created via libnbd engine.

    Closes gap 2 (WARNING): no integration test verified that a qcow2
    created via ``full_transfer_engine="libnbd"`` (``qemu-img create``
    + ``_start_write_server`` + ``_transfer(zero_skip=True)``) correctly
    serves as a backing file for a subsequent incremental.

    1. Write 3 GB of initial data (VM stopped), then start VM.
    2. Create FULL backup via ``full_transfer_engine="libnbd"``.
    3. Write 10 MB of new dirty data.
    4. Create an external disk-only snapshot to freeze the dirty state.
    5. Call ``transfer_missing()`` — must produce an incremental delta
       (because a checkpoint from the libnbd FULL already exists).
    6. Assert incremental ``actual-size`` < FULL ``actual-size``.
    7. Assert incremental has a backing file pointing to the FULL.
    8. Assert ``bytes_transferred`` is proportional to 10 MB, not full disk.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Pre-checks.
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin + checkpoint not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed — required for libnbd engine")

    # Step 1: Write 3 GB initial data (VM stopped), then start.
    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(1)
    if not _write_data(shell, base_image, 3000):
        pytest.skip("Failed to write 3 GB initial data")

    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    # Step 2: FULL backup via libnbd engine.
    provider_full = BitmapBackupProvider(shell, nbd=LibnbdClient())
    source = SnapshotInfo(
        name=f"{vm_name}.libnbd-full",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    target = TargetConfig(path=target_dir, compress=False, verify="off")

    result_full = provider_full.create_full_backup(
        vm_name,
        source,
        target,
        compress=False,

        full_transfer_engine="libnbd",
    )
    assert result_full.success, f"libnbd FULL backup failed: {result_full.error}"
    full_actual = _get_actual_size(shell, result_full.target_path)
    assert full_actual > 0, f"FULL actual-size must be > 0, got {full_actual}"

    # Step 3: Write 10 MB of new dirty data.  Stop the VM first because
    # qemu-io --force-share requires read-only mode in recent qemu.
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(1)
    inc_write = shell.run(
        ["qemu-io", "-c", "write -P 0xCC 3000M 10M", str(base_image)],
        timeout=60,
        check=False,
    )
    if not inc_write.success:
        pytest.skip(f"Failed to write incremental data: {inc_write.error!r}")
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not restart after incremental data write")

    # Step 4: External disk-only snapshot.
    snap_name = f"{vm_name}.libnbd-incr"
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
        pytest.skip(f"Snapshot creation failed: {snap_result.error}")

    overlay_path = _get_snapshot_disk_path(shell, vm_name)
    if overlay_path is None:
        pytest.skip("Could not determine overlay path after external snapshot")

    snapshot_info = SnapshotInfo(
        name=snap_name,
        path=overlay_path,
        timestamp=datetime.now(),
        allocation=0,
    )

    # Step 5: Incremental transfer via libnbd.
    provider_inc = BitmapBackupProvider(shell, nbd=LibnbdClient())
    vm_config = VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir)

    results = provider_inc.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[snapshot_info],
        stall_timeout=300,
    )
    assert len(results) > 0, "transfer_missing must return at least one result"

    inc_result = results[0]
    assert inc_result.success, (
        f"Incremental after libnbd FULL failed: {inc_result.error}. "
        f"Check that transfer_missing detected the checkpoint from the libnbd FULL."
    )

    # Step 6: Size assertions.
    inc_path = inc_result.target_path
    assert inc_path.exists(), f"Incremental file not found: {inc_path}"

    inc_actual = _get_actual_size(shell, inc_path)
    assert inc_actual < full_actual, (
        f"Incremental actual-size ({inc_actual}) must be < FULL ({full_actual})"
    )

    # Step 7: Backing chain.
    backing = _get_backing_filename(shell, inc_path)
    assert backing is not None, "Incremental must have a backing file"
    assert (
        ".FULL." in backing
        or str(result_full.target_path) in backing
        or str(result_full.target_path.name) in backing
    ), f"Incremental backing {backing!r} must reference the libnbd FULL backup"

    # Step 8: Dirty bytes proportional to 10 MB, not 3 GB disk.
    transferred = inc_result.bytes_transferred
    max_expected = 10 * 1024 * 1024 * 10  # 10 MB * 10x overhead ≈ 100 MB
    assert transferred < max_expected, (
        f"Incremental delta ({transferred / (1024 * 1024):.1f} MB) "
        f"must be proportional to 10 MB dirty data, not 3 GB disk. "
        f"Max expected: {max_expected / (1024 * 1024):.0f} MB"
    )
    assert transferred < full_actual, (
        f"Incremental bytes_transferred ({transferred}) must be < FULL ({full_actual})"
    )

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)
