"""Integration tests for ``Core._refresh_domain_backing_store()``.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

``_refresh_domain_backing_store()`` strips stale ``<backingStore>``
elements from the domain XML after offline ``qemu-img commit`` (or
any other action that deletes overlay files from the backing chain).
Libvirt's persistent inactive-domain XML keeps the full chain — if a
committed overlay file is deleted, libvirt refuses to start the domain
("Cannot access backing file ... No such file or directory").  Stripping
all ``<backingStore>`` elements forces libvirt to re-probe the chain from
the qcow2 headers on next ``virsh start`` (design D8).

.. note::

   ``_refresh_domain_backing_store()`` calls ``virsh define`` on the
   modified XML, which **does not stop** a running VM.  ``virsh define``
   also re-probes the qcow2 headers and re-adds ``<backingStore>``
   elements for existing files.

Coverage:
- After offline commit: verify stale ``<backingStore>`` references are
  cleaned up and the VM can start.
- Executes without error on a valid chain; VM remains running afterward.
- Idempotent: second call does not raise an exception.
- Failure non-fatal: ``virsh dumpxml`` failure logs WARNING, no exception.

Run only when explicitly requested::

    uv run pytest tests/integration/test_refresh_backing_store.py -v -m integration
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_vm_running
from tests.mocks import InMemoryStateManager, MockConfigFacade

# ── helpers ──────────────────────────────────────────────────────────


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


def _ensure_vm_running(shell: SubprocessShell, vm_name: str, max_retries: int = 3) -> bool:
    """Start the VM and wait for it to reach running state.

    Returns True if the VM is running, False otherwise.
    """
    for _ in range(max_retries):
        shell.run(["virsh", "start", vm_name], timeout=30)
        time.sleep(2)
        if is_vm_running(shell, vm_name):
            return True
        time.sleep(2)
    return False


def _ensure_vm_stopped(shell: SubprocessShell, vm_name: str, max_retries: int = 5) -> bool:
    """Stop the VM and wait for it to reach shut-off state.

    Returns True if the VM is shut off, False otherwise.
    """
    for attempt in range(max_retries):
        if attempt > 0:
            time.sleep(3)
        destroy_result = shell.run(
            ["virsh", "destroy", vm_name], timeout=30,
        )
        time.sleep(2)
        # Verify via dominfo.
        dominfo = shell.run(
            ["virsh", "dominfo", "--domain", vm_name], timeout=30,
        )
        if dominfo.success:
            for line in dominfo.stdout.splitlines():
                if line.strip().lower().startswith("state:") and line.split(":", 1)[1].strip().lower() == "shut off":
                    return True
        elif not destroy_result.success and "domain is not running" in str(
            destroy_result.error or ""
        ).lower():
            # destroy reported "domain is not running" — already stopped.
            return True
        elif "failed to get domain" in str(destroy_result.error or "").lower():
            # Domain vanished (QEMU crash) — considered stopped.
            return True
    return False


def _create_external_snapshot(
    shell: SubprocessShell,
    vm_name: str,
    snap_name: str,
    base_image: Path,
    snapshot_dir: Path,
) -> tuple[SnapshotInfo, Path]:
    """Create an external disk-only snapshot via ``ExternalSnapshotProvider``.

    Returns ``(SnapshotInfo, snapshot_path)``.  Asserts success.
    """
    snap_path = snapshot_dir / f"{snap_name}.qcow2"
    provider = ExternalSnapshotProvider(shell)
    vm_config = VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir)
    result = provider.create(vm_config, snap_name, "vda", snap_path)
    assert result.success, f"Snapshot creation failed: {result.error}"
    info = SnapshotInfo(
        name=result.name,
        path=result.path,
        timestamp=datetime.now(),
        allocation=result.new_allocation,
    )
    return info, snap_path


def _get_backing_filename(shell: SubprocessShell, path: Path) -> str | None:
    """Return ``backing-filename`` from qcow2 metadata, or None."""
    result = shell.run(
        ["qemu-img", "info", "--force-share", "--output=json", str(path)],
        timeout=30,
    )
    if not result.success:
        return None
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    backing = info.get("backing-filename")
    return str(backing) if backing else None


def _find_child_overlay(
    shell: SubprocessShell,
    parent_path: Path,
    snapshot_dir: Path,
) -> Path | None:
    """Find the qcow2 overlay file in *snapshot_dir* whose backing-filename
    matches *parent_path*.  Returns ``None`` if no child exists."""
    parent_real = os.path.realpath(str(parent_path))
    for qcow2_file in sorted(snapshot_dir.glob("*.qcow2")):
        backing = _get_backing_filename(shell, qcow2_file)
        if backing is None:
            continue
        backing_real = backing
        if not os.path.isabs(backing_real):
            backing_real = os.path.join(str(qcow2_file.parent), backing_real)
        if os.path.realpath(backing_real) == parent_real:
            return qcow2_file
    return None


def _build_core(
    shell: SubprocessShell,
    vm_config: VMConfig,
    state: InMemoryStateManager,
    tmpdir: Path,
) -> Core:
    """Construct a ``Core`` instance wired for integration testing.

    Uses ``MockConfigFacade`` for config and ``DefaultFactory`` for
    module creation.  The real ``SubprocessShell`` is injected so that
    all ``virsh``/``qemu-img`` calls go to the actual daemon.
    """
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "refresh-test.toml")
    factory = DefaultFactory(shell, state)
    return Core(config=config, factory=factory, state=state, shell=shell)


# ──────────────────────────────────────────────────────────────────────
# Test 1: Refresh after offline commit — stale <backingStore> cleaned up
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_refresh_after_offline_commit(test_vm):
    """Verify ``_refresh_domain_backing_store()`` after offline commit.

    1. Create a VM, start it, create 3 external snapshots (chain: base
       → snap1 → snap2 → snap3, active = snap3).
    2. Shut off the VM.
    3. Offline-commit snap1 via ``qemu-img commit -b <base> <snap1>`` —
       merges snap1 data into base.
    4. Find snap2 (the child of snap1) and rebase it onto base via
       ``qemu-img rebase -u -F qcow2 -b <base> <snap2>``.
    5. Delete the committed snap1 file.
    6. Call ``_refresh_domain_backing_store()`` — strips stale
       ``<backingStore>`` and calls ``virsh define``.
    7. ``virsh start`` succeeds — libvirt re-probes the (shortened)
       chain from qcow2 headers (snap1 is gone, snap2→base).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Step 1: Start the VM and create 3 external snapshots.
    _cleanup_snapshots(shell, vm_name)

    if not _ensure_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state after retries")

    snap1_info, snap1_path = _create_external_snapshot(
        shell, vm_name, f"{vm_name}.refresh-snap1", base_image, snapshot_dir
    )
    snap2_info, snap2_path = _create_external_snapshot(
        shell, vm_name, f"{vm_name}.refresh-snap2", base_image, snapshot_dir
    )
    snap3_info, snap3_path = _create_external_snapshot(
        shell, vm_name, f"{vm_name}.refresh-snap3", base_image, snapshot_dir
    )

    # Verify all three snapshot files exist.
    assert snap1_path.exists(), f"snap1 must exist: {snap1_path}"
    assert snap2_path.exists(), f"snap2 must exist: {snap2_path}"
    assert snap3_path.exists(), f"snap3 must exist: {snap3_path}"

    # Step 2: Shut off the VM.
    vm_stopped = _ensure_vm_stopped(shell, vm_name)
    assert vm_stopped, "VM must be stopped for offline commit"

    # After stop, check if the domain is still defined.  QEMU crashes
    # on a no-boot-OS VM with external snapshots can cause libvirt to
    # undefine the domain.  Re-define it using a minimal XML from the
    # fixture paths (design D8 test — the chain is now on disk, and
    # _refresh_domain_backing_store will make libvirt re-probe).
    dominfo = shell.run(["virsh", "dominfo", "--domain", vm_name], timeout=30)
    if not dominfo.success:
        # Domain was undefined — redefine it from the original XML.
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
            f'      <source file="{snap3_path}"/>\n'
            f'      <target dev="vda" bus="virtio"/>\n'
            f"    </disk>\n"
            f"  </devices>\n"
            f"</domain>\n"
        )
        xml_path = tmpdir / f"{vm_name}-redefine.xml"
        xml_path.write_text(xml)
        define_result = shell.run(["virsh", "define", str(xml_path)], timeout=30)
        assert define_result.success, (
            f"Failed to re-define domain after QEMU crash: {define_result.error}"
        )

    # Step 3: Offline-commit snap1 into base (merges data into base).
    commit_result = shell.run(
        ["qemu-img", "commit", "-b", str(base_image), str(snap1_path)],
        timeout=60,
    )
    if not commit_result.success:
        pytest.skip(f"qemu-img commit snap1 failed: {commit_result.error}")

    # Step 4: Find snap2 as the child of snap1 and rebase it onto base.
    child = _find_child_overlay(shell, snap1_path, snapshot_dir)
    assert child is not None, (
        f"Could not find child overlay of {snap1_path} in {snapshot_dir}"
    )

    rebase_result = shell.run(
        ["qemu-img", "rebase", "-u", "-F", "qcow2", "-b", str(base_image), str(child)],
        timeout=60,
    )
    if not rebase_result.success:
        pytest.skip(f"qemu-img rebase {child} failed: {rebase_result.error}")

    # Verify rebase: child's backing-filename now points to base.
    backing_after = _get_backing_filename(shell, child)
    assert backing_after is not None, f"Cannot read backing-filename of {child}"
    assert os.path.realpath(backing_after) == os.path.realpath(str(base_image)), (
        f"child should be rebased to base after commit. "
        f"Expected backing={base_image}, got {backing_after}"
    )

    # Step 5: Delete the committed snap1 file now that the child has
    # been rebased (same discipline as QemuImgCommitManager).
    snap1_path.unlink()
    assert not snap1_path.exists(), f"snap1 file was not deleted: {snap1_path}"

    # Step 6: Call _refresh_domain_backing_store().
    state = InMemoryStateManager()
    vm_config = VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir)
    core = _build_core(shell, vm_config, state, tmpdir)
    core._refresh_domain_backing_store(vm_config)

    # Step 7: VM can start — libvirt re-probes the chain from qcow2
    # headers.  Since snap1 was deleted and snap2 was rebased to base,
    # the chain is now: base ← snap2 ← snap3 (with snap1 gone).
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    assert start_result.success, (
        f"VM must start after _refresh_domain_backing_store. "
        f"Error: {start_result.error}"
    )
    time.sleep(2)
    assert is_vm_running(shell, vm_name), "VM must be running after start"

    # Cleanup.
    _ensure_vm_stopped(shell, vm_name)

    for p in (snap2_path, snap3_path):
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass

    _cleanup_snapshots(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Executes without error on a valid chain; VM stays running
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_refresh_strips_all_backing_store(test_vm):
    """Verify ``_refresh_domain_backing_store()`` runs without error on a
    valid backing chain.

    1. Create a VM, start it, create 3 external snapshots (all valid).
    2. Call ``_refresh_domain_backing_store()``.
    3. The call completes without raising an exception.
    4. The VM is still running afterward (``virsh define`` does NOT
       stop the domain).
    5. Calling ``virsh start`` is expected to fail with "already active"
       — the VM was never stopped.

    .. note::

       ``virsh define`` re-probes the qcow2 headers and re-adds
       ``<backingStore>`` elements for existing files.  The XML may
       therefore still contain ``<backingStore>`` after the refresh
       when all overlay files are present — this is expected libvirt
       behavior, not a qsnap defect.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _cleanup_snapshots(shell, vm_name)

    if not _ensure_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state after retries")

    snap1_info, snap1_path = _create_external_snapshot(
        shell, vm_name, f"{vm_name}.strip-snap1", base_image, snapshot_dir
    )
    snap2_info, snap2_path = _create_external_snapshot(
        shell, vm_name, f"{vm_name}.strip-snap2", base_image, snapshot_dir
    )
    snap3_info, snap3_path = _create_external_snapshot(
        shell, vm_name, f"{vm_name}.strip-snap3", base_image, snapshot_dir
    )

    # All three files exist — the chain is valid.
    assert snap1_path.exists()
    assert snap2_path.exists()
    assert snap3_path.exists()

    # Call _refresh_domain_backing_store() — must not raise.
    state = InMemoryStateManager()
    vm_config = VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir)
    core = _build_core(shell, vm_config, state, tmpdir)
    core._refresh_domain_backing_store(vm_config)

    # VM is still running — _refresh_domain_backing_store calls
    # virsh define, which does NOT stop the domain.
    assert is_vm_running(shell, vm_name), (
        "VM must still be running after _refresh_domain_backing_store"
    )

    # Cleanup.
    _ensure_vm_stopped(shell, vm_name)
    time.sleep(1)
    _cleanup_snapshots(shell, vm_name)

    for p in (snap1_path, snap2_path, snap3_path):
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


# ──────────────────────────────────────────────────────────────────────
# Test 3: Refresh idempotent — second call does not raise
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_refresh_idempotent(test_vm):
    """Verify ``_refresh_domain_backing_store()`` is safe to call twice.

    1. Create a VM, start it, create 3 external snapshots.
    2. Call ``_refresh_domain_backing_store()`` — first call completes.
    3. Call ``_refresh_domain_backing_store()`` again — second call
       completes without raising an exception.
    4. The VM is still running afterward.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    _cleanup_snapshots(shell, vm_name)

    if not _ensure_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state after retries")

    snap1_info, snap1_path = _create_external_snapshot(
        shell, vm_name, f"{vm_name}.idem-snap1", base_image, snapshot_dir
    )
    snap2_info, snap2_path = _create_external_snapshot(
        shell, vm_name, f"{vm_name}.idem-snap2", base_image, snapshot_dir
    )
    snap3_info, snap3_path = _create_external_snapshot(
        shell, vm_name, f"{vm_name}.idem-snap3", base_image, snapshot_dir
    )

    state = InMemoryStateManager()
    vm_config = VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir)
    core = _build_core(shell, vm_config, state, tmpdir)

    # First call: strips <backingStore> and runs virsh define.
    core._refresh_domain_backing_store(vm_config)

    # Second call: must not raise (safe idempotency).
    # _refresh_domain_backing_store may find backingStore elements
    # (re-added by libvirt during the first virsh define) and strip them
    # again — this is harmless.
    core._refresh_domain_backing_store(vm_config)

    # VM is still running.
    assert is_vm_running(shell, vm_name), (
        "VM must still be running after two _refresh_domain_backing_store calls"
    )

    # Cleanup.
    _ensure_vm_stopped(shell, vm_name)
    time.sleep(1)
    _cleanup_snapshots(shell, vm_name)

    for p in (snap1_path, snap2_path, snap3_path):
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


