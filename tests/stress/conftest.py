"""Shared fixtures for qsnap stress tests.

Stress tests exercise long snapshot chains, concurrent pipeline runs,
and disk-full scenarios.  They are marked with ``@pytest.mark.stress``
and excluded from normal test runs via ``-m "not stress"``.

The ``conftest`` is debugging-friendly: it does not capture stdout
(equivalent to ``-s``) and uses longer timeouts than integration tests.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from qsnap.shell.subprocess_shell import SubprocessShell


@pytest.fixture
def stress_env():
    """Create a disposable test VM environment for stress tests.

    Similar to the integration ``test_vm`` fixture but with larger disk
    images and directories sized for stress scenarios (long chains,
    repeated snapshots).

    Yields a dict with the same keys as the integration ``test_vm``
    fixture.  Skips if libvirt/qemu-img are unavailable.
    """
    shell = SubprocessShell()
    tmpdir = Path(tempfile.mkdtemp(prefix="qsnap-stress-"))
    vm_name = "qsnap-stress-vm"
    base_image = tmpdir / f"{vm_name}.qcow2"
    snapshot_dir = tmpdir / "snapshots"
    target_dir = tmpdir / "backup"

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Create a qcow2 disk image (512M — larger than integration for chain depth)
    create_result = shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(base_image), "512M"],
        timeout=30,
    )
    if not create_result.success:
        pytest.skip(f"qemu-img create failed (qemu-img not available?): {create_result.error}")

    # Pre-cleanup: destroy, delete checkpoints, and undefine any stale
    # domain left behind by a crashed stress run (mirrors the integration
    # conftest).  Without this, a single SIGKILL/timeout poisons every
    # subsequent stress session with "domain already exists".
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    cp_result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if cp_result.success:
        for line in cp_result.stdout.strip().splitlines():
            cp = line.strip()
            if cp:
                shell.run(
                    ["virsh", "checkpoint-delete", "--domain", vm_name, cp, "--metadata"],
                    timeout=30,
                )
    shell.run(["virsh", "undefine", vm_name], timeout=30)

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
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        shell.run(["virsh", "undefine", vm_name], timeout=30)
        shell.run(
            ["rm", "-f", f"/tmp/qsnap-backup-{os.getpid()}.sock"],
            timeout=10,
        )
        shutil.rmtree(str(tmpdir), ignore_errors=True)
