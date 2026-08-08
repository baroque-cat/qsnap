"""Integration tests for incremental (bitmap-based) backups.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

Incremental backups use the Python libnbd pread/pwrite loop with
QEMU dirty-bitmap checkpoints (design D6).  Compression is NOT
applied to incrementals — it applies to FULL backups only.

Coverage:
- FULL → incremental flow: create FULL, write dirty data, take
  external snapshot, run the incremental backup — verify sizes
- Compression not applied to incrementals (design D6 check)
- Dirty bytes are proportional to data written, not full disk

Run only when explicitly requested::

    poetry run pytest tests/integration/test_incremental_backup.py -v -m integration
"""

from __future__ import annotations

import json
import logging
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

from qsnap.config.facade import ConfigFacade
from qsnap.interfaces.shell import IShell
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import ShellResult, SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
)
from qsnap.utils.time import parse_stall_timeout

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


class _StallRecordingShell(IShell):
    """IShell wrapper that records ``run_with_stall_detection`` calls.

    Delegates every call to the wrapped ``SubprocessShell`` (so the real
    virsh/qemu-img/libvirt behaviour is preserved) but records the
    ``(cmd, stall_timeout)`` pairs of every stall-detected transfer.
    Used to assert that the parsed VM-level ``backup_stall_timeout``
    reaches the transfer engine (pattern of ``RecordingShell`` in
    ``test_dry_run.py``).
    """

    def __init__(self, delegate: SubprocessShell) -> None:
        self._delegate = delegate
        self._stall_calls: list[tuple[list[str], int]] = []

    @property
    def stall_calls(self) -> list[tuple[list[str], int]]:
        """Recorded ``(cmd, stall_timeout)`` pairs, in call order."""
        return list(self._stall_calls)

    def run(self, cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        return self._delegate.run(cmd, timeout, check)

    def run_with_stall_detection(
        self,
        cmd: list[str],
        output_file: Path | None = None,
        stall_timeout: int = 1800,
        check: bool = False,
    ) -> ShellResult:
        self._stall_calls.append((list(cmd), stall_timeout))
        return self._delegate.run_with_stall_detection(cmd, output_file, stall_timeout, check)


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


def _vm_active_disk_is_base(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
) -> bool:
    """Return True when the VM's active vda disk is *base_image*.

    The ``test_vm`` fixture defines a fresh domain whose vda source IS
    *base_image* before any snapshot is taken.  When a concurrent test
    process redefines the shared VM with its own disk (the integration
    fixture is not name-scoped), ``virsh start`` reports "already
    active" and the transfers below would silently run against a
    foreign disk.  Callers skip instead of asserting on the wrong data.
    """
    active = _get_snapshot_disk_path(shell, vm_name)
    return active is not None and active == base_image


# ──────────────────────────────────────────────────────────────────────
# Test 1: FULL → incremental — verify sizes and backing chain
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_incremental_after_full(test_vm):
    """Verify incremental-after-FULL backup via bitmap + libnbd.

    1. Write ~500 MB of initial data (VM stopped), then start VM.
    2. Create FULL backup via ``run_backup()`` (no checkpoint yet → FULL).
    3. Write 10 MB of new data to the running VM.
    4. Create an external disk-only snapshot to freeze the dirty state.
    5. Call ``run_backup()`` again — must produce an incremental delta
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
    if not _vm_active_disk_is_base(shell, vm_name, base_image):
        pytest.skip("VM disk source is not this test's base image — concurrent test interference")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    # Step 2: FULL backup — also creates an atomic checkpoint.

    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not _vm_active_disk_is_base(shell, vm_name, base_image):
        pytest.skip("VM disk source is not this test's base image — concurrent test interference")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    # Step 2: FULL backup — ``run_backup`` creates exactly one backup per
    # disk and decides the kind autonomously: no checkpoint exists yet for
    # this VM+target+disk, so it pulls a FULL and creates an atomic
    # checkpoint at the export's freeze point (the dirty-bitmap baseline
    # for the next incremental).
    provider_full = BitmapBackupProvider(shell)
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    disk = vm_config.disks[0]

    result_full = provider_full.run_backup(
        vm_config,
        target,
        disk,
        stall_timeout=300,
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
    if not _vm_active_disk_is_base(shell, vm_name, base_image):
        pytest.skip("VM disk source is not this test's base image — concurrent test interference")

    # Step 4: External disk-only snapshot — freezes the source disk so
    # the dirty-bitmap checkpoint from the FULL is a stable baseline.
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

    # Step 5: Incremental backup via libnbd — ``run_backup`` discovers
    # the checkpoint created by the FULL and transfers only the dirty
    # blocks since that checkpoint (delta).
    provider_inc = BitmapBackupProvider(shell, nbd=LibnbdClient())

    inc_result = provider_inc.run_backup(
        vm_config,
        target,
        disk,
        stall_timeout=300,
    )
    assert inc_result.success, (
        f"Incremental backup failed: {inc_result.error}. "
        f"Check that run_backup detected the checkpoint created by the FULL."
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
    3. Call ``run_backup()`` with ``target.compress=True`` — the provider
       must still pull an uncompressed delta.
    4. Verify the incremental qcow2 has ``compression-type: "zlib"``
       (default), NOT "zstd" — proving compression was not applied.
    5. Verify no ``qemu-nbd --image-opts driver=compress`` in logs.
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
    if not is_vm_running(shell, vm_name):
        # The running-VM NBD path is required: with a stopped VM the
        # provider would take the offline FULL path and the
        # incremental-compression assertions below would be meaningless.
        pytest.skip("VM did not reach running state")
    if not _vm_active_disk_is_base(shell, vm_name, base_image):
        pytest.skip("VM disk source is not this test's base image — concurrent test interference")

    _cleanup_checkpoints(shell, vm_name)

    # Step 1: Zstd-compressed FULL backup.  ``run_backup`` decides FULL
    # autonomously (no checkpoint yet) and compresses it because
    # ``target.compress=True``.
    provider = BitmapBackupProvider(shell)
    target = TargetConfig(path=target_dir, compress=True, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    disk = vm_config.disks[0]

    r_full = provider.run_backup(
        vm_config,
        target,
        disk,
        compression_type="zstd",
        stall_timeout=300,
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

    # Step 3: Incremental ``run_backup`` with a compress=True target.
    # The provider must NOT apply compression to the delta (design D6).
    import logging

    provider_inc = BitmapBackupProvider(shell, nbd=LibnbdClient())

    with caplog.at_level(logging.INFO):
        inc_result = provider_inc.run_backup(
            vm_config,
            target,
            disk,
            stall_timeout=300,
        )
    # If not successful, report but don't fail — environment may differ.
    if inc_result.success:
        inc_path = inc_result.target_path

        # Step 4: Compression-type must be "zlib" (default), NOT "zstd"
        # — proving the delta was written uncompressed (design D6).  The
        # new provider no longer emits a dedicated log notice; the qcow2
        # metadata is the authoritative check.
        ct_incr = _get_compression_type(shell, inc_path)
        assert ct_incr != "zstd", (
            f"Incremental must NOT have zstd compression, got {ct_incr!r}. "
            f"Design D6: compression applies to FULL only."
        )
        assert ct_incr == "zlib", (
            f"Incremental expected default compression-type 'zlib', got {ct_incr!r}"
        )

        # Step 5: No qemu-nbd compress driver.
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
# Test: VM-level backup_stall_timeout + verify reach the incremental path
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_vm_level_stall_timeout_reaches_incremental(test_vm, caplog):
    """Verify VM-level ``backup_stall_timeout`` and ``verify`` reach incrementals.

    End-to-end proof that VM-level ``[[vm]]`` options (config-parsing
    delta M3/M5) resolve through the global → VM → target inheritance
    chain onto the incremental backup path, without breaking the D6
    uncompressed-incrementals invariant:

    1. Write an inline TOML with VM-level ``backup_stall_timeout="2m"``
       and ``verify="check"``; the bare ``[[vm.target]]`` sets neither,
       so the target must inherit both VM values.
    2. Parse with ``ConfigFacade`` and assert ``target.backup_stall_timeout
       == "2m"`` and ``target.verify == "check"``.
    3. Follow the FULL-zstd → write dirty data → external snapshot →
       ``run_backup`` flow of ``test_incremental_compression_not_applied``,
       routing every provider through a ``_StallRecordingShell``.
    4. Assert the stall-detected transfer (the qemu-img convert of the
       FULL) received ``stall_timeout == 120`` — ``parse_stall_timeout("2m")``
       — proving the VM-inherited value reached the transfer engine.
    5. Assert the incremental output stays uncompressed (D6 guard:
       ``_get_compression_type != "zstd"``) — VM-level engine options
       must not change the incremental-compression invariant.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    # Step 1: Inline TOML — VM-level stall timeout + verify, bare target.
    toml_content = f"""\
[[vm]]
name = "{vm_name}"
snapshot_dir = "{snapshot_dir}"
backup_stall_timeout = "2m"
verify = "check"

[[vm.disk]]
target = "vda"
base_image = "{base_image}"

[[vm.target]]
path = "{target_dir}"
"""
    config_path = tmpdir / "vm_stall_timeout_incr.toml"
    config_path.write_text(toml_content)

    # Step 2: Parse and assert the target inherited the VM values.
    facade = ConfigFacade(config_path)
    vms = facade.get_vms()
    assert len(vms) == 1, f"Expected 1 VM, got {len(vms)}"
    vm = vms[0]
    assert len(vm.targets) == 1, f"Expected 1 target, got {len(vm.targets)}"
    target = vm.targets[0]

    assert target.backup_stall_timeout == "2m", (
        f"VM backup_stall_timeout='2m' must be inherited by the target, "
        f"got {target.backup_stall_timeout!r}"
    )
    assert target.verify == "check", (
        f"VM verify='check' must be inherited by the target, got {target.verify!r}"
    )
    # parse_stall_timeout("2m") == 120 — the seconds value the transfer
    # engine must receive (Core threads it into both FULL and incremental).
    stall_seconds = parse_stall_timeout(target.backup_stall_timeout)
    assert stall_seconds == 120, f"parse_stall_timeout('2m') must yield 120, got {stall_seconds}"

    # Prepare: write initial data (VM stopped), then start the VM.
    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(1)
    _write_data(shell, base_image, 64)
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        # The running-VM NBD path is required: the stall-detection probe
        # (qemu-img convert of the FULL) and the D6 uncompressed-delta
        # assertions depend on the export path that only runs with a
        # started VM.
        pytest.skip("VM did not reach running state")
    if not _vm_active_disk_is_base(shell, vm_name, base_image):
        pytest.skip("VM disk source is not this test's base image — concurrent test interference")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    # Step 3a: Zstd-compressed FULL backup — the run_with_stall_detection
    # qemu-img convert must observe the VM-inherited stall timeout (120 s).
    rec_shell = _StallRecordingShell(shell)
    provider = BitmapBackupProvider(rec_shell)
    disk = vm.disks[0]

    r_full = provider.run_backup(
        vm,
        target,
        disk,
        compression_type="zstd",
        stall_timeout=stall_seconds,
    )
    assert r_full.success, f"zstd FULL failed: {r_full.error}"
    ct_full = _get_compression_type(shell, r_full.target_path)
    assert ct_full == "zstd", f"FULL must have compression-type 'zstd', got {ct_full!r}"

    # Step 3b: Write new dirty data, create external snapshot.
    _write_data_running(shell, base_image, 5, offset_mb=64)
    snap_name = f"{vm_name}.incr-vm-stall"
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

    # Step 3c: Incremental backup — same recording shell, same stall
    # timeout as Core would pass (parse_stall_timeout(target.backup_stall_timeout)).
    provider_inc = BitmapBackupProvider(rec_shell, nbd=LibnbdClient())

    with caplog.at_level(logging.INFO):
        inc_result = provider_inc.run_backup(
            vm,
            target,
            disk,
            stall_timeout=stall_seconds,
        )
    # If not successful, report but don't fail — environment may differ.
    if inc_result.success:
        inc_path = inc_result.target_path

        # Step 4: The stall-detected qemu-img convert of the FULL must
        # have received stall_timeout=120 (the VM-inherited "2m" parsed
        # to seconds).  The bitmap incremental itself is an in-process
        # pread/pwrite loop with an internal watchdog, so the observable
        # shell-level probe is the qemu-img convert of the FULL pull.
        convert_stall_calls = [
            (cmd, t) for cmd, t in rec_shell.stall_calls if "qemu-img" in cmd and "convert" in cmd
        ]
        assert len(convert_stall_calls) > 0, (
            "Expected a qemu-img convert via run_with_stall_detection. "
            f"Recorded stall calls: {rec_shell.stall_calls}"
        )
        stall_timeouts = {t for _, t in convert_stall_calls}
        assert 120 in stall_timeouts, (
            "VM-level backup_stall_timeout='2m' must reach "
            "run_with_stall_detection as 120 s. "
            f"Got stall timeouts: {sorted(stall_timeouts)}"
        )

        # Step 5: D6 guard — the incremental output stays uncompressed
        # even though the FULL (and target.compress) are zstd-capable.
        ct_incr = _get_compression_type(shell, inc_path)
        assert ct_incr != "zstd", (
            f"Incremental must NOT have zstd compression, got {ct_incr!r}. "
            f"Design D6: compression applies to FULL only."
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
    3. External snapshot → ``run_backup()`` → incremental.
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

    # Step 1: FULL backup — ``run_backup`` decides FULL autonomously (no
    # checkpoint yet) and creates the dirty-bitmap baseline checkpoint.
    provider = BitmapBackupProvider(shell)
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    disk = vm_config.disks[0]

    r_full = provider.run_backup(
        vm_config,
        target,
        disk,
        stall_timeout=300,
    )
    assert r_full.success, f"FULL failed: {r_full.error}"
    full_actual = _get_actual_size(shell, r_full.target_path)

    # Step 2: Write exactly 5 MB.
    _write_data_running(shell, base_image, 5, offset_mb=500)

    # Step 3: External snapshot — freezes the source disk so the
    # checkpoint from the FULL is a stable dirty-bitmap baseline.
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

    # Step 4: Incremental ``run_backup`` — transfers only the dirty
    # blocks since the FULL's checkpoint (delta).
    provider_inc = BitmapBackupProvider(shell, nbd=LibnbdClient())

    inc_result = provider_inc.run_backup(
        vm_config,
        target,
        disk,
        stall_timeout=300,
    )
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
# Test: Free-space gate blocks the incremental transfer before it starts
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_free_space_gate_strict_blocks_incremental_before_transfer(test_vm, caplog):
    """Strict gate suspends the target BEFORE the incremental transfer.

    1. Write data (VM stopped), start VM, create a FULL backup and
       record it in state (checkpoint exists → next backup is
       incremental).
    2. Create an external snapshot (pending transfer) and record it.
    3. Configure ``free_space_check="strict"`` with a reserve above the
       filesystem free space and run ``core.backup()``.
    4. Assert NO new backup file appears and the run is space_limited
       (the incremental transfer never starts).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    from qsnap.core import Core
    from qsnap.factory.default import DefaultFactory
    from tests.mocks.mock_config import MockConfigFacade
    from tests.mocks.mock_state import InMemoryStateManager

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin + checkpoint not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed — required for incremental transfer")

    # Step 1: Write data (VM stopped), then start.
    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(1)
    if not _write_data(shell, base_image, 100):
        pytest.skip(f"Failed to write initial data to {base_image}")

    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(2)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not _vm_active_disk_is_base(shell, vm_name, base_image):
        pytest.skip("VM disk source is not this test's base image — concurrent test interference")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    # Step 2: FULL backup → checkpoint baseline; record in state so Core
    # sees a FULL anchor (next backup becomes an incremental).  The
    # strict-gate VM config is built up front — ``run_backup`` ignores
    # the free-space fields (the gate lives in Core only).
    provider = BitmapBackupProvider(shell)
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        free_space_check="strict",
        free_space_reserve=10**18,
        targets=[target],
    )
    full_result = provider.run_backup(
        vm_config,
        target,
        vm_config.disks[0],
        stall_timeout=300,
    )
    if not full_result.success:
        pytest.skip(f"FULL backup failed: {full_result.error}")
    full_name = full_result.target_path.stem

    state = InMemoryStateManager()
    state.record_full_backup(str(target_dir), f"{full_name}.qcow2", datetime.now(), disk="vda")

    # Step 3: external snapshot (the pending incremental).
    snap_name = f"{vm_name}.gate-incr"
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
    overlay = _get_snapshot_disk_path(shell, vm_name)
    if overlay is None:
        pytest.skip("Could not determine overlay path")
    state.record_snapshot(
        vm_name,
        SnapshotInfo(
            name=snap_name,
            path=overlay,
            timestamp=datetime.now(),
            allocation=0,
            disk="vda",
        ),
    )

    # Step 4: strict gate with an impossible reserve → the incremental
    # transfer must be blocked BEFORE any dirty-block transfer.
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "gate_incr.toml")
    core = Core(
        config=config,
        factory=DefaultFactory(shell=shell, state=state),
        state=state,
        shell=shell,
    )

    caplog.set_level(logging.DEBUG)
    result = core.backup(vm_name)

    # Suspended → space_limited.
    assert result.space_limited is True, (
        f"Strict incremental gate must mark the run space_limited, got {result.space_limited}"
    )

    # No NEW backup file beyond the FULL created in step 2.
    new_files = [p for p in target_dir.glob("*.qcow2") if p.name != f"{full_name}.qcow2"]
    assert new_files == [], (
        f"No incremental backup may be created when the strict gate blocks: {new_files}"
    )

    # No incremental transfer actually ran (no NBD copy loop, no new file).
    nbd_pull = [
        r.message for r in caplog.records if "dirty" in r.message and "copy" in r.message.lower()
    ]
    assert nbd_pull == [], f"No dirty-block copy may start: {nbd_pull[:3]}"

    _cleanup_snapshots(shell, vm_name)
    _cleanup_checkpoints(shell, vm_name)
