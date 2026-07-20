"""Shared fixtures for qsnap integration tests.

Integration tests require a running libvirt daemon and are marked with
``@pytest.mark.integration``.  They are excluded from normal test runs
via ``-m "not integration"``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from qsnap.shell.subprocess_shell import SubprocessShell


@pytest.fixture
def test_vm():
    """Create and manage a disposable test VM for integration tests.

    Creates a minimal VM with a tiny qcow2 disk.  The VM is defined in
    libvirt but NOT started — individual tests decide whether to start
    it.  Uses ``type='qemu'`` to avoid KVM hardware dependency.

    The VM has no bootable media, so starting it creates a running QEMU
    process (enough for NBD tests) that won't boot an OS.

    Yields a dict with:
        shell
            ``SubprocessShell`` instance for real virsh/qemu-img calls.
        vm_name : str
            Name of the test VM (``"qsnap-int-test-vm"``).
        base_image : Path
            Path to the base qcow2 disk image.
        snapshot_dir : Path
            Directory for snapshot storage.
        target_dir : Path
            Directory for backup target storage.
        tmpdir : Path
            Root of the temporary working directory.

    Teardown: destroys the VM, undefines it, cleans up the NBD socket,
    and removes all temporary files.
    """
    shell = SubprocessShell()
    tmpdir = Path(tempfile.mkdtemp(prefix="qsnap-integration-"))
    vm_name = "qsnap-int-test-vm"
    base_image = tmpdir / f"{vm_name}.qcow2"
    snapshot_dir = tmpdir / "snapshots"
    target_dir = tmpdir / "backup"

    # Create directories
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Create a minimal qcow2 disk image (256M)
    create_result = shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(base_image), "256M"],
        timeout=30,
    )
    if not create_result.success:
        pytest.skip(f"qemu-img create failed (qemu-img not available?): {create_result.error}")

    # Build minimal VM XML — no bootable media so QEMU starts but
    # doesn't boot an OS (sufficient for NBD export tests).
    xml = (
        f'<domain type="qemu">\n'
        f"  <name>{vm_name}</name>\n"
        f"  <memory unit='KiB'>262144</memory>\n"
        f"  <vcpu placement='static'>1</vcpu>\n"
        f"  <os>\n"
        f"    <type arch='x86_64' machine='pc'>hvm</type>\n"
        f'    <boot dev="hd"/>\n'
        f"  </os>\n"
        f"  <devices>\n"
        f'    <disk type="file" device="disk">\n'
        f'      <driver name="qemu" type="qcow2"/>\n'
        f'      <source file="{base_image}"/>\n'
        f'      <target dev="vda" bus="virtio"/>\n'
        f"    </disk>\n"
        f"  </devices>\n"
        f"</domain>\n"
    )
    xml_path = tmpdir / f"{vm_name}.xml"
    xml_path.write_text(xml)

    # Define the VM in libvirt
    define_result = shell.run(
        ["virsh", "define", str(xml_path)],
        timeout=30,
    )
    if not define_result.success:
        shutil.rmtree(str(tmpdir), ignore_errors=True)
        pytest.skip(f"virsh define failed (libvirt daemon not available?): {define_result.error}")

    try:
        yield {
            "shell": shell,
            "vm_name": vm_name,
            "base_image": base_image,
            "snapshot_dir": snapshot_dir,
            "target_dir": target_dir,
            "tmpdir": tmpdir,
        }
    finally:
        # Teardown: destroy, undefine, and clean up all files.
        # First, clean up any leftover checkpoints to allow undefine.
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        _cleanup_checkpoints(shell, vm_name)
        shell.run(["virsh", "undefine", vm_name], timeout=30)
        shell.run(
            ["rm", "-f", f"/tmp/qsnap-backup-{os.getpid()}.sock"],
            timeout=10,
        )
        shutil.rmtree(str(tmpdir), ignore_errors=True)


def _cleanup_checkpoints(shell, vm_name: str) -> None:
    """Delete all checkpoints for *vm_name* so virsh undefine can succeed."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if result.success:
        for line in result.stdout.strip().splitlines():
            cp = line.strip()
            if cp:
                shell.run(
                    ["virsh", "checkpoint-delete", "--domain", vm_name, cp, "--metadata"],
                    timeout=30,
                )
