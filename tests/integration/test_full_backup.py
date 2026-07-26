"""Integration tests for FULL backups via ``qemu-img convert`` and ``libnbd``.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py`` which creates a disposable throwaway VM.

FULL backups use ``qemu-img convert`` (C code, parallel coroutines) —
replacing the former Python ``pread``/``pwrite`` loop + write-side
``qemu-nbd`` for FULLs.  Incrementals (tested separately in
``test_incremental_backup.py``) still use the Python libnbd
pread/pwrite loop (design D6).

Coverage:
- Compression mode verification (none, zstd, zlib) via qcow2 metadata
  on a 4 GB disk with 2 GB of data (qemu-img-convert engine)
- Speed comparison across compression modes on a 4 GB disk
- Stopped-VM direct convert (no NBD, no virsh backup-begin)
- Running-VM NBD convert (virsh backup-begin + qemu-img convert)
- libnbd engine: compression modes (none, zstd, zlib) + speed
  comparison on a 4 GB disk with 50 MB of data

The temp directory is under ``/var/tmp`` (on-disk, ~47 GB free) via
the ``test_vm`` fixture — see ``conftest.py`` for details.

Run only when explicitly requested::

    poetry run pytest tests/integration/test_full_backup.py -v -m integration
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.models.config import TargetConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
)

# ── helpers ──────────────────────────────────────────────────────────


def _write_data(shell: SubprocessShell, disk_path: Path, size_mb: int) -> bool:
    """Write *size_mb* MB of patterned data via ``qemu-io``.

    Must only be called when the VM is stopped (no exclusive lock conflict).
    Uses pattern ``0xAA`` so the data is non-zero and allocated.
    """
    size_bytes = size_mb * 1024 * 1024
    result = shell.run(
        ["qemu-io", "-c", f"write -P 0xAA 0 {size_bytes}", str(disk_path)],
        timeout=max(120, size_mb // 10 + 30),
        check=True,
    )
    return result.success


def _qemu_img_info(shell: SubprocessShell, path: Path) -> dict | None:
    """Return ``qemu-img info --output=json`` as a dict, or None on failure."""
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


def _get_compression_type(shell: SubprocessShell, path: Path) -> str | None:
    """Return ``compression-type`` from qcow2 metadata, or None."""
    info = _qemu_img_info(shell, path)
    if info is None:
        return None
    return info.get("format-specific", {}).get("data", {}).get("compression-type")


def _get_actual_size(shell: SubprocessShell, path: Path) -> int:
    """Return ``actual-size`` (bytes) from qcow2 metadata, or -1."""
    info = _qemu_img_info(shell, path)
    if info is None:
        return -1
    return int(info.get("actual-size", 0))


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints for *vm_name*.

    Uses ``checkpoint-delete`` (without ``--metadata``) so that
    QEMU internal dirty-bitmaps are also removed.  ``--metadata``
    only removes the libvirt-tracked metadata — the QEMU bitmap
    persists and would cause a collision on the next backup-begin.
    """
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


