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
from typing import TYPE_CHECKING

import pytest

from qsnap.config.facade import ConfigFacade
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.shell.subprocess_shell import SubprocessShell

if TYPE_CHECKING:
    from qsnap.core import Core
    from tests.mocks.mock_state import InMemoryStateManager
from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
)
from qsnap.utils.time import parse_stall_timeout

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

    # Phase 2: the backup world is orthogonal to snapshots — the
    # provider no longer accepts a SnapshotInfo source.  run_backup()
    # takes a VMConfig + DiskConfig and decides FULL vs delta from
    # checkpoint state; compression is driven by TargetConfig.compress
    # (not a call kwarg).
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
    )

    # Step 3: No compression.
    provider = BitmapBackupProvider(shell)
    result_none = provider.run_backup(
        vm_config,
        TargetConfig(path=target_dir, compress=False, verify="off"),
        vm_config.disks[0],
    )
    assert result_none.success, f"Uncompressed FULL failed: {result_none.error}"
    assert result_none.kind == "full", (
        f"First backup with no checkpoint must be a FULL, got {result_none.kind!r}"
    )
    _assert_standalone_qcow2(shell, result_none.target_path)
    ct_none = _get_compression_type(shell, result_none.target_path)
    assert ct_none == "zlib", (
        f"Uncompressed FULL: expected compression-type 'zlib' (default), got {ct_none!r}"
    )
    actual_none = _get_actual_size(shell, result_none.target_path)

    # Step 4: zstd compression.
    _cleanup_checkpoints(shell, vm_name)
    time.sleep(1.1)  # Ensure timestamp differs from previous checkpoint
    result_zstd = provider.run_backup(
        vm_config,
        TargetConfig(path=target_dir, compress=True, verify="off"),
        vm_config.disks[0],
        compression_type="zstd",
    )
    assert result_zstd.success, f"zstd FULL failed: {result_zstd.error}"
    assert result_zstd.kind == "full", (
        f"Compressed FULL must report kind 'full', got {result_zstd.kind!r}"
    )
    _assert_standalone_qcow2(shell, result_zstd.target_path)
    ct_zstd = _get_compression_type(shell, result_zstd.target_path)
    assert ct_zstd == "zstd", f"zstd FULL: expected compression-type 'zstd', got {ct_zstd!r}"
    actual_zstd = _get_actual_size(shell, result_zstd.target_path)

    # Step 5: zlib compression.
    _cleanup_checkpoints(shell, vm_name)
    time.sleep(1.1)
    result_zlib = provider.run_backup(
        vm_config,
        TargetConfig(path=target_dir, compress=True, verify="off"),
        vm_config.disks[0],
        compression_type="zlib",
    )
    assert result_zlib.success, f"zlib FULL failed: {result_zlib.error}"
    assert result_zlib.kind == "full", (
        f"Compressed FULL must report kind 'full', got {result_zlib.kind!r}"
    )
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
    2. Call ``run_backup()`` — must succeed via direct convert.
    3. Verify ``qemu-img convert`` was used (log check).
    4. Verify NO ``nbd:unix:`` in command logs (direct path, no NBD).
    5. Verify NO ``virsh backup-begin`` in logs.
    6. Verify the convert source is the configured base image
       (the orthogonal API takes the disk path from ``DiskConfig``).
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

    # The orthogonal API derives the source from DiskConfig.base_image —
    # no SnapshotInfo source is involved in the backup world anymore.
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
    )

    provider = BitmapBackupProvider(shell)
    caplog.set_level(logging.DEBUG)
    result = provider.run_backup(
        vm_config,
        TargetConfig(path=target_dir, compress=False, verify="off"),
        vm_config.disks[0],
    )

    assert result.success, f"Stopped-VM FULL must succeed, got: {result.error}"

    # Stopped-VM FULL is still a FULL — the result must carry kind="full".
    assert result.kind == "full", f"Stopped-VM FULL must report kind 'full', got {result.kind!r}"

    # Stopped-VM FULL creates NO checkpoint (design D1): the direct
    # qemu-img convert path never runs virsh backup-begin, so the
    # BackupResult must report checkpoint=None.
    assert result.checkpoint is None, (
        f"Stopped-VM FULL must not report a checkpoint, got {result.checkpoint!r}"
    )

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

    # The offline convert must read from the configured base image
    # (Phase 2: the source path comes from DiskConfig.base_image — no
    # virsh domblklist discovery happens in the backup world).
    base_source_calls = [
        r.message for r in caplog.records if "convert" in r.message and str(base_image) in r.message
    ]
    assert len(base_source_calls) > 0, (
        f"qemu-img convert must read from the configured base image {base_image}"
    )

    _assert_standalone_qcow2(shell, result.target_path)