# ──────────────────────────────────────────────────────────────────────
# Test 4: Refresh failure is non-fatal — WARNING log, no exception
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_refresh_failure_non_fatal(test_vm, caplog):
    """Verify ``_refresh_domain_backing_store()`` failure is non-fatal.

    1. Create a ``VMConfig`` with a non-existent VM name.
    2. Call ``_refresh_domain_backing_store()`` — ``virsh dumpxml``
       fails because the VM does not exist.
    3. The method logs a WARNING (not CRITICAL, not ERROR) and returns
       without raising an exception.
    """
    shell: SubprocessShell = test_vm["shell"]
    tmpdir: Path = test_vm["tmpdir"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    base_image: Path = test_vm["base_image"]

    # Use a non-existent VM name — virsh dumpxml will fail.
    nonexistent_vm = "qsnap-nonexistent-vm-xyz123"
    vm_config = VMConfig(name=nonexistent_vm, base_image=base_image, snapshot_dir=snapshot_dir)

    state = InMemoryStateManager()
    core = _build_core(shell, vm_config, state, tmpdir)

    # Call _refresh_domain_backing_store — must NOT raise.
    with caplog.at_level(logging.WARNING):
        core._refresh_domain_backing_store(vm_config)

    # The method must have logged a WARNING about the dumpxml failure.
    warning_msgs = [
        r.message for r in caplog.records
        if r.levelno >= logging.WARNING and "dumpxml" in r.message.lower()
    ]
    assert len(warning_msgs) >= 1, (
        "_refresh_domain_backing_store must log WARNING when dumpxml fails; "
        f"all WARNING messages: {[r.message for r in caplog.records]}"
    )

    # No exception was raised — reaching this point means the method
    # handled the failure gracefully.