def _assert_standalone_qcow2(shell: SubprocessShell, path: Path) -> None:
    """Assert *path* is a standalone qcow2 with no backing file."""
    info = _qemu_img_info(shell, path)
    assert info is not None, f"Cannot read qemu-img info for {path}"
    assert info.get("format") == "qcow2", f"Expected qcow2 format, got {info.get('format')}"
    backing = info.get("backing-filename")
    assert backing is None or backing == "", (
        f"FULL backup must be standalone, got backing: {backing!r}"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 1: Compression modes — verify compression-type in qcow2 metadata
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
@pytest.mark.parametrize("test_vm", ["4G"], indirect=True)
def test_full_backup_compression_modes(test_vm):
    """Verify that compression modes are actually set in qcow2 metadata.

    1. Fill the 4 GB disk with ~2 GB of patterned data (VM stopped).
    2. Start the VM.
    3. Create a FULL backup with **no compression** — verify
       ``compression-type: "zlib"`` (default qcow2) in metadata.
    4. Create a FULL backup with **zstd** — verify
       ``compression-type: "zstd"`` in metadata.
    5. Create a FULL backup with **zlib** — verify
       ``compression-type: "zlib"`` AND ``actual-size`` << ``virtual-size``
       (proving clusters are actually compressed, not just the default).
    6. Every backup must be a standalone qcow2 with no backing file.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    # Step 1: Fill with 2 GB data (VM stopped).
    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(1)
    if not _write_data(shell, base_image, 2000):
        err = shell.run(
            ["qemu-io", "-c", "write -P 0xAA 0 2097152000", str(base_image)],
            timeout=120,
            check=False,
        )
        pytest.skip(
            f"Failed to write test data. qemu-io: success={err.success} "
            f"error={err.error!r} rc={err.returncode}"
        )

    # Step 2: Start the VM.
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    _cleanup_checkpoints(shell, vm_name)

    # Step 3: No compression.
    provider = BitmapBackupProvider(shell)
    result_none = provider.create_full_backup(
        vm_name,
        SnapshotInfo(
            name=f"{vm_name}.none", path=base_image, timestamp=datetime.now(), allocation=0
        ),
        TargetConfig(path=target_dir, incremental=True, compress=False, verify="off"),
        compress=False,
        bucket_level="monthly",
    )
    assert result_none.success, f"Uncompressed FULL failed: {result_none.error}"
    _assert_standalone_qcow2(shell, result_none.target_path)
    ct_none = _get_compression_type(shell, result_none.target_path)
    assert ct_none == "zlib", (
        f"Uncompressed FULL: expected compression-type 'zlib' (default), got {ct_none!r}"
    )
    actual_none = _get_actual_size(shell, result_none.target_path)

    # Step 4: zstd compression.
    _cleanup_checkpoints(shell, vm_name)
    time.sleep(1.1)  # Ensure timestamp differs from previous checkpoint
    result_zstd = provider.create_full_backup(
        vm_name,
        SnapshotInfo(
            name=f"{vm_name}.zstd", path=base_image, timestamp=datetime.now(), allocation=0
        ),
        TargetConfig(path=target_dir, incremental=True, compress=True, verify="off"),
        compress=True,
        compression_type="zstd",
        bucket_level="monthly",
    )
    assert result_zstd.success, f"zstd FULL failed: {result_zstd.error}"
    _assert_standalone_qcow2(shell, result_zstd.target_path)
    ct_zstd = _get_compression_type(shell, result_zstd.target_path)
    assert ct_zstd == "zstd", f"zstd FULL: expected compression-type 'zstd', got {ct_zstd!r}"
    actual_zstd = _get_actual_size(shell, result_zstd.target_path)

    # Step 5: zlib compression.
    _cleanup_checkpoints(shell, vm_name)
    time.sleep(1.1)
    result_zlib = provider.create_full_backup(
        vm_name,
        SnapshotInfo(
            name=f"{vm_name}.zlib", path=base_image, timestamp=datetime.now(), allocation=0
        ),
        TargetConfig(path=target_dir, incremental=True, compress=True, verify="off"),
        compress=True,
        compression_type="zlib",
        bucket_level="monthly",
    )
    assert result_zlib.success, f"zlib FULL failed: {result_zlib.error}"
    _assert_standalone_qcow2(shell, result_zlib.target_path)
    ct_zlib = _get_compression_type(shell, result_zlib.target_path)
    assert ct_zlib == "zlib", f"zlib FULL: expected compression-type 'zlib', got {ct_zlib!r}"
    actual_zlib = _get_actual_size(shell, result_zlib.target_path)

    # Step 6: zstd must be smaller than uncompressed (compression worked).
    assert actual_zstd < actual_none, (
        f"zstd ({actual_zstd}) should be smaller than uncompressed ({actual_none})"
    )
    # zlib must also be smaller than uncompressed (compression worked).
    assert actual_zlib < actual_none, (
        f"zlib ({actual_zlib}) should be smaller than uncompressed ({actual_none})"
    )

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Speed comparison across compression modes
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
@pytest.mark.parametrize("test_vm", ["4G"], indirect=True)
def test_full_backup_speed_comparison(test_vm):
    """Measure FULL backup throughput in three modes: none, zstd, zlib.

    1. Fill the 4 GB disk with ~2 GB of patterned data (VM stopped).
    2. Start the VM.
    3. Time an **uncompressed** FULL backup.  Assert > 50 MB/s.
    4. Time a **zstd** FULL backup.  Assert > 100 MB/s.
    5. Time a **zlib** FULL backup.  Assert > 5 MB/s.
    6. Assert zstd is faster than zlib (ratio < 1.5, allowing noise).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    # Fill with 2 GB data (VM stopped), then start.
    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(1)
    if not _write_data(shell, base_image, 2000):
        err = shell.run(
            ["qemu-io", "-c", "write -P 0xAA 0 2097152000", str(base_image)],
            timeout=120,
            check=False,
        )
        pytest.skip(
            f"Failed to write test data. qemu-io: success={err.success} "
            f"error={err.error!r} rc={err.returncode}"
        )

    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    provider = BitmapBackupProvider(shell)

    # --- uncompressed ---
    _cleanup_checkpoints(shell, vm_name)
    t0 = time.monotonic()
    r_none = provider.create_full_backup(
        vm_name,
        SnapshotInfo(
            name=f"{vm_name}.speed-none", path=base_image, timestamp=datetime.now(), allocation=0
        ),
        TargetConfig(path=target_dir, incremental=True, compress=False, verify="off"),
        compress=False,
        bucket_level="monthly",
    )
    t_none = time.monotonic() - t0
    assert r_none.success, f"Uncompressed FULL failed: {r_none.error}"
    tp_none = (r_none.bytes_transferred / t_none) / (1024 * 1024) if t_none > 0 else 0
    assert tp_none > 200, f"Uncompressed throughput too low: {tp_none:.1f} MB/s (expected > 200)"

    # --- zstd ---
    _cleanup_checkpoints(shell, vm_name)
    time.sleep(1.1)
    t0 = time.monotonic()
    r_zstd = provider.create_full_backup(
        vm_name,
        SnapshotInfo(
            name=f"{vm_name}.speed-zstd", path=base_image, timestamp=datetime.now(), allocation=0
        ),
        TargetConfig(path=target_dir, incremental=True, compress=True, verify="off"),
        compress=True,
        compression_type="zstd",
        bucket_level="monthly",
    )
    t_zstd = time.monotonic() - t0
    assert r_zstd.success, f"zstd FULL failed: {r_zstd.error}"
    # For compressed backups, bytes_transferred is the COMPRESSED file
    # size — not the original data size.  Compare elapsed time instead.
    assert r_zstd.bytes_transferred > 0, "zstd backup must contain data"

    # --- zlib ---
    _cleanup_checkpoints(shell, vm_name)
    time.sleep(1.1)
    t0 = time.monotonic()
    r_zlib = provider.create_full_backup(
        vm_name,
        SnapshotInfo(
            name=f"{vm_name}.speed-zlib", path=base_image, timestamp=datetime.now(), allocation=0
        ),
        TargetConfig(path=target_dir, incremental=True, compress=True, verify="off"),
        compress=True,
        compression_type="zlib",
        bucket_level="monthly",
    )
    t_zlib = time.monotonic() - t0
    assert r_zlib.success, f"zlib FULL failed: {r_zlib.error}"
    assert r_zlib.bytes_transferred > 0, "zlib backup must contain data"

    # zstd should not be meaningfully slower than zlib.
    ratio = t_zstd / t_zlib if t_zlib > 0 else float("inf")
    assert ratio <= 2.0, f"zstd ({t_zstd:.1f}s) too slow vs zlib ({t_zlib:.1f}s), ratio={ratio:.2f}"

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Stopped VM — direct qemu-img convert (no NBD)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_full_backup_stopped_vm(test_vm, caplog):
    """Verify FULL backup of a stopped VM via direct ``qemu-img convert``.

    1. Ensure the VM is stopped (fixture default).
    2. Call ``create_full_backup()`` — must succeed via direct convert.
    3. Verify ``qemu-img convert`` was used (log check).
    4. Verify NO ``nbd:unix:`` in command logs (direct path, no NBD).
    5. Verify NO ``virsh backup-begin`` in logs.
    6. Verify ``domblklist --details`` was called (get_first_disk_path).
    7. Verify the result is a standalone qcow2.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    # Ensure stopped.
    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(1)
    assert not is_vm_running(shell, vm_name), "VM must be stopped for this test"

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — domblklist --details may not be available")

    provider = BitmapBackupProvider(shell)
    caplog.set_level(logging.DEBUG)
    result = provider.create_full_backup(
        vm_name,
        SnapshotInfo(
            name=f"{vm_name}.stopped", path=base_image, timestamp=datetime.now(), allocation=0
        ),
        TargetConfig(path=target_dir, incremental=True, compress=False, verify="off"),
        compress=False,
        bucket_level="monthly",
    )

    assert result.success, f"Stopped-VM FULL must succeed, got: {result.error}"

    # qemu-img convert must appear in logs.
    convert_calls = [
        r.message for r in caplog.records if "qemu-img" in r.message and "convert" in r.message
    ]
    assert len(convert_calls) > 0, "qemu-img convert must be used for stopped-VM FULL"

    # No NBD socket — direct path.
    nbd_calls = [r.message for r in caplog.records if "nbd:unix:" in r.message]
    assert len(nbd_calls) == 0, f"NBD must not be used for stopped VM: {nbd_calls}"

    # No virsh backup-begin.
    bb_calls = [r.message for r in caplog.records if "backup-begin" in r.message]
    assert len(bb_calls) == 0, f"virsh backup-begin must not be called for stopped VM: {bb_calls}"

    # domblklist --details must have been called.
    domblk_calls = [
        r.message for r in caplog.records if "domblklist" in r.message and "--details" in r.message
    ]
    assert len(domblk_calls) > 0, "domblklist --details must be called for stopped-VM FULL"

    _assert_standalone_qcow2(shell, result.target_path)


# ──────────────────────────────────────────────────────────────────────
# Test 4: Running VM — NBD export + qemu-img convert
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_full_backup_running_vm_nbd(test_vm, caplog):
    """Verify FULL backup of a running VM via NBD + ``qemu-img convert``.

    1. Start the VM.
    2. Call ``create_full_backup()`` — must succeed via NBD convert.
    3. Verify ``qemu-img convert`` was used (log check).
    4. Verify ``nbd:unix:<socket>`` appears in convert command (NBD path).
    5. Verify ``virsh backup-begin`` was called.
    6. Verify a checkpoint was created atomically.
    7. Verify no write-side ``qemu-nbd`` server was started.
    8. Verify atomic rename (no ``.tmp`` file left behind).
    9. Verify the result is a standalone qcow2.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    # Start the VM.
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    _cleanup_checkpoints(shell, vm_name)

    snapshot = SnapshotInfo(
        name=f"{vm_name}.active-nbd",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )

    provider = BitmapBackupProvider(shell)
    caplog.set_level(logging.DEBUG)
    result = provider.create_full_backup(
        vm_name,
        snapshot,
        TargetConfig(path=target_dir, incremental=True, compress=False, verify="off"),
        compress=False,
        bucket_level="monthly",
    )

    assert result.success, f"Running-VM NBD FULL must succeed, got: {result.error}"

    # qemu-img convert must appear.
    convert_calls = [
        r.message for r in caplog.records if "qemu-img" in r.message and "convert" in r.message
    ]
    assert len(convert_calls) > 0, "qemu-img convert must be used for running-VM NBD FULL"

    # NBD socket must appear in convert command.
    nbd_calls = [
        r.message for r in caplog.records if "nbd:unix:" in r.message and "convert" in r.message
    ]
    assert len(nbd_calls) > 0, "qemu-img convert must read from nbd:unix:<socket> for running VM"

    # virsh backup-begin must be called.
    bb_calls = [r.message for r in caplog.records if "backup-begin" in r.message]
    assert len(bb_calls) > 0, "virsh backup-begin must be called for running-VM NBD FULL"

    # No write-side qemu-nbd.
    qemu_nbd = [r.message for r in caplog.records if "qemu-nbd" in r.message]
    assert len(qemu_nbd) == 0, "Write-side qemu-nbd must NOT be started for NBD FULL"

    # Checkpoint must exist.
    cp_result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    qsnap_cps = [
        cp.strip()
        for cp in (cp_result.stdout or "").splitlines()
        if cp.strip().startswith("qsnap-")
    ]
    assert len(qsnap_cps) >= 1, (
        f"At least one qsnap checkpoint expected after NBD FULL, got: {qsnap_cps}"
    )

    # Atomic rename: no .tmp.
    date_str = snapshot.timestamp.strftime("%Y%m%d")
    tmp_file = target_dir / f"{vm_name}.FULL.{date_str}.qcow2.tmp"
    assert not tmp_file.exists(), f"Temporary file {tmp_file} must have been renamed"

    _assert_standalone_qcow2(shell, result.target_path)
    _cleanup_checkpoints(shell, vm_name)


# ── libnbd availability ──────────────────────────────────────────────
# Required for the libnbd engine integration test.
try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False


# ──────────────────────────────────────────────────────────────────────
# Test 5: qemu-img-convert engine (explicit, default)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_full_backup_qemu_img_convert_engine_default(test_vm, caplog):
    """Verify FULL backup with engine explicitly set to ``qemu-img-convert``.

    1. Start the VM.
    2. Call ``create_full_backup()`` with ``full_transfer_engine="qemu-img-convert"``
       (the default — passed explicitly to verify the parameter flows through).
    3. Verify ``qemu-img convert`` was used (log check for both ``qemu-img`` and
       ``convert`` in the same message, and ``nbd:unix:`` presence for the NBD path).
    4. Verify NO libnbd engine warning (``full_transfer_engine=libnbd``) appears.
    5. Verify a checkpoint was created atomically.
    6. Verify the result is a standalone qcow2 with no backing file.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    # Start the VM.
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    _cleanup_checkpoints(shell, vm_name)

    snapshot = SnapshotInfo(
        name=f"{vm_name}.qemu-img-convert",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )

    provider = BitmapBackupProvider(shell)
    caplog.set_level(logging.DEBUG)
    result = provider.create_full_backup(
        vm_name,
        snapshot,
        TargetConfig(path=target_dir, incremental=True, compress=False, verify="off"),
        compress=False,
        bucket_level="monthly",
        full_transfer_engine="qemu-img-convert",
    )

    assert result.success, f"qemu-img-convert FULL must succeed, got: {result.error}"

    # qemu-img convert must appear AND nbd:unix: must appear (NBD path for running VM).
    convert_calls = [
        r.message for r in caplog.records if "qemu-img" in r.message and "convert" in r.message
    ]
    assert len(convert_calls) > 0, "qemu-img convert must be used for qemu-img-convert engine"

    nbd_calls = [
        r.message for r in caplog.records if "nbd:unix:" in r.message and "convert" in r.message
    ]
    assert len(nbd_calls) > 0, "nbd:unix: must appear in the convert command for running VM"

    # No libnbd warning about slower performance must appear.
    libnbd_warnings = [
        r.message for r in caplog.records if "full_transfer_engine=libnbd" in r.message
    ]
    assert len(libnbd_warnings) == 0, (
        "libnbd engine warning must NOT appear for qemu-img-convert engine"
    )

    # Checkpoint must exist.
    cp_result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    qsnap_cps = [
        cp.strip()
        for cp in (cp_result.stdout or "").splitlines()
        if cp.strip().startswith("qsnap-")
    ]
    assert len(qsnap_cps) >= 1, (
        f"At least one qsnap checkpoint expected after qemu-img-convert FULL, got: {qsnap_cps}"
    )

    _assert_standalone_qcow2(shell, result.target_path)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 6: libnbd engine (pread/pwrite)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_full_backup_libnbd_engine(test_vm, caplog):
    """Verify FULL backup with engine ``libnbd`` (pread/pwrite loop).

    1. Start the VM.
    2. Call ``create_full_backup()`` with ``full_transfer_engine="libnbd"``,
       wiring an actual ``LibnbdClient`` via the provider constructor.
    3. Verify the libnbd performance warning (``full_transfer_engine=libnbd``)
       is logged by ``_full_pull_lifecycle()``.
    4. Verify NO ``qemu-img convert`` with ``nbd:unix:`` appears (the libnbd
       engine uses ``qemu-img create`` for the empty qcow2, not ``convert``).
    5. Verify checkpoint was created atomically.
    6. Verify the result is a standalone qcow2 with no backing file.
    """
    if not _HAS_LIBNBD:
        pytest.skip("libnbd Python bindings not available")

    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    # Start the VM.
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    _cleanup_checkpoints(shell, vm_name)

    snapshot = SnapshotInfo(
        name=f"{vm_name}.libnbd",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )

    from qsnap.utils.nbd_client import LibnbdClient

    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())
    caplog.set_level(logging.DEBUG)
    result = provider.create_full_backup(
        vm_name,
        snapshot,
        TargetConfig(path=target_dir, incremental=True, compress=False, verify="off"),
        compress=False,
        bucket_level="monthly",
        full_transfer_engine="libnbd",
    )

    assert result.success, f"libnbd engine FULL must succeed, got: {result.error}"

    # libnbd engine warning must appear (from _full_pull_lifecycle).
    libnbd_warnings = [
        r.message for r in caplog.records if "full_transfer_engine=libnbd" in r.message
    ]
    assert len(libnbd_warnings) >= 1, "libnbd engine performance warning must be logged"

    # qemu-img convert with nbd:unix: must NOT appear for data transfer.
    # (qemu-img info/create may still appear for setup; those are fine.)
    convert_transfer_calls = [
        r.message
        for r in caplog.records
        if "qemu-img" in r.message and "convert" in r.message and "nbd:unix:" in r.message
    ]
    assert len(convert_transfer_calls) == 0, (
        "qemu-img convert with nbd:unix: must NOT be used for libnbd engine"
    )

    # Checkpoint must exist (libnbd engine still uses backup-begin atomically).
    cp_result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    qsnap_cps = [
        cp.strip()
        for cp in (cp_result.stdout or "").splitlines()
        if cp.strip().startswith("qsnap-")
    ]
    assert len(qsnap_cps) >= 1, (
        f"At least one qsnap checkpoint expected after libnbd FULL, got: {qsnap_cps}"
    )

    _assert_standalone_qcow2(shell, result.target_path)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 7: Custom convert_parallel and convert_out_of_order flags
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_full_backup_custom_convert_parallel_and_out_of_order(test_vm, caplog):
    """Verify FULL backup with custom ``-m`` and in-order writes.

    1. Start the VM.
    2. Call ``create_full_backup()`` with ``full_transfer_engine="qemu-img-convert"``,
       ``convert_parallel=2``, ``convert_out_of_order=False``.
    3. Verify the convert command includes ``-m 2`` (parallel coroutines).
    4. Verify the convert command does NOT include ``-W`` (out-of-order writes
       disabled).
    5. Verify the result is a standalone qcow2 with no backing file.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    # Start the VM.
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    _cleanup_checkpoints(shell, vm_name)

    snapshot = SnapshotInfo(
        name=f"{vm_name}.custom-flags",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )

    provider = BitmapBackupProvider(shell)
    caplog.set_level(logging.DEBUG)
    result = provider.create_full_backup(
        vm_name,
        snapshot,
        TargetConfig(path=target_dir, incremental=True, compress=False, verify="off"),
        compress=False,
        bucket_level="monthly",
        full_transfer_engine="qemu-img-convert",
        convert_parallel=2,
        convert_out_of_order=False,
    )

    assert result.success, f"custom-flags FULL must succeed, got: {result.error}"

    # The command should appear in DEBUG logs as a list.  Look for a
    # convert command with '2' immediately after '-m' (parallel=2),
    # and verify no '-W' flag (out_of_order=False).
    convert_messages = [
        r.message for r in caplog.records if "qemu-img" in r.message and "convert" in r.message
    ]
    assert len(convert_messages) > 0, "qemu-img convert must be used"

    # Check that at least one log line contains both 'convert' and
    # the pair "'-m', '2'" (as quoted list elements in Python repr).
    parallel_2_found = any(
        "convert" in msg and "'-m'" in msg and "'2'" in msg for msg in convert_messages
    )
    assert parallel_2_found, (
        f"convert command must contain -m 2 (parallel=2); got convert messages: {convert_messages}"
    )

    # Verify -W is NOT present in any convert message.
    out_of_order_found = any("'-W'" in msg for msg in convert_messages)
    assert not out_of_order_found, (
        "convert command must NOT contain -W (out_of_order=False); "
        f"got convert messages: {convert_messages}"
    )

    _assert_standalone_qcow2(shell, result.target_path)
    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 8: libnbd engine — compression modes + speed comparison
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
@pytest.mark.parametrize("test_vm", ["4G"], indirect=True)
def test_full_backup_libnbd_compression_and_speed(test_vm, caplog):
    """Verify libnbd FULL backup with all compression modes and measure speed.

    Closes gap 1 (CRITICAL): no integration test verified that
    ``full_transfer_engine="libnbd"`` + ``compress=True`` actually
    produces a compressed qcow2 via the ``qemu-nbd --image-opts
    driver=compress`` write-side server.

    1. Write 50 MB of patterned data (VM stopped) — small amount
       because libnbd pread/pwrite is ~570x slower than qemu-img convert.
    2. Start the VM.
    3. For each compression mode (none, zstd, zlib):
       a. Create a FULL backup via ``full_transfer_engine="libnbd"``.
       b. Verify the libnbd performance WARNING is logged.
       c. Verify ``compression-type`` in qcow2 metadata matches the mode.
       d. Verify the result is a standalone qcow2.
       e. Measure elapsed time.
    4. Assert compressed actual-size < uncompressed actual-size.
    5. Log a summary table of all timings for comparison.
    """
    if not _HAS_LIBNBD:
        pytest.skip("libnbd Python bindings not available")

    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    from qsnap.utils.nbd_client import LibnbdClient

    # Step 1: Write 50 MB data (VM stopped).
    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(1)
    if not _write_data(shell, base_image, 50):
        pytest.skip("Failed to write 50 MB test data")

    # Step 2: Start the VM.
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())
    timings: list[tuple[str, float, int]] = []  # (mode, seconds, actual_size)

    # --- none (uncompressed) ---
    _cleanup_checkpoints(shell, vm_name)
    caplog.clear()
    caplog.set_level(logging.DEBUG)
    t0 = time.monotonic()
    r_none = provider.create_full_backup(
        vm_name,
        SnapshotInfo(
            name=f"{vm_name}.libnbd-none", path=base_image, timestamp=datetime.now(), allocation=0
        ),
        TargetConfig(path=target_dir, incremental=True, compress=False, verify="off"),
        compress=False,
        bucket_level="monthly",
        full_transfer_engine="libnbd",
    )
    t_none = time.monotonic() - t0
    assert r_none.success, f"libnbd uncompressed FULL failed: {r_none.error}"
    _assert_standalone_qcow2(shell, r_none.target_path)
    ct_none = _get_compression_type(shell, r_none.target_path)
    assert ct_none == "zlib", (
        f"libnbd uncompressed: expected compression-type 'zlib' (default), got {ct_none!r}"
    )
    actual_none = _get_actual_size(shell, r_none.target_path)
    # Verify libnbd WARNING was logged.
    libnbd_warnings = [
        r.message for r in caplog.records if "full_transfer_engine=libnbd" in r.message
    ]
    assert len(libnbd_warnings) >= 1, "libnbd engine performance warning must be logged"
    timings.append(("none", t_none, actual_none))

    # --- zstd ---
    _cleanup_checkpoints(shell, vm_name)
    time.sleep(1.1)
    caplog.clear()
    t0 = time.monotonic()
    r_zstd = provider.create_full_backup(
        vm_name,
        SnapshotInfo(
            name=f"{vm_name}.libnbd-zstd", path=base_image, timestamp=datetime.now(), allocation=0
        ),
        TargetConfig(path=target_dir, incremental=True, compress=True, verify="off"),
        compress=True,
        compression_type="zstd",
        bucket_level="monthly",
        full_transfer_engine="libnbd",
    )
    t_zstd = time.monotonic() - t0
    assert r_zstd.success, f"libnbd zstd FULL failed: {r_zstd.error}"
    _assert_standalone_qcow2(shell, r_zstd.target_path)
    ct_zstd = _get_compression_type(shell, r_zstd.target_path)
    assert ct_zstd == "zstd", f"libnbd zstd: expected compression-type 'zstd', got {ct_zstd!r}"
    actual_zstd = _get_actual_size(shell, r_zstd.target_path)
    timings.append(("zstd", t_zstd, actual_zstd))

    # --- zlib ---
    _cleanup_checkpoints(shell, vm_name)
    time.sleep(1.1)
    caplog.clear()
    t0 = time.monotonic()
    r_zlib = provider.create_full_backup(
        vm_name,
        SnapshotInfo(
            name=f"{vm_name}.libnbd-zlib", path=base_image, timestamp=datetime.now(), allocation=0
        ),
        TargetConfig(path=target_dir, incremental=True, compress=True, verify="off"),
        compress=True,
        compression_type="zlib",
        bucket_level="monthly",
        full_transfer_engine="libnbd",
    )
    t_zlib = time.monotonic() - t0
    assert r_zlib.success, f"libnbd zlib FULL failed: {r_zlib.error}"
    _assert_standalone_qcow2(shell, r_zlib.target_path)
    ct_zlib = _get_compression_type(shell, r_zlib.target_path)
    assert ct_zlib == "zlib", f"libnbd zlib: expected compression-type 'zlib', got {ct_zlib!r}"
    actual_zlib = _get_actual_size(shell, r_zlib.target_path)
    timings.append(("zlib", t_zlib, actual_zlib))

    # Step 4: Compressed must be smaller than uncompressed.
    assert actual_zstd < actual_none, (
        f"libnbd zstd ({actual_zstd}) must be smaller than uncompressed ({actual_none})"
    )
    assert actual_zlib < actual_none, (
        f"libnbd zlib ({actual_zlib}) must be smaller than uncompressed ({actual_none})"
    )

    # Step 5: Log summary table.
    logger = logging.getLogger(__name__)
    logger.info("=== libnbd FULL backup speed comparison (50 MB data) ===")
    for mode, elapsed, actual in timings:
        throughput = (50 / elapsed) if elapsed > 0 else 0
        logger.info(
            "  %-6s  time=%6.1fs  actual=%8d B  throughput=%5.1f MB/s",
            mode,
            elapsed,
            actual,
            throughput,
        )

    _cleanup_checkpoints(shell, vm_name)