# ──────────────────────────────────────────────────────────────────────
# Test 4: Running VM — NBD export + qemu-img convert
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_full_backup_running_vm_nbd(test_vm, caplog):
    """Verify FULL backup of a running VM via NBD + ``qemu-img convert``.

    1. Start the VM.
    2. Call ``run_backup()`` — must succeed via NBD convert.
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

    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
    )

    provider = BitmapBackupProvider(shell)
    caplog.set_level(logging.DEBUG)
    result = provider.run_backup(
        vm_config,
        TargetConfig(path=target_dir, compress=False, verify="off"),
        vm_config.disks[0],
    )

    assert result.success, f"Running-VM NBD FULL must succeed, got: {result.error}"

    # Every run_backup success must carry the backup kind (spec:
    # backup-provider — "Backup results carry the backup kind").
    assert result.kind == "full", (
        f"Running-VM NBD FULL must report kind 'full', got {result.kind!r}"
    )

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

    # No write-side qemu-nbd server may be STARTED for a FULL (that is
    # the delta path's mechanism).  Phase 2's finally-block hygiene
    # `rm` mentions the pid filename, so match actual qemu-nbd command
    # invocations instead of any log line containing the string.
    qemu_nbd = [r.message for r in caplog.records if "cmd=['qemu-nbd'" in r.message]
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

    # The result must carry the exact checkpoint name created atomically
    # by backup-begin (design D1), and that name must be the one libvirt
    # lists — not a bulk-filtered guess.
    assert result.checkpoint is not None, "Running-VM NBD FULL must report its checkpoint name"
    assert result.checkpoint in qsnap_cps, (
        f"Reported checkpoint {result.checkpoint!r} must appear in "
        f"virsh checkpoint-list output: {qsnap_cps}"
    )

    # Atomic rename: no .tmp file may be left behind.  Freeze-timestamp
    # naming makes the exact name unpredictable, so check the pattern.
    tmp_files = list(target_dir.glob("*.tmp"))
    assert tmp_files == [], (
        f"Temporary files must have been renamed, got: {[p.name for p in tmp_files]}"
    )

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
    2. Call ``run_backup()`` (no engine param — defaults to qemu-img-convert).
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

    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
    )

    provider = BitmapBackupProvider(shell)
    caplog.set_level(logging.DEBUG)
    result = provider.run_backup(
        vm_config,
        TargetConfig(path=target_dir, compress=False, verify="off"),
        vm_config.disks[0],
    )

    assert result.success, f"qemu-img-convert FULL must succeed, got: {result.error}"

    assert result.kind == "full", (
        f"qemu-img-convert FULL must report kind 'full', got {result.kind!r}"
    )

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
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        tmpdir,
        free_space_check="strict",
        reserve=10**18,
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
    suspend_logs = [r.message for r in caplog.records if "suspending target (strict)" in r.message]
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
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        tmpdir,
        free_space_check="warn",
        reserve=10**18,
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
    2. Call ``run_backup()`` with ``convert_parallel=2``, ``convert_out_of_order=False``.
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

    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
    )

    provider = BitmapBackupProvider(shell)
    caplog.set_level(logging.DEBUG)
    result = provider.run_backup(
        vm_config,
        TargetConfig(path=target_dir, compress=False, verify="off"),
        vm_config.disks[0],
        convert_parallel=2,
        convert_out_of_order=False,
    )

    assert result.success, f"custom-flags FULL must succeed, got: {result.error}"

    assert result.kind == "full", f"custom-flags FULL must report kind 'full', got {result.kind!r}"

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
# Test: VM-level TOML engine options reach the real qemu-img convert
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_vm_level_engine_options_reach_convert_command(test_vm, caplog):
    """Verify VM-level TOML engine options reach the real ``qemu-img convert``.

    End-to-end proof that the VM-level ``[[vm]]`` engine options
    (config-parsing delta A1/M1) survive the global → VM → target
    inheritance chain and reach both the ``qemu-img convert`` argv and
    the resulting qcow2 metadata:

    1. Write an inline TOML with global defaults that the VM overrides:
       global ``compress=false, compression_type="zlib", convert_parallel=2,
       convert_out_of_order=false, backup_stall_timeout="1h"``; VM-level
       ``compress=true, compression_type="zstd", convert_parallel=8,
       convert_out_of_order=false, backup_stall_timeout="30m",
       verify="compare"``; a bare ``[[vm.target]]`` (path only) so the
       target inherits every option from the VM.
    2. Parse with ``ConfigFacade`` and assert the target resolved the
       VM values (not the global ones).
    3. Start the VM and run ``run_backup()`` passing the parsed VM
       config, target, and disk (exactly like Core does).
    4. Assert at DEBUG level that the ``qemu-img convert`` argv contains
       ``-m 8`` and ``-o compression_type=zstd`` and does NOT contain
       ``-W`` (VM-level ``convert_out_of_order=false``).
    5. Verify the resulting qcow2 reports ``compression-type: "zstd"``.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Step 1: Inline TOML — global defaults overridden at VM level; the
    # bare target inherits the VM-resolved values (config-parsing M1).
    toml_content = f"""\
compress = false
compression_type = "zlib"
convert_parallel = 2
convert_out_of_order = false
backup_stall_timeout = "1h"

[[vm]]
name = "{vm_name}"
snapshot_dir = "{snapshot_dir}"
compress = true
compression_type = "zstd"
convert_parallel = 8
convert_out_of_order = false
backup_stall_timeout = "30m"
verify = "compare"

[[vm.disk]]
target = "vda"
base_image = "{base_image}"

[[vm.target]]
path = "{target_dir}"
"""
    config_path = tmpdir / "vm_engine_options_full.toml"
    config_path.write_text(toml_content)

    # Step 2: Parse and assert the target inherited the VM values.
    facade = ConfigFacade(config_path)
    vms = facade.get_vms()
    assert len(vms) == 1, f"Expected 1 VM, got {len(vms)}"
    vm = vms[0]
    assert len(vm.targets) == 1, f"Expected 1 target, got {len(vm.targets)}"
    target = vm.targets[0]

    # VM overrides global for every engine option.
    assert target.compress is True, (
        f"VM compress=true must override global false, got {target.compress}"
    )
    assert target.compression_type == "zstd", (
        f"VM compression_type='zstd' must override global 'zlib', got {target.compression_type!r}"
    )
    assert target.convert_parallel == 8, (
        f"VM convert_parallel=8 must override global 2, got {target.convert_parallel}"
    )
    assert target.convert_out_of_order is False, (
        f"VM convert_out_of_order=false must be inherited, got {target.convert_out_of_order}"
    )
    assert target.backup_stall_timeout == "30m", (
        f"VM backup_stall_timeout='30m' must override global '1h', "
        f"got {target.backup_stall_timeout!r}"
    )
    assert target.verify == "compare", (
        f"VM verify='compare' must be inherited by the target, got {target.verify!r}"
    )

    # Step 3: Start the VM and run the FULL backup with the parsed
    # config objects (Core threads exactly these into run_backup).
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    _cleanup_checkpoints(shell, vm_name)

    provider = BitmapBackupProvider(shell)
    caplog.set_level(logging.DEBUG)
    result = provider.run_backup(
        vm,
        target,
        vm.disks[0],
        compression_type=target.compression_type,
        stall_timeout=parse_stall_timeout(target.backup_stall_timeout),
        convert_parallel=target.convert_parallel,
        convert_out_of_order=target.convert_out_of_order,
    )

    assert result.success, f"VM-level engine options FULL must succeed, got: {result.error}"

    assert result.kind == "full", (
        f"VM-level engine options FULL must report kind 'full', got {result.kind!r}"
    )

    # Step 4: Assert the convert argv at DEBUG level.
    convert_messages = [
        r.message for r in caplog.records if "qemu-img" in r.message and "convert" in r.message
    ]
    assert len(convert_messages) > 0, "qemu-img convert must be used"

    # -m 8 (VM convert_parallel=8) — the pattern follows the existing
    # test_full_backup_custom_convert_parallel_and_out_of_order test.
    parallel_8_found = any(
        "convert" in msg and "'-m'" in msg and "'8'" in msg for msg in convert_messages
    )
    assert parallel_8_found, (
        f"convert command must contain -m 8 (VM convert_parallel=8); "
        f"got convert messages: {convert_messages}"
    )

    # -o compression_type=zstd (VM compression_type='zstd').
    compression_zstd_found = any("compression_type=zstd" in msg for msg in convert_messages)
    assert compression_zstd_found, (
        "convert command must contain -o compression_type=zstd (VM "
        f"compression_type='zstd'); got convert messages: {convert_messages}"
    )

    # No -W (VM convert_out_of_order=false).
    out_of_order_found = any("'-W'" in msg for msg in convert_messages)
    assert not out_of_order_found, (
        "convert command must NOT contain -W (VM convert_out_of_order=false); "
        f"got convert messages: {convert_messages}"
    )

    # Step 5: The resulting qcow2 must actually be zstd-compressed.
    _assert_standalone_qcow2(shell, result.target_path)
    ct = _get_compression_type(shell, result.target_path)
    assert ct == "zstd", f"Expected compression-type 'zstd', got {ct!r}"

    _cleanup_checkpoints(shell, vm_name)
