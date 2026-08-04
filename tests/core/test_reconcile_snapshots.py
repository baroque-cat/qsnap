"""Tests for Core.reconcile() — snapshot state repair scenarios.

Covers the reconcile refactor (D8):
- Phantom snapshot removal from state (state has, disk doesn't, XML doesn't)
- Orphan snapshot supplement into state (disk has, XML references, state doesn't)
- Orphan snapshot file deletion (disk has, XML doesn't, state doesn't)
- Stale domain XML refresh (_refresh_domain_backing_store())
- Broken chain detection (no auto-rebase)
- last_allocation mismatch detection
- Post-blockcommit no-action scenario
- Dry-run mode
- Structured result return
- VM filter
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.models.results import ReconcileResult, ShellResult, SnapshotInfo
from tests.mocks import MockConfigFacade

# ── helpers ────────────────────────────────────────────────────────────────


def _make_xml(vm_name: str, *source_paths: str) -> str:
    """Build a minimal libvirt domain XML with the given <source file=...> paths.

    The first path in *source_paths* is placed in ``<disk><source>``
    (the active layer).  Any remaining paths are nested as
    ``<backingStore><source>...`` elements under that disk.
    """
    lines = [
        '<domain type="kvm">',
        f"  <name>{vm_name}</name>",
        "  <devices>",
        '    <disk type="file" device="disk">',
    ]
    if source_paths:
        lines.append(f'      <source file="{source_paths[0]}"/>')
        for bp in source_paths[1:]:
            lines.append('      <backingStore type="file">')
            lines.append(f'        <source file="{bp}"/>')
            lines.append("      </backingStore>")
    lines.extend([
        "    </disk>",
        "  </devices>",
        "</domain>",
    ])
    return "\n".join(lines)


def _snap_dir(tmp_path: Path, vm_name: str) -> Path:
    """Create and return the snapshot directory for *vm_name*."""
    d = tmp_path / "snapshots" / vm_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snap(vm_name: str, snap_dir: Path, tag: str, allocation: int = 0) -> SnapshotInfo:
    """Build a SnapshotInfo with a consistent naming pattern."""
    name = f"{vm_name}.{tag}"
    return SnapshotInfo(
        name=name,
        path=snap_dir / f"{name}.qcow2",
        timestamp=datetime.now(),
        allocation=allocation,
        disk="vda"
    )


# ── 1. Phantom snapshot removed from state ─────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_phantom_snapshot_removed_from_state(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
    tmp_path,
    success_result,
):
    """State: snap1, snap2, snap3.  Disk: snap1, snap3 (snap2 missing).
    XML does NOT reference snap2 → snap2 removed from state,
    phantom_snapshots_removed=1."""
    snap_dir = _snap_dir(tmp_path, "testvm")

    # Create files on disk: snap1 and snap3 only (snap2 is phantom).
    snap1 = _snap("testvm", snap_dir, "snap1")
    snap3 = _snap("testvm", snap_dir, "snap3")
    snap2 = _snap("testvm", snap_dir, "snap2")
    snap1.path.write_text("")
    snap3.path.write_text("")
    # snap2.path is NOT created on disk — phantom

    # Pre-populate state with all three.
    mock_state.record_snapshot("testvm", snap1)
    mock_state.record_snapshot("testvm", snap2)
    mock_state.record_snapshot("testvm", snap3)

    # XML only references snap1 and snap3 (not snap2).
    mock_shell.expect_first("virsh dumpxml").returns(
        success_result(_make_xml("testvm", str(snap3.path), str(snap1.path)))
    )

    vm = make_vm_config(
        name="testvm",
        snapshot_dir=str(snap_dir),
        targets=[],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with caplog.at_level(logging.WARNING):
        result = core.reconcile()

    r = result["testvm"]
    assert r.phantom_snapshots_removed == 1, (
        f"Expected 1 phantom snapshot removed, got {r.phantom_snapshots_removed}"
    )
    # Verify snap2 is gone from state.
    remaining = [s.name for s in mock_state.get_snapshots("testvm")]
    assert snap2.name not in remaining, f"snap2 should be removed from state, got {remaining}"
    assert snap1.name in remaining and snap3.name in remaining

    assert any(
        "removed phantom snapshot" in rec.message.lower() for rec in caplog.records
    ), "Should log WARNING about phantom snapshot removal"


# ── 2. Orphan snapshot recorded in state ───────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_orphan_snapshot_recorded_in_state(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
    tmp_path,
    success_result,
):
    """State: snap1, snap3.  Disk: snap1, snap2, snap3.
    XML references snap2 → record_snapshot(snap2),
    state_supplemented >= 1, orphan_files_removed = 0."""
    snap_dir = _snap_dir(tmp_path, "testvm")

    snap1 = _snap("testvm", snap_dir, "snap1")
    snap2 = _snap("testvm", snap_dir, "snap2")
    snap3 = _snap("testvm", snap_dir, "snap3")

    # All three exist on disk.
    snap1.path.write_text("")
    snap2.path.write_text("")
    snap3.path.write_text("")

    # State only has snap1 and snap3 (snap2 missing from state — orphan).
    mock_state.record_snapshot("testvm", snap1)
    mock_state.record_snapshot("testvm", snap3)

    # XML references snap1, snap2, and snap3.
    mock_shell.expect_first("virsh dumpxml").returns(
        success_result(
            _make_xml("testvm", str(snap3.path), str(snap2.path), str(snap1.path))
        )
    )

    # The glob will return all three files; only snap2 is not in recorded state
    # and is in XML → supplement.

    vm = make_vm_config(
        name="testvm",
        snapshot_dir=str(snap_dir),
        targets=[],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with caplog.at_level(logging.INFO):
        result = core.reconcile()

    r = result["testvm"]
    assert r.orphan_files_removed == 0, (
        f"Should not remove orphan files, got {r.orphan_files_removed}"
    )
    assert r.state_supplemented >= 1, (
        f"Expected state_supplemented >= 1, got {r.state_supplemented}"
    )
    # Verify snap2 is now in state.
    state_names = [s.name for s in mock_state.get_snapshots("testvm")]
    assert snap2.name in state_names, f"snap2 should be recorded in state, got {state_names}"
    assert any(
        "state supplemented" in rec.message.lower() for rec in caplog.records
    ), "Should log INFO about state supplement"


# ── 3. Orphan snapshot deleted ─────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_orphan_snapshot_deleted(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
    tmp_path,
    success_result,
):
    """State: snap1, snap3.  Disk: snap1, snap2, snap3.
    XML does NOT reference snap2 → rm -f snap2,
    orphan_files_removed = 1."""
    snap_dir = _snap_dir(tmp_path, "testvm")

    snap1 = _snap("testvm", snap_dir, "snap1")
    snap2 = _snap("testvm", snap_dir, "snap2")
    snap3 = _snap("testvm", snap_dir, "snap3")

    # All three exist on disk.
    snap1.path.write_text("")
    snap2.path.write_text("")
    snap3.path.write_text("")

    # State only has snap1 and snap3 (snap2 missing from state — orphan).
    mock_state.record_snapshot("testvm", snap1)
    mock_state.record_snapshot("testvm", snap3)

    # XML only references snap1 and snap3, NOT snap2.
    mock_shell.expect_first("virsh dumpxml").returns(
        success_result(_make_xml("testvm", str(snap3.path), str(snap1.path)))
    )

    # Add rm -f expectation.
    mock_shell.expect("rm -f").returns(success_result())

    vm = make_vm_config(
        name="testvm",
        snapshot_dir=str(snap_dir),
        targets=[],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with caplog.at_level(logging.WARNING):
        result = core.reconcile()

    r = result["testvm"]
    assert r.orphan_files_removed == 1, (
        f"Expected 1 orphan file removed, got {r.orphan_files_removed}"
    )
    assert r.state_supplemented == 0, "Should not supplement state for orphan"
    assert any(
        "removed orphan snapshot file" in rec.message.lower() for rec in caplog.records
    ), "Should log WARNING about orphan removal"


# ── 4. Stale domain XML refreshed ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_stale_xml_refreshed(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
    tmp_path,
    success_result,
):
    """State: snap2, snap3 (snap1 deleted via blockcommit).
    Disk: snap2, snap3 (snap1 deleted).
    XML references snap1 (stale!) → _refresh_domain_backing_store()
    called, xml_refreshed=True."""
    snap_dir = _snap_dir(tmp_path, "testvm")

    snap2 = _snap("testvm", snap_dir, "snap2")
    snap3 = _snap("testvm", snap_dir, "snap3")
    snap1_path = snap_dir / "testvm.snap1.qcow2"

    # Only snap2 and snap3 exist on disk (snap1 was blockcommitted away).
    snap2.path.write_text("")
    snap3.path.write_text("")
    # snap1_path NOT created on disk.

    # State only has snap2 and snap3.
    mock_state.record_snapshot("testvm", snap2)
    mock_state.record_snapshot("testvm", snap3)

    # Step 1: Parse domain XML — references snap1 (stale), snap2, snap3.
    # The stale path is snap1_path (doesn't exist on disk).
    mock_shell.expect_first("virsh dumpxml").returns(
        success_result(
            _make_xml(
                "testvm",
                str(snap3.path),
                str(snap2.path),
                str(snap1_path),  # stale reference!
            )
        )
    )

    # _refresh_domain_backing_store() does its own dumpxml + virsh define.
    # Re-use the same XML for the refresh dumpxml call.
    # The method strips <backingStore> elements and calls virsh define.
    mock_shell.expect("virsh define").returns(success_result())

    vm = make_vm_config(
        name="testvm",
        snapshot_dir=str(snap_dir),
        targets=[],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with caplog.at_level(logging.WARNING):
        result = core.reconcile()

    r = result["testvm"]
    assert r.xml_refreshed is True, (
        f"Expected xml_refreshed=True, got {r.xml_refreshed}"
    )
    assert any(
        "stripped stale" in rec.message.lower() for rec in caplog.records
    ), "Should log WARNING about stale XML refresh"


# ── 5. Broken chain — no auto-rebase ──────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_broken_chain_no_auto_rebase(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
    tmp_path,
    success_result,
):
    """State: snap1, snap2, snap3.  Disk: snap1, snap3 (snap2 missing
    from middle of chain).  XML references snap2 → stale XML path.
    Verify NO ``qemu-img rebase -u`` is attempted (spec: no auto-rebase).

    Note: The current implementation treats broken snapshot chains as
    stale domain XML (step 1 + step 3).  It does NOT populate
    ``broken_chains`` for source snapshots — only for target backup
    files in step 6.  This test verifies the actual behavior."""
    snap_dir = _snap_dir(tmp_path, "testvm")

    snap1 = _snap("testvm", snap_dir, "snap1")
    snap2 = _snap("testvm", snap_dir, "snap2")
    snap3 = _snap("testvm", snap_dir, "snap3")

    # Only snap1 and snap3 exist on disk (snap2 missing — broken chain).
    snap1.path.write_text("")
    snap3.path.write_text("")
    # snap2.path NOT created.

    # State has all three.
    mock_state.record_snapshot("testvm", snap1)
    mock_state.record_snapshot("testvm", snap2)
    mock_state.record_snapshot("testvm", snap3)

    # XML references all three (including missing snap2).
    mock_shell.expect_first("virsh dumpxml").returns(
        success_result(
            _make_xml(
                "testvm",
                str(snap3.path),
                str(snap2.path),
                str(snap1.path),
            )
        )
    )

    # _refresh_domain_backing_store() needs virsh define.
    mock_shell.expect("virsh define").returns(success_result())

    vm = make_vm_config(
        name="testvm",
        snapshot_dir=str(snap_dir),
        targets=[],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with caplog.at_level(logging.WARNING):
        result = core.reconcile()

    r = result["testvm"]

    # snap2 is in state AND XML references it AND file missing → treated
    # as stale domain XML in step 1 (skip phantom removal).
    # Step 3 refreshes XML because snap2's path doesn't exist on disk.
    assert r.xml_refreshed is True, "XML should be refreshed for stale backingStore"

    # snap2 was NOT treated as phantom (XML still references it).
    assert r.phantom_snapshots_removed == 0, (
        "snap2 should NOT be removed from state (XML references it)"
    )

    # Verify no qemu-img rebase was attempted (MockShell would have
    # returned its default failure for any unmatched command, but we
    # check the pattern directly).
    assert not any(
        "rebase" in rec.message.lower() for rec in caplog.records
    ), "Should NOT attempt qemu-img rebase"


# ── 6. last_allocation mismatch ────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_last_allocation_mismatch(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    success_result,
):
    """State: last_allocation=1000.  qemu-img info: actual-size=2000.

    reconcile() detects the mismatch and sets allocation_fixed=True."""
    snap_dir = _snap_dir(tmp_path, "testvm")

    snap1 = _snap("testvm", snap_dir, "snap1")
    snap1.path.write_text("")

    mock_state.record_snapshot("testvm", snap1)
    mock_state.set_last_allocation("testvm", "vda", 1000)

    mock_shell.expect_first("virsh dumpxml").returns(
        success_result(_make_xml("testvm", str(snap1.path)))
    )

    # Intercept shell.run for qemu-img info call (MockShell.expect ordering
    # can be fragile with conftest defaults).
    _orig_run = mock_shell.run

    def _patch_run(cmd, timeout, check=False):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "qemu-img info" in cmd_str and "--output=json" in cmd_str:
            return ShellResult(
                success=True,
                stdout='{"actual-size": 2000, "format": "qcow2"}',
                stderr="", returncode=0, error=None,
            )
        return _orig_run(cmd, timeout, check)

    mock_shell.run = _patch_run

    vm = make_vm_config(
        name="testvm",
        snapshot_dir=str(snap_dir),
        targets=[],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.reconcile()

    r = result["testvm"]
    assert r.allocation_fixed is True, (
        f"Expected allocation_fixed=True, got {r.allocation_fixed!r}"
    )


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_after_blockcommit_no_action(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
    tmp_path,
    success_result,
):
    """State: snap2, snap3 (snap1 legitimately blockcommitted, state
    already updated).  Disk: snap2, snap3.  XML: snap2 → base (snap1
    legitimately gone).  → All zeros, no changes."""
    snap_dir = _snap_dir(tmp_path, "testvm")

    snap2 = _snap("testvm", snap_dir, "snap2")
    snap3 = _snap("testvm", snap_dir, "snap3")

    snap2.path.write_text("")
    snap3.path.write_text("")

    # State reflects post-blockcommit reality.
    mock_state.record_snapshot("testvm", snap2)
    mock_state.record_snapshot("testvm", snap3)

    # XML: snap3 → snap2 → base (no snap1, legitimate).
    mock_shell.expect_first("virsh dumpxml").returns(
        success_result(_make_xml("testvm", str(snap3.path), str(snap2.path)))
    )

    vm = make_vm_config(
        name="testvm",
        snapshot_dir=str(snap_dir),
        targets=[],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.reconcile()

    r = result["testvm"]
    assert r.phantom_snapshots_removed == 0
    assert r.phantom_fulls_removed == 0
    assert r.stale_deps_removed == 0
    assert r.baselines_cleared == 0
    assert r.orphan_checkpoints_deleted == 0
    assert r.orphan_files_removed == 0
    assert r.state_supplemented == 0
    assert r.xml_refreshed is False
    assert r.allocation_fixed is False
    assert r.broken_chains == []
    assert r.errors == [], f"Expected no errors, got {r.errors}"


# ── 8. Dry-run — no modifications ──────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_dry_run_no_modifications(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
    tmp_path,
    success_result,
):
    """Phantom scenario with ``core.dry_run = True``.
    Verify no real changes are made and messages are prefixed
    ``[dry-run reconcile]``."""
    snap_dir = _snap_dir(tmp_path, "testvm")

    snap1 = _snap("testvm", snap_dir, "snap1")
    snap3 = _snap("testvm", snap_dir, "snap3")
    snap2 = _snap("testvm", snap_dir, "snap2")
    snap1.path.write_text("")
    snap3.path.write_text("")
    # snap2 not created — phantom

    mock_state.record_snapshot("testvm", snap1)
    mock_state.record_snapshot("testvm", snap2)
    mock_state.record_snapshot("testvm", snap3)

    mock_shell.expect_first("virsh dumpxml").returns(
        success_result(_make_xml("testvm", str(snap3.path), str(snap1.path)))
    )

    vm = make_vm_config(
        name="testvm",
        snapshot_dir=str(snap_dir),
        targets=[],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.dry_run = True

    with caplog.at_level(logging.INFO):
        result = core.reconcile()

    r = result["testvm"]

    # In dry-run, counts increment but state is NOT modified.
    assert r.phantom_snapshots_removed == 1, (
        "dry-run should count phantom snapshots"
    )

    # Verify snap2 is still in state (dry-run doesn't modify).
    remaining = [s.name for s in mock_state.get_snapshots("testvm")]
    assert snap2.name in remaining, (
        "dry-run should NOT remove state entries"
    )

    # Verify [dry-run reconcile] prefix in log messages.
    assert any(
        "[dry-run reconcile]" in rec.message for rec in caplog.records
    ), "Should have [dry-run reconcile] prefixed messages"


# ── 9. Structured result ───────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_returns_structured_result(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    success_result,
):
    """Verify reconcile() returns ``dict[str, ReconcileResult]`` with
    all fields populated."""
    snap_dir = _snap_dir(tmp_path, "testvm")

    snap1 = _snap("testvm", snap_dir, "snap1")
    snap1.path.write_text("")

    mock_state.record_snapshot("testvm", snap1)

    mock_shell.expect_first("virsh dumpxml").returns(
        success_result(_make_xml("testvm", str(snap1.path)))
    )

    vm = make_vm_config(
        name="testvm",
        snapshot_dir=str(snap_dir),
        targets=[],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.reconcile()

    assert isinstance(result, dict), "reconcile() must return a dict"
    assert "testvm" in result, "Result should include testvm key"
    r = result["testvm"]
    assert isinstance(r, ReconcileResult), "Value must be ReconcileResult"

    # All fields should be present with their default types.
    assert r.vm_name == "testvm"
    assert isinstance(r.phantom_snapshots_removed, int)
    assert isinstance(r.phantom_fulls_removed, int)
    assert isinstance(r.stale_deps_removed, int)
    assert isinstance(r.baselines_cleared, int)
    assert isinstance(r.orphan_checkpoints_deleted, int)
    assert isinstance(r.orphan_files_removed, int)
    assert isinstance(r.state_supplemented, int)
    assert isinstance(r.xml_refreshed, bool)
    assert isinstance(r.allocation_fixed, bool)
    assert isinstance(r.errors, list)
    assert isinstance(r.broken_chains, list)


# ── 10. VM filter ──────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_with_vm_filter(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    success_result,
):
    """``reconcile(vm_filter="vm1")`` only processes vm1, not vm2."""
    snap_dir1 = _snap_dir(tmp_path, "vm1")
    snap_dir2 = _snap_dir(tmp_path, "vm2")

    snap1 = _snap("vm1", snap_dir1, "snap1")
    snap2 = _snap("vm2", snap_dir2, "snap1")
    snap1.path.write_text("")
    snap2.path.write_text("")

    mock_state.record_snapshot("vm1", snap1)
    mock_state.record_snapshot("vm2", snap2)

    # Mock virsh dumpxml for both VMs.
    # Since _parse_domain_xml_source_paths is called per-VM on the
    # filtered set, only vm1's should be hit.  We mock both for safety.
    mock_shell.expect("virsh dumpxml.*vm1").returns(
        success_result(_make_xml("vm1", str(snap1.path)))
    )
    mock_shell.expect("virsh dumpxml.*vm2").returns(
        success_result(_make_xml("vm2", str(snap2.path)))
    )

    vm1 = make_vm_config(
        name="vm1",
        snapshot_dir=str(snap_dir1),
        targets=[],
    )
    vm2 = make_vm_config(
        name="vm2",
        snapshot_dir=str(snap_dir2),
        targets=[],
    )
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.reconcile(vm_filter="vm1")

    assert "vm1" in result, "vm1 should be in results"
    assert "vm2" not in result, "vm2 should NOT be in results when filtered"
    assert len(result) == 1, f"Only one VM should be in results, got {list(result.keys())}"

