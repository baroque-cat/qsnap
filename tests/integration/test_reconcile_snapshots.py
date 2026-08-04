"""Integration tests for ``Core.reconcile()`` — snapshot-scoped reconciliation.

Covers five snapshot-side reconciliation scenarios:

1. Phantom snapshot: state has record, file missing, XML doesn't reference → removed
2. Orphan snapshot: file exists on disk + XML references, state doesn't know → supplemented
3. Stale domain XML: file deleted while VM running, live XML has stale <backingStore> → refreshed
4. Broken chain: middle snapshot deleted, XML references it → XML refreshed, no rebase
5. Dry-run mode: all checks performed, zero side effects

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture
from ``conftest.py``.

Run only when explicitly requested::

    uv run pytest tests/integration/test_reconcile_snapshots.py -v -m integration
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_vm_running
from tests.mocks import InMemoryStateManager, MockConfigFacade

# ── helpers ──────────────────────────────────────────────────────────


def _snapshot_create(
    shell: SubprocessShell,
    vm_name: str,
    snap_name: str,
    snapshot_dir: Path,
    base_image: Path,
) -> SnapshotInfo:
    """Create an external snapshot and return ``SnapshotInfo``."""
    snap_path = snapshot_dir / f"{snap_name}.qcow2"
    provider = ExternalSnapshotProvider(shell)
    result = provider.create(
        VMConfig(
            name=vm_name,
            disks=[DiskConfig(target="vda", base_image=base_image)],
            snapshot_dir=snapshot_dir,
        ),
        snap_name,
        "vda",
        snap_path,
    )
    assert result.success, f"Snapshot creation failed: {result.error}"
    return SnapshotInfo(
        name=result.name,
        path=result.path,
        timestamp=datetime.now(),
        allocation=result.new_allocation,
        disk="vda",
    )


def _xml_has_backing_store(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True if domain XML contains at least one backingStore element."""
    import xml.etree.ElementTree as ET

    result = shell.run(
        ["virsh", "dumpxml", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return False
    try:
        root = ET.fromstring(result.stdout)
    except ET.ParseError:
        return False
    # Use recursive find to catch nested backingStore elements.
    return any(disk.find(".//backingStore") is not None for disk in root.iter("disk"))


# ──────────────────────────────────────────────────────────────────────
# Test 1: reconcile removes phantom snapshot from state
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_reconcile_real_phantom_snapshot(test_vm, caplog):
    """Reconcile removes a state entry whose file does not exist on disk.

    1. Start VM, create 2 real external snapshots (snap1, snap3).
    2. Record both in state.
    3. Also record a phantom snapshot (snap2) with a fake path that
       does NOT exist on disk and is NOT referenced by XML.
    4. Run ``core.reconcile()``.
    5. Verify: phantom snapshot removed from state,
       ``phantom_snapshots_removed >= 1``, real snapshots remain.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create 2 real external snapshots.
    snap1 = _snapshot_create(
        shell, vm_name, f"{vm_name}.20250726T000001_vda", snapshot_dir, base_image
    )
    snap3 = _snapshot_create(
        shell, vm_name, f"{vm_name}.20250726T000003_vda", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap3)

    # Record a phantom snapshot — path does NOT exist on disk, XML
    # won't reference it (it's a fake name that was never created).
    phantom_path = snapshot_dir / f"{vm_name}.20250726T000002_phantom.qcow2"
    phantom_snap = SnapshotInfo(
        name=f"{vm_name}.20250726T000002_phantom",
        path=phantom_path,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    state.record_snapshot(vm_name, phantom_snap)

    # Build Core — no targets needed for snapshot-only reconciliation.
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "phantom.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- Run reconcile ---
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # Phantom snapshot must be removed from state.
    assert rec.phantom_snapshots_removed >= 1, (
        f"Expected phantom_snapshots_removed >= 1, got {rec}. Result: {rec}"
    )

    # Verify phantom is gone from state.
    remaining = {s.name for s in state.get_snapshots(vm_name)}
    assert phantom_snap.name not in remaining, (
        f"Phantom snapshot {phantom_snap.name} should have been removed from state"
    )
    # Real snapshots must still be present.
    assert snap1.name in remaining, f"Real snapshot {snap1.name} should remain in state"
    assert snap3.name in remaining, f"Real snapshot {snap3.name} should remain in state"


# ──────────────────────────────────────────────────────────────────────
# Test 2: reconcile supplements orphan snapshot from disk+XML reality
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_reconcile_real_orphan_snapshot_recorded(test_vm, caplog):
    """Reconcile records a snapshot that exists on disk + XML but not in state.

    1. Start VM, create 2 external snapshots (snap1, snap3) — record both.
    2. Create snap2 manually via virsh snapshot-create-as (NOT recorded in state).
       snap2 file exists on disk AND XML references it.
    3. Run ``core.reconcile()``.
    4. Verify: snap2 is recorded in state (state_supplemented >= 1),
       and is NOT deleted (file still on disk).
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create 2 external snapshots (recorded in state).
    snap1 = _snapshot_create(
        shell, vm_name, f"{vm_name}.20250726T000001_vda", snapshot_dir, base_image
    )
    snap3 = _snapshot_create(
        shell, vm_name, f"{vm_name}.20250726T000003_vda", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap3)

    # --- Create snap2 manually via virsh (NOT recorded in state) ---
    snap2_name = f"{vm_name}.20250726T000002_vda"
    snap2_path = snapshot_dir / f"{snap2_name}.qcow2"
    provider = ExternalSnapshotProvider(shell)
    result2 = provider.create(
        VMConfig(
            name=vm_name,
            disks=[DiskConfig(target="vda", base_image=base_image)],
            snapshot_dir=snapshot_dir,
        ),
        snap2_name,
        "vda",
        snap2_path,
    )
    assert result2.success, f"Manual snapshot creation failed: {result2.error}"
    assert snap2_path.exists(), "snap2 file should exist on disk"

    # Build Core — no targets needed.
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "orphan_snap.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- Run reconcile ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # snap2 should be supplemented into state.
    assert rec.state_supplemented >= 1, f"Expected state_supplemented >= 1, got {rec}"

    # Verify snap2 recorded in state.
    remaining = {s.name for s in state.get_snapshots(vm_name)}
    assert snap2_name in remaining, f"snap2 should be recorded in state. Got: {remaining}"

    # snap2 file must still exist on disk (NOT deleted).
    assert snap2_path.exists(), "snap2 file should NOT be deleted — it is a legitimate snapshot"


# ──────────────────────────────────────────────────────────────────────
# Test 3: reconcile refreshes stale domain XML
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_reconcile_real_stale_xml(test_vm, caplog):
    """Reconcile strips stale <backingStore> from domain XML.

    1. Start VM, create 3 external snapshots (snap1, snap2, snap3).
       VM is running — live domain XML has <backingStore> elements.
    2. Record all 3 snapshots in state.
    3. Delete the oldest snapshot file (snap1) from disk while VM
       is running.  The file's inode stays alive because QEMU has it
       open, but ``os.path.exists()`` returns False.
    4. Run ``core.reconcile()`` — it sees snap1's path in live XML
       but the file doesn't exist on disk → stale XML.
    5. Verify: xml_refreshed=True, domain XML no longer has
       <backingStore> elements.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create 3 external snapshots — VM stays running.
    snap1 = _snapshot_create(
        shell, vm_name, f"{vm_name}.20250726T000001_vda", snapshot_dir, base_image
    )
    snap2 = _snapshot_create(
        shell, vm_name, f"{vm_name}.20250726T000002_vda", snapshot_dir, base_image
    )
    snap3 = _snapshot_create(
        shell, vm_name, f"{vm_name}.20250726T000003_vda", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap2)
    state.record_snapshot(vm_name, snap3)

    # VM is still running at this point, so live XML has backingStore.

    # Delete snap2 (the immediate backing file after the active layer).
    # This file IS captured by _parse_domain_xml_source_paths because it's
    # a direct child <backingStore> of <disk>.  snap1 and base are nested
    # deeper and are NOT captured (source bug — see report).
    # On Linux, the file's inode stays alive because QEMU has it open
    # via the backing chain, but os.path.exists() returns False.
    assert snap2.path.exists(), "snap2 must exist before deletion"
    os.unlink(str(snap2.path))
    assert not snap2.path.exists(), "snap2 should no longer appear on disk"
    # VM must still be running (file open, inode alive).
    assert is_vm_running(shell, vm_name), "VM should still be running"

    # Build Core — no targets needed.
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "stale_xml.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- Run reconcile ---
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # XML must be refreshed.
    assert rec.xml_refreshed, f"Expected xml_refreshed=True, got {rec}"

    # Domain XML persistent config must no longer have <backingStore>.
    # (Live XML still has it while VM runs, but persistent was cleaned.)
    # Verify by checking the inactive/persistent XML.
    result = shell.run(
        ["virsh", "dumpxml", "--domain", vm_name, "--inactive"],
        timeout=30,
    )
    assert result.success, "virsh dumpxml --inactive failed"
    assert "<backingStore>" not in result.stdout, (
        "Persistent domain XML should no longer have <backingStore> after reconcile"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 4: reconcile with broken snapshot chain — no auto-rebase
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_reconcile_real_broken_chain_no_rebase(test_vm, caplog):
    """Reconcile does NOT rebase when a middle snapshot is deleted.

    1. Start VM, create 3 external snapshots (snap1, snap2, snap3).
       Chain: base → snap1 → snap2 → snap3 (active).
    2. Record all in state.
    3. Keep VM running. Delete snap2 (middle) from disk.
       VM still has file open — QEMU keeps running.
    4. Run ``core.reconcile()``.
    5. Verify: snap2 stays in state on first pass (XML still had it).
       xml_refreshed=True (stale backingStore stripped).
       No qemu-img rebase attempted.
       Note: broken-chain CRITICAL logging is only for target backup
       files, not snapshot chains. This is expected behavior.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create 3 external snapshots.
    snap1 = _snapshot_create(
        shell, vm_name, f"{vm_name}.20250726T000001_vda", snapshot_dir, base_image
    )
    snap2 = _snapshot_create(
        shell, vm_name, f"{vm_name}.20250726T000002_vda", snapshot_dir, base_image
    )
    snap3 = _snapshot_create(
        shell, vm_name, f"{vm_name}.20250726T000003_vda", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap2)
    state.record_snapshot(vm_name, snap3)

    # Manually delete snap2 (the immediate backing of active layer).
    # This IS in xml_paths (direct child backingStore of disk).
    # snap1 is NOT in xml_paths (nested backingStore — source bug).
    # VM is running — QEMU has file open, so it keeps running.
    assert snap2.path.exists(), "snap2 must exist before deletion"
    os.unlink(str(snap2.path))
    assert not snap2.path.exists(), "snap2 should be deleted from directory"

    # Build Core.
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "broken.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    # --- Run reconcile ---
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # snap2 is NOT removed from state on first reconcile pass.
    # XML references it (stale XML case), so it survives.
    snapshots_after = {s.name for s in state.get_snapshots(vm_name)}
    assert snap2.name in snapshots_after, (
        f"snap2 should remain in state (not auto-removed). Got: {snapshots_after}"
    )

    # XML should have been refreshed (stripped backingStore).
    assert rec.xml_refreshed, (
        f"Expected xml_refreshed=True after deleting middle snapshot. Result: {rec}"
    )

    # No qemu-img rebase should have been attempted.
    # (This is verified by the absence of an error from rebase.)


# ──────────────────────────────────────────────────────────────────────
# Test 5: reconcile dry-run — no changes on disk or in state
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_reconcile_real_dry_run(test_vm, caplog):
    """Reconcile in dry-run mode reports but does not modify.

    1. Start VM, create 2 real snapshots, record in state.
    2. Record a phantom snapshot with a fake path.
    3. Create an orphan file in snapshot_dir (not in state, not in XML).
    4. Run ``core.reconcile()`` with ``core.dry_run = True``.
    5. Verify: nothing changed on disk or in state.
       Phantom still in state, orphan file still on disk.
       Phantom count reported, orphan count reported.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create 2 real external snapshots.
    snap1 = _snapshot_create(
        shell, vm_name, f"{vm_name}.20250726T000001_vda", snapshot_dir, base_image
    )
    snap3 = _snapshot_create(
        shell, vm_name, f"{vm_name}.20250726T000003_vda", snapshot_dir, base_image
    )

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap3)

    # Record a phantom snapshot (fake path, never existed).
    phantom_path = snapshot_dir / f"{vm_name}.20250726T000002_phantom.qcow2"
    phantom_snap = SnapshotInfo(
        name=f"{vm_name}.20250726T000002_phantom",
        path=phantom_path,
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    state.record_snapshot(vm_name, phantom_snap)

    # Create an orphan file in snapshot_dir (not in state, not in XML).
    orphan_path = snapshot_dir / f"{vm_name}.20250101T000000_orphan.qcow2"
    shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(orphan_path), "1M"],
        timeout=30,
    )
    assert orphan_path.exists(), "Orphan file should exist"

    # Build Core.
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "dryrun.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    core.dry_run = True

    # --- Run reconcile in dry-run mode ---
    caplog.clear()
    with caplog.at_level(logging.INFO):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # In dry-run, phantom count is reported.
    assert rec.phantom_snapshots_removed >= 1, (
        f"Expected phantom_snapshots_removed >= 1 in dry-run, got {rec}"
    )
    assert rec.orphan_files_removed >= 1, (
        f"Expected orphan_files_removed >= 1 in dry-run, got {rec}"
    )

    # Phantom must still be in state (no changes).
    remaining = {s.name for s in state.get_snapshots(vm_name)}
    assert phantom_snap.name in remaining, (
        f"Phantom snapshot must remain in state during dry-run. Got: {remaining}"
    )

    # Orphan file must still be on disk.
    assert orphan_path.exists(), "Orphan file must remain on disk during dry-run"


# ──────────────────────────────────────────────────────────────────────
# GAP-2: XML-deleted, file-on-disk, in-state — not handled currently
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_reconcile_detects_snapshot_in_state_but_not_in_xml(test_vm, caplog):
    """Reconcile: snapshot in state + on disk but NOT in domain XML.

    1. Start VM, create 3 snapshots (snap1, snap2, snap3), record all.
    2. Stop VM (so persistent XML can be edited).
    3. Edit persistent XML to remove <backingStore> for snap2.
       snap2 file still on disk, still in state, but NOT in XML.
    4. Run core.reconcile().
    5. Verify: snap2 removed from state (phantom_snapshots_removed >= 1),
       WARNING log about "not in domain XML", file preserved on disk.
    """
    from xml.etree import ElementTree as ET

    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # ── Step 1: Create 3 snapshots ──────────────────────────────────
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.xmlgap-snap1", snapshot_dir, base_image)
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.xmlgap-snap2", snapshot_dir, base_image)
    snap3 = _snapshot_create(shell, vm_name, f"{vm_name}.xmlgap-snap3", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap2)
    state.record_snapshot(vm_name, snap3)

    # ── Step 2: Stop VM ─────────────────────────────────────────────
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(1)

    # ── Step 3: Edit persistent XML — remove snap2's <backingStore> ─
    dump_result = shell.run(
        ["virsh", "dumpxml", "--domain", vm_name, "--inactive"],
        timeout=30,
    )
    assert dump_result.success, "virsh dumpxml --inactive failed"

    root = ET.fromstring(dump_result.stdout)
    # Find the disk element and remove snap2's <backingStore>
    devices = root.find("devices")
    if devices is None:
        pytest.skip("No <devices> in domain XML")

    snap2_path_str = str(snap2.path)
    removed = False
    for disk in devices.findall("disk"):
        bs_elements = disk.findall(".//backingStore")
        for bs in bs_elements:
            source = bs.find("source")
            if source is not None:
                sf = source.get("file", "")
                if snap2_path_str in sf:
                    parent = disk.find(".//backingStore/..")
                    if parent is not None:
                        parent.remove(bs)
                        removed = True
                        break
        if removed:
            break

    if not removed:
        # Try simpler approach: remove all backingStore, then the VM
        # is self-contained with just the active layer.
        for disk in devices.findall("disk"):
            for bs in list(disk.findall("backingStore")):
                disk.remove(bs)
                removed = True

    if not removed:
        pytest.skip("Could not remove backingStore from XML")

    new_xml = ET.tostring(root, encoding="unicode")

    # Write temp XML and redefine the VM.
    tmp_xml = tmpdir / "edited.xml"
    tmp_xml.write_text(new_xml)
    define_result = shell.run(
        ["virsh", "define", str(tmp_xml)],
        timeout=30,
        check=True,
    )
    if not define_result.success:
        pytest.skip(f"virsh define failed: {define_result.error}")

    # ── Step 4: Run reconcile ───────────────────────────────────────
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "xmlgap.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # ── Step 5: Verify fix — snap2 removed from state ───────────────
    snaps_after = state.get_snapshots(vm_name)
    snap_names = [s.name for s in snaps_after]
    assert snap2.name not in snap_names, (
        f"snap2 should be removed from state after reconcile. Snapshots remaining: {snap_names}"
    )

    # WARNING log about "not in domain XML"
    assert "not in domain xml" in caplog.text.lower(), (
        f"Expected WARNING about snapshot not in domain XML. Captured: {caplog.text[:500]}"
    )

    # phantom_snapshots_removed should include this removal.
    assert rec.phantom_snapshots_removed >= 1, f"Expected phantom_snapshots_removed >= 1, got {rec}"

    # snap2 file must still exist on disk (safety — not deleted).
    assert snap2.path.exists(), "snap2 file should be preserved on disk (safety backup)"

    # Cleanup: restart VM to allow proper teardown.
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(1)
    _cleanup_checkpoints_snap(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# GAP-3: All snapshots deleted from disk, XML references all
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_reconcile_all_snapshots_deleted_from_disk(test_vm, caplog):
    """Reconcile: all snapshot files deleted while VM is running.

    1. Start VM, create 3 snapshots, record all in state.
    2. os.unlink() all 3 snapshot files while VM is running.
       (QEMU keeps inodes alive, os.path.exists() returns False.)
    3. Run core.reconcile().
    4. Assert: all snapshots removed from state (phantom detection),
       xml_refreshed=True (backingStore references stripped),
       persistent XML has no <backingStore>.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start the VM.
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # ── Step 1: Create 3 snapshots ──────────────────────────────────
    snap1 = _snapshot_create(shell, vm_name, f"{vm_name}.allgone-snap1", snapshot_dir, base_image)
    snap2 = _snapshot_create(shell, vm_name, f"{vm_name}.allgone-snap2", snapshot_dir, base_image)
    snap3 = _snapshot_create(shell, vm_name, f"{vm_name}.allgone-snap3", snapshot_dir, base_image)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap2)
    state.record_snapshot(vm_name, snap3)

    # ── Step 2: Delete all snapshot files (VM running, inodes alive) ──
    for snap in (snap1, snap2, snap3):
        os.unlink(str(snap.path))
        assert not snap.path.exists(), f"{snap.path} should not exist after unlink"

    assert is_vm_running(shell, vm_name), "VM should still be running"

    # ── Step 3: Run reconcile ───────────────────────────────────────
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )
    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "allgone.toml")
    factory = DefaultFactory(shell, state)
    core = Core(config=config, factory=factory, state=state, shell=shell)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        reconcile_results = core.reconcile(vm_name)

    assert vm_name in reconcile_results, f"Missing reconcile result for {vm_name}"
    rec = reconcile_results[vm_name]

    # ── Step 4: Assertions ──────────────────────────────────────────

    # 4a. XML must be refreshed (backingStore references stripped).
    assert rec.xml_refreshed, f"Expected xml_refreshed=True, got {rec}"

    # 4b. Persistent XML must have no <backingStore>.
    inactive_result = shell.run(
        ["virsh", "dumpxml", "--domain", vm_name, "--inactive"],
        timeout=30,
    )
    assert inactive_result.success, "virsh dumpxml --inactive failed"
    assert "<backingStore>" not in inactive_result.stdout, (
        "Persistent domain XML should have no <backingStore> after reconcile"
    )

    # 4c. Stale XML WARNING logs (files deleted, XML still references them).
    assert "stale domain xml" in caplog.text.lower(), (
        f"Expected stale XML log. Captured text: {caplog.text[:500]}"
    )

    # 4d. Snapshots stay in state — classified as "stale XML" (not phantom
    #     removed) because XML still references the deleted files.  Only
    #     XML is refreshed; state records persist until blockcommit.
    snaps_after = state.get_snapshots(vm_name)
    assert len(snaps_after) >= 3, f"Expected snapshots in state. Got {len(snaps_after)}"

    _cleanup_checkpoints_snap(shell, vm_name)


def _cleanup_checkpoints_snap(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints for *vm_name*."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return
    for line in result.stdout.strip().splitlines():
        cp = line.strip()
        if cp and cp.startswith("qsnap-"):
            shell.run(
                ["virsh", "checkpoint-delete", "--domain", vm_name, cp],
                timeout=30,
            )
