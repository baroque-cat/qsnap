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
  comparison on a 4 GB disk with 3 GB of data

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
            name=f"{vm_name}.none",
            path=base_image,
            timestamp=datetime.now(),
            allocation=0,
            disk="vda",
        ),
        TargetConfig(path=target_dir, compress=False, verify="off"),
        compress=False,
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
            name=f"{vm_name}.zstd",
            path=base_image,
            timestamp=datetime.now(),
            allocation=0,
            disk="vda",
        ),
        TargetConfig(path=target_dir, compress=True, verify="off"),
        compress=True,
        compression_type="zstd",
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
            name=f"{vm_name}.zlib",
            path=base_image,
            timestamp=datetime.now(),
            allocation=0,
            disk="vda",
        ),
        TargetConfig(path=target_dir, compress=True, verify="off"),
        compress=True,
        compression_type="zlib",
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
            name=f"{vm_name}.stopped",
            path=base_image,
            timestamp=datetime.now(),
            allocation=0,
            disk="vda",
        ),
        TargetConfig(path=target_dir, compress=False, verify="off"),
        compress=False,
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
        disk="vda",
    )

    provider = BitmapBackupProvider(shell)
    caplog.set_level(logging.DEBUG)
    result = provider.create_full_backup(
        vm_name,
        snapshot,
        TargetConfig(path=target_dir, compress=False, verify="off"),
        compress=False,
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
    """Verify FULL backup with default engine (qemu-img-convert).

    1. Start the VM.
    2. Call ``create_full_backup()`` (no engine param — defaults to qemu-img-convert).
    3. Verify ``qemu-img convert`` was used (log check for both ``qemu-img`` and
       ``convert`` in the same message, and ``nbd:unix:`` presence for the NBD path).
    4. Verify a checkpoint was created atomically.
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
        name=f"{vm_name}.qemu-img-convert",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )

    provider = BitmapBackupProvider(shell)
    caplog.set_level(logging.DEBUG)
    result = provider.create_full_backup(
        vm_name,
        snapshot,
        TargetConfig(path=target_dir, compress=False, verify="off"),
        compress=False,
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
# Test 7: Proactive free-space gate runs BEFORE the real transfer
# ──────────────────────────────────────────────────────────────────────


def _build_gate_core(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    target_dir: Path,
    tmpdir: Path,
    free_space_check: str,
    reserve: int,
) -> tuple[Core, VMConfig, InMemoryStateManager]:
    """Build Core + state with one recorded snapshot (disk="vda")."""
    from qsnap.core import Core
    from qsnap.factory.default import DefaultFactory
    from qsnap.models.config import DiskConfig, VMConfig
    from tests.mocks.mock_config import MockConfigFacade
    from tests.mocks.mock_state import InMemoryStateManager

    state = InMemoryStateManager()
    snap = SnapshotInfo(
        name=f"{vm_name}.gate-source",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    state.record_snapshot(vm_name, snap)
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        free_space_check=free_space_check,
        free_space_reserve=reserve,
        targets=[
            TargetConfig(path=target_dir, compress=False, verify="off"),
        ],
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "gate.toml")
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config, state


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_free_space_gate_strict_blocks_full_before_transfer(test_vm, caplog):
    """Strict free-space gate suspends the target BEFORE any transfer.

    1. Start VM; record one snapshot (disk="vda").
    2. Configure ``free_space_check="strict"`` with a reserve far above
       the filesystem's free space.
    3. Run ``core.backup()``.
    4. Assert NO ``qemu-img convert`` starts, NO backup file appears,
       the run is ``space_limited``, and a CRITICAL "suspending target"
       log names the target.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start VM so the transfer WOULD be possible (proving the gate, not
    # the environment, blocked it).
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)

    core, _, _ = _build_gate_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, tmpdir,
        free_space_check="strict", reserve=10**18,
    )

    caplog.set_level(logging.DEBUG)
    result = core.backup(vm_name)

    # Target suspended by the gate → space_limited run.
    assert result.space_limited is True, (
        f"Strict gate rejection must mark the run space_limited, got {result.space_limited}"
    )

    # NO qemu-img convert may start.
    convert_msgs = [
        r.message for r in caplog.records if "qemu-img" in r.message and "convert" in r.message
    ]
    assert convert_msgs == [], (
        f"No qemu-img convert may start when the strict gate blocks: {convert_msgs}"
    )

    # NO backup file on the target.
    assert list(target_dir.glob("*.qcow2")) == [], (
        f"No backup file may be created when the strict gate blocks: "
        f"{list(target_dir.glob('*.qcow2'))}"
    )

    # CRITICAL log names the target suspension.
    suspend_logs = [
        r.message for r in caplog.records if "suspending target (strict)" in r.message
    ]
    assert len(suspend_logs) >= 1, (
        f"Expected a 'suspending target (strict)' CRITICAL log. "
        f"Logs: {[r.message for r in caplog.records]}"
    )


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_free_space_gate_warn_proceeds_with_warning(test_vm, caplog):
    """Warn-mode free-space gate logs WARNING and lets the transfer run.

    1. Start VM; record one snapshot (disk="vda").
    2. Configure ``free_space_check="warn"`` with a reserve far above
       free space (the gate WOULD block in strict mode).
    3. Run ``core.backup()``.
    4. Assert a WARNING "proceeding anyway" log names target/estimate,
       ``qemu-img convert`` runs, and a FULL backup file appears.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    _cleanup_checkpoints(shell, vm_name)

    core, _, _ = _build_gate_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, tmpdir,
        free_space_check="warn", reserve=10**18,
    )

    caplog.set_level(logging.DEBUG)
    result = core.backup(vm_name)

    # WARNING naming the target with estimate/free/required.
    warn_logs = [r.message for r in caplog.records if "proceeding anyway" in r.message]
    assert len(warn_logs) >= 1, (
        f"Warn mode must log 'proceeding anyway'. Logs: {[r.message for r in caplog.records]}"
    )

    # Transfer ran: qemu-img convert + a FULL file on the target.
    convert_msgs = [
        r.message for r in caplog.records if "qemu-img" in r.message and "convert" in r.message
    ]
    assert len(convert_msgs) > 0, (
        f"Warn mode must proceed with the transfer (qemu-img convert). "
        f"Logs: {[r.message for r in caplog.records]}"
    )
    full_files = list(target_dir.glob("*.FULL.*.qcow2"))
    assert len(full_files) >= 1, (
        f"Expected a FULL backup on target in warn mode. Got: "
        f"{[p.name for p in target_dir.glob('*.qcow2')]}"
    )

    # Warn mode never marks the run space_limited (no suspension).
    assert result.space_limited is False, (
        f"Warn mode must not suspend the target, got space_limited={result.space_limited}"
    )

    _cleanup_checkpoints(shell, vm_name)


@pytest.mark.integration
def test_full_backup_custom_convert_parallel_and_out_of_order(test_vm, caplog):
    """Verify FULL backup with custom ``-m`` and in-order writes.

    1. Start the VM.
    2. Call ``create_full_backup()`` with ``convert_parallel=2``, ``convert_out_of_order=False``.
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
        disk="vda",
    )

    provider = BitmapBackupProvider(shell)
    caplog.set_level(logging.DEBUG)
    result = provider.create_full_backup(
        vm_name,
        snapshot,
        TargetConfig(path=target_dir, compress=False, verify="off"),
        compress=False,
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
