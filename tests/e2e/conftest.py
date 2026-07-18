"""Shared fixtures for qsnap end-to-end tests.

E2E tests exercise the full pipeline from a TOML config file through
snapshot creation, backup transfer, retention, and restore.  They are
marked with ``@pytest.mark.e2e`` and excluded from normal test runs
via ``-m "not e2e"``.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from qsnap.shell.subprocess_shell import SubprocessShell


@pytest.fixture
def e2e_vm():
    """Create a disposable test VM for end-to-end pipeline tests.

    Creates a minimal VM with a qcow2 disk, writes a TOML config file
    that references it, and yields everything needed to run the full
    ``qsnap`` pipeline against it.

    Yields a dict with:
        shell
            ``SubprocessShell`` instance for real virsh/qemu-img calls.
        vm_name : str
            Name of the test VM.
        base_image : Path
            Path to the base qcow2 disk image.
        config_path : Path
            Path to the TOML config file for qsnap.
        snapshot_dir : Path
            Directory for snapshot storage.
        target_dir : Path
            Directory for backup target storage.
        tmpdir : Path
            Root of the temporary working directory.

    Skips if libvirt/qemu-img are unavailable.
    """
    shell = SubprocessShell()
    tmpdir = Path(tempfile.mkdtemp(prefix="qsnap-e2e-"))
    vm_name = "qsnap-e2e-vm"
    base_image = tmpdir / f"{vm_name}.qcow2"
    snapshot_dir = tmpdir / "snapshots"
    target_dir = tmpdir / "backup"
    config_path = tmpdir / "qsnap.toml"

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    create_result = shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(base_image), "256M"],
        timeout=30,
    )
    if not create_result.success:
        pytest.skip(f"qemu-img create failed (qemu-img not available?): {create_result.error}")

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

    # Write a minimal qsnap TOML config referencing the test VM.
    config_path.write_text(
        f"[global]\n"
        f'state_dir = "{tmpdir / "state"}"\n'
        f"\n"
        f"[[vm]]\n"
        f'name = "{vm_name}"\n'
        f'snapshot_dir = "{snapshot_dir}"\n'
        f"\n"
        f"  [[vm.target]]\n"
        f'  path = "{target_dir}"\n'
        f"  incremental = true\n"
    )

    try:
        yield {
            "shell": shell,
            "vm_name": vm_name,
            "base_image": base_image,
            "config_path": config_path,
            "snapshot_dir": snapshot_dir,
            "target_dir": target_dir,
            "tmpdir": tmpdir,
        }
    finally:
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        shell.run(["virsh", "undefine", vm_name], timeout=30)
        shutil.rmtree(str(tmpdir), ignore_errors=True)
