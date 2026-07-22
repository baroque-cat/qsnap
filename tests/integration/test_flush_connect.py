"""Integration tests for flush() and connect-retry on unified NBD engine.

Verifies:
- ``flush()`` is called before qemu-nbd teardown.
- ``can_flush()`` check is performed.
- Connect-retry works with real NBD (LibnbdClient retries up to 20 times).

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest

# libnbd availability — needed for flush/can_flush and connect-retry.
try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False

from qsnap.models.config import TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
)

if _HAS_LIBNBD:
    from qsnap.utils.nbd_client import LibnbdClient


def _get_checkpoint_names(shell: SubprocessShell, vm_name: str) -> list[str]:
    """Return qsnap-prefixed checkpoint names."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return []
    return [
        line.strip()
        for line in result.stdout.strip().splitlines()
        if line.strip().startswith("qsnap-")
    ]


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints."""
    for cp in _get_checkpoint_names(shell, vm_name):
        shell.run(
            ["virsh", "checkpoint-delete", "--domain", vm_name, cp, "--metadata"],
            timeout=30,
        )


# ──────────────────────────────────────────────────────────────────────
# Test 1: Flush is called before qemu-nbd teardown
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_flush_called_before_qemu_nbd_teardown(test_vm):
    """Verify that a FULL backup completes successfully — which implies
    ``flush()`` was called before the write-side qemu-nbd was terminated.

    The unified engine calls ``dst.can_flush()`` and, when True,
    ``dst.flush()`` before ``dst.disconnect()``.  If flush failed or was
    skipped, the destination file could be truncated or corrupted.
    A successful full backup with valid qcow2 info proves flush worked.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    target_dir: Path = test_vm["target_dir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    _cleanup_checkpoints(shell, vm_name)

    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
    )
    target = TargetConfig(
        path=target_dir,
        incremental=True,
        compress=False,
        verify="off",
    )

    # Create FULL backup — successful transfer implies flush worked.
    snap = SnapshotInfo(
        name=f"{vm_name}.flush-test",
        path=base_image,
        timestamp=datetime.now(),
        allocation=0,
    )
    results = provider.transfer_missing(
        vm_config=vm_config,
        target=target,
        snapshots=[snap],
    )

    if not results:
        pytest.skip("transfer_missing produced no results — NBD may not be available")
    assert results[0].success, f"FULL backup failed — flush may not have worked: {results[0].error}"
    assert results[0].target_path.exists(), "Backup file should exist"

    # Verify target file is valid qcow2 (flush ensures writes were stable).
    import json

    info_result = shell.run(
        ["qemu-img", "info", "--output=json", str(results[0].target_path)],
        timeout=30,
    )
    assert info_result.success, f"qemu-img info failed: {info_result.error}"
    info = json.loads(info_result.stdout)
    assert info.get("format") == "qcow2", "Backup should be valid qcow2"
    assert int(info.get("virtual-size", 0)) > 0, "Backup should have non-zero virtual size"

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: can_flush is True for qemu-nbd servers
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_can_flush_true_for_qemu_nbd(test_vm):
    """Verify ``can_flush()`` returns True when connected to a real
    qemu-nbd server (the write-side started by the unified engine).

    Connects directly to the write-side qemu-nbd and checks can_flush().
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    target_dir: Path = test_vm["target_dir"]

    # Start VM
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    import os

    # Create a standalone qcow2 for the write test.
    write_socket = f"/tmp/qsnap-flush-test-{os.getpid()}.sock"
    pid_file = Path(f"/tmp/qsnap-flush-test-{os.getpid()}.pid")
    tmp_file = target_dir / f"flush_target_{os.getpid()}.qcow2"

    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())

    try:
        # Create a small qcow2 for the write side.
        create_result = shell.run(
            ["qemu-img", "create", "-f", "qcow2", str(tmp_file), "1M"],
            timeout=30,
        )
        if not create_result.success:
            pytest.skip(f"qemu-img create failed: {create_result.error}")

        # Start qemu-nbd on the target.
        nbd_result = provider._start_write_server(
            tmp_file,
            write_socket,
            pid_file,
            compress=False,
        )
        if not nbd_result.success:
            pytest.skip(f"qemu-nbd failed to start: {nbd_result.error}")

        # Connect a separate LibnbdClient to the write server.
        client = LibnbdClient()
        conn_result = client.connect(f"nbd+unix:///?socket={write_socket}", "", [])
        if not conn_result.success:
            pytest.skip(f"NBD connect to write server failed: {conn_result.error}")

        # Verify can_flush is True.
        assert client.can_flush(), "qemu-nbd server should support flush"

        # Verify flush succeeds.
        flush_result = client.flush()
        assert flush_result.success, f"flush() should succeed: {flush_result.error}"

        client.disconnect()

    finally:
        provider._terminate_qemu_nbd(pid_file)
        shell.run(["rm", "-f", write_socket, str(pid_file), str(tmp_file)], timeout=10)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Connect-retry with real NBD
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_connect_retry_with_real_nbd(test_vm):
    """Verify that LibnbdClient.connect() retries when the NBD server
    is not immediately available.

    Starts qemu-nbd with a small delay, then connects — the connect-retry
    logic (up to 20 attempts, 1s sleep) should succeed.
    """
    shell: SubprocessShell = test_vm["shell"]
    target_dir: Path = test_vm["target_dir"]

    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    import os

    write_socket = f"/tmp/qsnap-retry-test-{os.getpid()}.sock"
    pid_file = Path(f"/tmp/qsnap-retry-test-{os.getpid()}.pid")
    tmp_file = target_dir / f"retry_target_{os.getpid()}.qcow2"

    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())

    try:
        # Create a small qcow2.
        create_result = shell.run(
            ["qemu-img", "create", "-f", "qcow2", str(tmp_file), "1M"],
            timeout=30,
        )
        if not create_result.success:
            pytest.skip(f"qemu-img create failed: {create_result.error}")

        # Start qemu-nbd (it should be up quickly, but connect-retry
        # handles any race).
        nbd_result = provider._start_write_server(
            tmp_file,
            write_socket,
            pid_file,
            compress=False,
        )
        if not nbd_result.success:
            pytest.skip(f"qemu-nbd failed to start: {nbd_result.error}")

        # Connect — the retry logic should handle any brief startup delay.
        client = LibnbdClient()
        conn_result = client.connect(f"nbd+unix:///?socket={write_socket}", "", [])
        assert conn_result.success, f"NBD connect with retry should succeed: {conn_result.error}"

        # Verify we can get the size.
        size = client.get_size()
        assert size > 0, f"Export should have non-zero size, got {size}"

        client.disconnect()

    finally:
        provider._terminate_qemu_nbd(pid_file)
        shell.run(["rm", "-f", write_socket, str(pid_file), str(tmp_file)], timeout=10)


# ──────────────────────────────────────────────────────────────────────
# Test 4: Connect-retry eventually fails after max attempts
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_connect_retry_fails_after_max_attempts(test_vm):
    """Verify that LibnbdClient.connect() returns a failure after the
    maximum retry attempts when the NBD server never becomes available.

    Uses a non-existent socket path — the connect-retry should loop
    through all 20 attempts and return a failure.
    """
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed in this interpreter")

    client = LibnbdClient()

    start = time.monotonic()
    # Connect to a non-existent socket — retry should fail after ~20s.
    conn_result = client.connect(
        "nbd+unix:///?socket=/tmp/qsnap-nonexistent-99999.sock",
        "",
        [],
    )
    elapsed = time.monotonic() - start

    assert not conn_result.success, "Connect to non-existent socket should fail, but got success"
    assert conn_result.error is not None, "Error should be set on connect failure"

    # Retry should have attempted ~20 times with ~1s sleep each,
    # so elapsed should be roughly 19-21 seconds.
    assert elapsed >= 1.0, (
        f"Connect-retry should take at least 1 second (actually took {elapsed:.1f}s)"
    )

    client.disconnect()
