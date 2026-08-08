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

    # Pre-cleanup: destroy and undefine any stale domain left behind by a
    # previous test run that crashed before its teardown (finally block) ran.
    # Without this, ``virsh define`` fails with "domain already exists" and
    # the fixture skips — the stale VM is never cleaned up, perpetuating the
    # problem across all subsequent runs.  Checkpoints must be deleted first
    # because ``virsh undefine`` refuses to remove a domain that still has
    # checkpoint metadata (the backup stage creates checkpoints).
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    _cleanup_checkpoints(shell, vm_name)
    shell.run(["virsh", "undefine", vm_name], timeout=30)

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
    # ``lockfile = "off"``: e2e tests run as non-root and cannot create
    # the default lockfile directory ``/var/lib/qsnap`` (PermissionError).
    # The sentinel disables locking for the duration of the tests.
    config_path.write_text(
        f"[global]\n"
        f'state_dir = "{tmpdir / "state"}"\n'
        f'lockfile = "off"\n'
        f"\n"
        f"[[vm]]\n"
        f'name = "{vm_name}"\n'
        f'snapshot_dir = "{snapshot_dir}"\n'
        f"\n"
        f"  [[vm.disk]]\n"
        f'  target = "vda"\n'
        f'  base_image = "{base_image}"\n'
        f"\n"
        f"  [[vm.target]]\n"
        f'  path = "{target_dir}"\n'
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
        # Teardown: destroy the VM, delete checkpoints (the backup stage
        # creates them and ``virsh undefine`` refuses to remove a domain
        # with checkpoint metadata), undefine, and remove all temp files.
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        _cleanup_checkpoints(shell, vm_name)
        shell.run(["virsh", "undefine", vm_name], timeout=30)
        shutil.rmtree(str(tmpdir), ignore_errors=True)


def _cleanup_checkpoints(shell, vm_name: str) -> None:
    """Delete all checkpoints for *vm_name* so ``virsh undefine`` can succeed.

    The backup stage creates libvirt checkpoints for dirty-bitmap NBD
    exports.  ``virsh undefine`` refuses to remove an inactive domain
    that still has checkpoint metadata, so they must be deleted first.
    """
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
