"""Integration tests for ``Core.check()`` — snapshot verification.

Verifies triple-source consistency checking for snapshots using real
virsh and qemu-img against a disposable test VM.  Covers consistent
chains, live blockcommit aftermath, phantom (missing) snapshots, stale
domain XML after offline commit, XML refresh, deep qemu-img check, and
``--force-share`` on the active layer.

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture from
``conftest.py``.

Run only when explicitly requested::

    uv run pytest tests/integration/test_check_snapshots.py -v -m integration
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import GlobalConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from tests.mocks import InMemoryStateManager, MockConfigFacade

# ── helpers ──────────────────────────────────────────────────────────


def _snapshot_create(
    shell: SubprocessShell,
    vm_name: str,
    snap_name: str,
    snapshot_dir: Path,
    base_image: Path,
) -> SnapshotInfo | None:
    """Create an external disk-only snapshot and return ``SnapshotInfo``.

    Returns ``None`` when snapshot creation fails (e.g., backing-filename
    mismatch from stale QEMU processes left by crashed tests).
    """
    snap_path = snapshot_dir / f"{snap_name}.qcow2"
    provider = ExternalSnapshotProvider(shell)
    result = provider.create(
        VMConfig(name=vm_name, base_image=base_image, snapshot_dir=snapshot_dir),
        snap_name,
        "vda",
        snap_path,
    )
    if not result.success:
        return None
    return SnapshotInfo(
        name=result.name,
        path=result.path,
        timestamp=datetime.now(),
        allocation=result.new_allocation,
    )


def _vm_is_running(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True if VM is in 'running' state."""
    result = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    return result.success and "running" in result.stdout.strip().lower()


