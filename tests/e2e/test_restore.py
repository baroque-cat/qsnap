"""E2E test: restore a snapshot to the base image with the default retention.

Verifies the restore path is unchanged under the default hysteresis mode
(trigger threshold H=72, collapse floor L=24): a single snapshot is far
below the threshold, so the grow phase never commits it (the chain stays
deep), yet ``qsnap restore`` still flattens the chain into a standalone
qcow2, replaces the base image, and the VM boots afterwards.

Marked ``@pytest.mark.e2e`` — requires a libvirt environment with a
disposable test VM.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from qsnap.cli.app import main
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_vm_running


def _qemu_img_check_ok(shell: SubprocessShell, path: Path) -> bool:
    result = shell.run(
        ["qemu-img", "check", "--force-share", str(path)],
        timeout=120,
    )
    return result.success and "No errors were found" in result.stdout


@pytest.mark.e2e
@pytest.mark.timeout(3600)
def test_restore_backup_to_new_vm(e2e_vm):
    """Restore still succeeds with the default hysteresis retention active."""
    shell: SubprocessShell = e2e_vm["shell"]
    vm_name: str = e2e_vm["vm_name"]
    config_path: Path = e2e_vm["config_path"]
    snapshot_dir: Path = e2e_vm["snapshot_dir"]
    base_image: Path = e2e_vm["base_image"]

    # Start the VM and create a snapshot via the CLI's snapshot command
    # (steps 1-4 only — no backup transfer, so the floor applies to the
    # snapshot chain without any backup-stage dependency).
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    assert is_vm_running(shell, vm_name), "VM should be running"

    snap_rc = main(["--config", str(config_path), "snapshot"])
    assert snap_rc == 0, f"qsnap snapshot must succeed, got rc={snap_rc}"

    snap_files = sorted(snapshot_dir.glob("*.qcow2"))
    assert len(snap_files) >= 1, f"Expected at least one snapshot, got {len(snap_files)}"
    # Default hysteresis mode: N=1 is far below the H=72 trigger, so the
    # grow phase preserved the snapshot — it was not blockcommitted.
    for p in snap_files:
        assert p.exists(), f"Snapshot must be preserved by the hysteresis threshold: {p}"

    # Stop the VM — restore requires a stopped domain.
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)
    assert not is_vm_running(shell, vm_name), "VM must be stopped for restore"

    snap_name = snap_files[0].stem

    # Restore (with --yes to skip the confirmation prompt).
    restore_rc = main(["--config", str(config_path), "restore", snap_name, "--yes"])
    assert restore_rc == 0, f"qsnap restore must succeed, got rc={restore_rc}"

    # The restored base image is a valid standalone qcow2.
    assert base_image.exists(), "Base image must exist after restore"
    assert _qemu_img_check_ok(shell, base_image), (
        f"Restored base image must pass qemu-img check: {base_image}"
    )

    # The VM boots off the restored image.
    start_after = shell.run(["virsh", "start", vm_name], timeout=30)
    assert start_after.success, f"VM must boot after restore, got: {start_after.error}"
    time.sleep(1)
    assert is_vm_running(shell, vm_name), "VM should be running after restore"

    shell.run(["virsh", "destroy", vm_name], timeout=30)
