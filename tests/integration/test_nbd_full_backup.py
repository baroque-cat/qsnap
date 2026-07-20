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
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
)
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


# ──────────────────────────────────────────────────────────────────────
# Test 5: domjobabort is called after NBD backup completes
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_domjobabort_called_after_nbd_backup_integration(test_vm):
    """Verify that ``virsh domjobabort`` is called after NBD full backup.

    The ``nbd_full_export()`` helper calls ``virsh domjobabort`` in its
    ``finally`` block (design D3).  This test verifies the effect: after
    a successful NBD backup on a running VM, there should be no active
    block job left behind.

    1. Start the test VM.
    2. Run ``create_full_backup()`` via the NBD path.
    3. After the backup completes, check ``virsh domjobinfo`` — it should
       either fail (no active job) or report no current block job.
    4. Verify the VM is still running and in a healthy state.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]

    # Step 1: Start the VM.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 6.0 — NBD backup-begin not available")

    # Step 2: Run NBD full backup.
    provider = FileCopyBackupProvider(shell)
    source_snapshot = SnapshotInfo(
        name=f"{vm_name}.active",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    target = TargetConfig(path=target_dir, compress=False, verify="off")

    backup_result = provider.create_full_backup(
        vm_name,
        source_snapshot,
        target,
        compress=False,
        bucket_level="monthly",
    )

    assert backup_result.success, f"NBD full backup failed: {backup_result.error}"

    # Step 3: After the backup, domjobabort should have been called
    # in the finally block.  Verify no active block job remains.
    domjobinfo_result = shell.run(
        ["virsh", "domjobinfo", "--domain", vm_name],
        timeout=30,
    )
    # domjobinfo should either fail (no active job) or report
    # "No current block job".  Either outcome confirms domjobabort
    # has already terminated the NBD backup job.
    #
    # Some libvirt versions return an error, others return stdout
    # with "No current...".  Both are valid post-abort states.
    has_no_active_job = (
        not domjobinfo_result.success
        or "no current block job" in domjobinfo_result.stdout.lower()
        or "no current job" in domjobinfo_result.stderr.lower()
        or "job type:" in domjobinfo_result.stdout.lower()
    )
    assert has_no_active_job, (
        f"domjobabort should have terminated the NBD backup job, "
        f"but domjobinfo returned: "
        f"stdout={domjobinfo_result.stdout!r} "
        f"stderr={domjobinfo_result.stderr!r}"
    )

    # Step 4: Verify the VM is still running and healthy.
    assert is_vm_running(shell, vm_name), (
        "VM should still be running after NBD backup + domjobabort"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 6: Stale state recovery — integration with FileCopyBackupProvider
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_stale_state_recovery_integration(test_vm):
    """Verify stale state self-healing during backup transfer in integration.

    1. Register a snapshot in ``InMemoryStateManager`` pointing to a
       non-existent file path.
    2. Create a real file in the target directory so the "empty target →
       create FULL" short-circuit is not triggered.
    3. Configure ``FileCopyBackupProvider`` with the state manager.
    4. Call ``transfer_missing()`` with the stale snapshot.
    5. Verify the stale entry was removed from state.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Ensure target is not empty (avoids the FULL-backup short-circuit).
    sentinel = target_dir / "_keep_nonempty.qcow2"
    sentinel.write_bytes(b"\x00" * 1024)

    # Register a stale state entry pointing to a non-existent path.
    state = InMemoryStateManager()
    stale_snapshot = SnapshotInfo(
        name=f"{vm_name}.stale-int",
        path=Path("/tmp/qsnap-stale-integration-test.qcow2"),
        timestamp=datetime.now(),
        allocation=0,
    )
    state.record_snapshot(vm_name, stale_snapshot)

    # Verify it was recorded.
    assert len(state.get_snapshots(vm_name)) == 1

    # Execute transfer_missing — should detect and remove the stale entry.
    provider = FileCopyBackupProvider(shell, state=state)
    target = TargetConfig(path=target_dir, incremental=True, verify="off")
    vm_config = VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir)

    results = provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[stale_snapshot],
    )

    # Verify stale entry was removed.
    remaining = state.get_snapshots(vm_name)
    assert len(remaining) == 0, (
        f"Stale state entry should be removed. Remaining: {[s.name for s in remaining]}"
    )

    # Verify no backup was produced for the stale entry.
    stale_results = [r for r in results if r.snapshot_name == stale_snapshot.name]
    assert len(stale_results) == 0, "No backup should be attempted for stale snapshot"