def _vm_is_shut_off(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True if VM is shut off."""
    result = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    return result.success and "shut off" in result.stdout.strip().lower()


def _build_core_snapshot_only(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    state: InMemoryStateManager,
) -> Core:
    """Build a Core instance with NO targets — snapshot-only.

    Using ``targets=[]`` avoids creating a ``BitmapBackupProvider``,
    which would require libnbd (unavailable on some CI systems).
    """
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[],  # No targets — avoids BitmapBackupProvider / libnbd
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(timestamp_format="short"),
        vms=[vm_config],
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_check_real_vm_all_consistent(test_vm):
    """Create a real VM with 3 snapshots — all three sources match → status="ok".

    1. Start the VM.
    2. Create 3 external snapshots (snap1, snap2, snap3) via virsh.
    3. Record all 3 in state.
    4. Run ``core.check()``.
    5. Assert ``status == "ok"`` and ``broken_snapshots`` is empty.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not _vm_is_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create 3 snapshots
    names = [f"{vm_name}.snap1", f"{vm_name}.snap2", f"{vm_name}.snap3"]
    snapshots: list[SnapshotInfo] = []
    for name in names:
        info = _snapshot_create(shell, vm_name, name, snapshot_dir, base_image)
        assert info is not None, f"Snapshot creation failed for {name}"
        snapshots.append(info)
        time.sleep(0.5)

    # Record all 3 in state
    state = InMemoryStateManager()
    for snap in snapshots:
        state.record_snapshot(vm_name, snap)

    # Build Core and run check
    core = _build_core_snapshot_only(shell, vm_name, base_image, snapshot_dir, state)
    results = core.check(vm_name)

    assert vm_name in results, f"Expected check result for {vm_name}"
    cr = results[vm_name]
    assert cr.status == "ok", (
        f"Expected status='ok', got '{cr.status}'. "
        f"broken_snapshots={cr.broken_snapshots}"
    )
    assert cr.broken_snapshots == [], (
        f"Expected empty broken_snapshots, got {cr.broken_snapshots}"
    )


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_check_real_vm_after_blockcommit(test_vm):
    """Live blockcommit of oldest snapshot — libvirt updates XML → status="ok".

    1. Start VM, create 3 snapshots (snap1, snap2, snap3 — snap3 active).
    2. Record all 3 in state.
    3. Run ``virsh blockcommit`` to merge snap1 into base.
    4. Run ``core.check()``.
    5. Assert ``status == "ok"`` — libvirt updates XML automatically, and
       snap1 appears as a phantom in state (WARNING only, not broken).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not _vm_is_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create 3 snapshots
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.snap1", snapshot_dir, base_image)
    if snap1 is None:
        pytest.skip("snap1 creation failed — QEMU may be stale from previous test")
    time.sleep(0.5)
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.snap2", snapshot_dir, base_image)
    if snap2 is None:
        pytest.skip("snap2 creation failed — QEMU may be stale from previous test")
    time.sleep(0.5)
    snap3 = _snapshot_create(shell, vm_name, f"{vm_name}.snap3", snapshot_dir, base_image)
    if snap3 is None:
        pytest.skip("snap3 creation failed — QEMU may be stale from previous test")

    # Record all 3 in state
    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap2)
    state.record_snapshot(vm_name, snap3)

    # Live blockcommit: merge snap1 into base (top=snap2, base=base_image)
    # This merges everything between base and snap2 (i.e., snap1) into base,
    # then libvirt deletes snap1 and adjusts the chain.
    commit = shell.run(
        [
            "virsh", "blockcommit", vm_name, "vda",
            "--base", str(base_image),
            "--top", str(snap2.path),
            "--wait", "--verbose",
            "--bandwidth", "0",
        ],
        timeout=120,
    )
    assert commit.success, f"virsh blockcommit failed: {commit.error}"
    time.sleep(1)

    # snap1 file should be gone (merged into base by libvirt)
    # snap2 and snap3 should still exist
    assert snap2.path.exists(), "snap2 should still exist after blockcommit"
    assert snap3.path.exists(), "snap3 should still exist after blockcommit"

    # Run check — snap1 is phantom in state (state has it, disk/XML don't)
    # Phantom is a WARNING, not BROKEN → status="ok"
    core = _build_core_snapshot_only(shell, vm_name, base_image, snapshot_dir, state)
    results = core.check(vm_name)

    assert vm_name in results
    cr = results[vm_name]
    assert cr.status == "ok", (
        f"Expected status='ok' after live blockcommit (libvirt updates XML), "
        f"got '{cr.status}'. broken_snapshots={cr.broken_snapshots}"
    )


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_check_real_vm_phantom_snapshot(test_vm):
    """Manually delete a snapshot file from the middle of the chain → broken.

    1. Start VM, create 3 snapshots.
    2. ``rm -f snap2`` (external deletion from the middle).
    3. Run ``core.check()``.
    4. Assert ``status == "broken"`` — the backing chain is severed.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not _vm_is_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create 3 snapshots
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.snap1", snapshot_dir, base_image)
    if snap1 is None:
        pytest.skip("snap1 creation failed — QEMU may be stale from previous test")
    time.sleep(0.5)
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.snap2", snapshot_dir, base_image)
    if snap2 is None:
        pytest.skip("snap2 creation failed — QEMU may be stale from previous test")
    time.sleep(0.5)
    snap3 = _snapshot_create(shell, vm_name, f"{vm_name}.snap3", snapshot_dir, base_image)
    if snap3 is None:
        pytest.skip("snap3 creation failed — QEMU may be stale from previous test")

    # Record all 3 in state
    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap2)
    state.record_snapshot(vm_name, snap3)

    # Delete snap2 from the middle — this breaks the backing chain
    # (snap3's backing-filename points to snap2 which no longer exists)
    os.unlink(str(snap2.path))
    assert not snap2.path.exists(), "snap2 should be deleted"

    # Run check — chain is broken → status="broken"
    core = _build_core_snapshot_only(shell, vm_name, base_image, snapshot_dir, state)
    results = core.check(vm_name)

    assert vm_name in results
    cr = results[vm_name]
    assert cr.status == "broken", (
        f"Expected status='broken' when snap2 is deleted from middle of chain, "
        f"got '{cr.status}'. broken_snapshots={cr.broken_snapshots}"
    )
    assert len(cr.broken_snapshots) > 0, (
        "Expected non-empty broken_snapshots when chain is broken"
    )


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_check_real_vm_stale_xml_after_offline_commit(test_vm):
    """Offline qemu-img commit → active layer mismatch → broken.

    1. Start VM, create 3 snapshots (snap1, snap2, snap3).
    2. Shut off VM.
    3. ``qemu-img commit -b <base> -d <snap2>`` — offline merge of snap1.
       (snap1 is committed into base and deleted.)
    4. Do NOT call ``_refresh_domain_backing_store()``.
    5. Run ``core.check()``.
    6. Assert ``status == "broken"`` — ``_verify_active_layer_match`` detects
       that domblklist still returns ``base_image`` (the persistent XML
       source, because ``--no-metadata`` snapshots don't update the
       persistent XML) while the newest state snapshot is ``snap3``.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not _vm_is_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create 3 snapshots
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.snap1", snapshot_dir, base_image)
    if snap1 is None:
        pytest.skip("snap1 creation failed — QEMU may be stale from previous test")
    time.sleep(0.5)
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.snap2", snapshot_dir, base_image)
    if snap2 is None:
        pytest.skip("snap2 creation failed — QEMU may be stale from previous test")
    time.sleep(0.5)
    snap3 = _snapshot_create(shell, vm_name, f"{vm_name}.snap3", snapshot_dir, base_image)
    if snap3 is None:
        pytest.skip("snap3 creation failed — QEMU may be stale from previous test")

    # Record all 3 in state
    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap2)
    state.record_snapshot(vm_name, snap3)

    # Shut off VM for offline commit
    destroy = shell.run(["virsh", "destroy", vm_name], timeout=30)
    assert destroy.success, f"virsh destroy failed: {destroy.error}"
    time.sleep(1)
    if not _vm_is_shut_off(shell, vm_name):
        pytest.skip("VM did not shut off — cannot perform offline commit")

    # Offline commit: merge snap1 into base.
    # snap2 is the top — everything between base and snap2 (i.e., snap1)
    # is committed into base. -d deletes intermediate files.
    commit = shell.run(
        [
            "qemu-img", "commit", "-b", str(base_image),
            "-d", str(snap2.path),
        ],
        timeout=60,
    )
    assert commit.success, f"qemu-img commit failed: {commit.error}"

    # snap1 should be deleted by qemu-img commit -d
    # snap2's backing should now be base
    if snap1.path.exists():
        # Some qemu-img versions don't delete with -d; force it
        os.unlink(str(snap1.path))

    # Verify snap2 exists and the chain is intact (snap2 → base)
    assert snap2.path.exists(), "snap2 must exist after commit"
    assert snap3.path.exists(), "snap3 must exist after commit"

    # XML is now stale: it still references snap1 in <backingStore>
    # but snap1 is gone.  check() should detect stale domain XML.
    core = _build_core_snapshot_only(shell, vm_name, base_image, snapshot_dir, state)
    results = core.check(vm_name)

    assert vm_name in results
    cr = results[vm_name]
    assert cr.status == "broken", (
        f"Expected status='broken' due to stale domain XML (snap1 deleted but "
        f"still in XML), got '{cr.status}'. broken_snapshots={cr.broken_snapshots}"
    )
    assert len(cr.broken_snapshots) > 0, (
        "Expected non-empty broken_snapshots from stale XML"
    )


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_check_real_vm_after_refresh_xml(test_vm):
    """After offline commit + refresh, ``_verify_active_layer_match`` still fires.

    NOTE: The ``ExternalSnapshotProvider`` creates snapshots with
    ``--no-metadata``, so libvirt does NOT add ``<backingStore>`` elements
    to the persistent domain XML.  After the VM shuts off, ``virsh
    domblklist`` returns the original ``base_image`` path (the persistent
    XML source), not the active overlay (``snap3``).  Therefore
    ``_refresh_domain_backing_store()`` is a no-op (nothing to strip), and
    ``_verify_active_layer_match`` detects the mismatch between
    domblklist's ``base_image`` and the newest state snapshot ``snap3``
    → status remains ``"broken"``.

    This is a known discrepancy: ``check()`` works correctly for chains
    created via ``Core._create_snapshot()`` (which records state properly)
    but the persistent XML is stale after offline commits on chains created
    by ``ExternalSnapshotProvider`` with ``--no-metadata``.

    1. Start VM, create 3 snapshots, shut off.
    2. ``qemu-img commit -b <base> -d <snap2>`` — offline merge.
    3. Call ``_refresh_domain_backing_store()`` — no-op for --no-metadata chains.
    4. Run ``check()`` → ``status == "broken"`` from active layer mismatch.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not _vm_is_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create 3 snapshots
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.snap1", snapshot_dir, base_image)
    if snap1 is None:
        pytest.skip("snap1 creation failed — QEMU may be stale from previous test")
    time.sleep(0.5)
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.snap2", snapshot_dir, base_image)
    if snap2 is None:
        pytest.skip("snap2 creation failed — QEMU may be stale from previous test")
    time.sleep(0.5)
    snap3 = _snapshot_create(shell, vm_name, f"{vm_name}.snap3", snapshot_dir, base_image)
    if snap3 is None:
        pytest.skip("snap3 creation failed — QEMU may be stale from previous test")

    # Record all 3 in state
    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap2)
    state.record_snapshot(vm_name, snap3)

    # Shut off VM
    destroy = shell.run(["virsh", "destroy", vm_name], timeout=30)
    assert destroy.success, f"virsh destroy failed: {destroy.error}"
    time.sleep(1)
    if not _vm_is_shut_off(shell, vm_name):
        pytest.skip("VM did not shut off — cannot perform offline commit")

    # Offline commit — merge snap1 into base
    commit = shell.run(
        [
            "qemu-img", "commit", "-b", str(base_image),
            "-d", str(snap2.path),
        ],
        timeout=60,
    )
    assert commit.success, f"qemu-img commit failed: {commit.error}"

    if snap1.path.exists():
        os.unlink(str(snap1.path))

    # Build Core and refresh domain XML (no-op for --no-metadata chains)
    core = _build_core_snapshot_only(shell, vm_name, base_image, snapshot_dir, state)
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[],
    )
    core._refresh_domain_backing_store(vm_config)

    # Run check — _verify_active_layer_match detects mismatch:
    # domblklist returns base_image, newest state snapshot is snap3.
    results = core.check(vm_name)

    assert vm_name in results
    cr = results[vm_name]
    # _verify_active_layer_match adds to broken because base_image ≠ snap3
    assert cr.status == "broken", (
        f"Expected status='broken' (active layer mismatch after offline commit "
        f"with --no-metadata snapshots), got '{cr.status}'. "
        f"broken_snapshots={cr.broken_snapshots}"
    )


