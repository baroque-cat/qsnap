"""Shared fixtures for qsnap integration tests.

Integration tests require a running libvirt daemon and are marked with
``@pytest.mark.integration``.  They are excluded from normal test runs
via ``-m "not integration"``.

Temp directories are created under ``/var/tmp`` (on-disk, ~47 GB free)
rather than ``/tmp`` (tmpfs, ~3.7 GB) so that performance tests can use
large disk images without exhausting RAM.  Unix-domain NBD sockets are
still placed in ``/tmp`` by the source code (see ``bitmap.py``) — they
are tiny and benefit from tmpfs latency.

A session-scoped autouse fixture cleans up stale
``qsnap-integration-*`` directories left behind by crashed tests.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from qsnap.shell.subprocess_shell import SubprocessShell

#: Base directory for integration-test working dirs.  ``/var/tmp`` is
#: on disk (not tmpfs) and typically has tens of GB free — enough for
#: 2+ GB qcow2 images and their backups.
_INTEGRATION_TMP = "/var/tmp"

#: Stale-dir cleanup threshold (seconds).  Directories older than this
#: are removed at session start to clean up after crashed tests.
_STALE_DIR_AGE_SEC = 24 * 3600  # 24 hours


@pytest.fixture(autouse=True, scope="session")
def _cleanup_stale_integration_dirs():
    """Remove stale ``qsnap-integration-*`` dirs from ``/var/tmp``.

    If a test process is killed (OOM, SIGKILL, timeout), the ``finally``
    block in :func:`test_vm` never runs and the temp directory is
    orphaned.  This fixture runs once at session start and removes
    directories older than :data:`_STALE_DIR_AGE_SEC` so they don't
    accumulate indefinitely.

    Directories younger than the threshold are left alone — they may
    belong to a concurrently running test session.
    """
    now = time.time()
    try:
        entries = os.listdir(_INTEGRATION_TMP)
    except OSError:
        return
    for name in entries:
        if not name.startswith("qsnap-integration-"):
            continue
        path = os.path.join(_INTEGRATION_TMP, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if now - mtime > _STALE_DIR_AGE_SEC:
            shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def test_vm(request):
    """Create and manage a disposable test VM for integration tests.

    Creates a minimal VM with a qcow2 disk.  The VM is defined in
    libvirt but NOT started — individual tests decide whether to start
    it.  Uses ``type='qemu'`` to avoid KVM hardware dependency.

    The VM has no bootable media, so starting it creates a running QEMU
    process (enough for NBD tests) that won't boot an OS.

    Disk size is configurable via indirect parametrisation::

        @pytest.mark.parametrize("test_vm", ["2G"], indirect=True)
        def test_something(test_vm):
            ...

    The default is ``"256M"`` (sufficient for functional tests).
    Performance tests should request ``"2G"`` or larger for reliable
    throughput measurement.

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
            Root of the temporary working directory (under ``/var/tmp``).
        disk_size : str
            The virtual disk size string (e.g. ``"256M"``, ``"2G"``).

    Teardown: destroys the VM, undefines it, cleans up the NBD socket,
    and removes all temporary files.
    """
    disk_size = getattr(request, "param", "256M")
    shell = SubprocessShell()
    tmpdir = Path(tempfile.mkdtemp(prefix="qsnap-integration-", dir=_INTEGRATION_TMP))
    vm_name = "qsnap-int-test-vm"
    base_image = tmpdir / f"{vm_name}.qcow2"
    snapshot_dir = tmpdir / "snapshots"
    target_dir = tmpdir / "backup"

    # Create directories
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Create a qcow2 disk image (size configurable via request.param).
    create_result = shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(base_image), disk_size],
        timeout=30,
    )
    if not create_result.success:
        shutil.rmtree(str(tmpdir), ignore_errors=True)
        pytest.skip(f"qemu-img create failed (qemu-img not available?): {create_result.error}")

    # Build minimal VM XML — no bootable media so QEMU starts but
    # doesn't boot an OS (sufficient for NBD export tests).
    # Auto-detect KVM acceleration: uses ``type="kvm"`` when
    # ``/dev/kvm`` is accessible (near-native performance for NBD
    # backup-begin exports), falls back to ``type="qemu"`` (TCG
    # full emulation, ~100× slower but works everywhere).
    domain_type = "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "qemu"
    xml = (
        f'<domain type="{domain_type}">\n'
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

    # Pre-cleanup: destroy and undefine any stale domain left behind by a
    # previous test run that crashed before its teardown (finally block) ran.
    # Without this, ``virsh define`` fails with "domain already exists" and
    # the fixture skips — the stale VM is never cleaned up, perpetuating the
    # problem across all subsequent runs.
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    _cleanup_checkpoints(shell, vm_name)
    shell.run(["virsh", "undefine", vm_name], timeout=30)

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
            "disk_size": disk_size,
        }
    finally:
        # Teardown: destroy, undefine, and clean up all files.
        # First, clean up any leftover checkpoints to allow undefine.
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        _cleanup_checkpoints(shell, vm_name)
        shell.run(["virsh", "undefine", vm_name], timeout=30)
        # NBD sockets are created in /tmp by the source code (bitmap.py).
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