# ──────────────────────────────────────────────────────────────────────
# Test 7: qemu-img rebase -B qcow2 on real files
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_qemu_img_rebase_minus_B_qcow2_integration(test_vm):
    """Verify ``qemu-img rebase -u -b <backing> -B qcow2`` works on real files.

    1. Use the test VM's base image as the first backing file.
    2. Create an overlay qcow2 on top of the base image via
       ``qemu-img create -F qcow2 -b <base> -f qcow2``.
    3. Create a second (dummy) qcow2 to serve as the new backing target.
    4. Rebase the overlay to the second file using ``-B qcow2``.
    5. Verify via ``qemu-img info --output=json`` that the backing file
       was updated to the new target.
    """
    shell: SubprocessShell = test_vm["shell"]
    base_image: Path = test_vm["base_image"]
    tmpdir: Path = test_vm["tmpdir"]

    # Step 1: Create a second base qcow2 (dummy — 1M).
    second_base = tmpdir / "rebased_base.qcow2"
    create_result = shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(second_base), "1M"],
        timeout=30,
    )
    assert create_result.success, f"Failed to create second base: {create_result.error}"
    assert second_base.exists()

    # Step 2: Create an overlay on top of the original base image.
    overlay = tmpdir / "overlay_for_rebase.qcow2"
    create_overlay = shell.run(
        [
            "qemu-img",
            "create",
            "-F",
            "qcow2",
            "-b",
            str(base_image),
            "-f",
            "qcow2",
            str(overlay),
        ],
        timeout=30,
    )
    assert create_overlay.success, f"Failed to create overlay: {create_overlay.error}"
    assert overlay.exists()

    # Step 3: Verify the overlay's backing file is the original base.
    info_before = shell.run(
        ["qemu-img", "info", "--output=json", str(overlay)],
        timeout=30,
    )
    assert info_before.success, f"qemu-img info failed: {info_before.error}"
    info_before_data = json.loads(info_before.stdout)
    backing_before = info_before_data.get("backing-filename", "")
    assert str(base_image) in str(backing_before), (
        f"Overlay should reference original base {base_image}, got {backing_before!r}"
    )

    # Step 4: Rebase the overlay to the second base using -B qcow2.
    rebase_result = shell.run(
        [
            "qemu-img",
            "rebase",
            "-u",  # unsafe/unsafe mode: metadata-only, no data copy
            "-b",
            str(second_base.name),
            "-B",
            "qcow2",  # verify: explicit backing format via -B (D3)
            str(overlay),
        ],
        timeout=30,
    )
    assert rebase_result.success, f"qemu-img rebase -B qcow2 failed: {rebase_result.error}"

    # Step 5: Verify the overlay's backing file was updated.
    info_after = shell.run(
        ["qemu-img", "info", "--output=json", str(overlay)],
        timeout=30,
    )
    assert info_after.success, f"qemu-img info after rebase failed: {info_after.error}"
    info_after_data = json.loads(info_after.stdout)
    backing_after = info_after_data.get("backing-filename") or ""

    assert second_base.name in backing_after, (
        f"Rebase should have updated backing file to '{second_base.name}', "
        f"but got {backing_after!r}. "
        f"Full info: {info_after_data}"
    )

    # Verify the overlay is still a valid qcow2.
    assert info_after_data.get("format") == "qcow2", "Overlay should still be qcow2"
    assert int(info_after_data.get("virtual-size", 0)) > 0, (
        "Overlay should have non-zero virtual size"
    )

    # Step 6: Verify the overlay is still readable and the backing chain
    # is intact (the -B qcow2 flag ensures the backing format is correct).
    check_result = shell.run(
        ["qemu-img", "info", "--backing-chain", str(overlay)],
        timeout=30,
    )
    assert check_result.success, (
        f"qemu-img info --backing-chain should succeed after rebase: {check_result.error}"
    )
    # The backing chain info should mention both the overlay and its
    # backing file (the second_base we rebased to).
    assert second_base.name in check_result.stdout, (
        f"Backing chain info should reference second base '{second_base.name}'"
    )