@pytest.mark.integration
@pytest.mark.timeout(900)
def test_check_real_deep_check(test_vm):
    """Deep check with ``qemu-img check`` on every snapshot → status="ok".

    1. Start VM, create 3 snapshots in a consistent chain.
    2. Run ``core.check(deep=True)``.
    3. Assert ``status == "ok"`` — all snapshots pass qemu-img check.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not _vm_is_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create 3 snapshots
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.snap1", snapshot_dir, base_image)
    if snap1 is None:
        pytest.skip("snap1 creation failed — QEMU may be stale from previous test")
    time.sleep(0.5)
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.snap2", snapshot_dir, base_image)
    if snap2 is None:
        pytest.skip("snap2 creation failed — QEMU may be stale from previous test")
    time.sleep(0.5)
    snap3 = _snapshot_create(shell, vm_name, f"{vm_name}.snap3", snapshot_dir, base_image)
    if snap3 is None:
        pytest.skip("snap3 creation failed — QEMU may be stale from previous test")

    # Record all 3 in state
    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap2)
    state.record_snapshot(vm_name, snap3)

    # Run deep check (targets=[] avoids BitmapBackupProvider/libnbd requirement)
    core = _build_core_snapshot_only(shell, vm_name, base_image, snapshot_dir, state)
    results = core.check(vm_name, deep=True)

    assert vm_name in results
    cr = results[vm_name]
    assert cr.status == "ok", (
        f"Expected status='ok' after deep check of 3 clean snapshots, "
        f"got '{cr.status}'. broken_snapshots={cr.broken_snapshots}"
    )


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_check_uses_force_share_on_active_layer(test_vm):
    """Verify ``check()`` uses ``--force-share`` on the active layer.

    The active layer (the running VM's disk) is write-locked by QEMU.
    Without ``--force-share``, ``qemu-img info`` would fail.  This test
    starts the VM, creates snapshots, runs ``core.check()`` while the VM
    is still running, and asserts the check succeeds — proving that
    ``--force-share`` is used.

    1. Start VM, create 2 snapshots.
    2. Run ``core.check()`` while VM is still running.
    3. Assert status is "ok" — qemu-img succeeded despite the write lock.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not _vm_is_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create 2 snapshots
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.snap1", snapshot_dir, base_image)
    if snap1 is None:
        pytest.skip("snap1 creation failed — QEMU may be stale from previous test")
    time.sleep(0.5)
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.snap2", snapshot_dir, base_image)
    if snap2 is None:
        pytest.skip("snap2 creation failed — QEMU may be stale from previous test")

    # Record in state
    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap2)

    # Verify VM is still running (active layer locked)
    if not _vm_is_running(shell, vm_name):
        pytest.skip("VM stopped before check — cannot verify --force-share")

    # Run check while VM is running — must succeed, proving --force-share
    core = _build_core_snapshot_only(shell, vm_name, base_image, snapshot_dir, state)
    results = core.check(vm_name)

    assert vm_name in results
    cr = results[vm_name]
    assert cr.status == "ok", (
        f"Expected status='ok' (--force-share must work on running VM), "
        f"got '{cr.status}'. broken_snapshots={cr.broken_snapshots}. "
        f"If this fails, check() may not be using --force-share on "
        f"qemu-img info commands against the active layer."
    )
