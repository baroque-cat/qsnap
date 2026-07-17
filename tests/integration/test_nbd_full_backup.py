"""Integration tests for NBD full backup and fork on live VMs.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py`` which creates a disposable throwaway VM.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration

Source bugs discovered during testing are reported as comments — not
fixed in this file.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.backup.file_copy import FileCopyBackupProvider
from qsnap.modules.backup.nbd_helper import (
    is_libvirt_new_enough,
    is_vm_running,
    nbd_full_export,
)
from qsnap.shell.subprocess_shell import SubprocessShell
from tests.mocks import (
    InMemoryStateManager,
    MockConfigFacade,
    MockVMModuleFactory,
)


# ──────────────────────────────────────────────────────────────────────
# Test 1: NBD full backup of a running VM
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_nbd_full_backup_running_vm_integration(test_vm):
    """Create a full backup of a running VM via NBD pull-model.

    1. Start the test VM.
    2. Verify ``is_vm_running()`` returns True.
    3. Create ``FileCopyBackupProvider`` and call ``create_full_backup()``.
    4. Verify the result file is a standalone qcow2 with no backing file.
    5. Verify no lock conflict occurred (the backup succeeded despite
       the VM holding an exclusive write lock on the active layer).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Step 1: Start the VM
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")

    # Give libvirt a moment to initialize QEMU
    time.sleep(1)

    # Step 2: Verify the VM is running
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state — is libvirt/QEMU healthy?")

    # Step 3: Check that libvirt is new enough for NBD
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 6.0 — NBD backup-begin not available")

    # Step 4: Create the backup provider and SnapshotInfo for the
    # active disk layer.
    provider = FileCopyBackupProvider(shell)
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

    # Step 5: Create full backup — this should use the NBD path
    # because the VM is running and libvirt is new enough.
    result = provider.create_full_backup(
        vm_name,
        source_snapshot,
        target,
        compress=False,
        bucket_level="monthly",
    )

    assert result.success, (
        f"NBD full backup failed: {result.error}. QEMU/libvirt may not support backup-begin."
    )
    assert result.target_path.exists(), f"Backup file not found at {result.target_path}"
    assert result.bytes_transferred > 0, "Backup should contain non-zero bytes"

    # Step 6: Verify the backup is a standalone qcow2 — no backing
    # file dependency (the whole point of a FULL anchor).
    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(result.target_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    backing = info.get("backing-filename")
    assert backing is None or backing == "", (
        f"FULL backup should be standalone (no backing file), got: {backing!r}"
    )

    # Step 7: Verify no lock conflict — the backup succeeded despite
    # the running VM's exclusive write lock.  This confirms NBD was
    # used rather than direct qemu-img convert.
    assert info.get("format") == "qcow2", "Backup should be valid qcow2"
    assert int(info.get("virtual-size", 0)) > 0, "Backup should have non-zero virtual size"


# ──────────────────────────────────────────────────────────────────────
# Test 2: Direct convert full backup of a stopped VM
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_full_backup_stopped_vm_direct_convert_integration(test_vm):
    """Create a full backup of a stopped VM via direct qemu-img convert.

    1. Ensure the VM is stopped (fixture never started it).
    2. Verify ``is_vm_running()`` returns False.
    3. Create ``FileCopyBackupProvider`` and call ``create_full_backup()``.
    4. Verify the result file exists and is a standalone qcow2.
    5. Verify no NBD socket was created (direct convert path used).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    # Step 1: VM is stopped by default (fixture only defines it).
    # Double-check.
    if is_vm_running(shell, vm_name):
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        time.sleep(1)

    assert not is_vm_running(shell, vm_name), "VM should be stopped for direct convert test"

    # Step 2: Create backup provider.
    provider = FileCopyBackupProvider(shell)
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

    # Step 3: Create full backup — should use direct qemu-img convert.
    result = provider.create_full_backup(
        vm_name,
        source_snapshot,
        target,
        compress=False,
        bucket_level="monthly",
    )

    assert result.success, f"Direct convert full backup failed: {result.error}"
    assert result.target_path.exists(), f"Backup file not found at {result.target_path}"
    assert result.bytes_transferred > 0

    # Step 4: Verify standalone qcow2.
    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(result.target_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    backing = info.get("backing-filename")
    assert backing is None or backing == "", (
        f"FULL backup should be standalone, got backing: {backing!r}"
    )

    # Step 5: Verify no NBD socket was created (confirm direct path).
    socket_path = Path(f"/tmp/qsnap-backup-{os.getpid()}.sock")
    assert not socket_path.exists(), (
        f"NBD socket {socket_path} should not exist — direct convert path should not touch NBD"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 3: NBD socket and .tmp cleanup after simulated crash
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_nbd_socket_cleanup_after_crash_integration(test_vm):
    """Verify NBD socket and .tmp cleanup after a simulated crash.

    1. Create stale socket and .tmp files to simulate a crashed run.
    2. Start the test VM.
    3. Call ``create_full_backup()`` — this will take the NBD path.
    4. Verify the socket file is removed (step (a) + finally block).
    5. Verify the .tmp file is removed on failure or renamed on success.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    # Step 1: Create stale artifacts from a simulated crashed run.
    # Use today's date for the .tmp file so it matches what
    # create_full_backup() would create (it uses the snapshot timestamp,
    # which is datetime.now() in this test).
    socket_path = Path(f"/tmp/qsnap-backup-{os.getpid()}.sock")
    today_str = datetime.now().strftime("%Y%m%d")
    tmp_file = target_dir / f"{vm_name}.FULL.{today_str}.qcow2.tmp"

    socket_path.write_text("")  # empty socket file
    tmp_file.write_bytes(b"\x00" * 1024)  # 1 KB of corrupted data

    assert socket_path.exists(), "Stale socket file should exist before test"
    assert tmp_file.exists(), "Stale .tmp file should exist before test"

    # Step 2: Start the VM to trigger the NBD path.
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(1)

    vm_running = is_vm_running(shell, vm_name)
    nbd_available = is_libvirt_new_enough(shell)

    if vm_running and nbd_available:
        # NBD path will be taken.  Even if the export fails (e.g.
        # backup-begin not supported on this QEMU version), the socket
        # *must* be cleaned up by nbd_full_export's finally block.
        provider = FileCopyBackupProvider(shell)
        source_snapshot = SnapshotInfo(
            name=f"{vm_name}.active",
            path=base_image,
            timestamp=datetime.now(),
            allocation=0,
        )
        target = TargetConfig(path=target_dir, compress=False, verify="off")

        result = provider.create_full_backup(
            vm_name,
            source_snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )

        # The socket MUST be gone — either removed by step (a) (rm -f
        # stale socket before starting) or by the finally block.
        assert not socket_path.exists(), (
            f"Socket {socket_path} was not cleaned up!  "
            f"nbd_full_export should always clean up in finally."
        )

        # The .tmp file MUST be gone — either renamed to final
        # (success) or rm -f'd (failure).
        assert not tmp_file.exists(), (
            f"Temporary file {tmp_file} was not cleaned up!  "
            f"create_full_backup should remove .tmp on failure "
            f"or rename it on success."
        )
    else:
        # NBD not available — test the direct convert cleanup path
        # with a source that will fail, proving .tmp cleanup.
        provider = FileCopyBackupProvider(shell)
        source_snapshot = SnapshotInfo(
            name=f"{vm_name}.active",
            path=Path("/nonexistent/path.qcow2"),
            timestamp=datetime.now(),
            allocation=0,
        )
        target = TargetConfig(path=target_dir, compress=False, verify="off")

        result = provider.create_full_backup(
            vm_name,
            source_snapshot,
            target,
            compress=False,
            bucket_level="monthly",
        )
        assert not result.success, "Expected failure with nonexistent source"
        assert not tmp_file.exists(), f"Temporary file {tmp_file} was not cleaned up on failure"

    # Final safety check: ensure the socket is gone regardless.
    assert not socket_path.exists(), f"Socket {socket_path} should be cleaned up in all code paths"


# ──────────────────────────────────────────────────────────────────────
# Test 4: Fork from a running VM via NBD
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_fork_running_vm_nbd_integration(test_vm):
    """Fork a running VM snapshot via NBD pull-model.

    1. Start the test VM.
    2. Register a snapshot in InMemoryStateManager pointing to the
       active disk layer.
    3. Configure Core with MockConfigFacade (test VM) and real shell.
    4. Call ``Core.fork()`` to create a standalone VM.
    5. Verify the forked qcow2 exists and is standalone (no backing).
    6. Verify the forked VM is defined in libvirt.
    7. Clean up the forked VM.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Step 1: Start the VM.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 6.0 — NBD fork not available")

    # Step 2: Register the VM's active layer as a "snapshot" in state,
    # so that Core._resolve_snapshot() can find it.
    state = InMemoryStateManager()
    snapshot_name = f"{vm_name}.active"
    state.record_snapshot(
        vm_name,
        SnapshotInfo(
            name=snapshot_name,
            path=base_image,
            timestamp=datetime.now(),
            allocation=0,
        ),
    )

    # Step 3: Configure Core with the real shell and mock config/factory.
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "qsnap-test.toml",
    )
    factory = MockVMModuleFactory()

    core = Core(
        config=config,
        factory=factory,
        state=state,
        shell=shell,
    )

    forked_vm_name = "qsnap-forked-test-vm"
    storage_dir = tmpdir / "forked"

    # Step 4: Fork from the snapshot.
    result = core.fork(
        snapshot_name=snapshot_name,
        new_vm_name=forked_vm_name,
        storage_dir=storage_dir,
    )

    assert result.success, f"Fork from running VM via NBD failed: {result.error}"
    assert result.restored_path.exists(), f"Forked qcow2 not found at {result.restored_path}"

    # Step 5: Verify the forked qcow2 is standalone (no backing file).
    info_result = shell.run(
        [
            "qemu-img",
            "info",
            "--output=json",
            str(result.restored_path),
        ],
        timeout=30,
    )
    assert info_result.success, f"Cannot inspect forked qcow2: {info_result.error}"
    info = json.loads(info_result.stdout)
    backing = info.get("backing-filename")
    assert backing is None or backing == "", (
        f"Forked qcow2 should be standalone, got backing: {backing!r}"
    )

    # Step 6: Verify the forked VM is defined in libvirt.
    dominfo_result = shell.run(
        ["virsh", "dominfo", "--domain", forked_vm_name],
        timeout=30,
    )
    if not dominfo_result.success:
        pytest.fail(
            f"Forked VM {forked_vm_name} not found in libvirt — "
            f"fork should have defined it: {dominfo_result.error}"
        )

    # Clean up the forked VM after all assertions.
    try:
        shell.run(["virsh", "destroy", forked_vm_name], timeout=30)
        shell.run(["virsh", "undefine", forked_vm_name], timeout=30)
    finally:
        pass
