"""Tests for Core.check() — per-disk active-layer verification.

Covers the multi-disk comparison logic in
``Core._verify_active_layer_match()``: each domblklist disk is compared
ONLY against its own newest snapshot (max timestamp per disk group), not
against a single VM-wide newest.  Disks with no snapshots in state are
skipped.

All tests use ``MockShell`` with ``clean_shell`` for full mock control.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.models.config import DiskConfig
from qsnap.models.results import ShellResult, SnapshotInfo
from tests.mocks import MockConfigFacade

# ── helpers ────────────────────────────────────────────────────────────────


def _shell_ok(stdout: str = "") -> ShellResult:
    """Shortcut: successful ShellResult with given stdout."""
    return ShellResult(success=True, stdout=stdout, stderr="", returncode=0, error=None)


def _chain_json(paths: list[Path]) -> str:
    """Build a ``qemu-img info --backing-chain --output=json`` array.

    *paths* must be ordered from active layer (index 0) to base (last).
    """
    entries: list[dict[str, object]] = []
    for i, p in enumerate(paths):
        entry: dict[str, object] = {
            "filename": str(p),
            "format": "qcow2",
            "virtual-size": 10737418240,
            "actual-size": 200704,
        }
        if i + 1 < len(paths):
            entry["backing-filename"] = str(paths[i + 1])
        entries.append(entry)
    return json.dumps(entries)


def _dumpxml(paths: list[Path]) -> str:
    """Build ``virsh dumpxml`` output with all *paths* under one <disk>.

    The first path becomes ``<source file="...">``; subsequent paths
    become direct ``<backingStore><source file="..."/>`` children so
    that ``_parse_domain_xml_source_paths`` discovers them.
    """
    parts = ['<domain type="kvm"><devices><disk>']
    parts.append(f'<source file="{paths[0]}"/>')
    for p in paths[1:]:
        parts.append(f'<backingStore><source file="{p}"/></backingStore>')
    parts.append("</disk></devices></domain>")
    return "".join(parts)


# ── tests ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_verify_active_layer_match_multi_disk_per_group(
    make_vm_config, mock_factory, mock_state, clean_shell, tmp_path
):
    """Two-disk VM with different newest snapshots — each disk compared
    against its OWN newest, no false mismatch even though one disk's
    newest timestamp is older than the other's.
    """
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    base_vda = tmp_path / "base_vda.qcow2"
    base_vdb = tmp_path / "base_vdb.qcow2"
    vda_snap1 = snap_dir / "testvm.vda.20250713T100000_vda_a1b2c3.qcow2"
    vda_snap2 = snap_dir / "testvm.vda.20250713T120000_vda_d4e5f6.qcow2"
    vdb_snap1 = snap_dir / "testvm.vdb.20250713T110000_vdb_g7h8i9.qcow2"

    for f in (base_vda, base_vdb, vda_snap1, vda_snap2, vdb_snap1):
        f.write_text("")

    now = datetime(2025, 7, 13, 10, 0)

    # vda snapshots: t+0h (oldest), t+2h (newest for vda)
    mock_state.record_snapshot(
        "testvm",
        SnapshotInfo(
            name="testvm.vda.20250713T100000_vda_a1b2c3",
            path=vda_snap1,
            timestamp=now,
            allocation=1000,
            disk="vda",
        ),
    )
    mock_state.record_snapshot(
        "testvm",
        SnapshotInfo(
            name="testvm.vda.20250713T120000_vda_d4e5f6",
            path=vda_snap2,
            timestamp=now + timedelta(hours=2),
            allocation=2000,
            disk="vda",
        ),
    )
    # vdb snapshot: t+1h — older than vda_snap2, but ONLY compared
    # against vdb's own newest
    mock_state.record_snapshot(
        "testvm",
        SnapshotInfo(
            name="testvm.vdb.20250713T110000_vdb_g7h8i9",
            path=vdb_snap1,
            timestamp=now + timedelta(hours=1),
            allocation=1500,
            disk="vdb",
        ),
    )

    # ── mock setup ─────────────────────────────────────────────────────

    clean_shell.expect("test -f").returns(_shell_ok())

    # domblklist: vda active = vda_snap2, vdb active = vdb_snap1
    domblklist_out = (
        f"Target   Source\n--------------------------------\nvda   {vda_snap2}\nvdb   {vdb_snap1}\n"
    )
    clean_shell.expect_first("virsh domblklist").returns(_shell_ok(domblklist_out))

    # backing chain for vda (called with vda_snap2 as entry_path)
    vda_chain = [vda_snap2, vda_snap1, base_vda]
    clean_shell.expect_first("--backing-chain" + ".*" + re.escape(str(vda_snap2))).returns(
        _shell_ok(_chain_json(vda_chain))
    )

    # backing chain for vdb (called with vdb_snap1 as entry_path)
    vdb_chain = [vdb_snap1, base_vdb]
    clean_shell.expect_first("--backing-chain" + ".*" + re.escape(str(vdb_snap1))).returns(
        _shell_ok(_chain_json(vdb_chain))
    )

    # dumpxml: include all paths from both chains
    all_paths = vda_chain + vdb_chain
    clean_shell.expect_first("virsh dumpxml").returns(_shell_ok(_dumpxml(all_paths)))

    # ── VM config with two disks ────────────────────────────────────────
    disks = [
        DiskConfig(target="vda", base_image=base_vda),
        DiskConfig(target="vdb", base_image=base_vdb),
    ]
    vm = make_vm_config(name="testvm", disks=disks, snapshot_dir=snap_dir, targets=[])

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=clean_shell)

    result = core.check()

    # Assertions: no mismatch reported — status must be "ok"
    assert result["testvm"].status == "ok", f"Expected status='ok', got {result['testvm'].status!r}"
    assert result["testvm"].broken_snapshots == [], (
        f"Expected no broken snapshots, got: {result['testvm'].broken_snapshots}"
    )


@pytest.mark.unit
@pytest.mark.mock
def test_verify_active_layer_match_disk_without_snapshots_skipped(
    make_vm_config, mock_factory, mock_state, clean_shell, tmp_path
):
    """domblklist lists a disk (vdb) that has NO snapshots in state —
    the active-layer comparison for that disk is skipped, no mismatch.
    """
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    base_vda = tmp_path / "base_vda.qcow2"
    base_vdb = tmp_path / "base_vdb.qcow2"
    vda_snap1 = snap_dir / "testvm.vda.20250713T100000_vda_a1b2c3.qcow2"
    vdb_active = base_vdb  # vdb has no snapshot overlays, base is active

    for f in (base_vda, base_vdb, vda_snap1):
        f.write_text("")

    now = datetime(2025, 7, 13, 10, 0)

    # Only vda has snapshots in state; vdb has none
    mock_state.record_snapshot(
        "testvm",
        SnapshotInfo(
            name="testvm.vda.20250713T100000_vda_a1b2c3",
            path=vda_snap1,
            timestamp=now,
            allocation=1000,
            disk="vda",
        ),
    )

    # ── mock setup ─────────────────────────────────────────────────────

    clean_shell.expect("test -f").returns(_shell_ok())

    # domblklist: vda → its snapshot, vdb → its base image
    domblklist_out = (
        "Target   Source\n"
        "--------------------------------\n"
        f"vda   {vda_snap1}\n"
        f"vdb   {vdb_active}\n"
    )
    clean_shell.expect_first("virsh domblklist").returns(_shell_ok(domblklist_out))

    # Single backing-chain mock — matches both vda and vdb scans.
    # For vda: chain is [vda_snap1, base_vda].
    # For vdb: the same chain JSON is returned but scan_backing_chain
    # accumulates paths from within the JSON; vdb_active is NOT in the
    # JSON, so it won't appear in disk_paths.  Since it's also absent
    # from state_paths, _cross_reference_snapshots classifies it as
    # "legitimately deleted" (no|no|no) → no warning, no broken.
    vda_chain = [vda_snap1, base_vda]
    clean_shell.expect_first("--backing-chain").returns(_shell_ok(_chain_json(vda_chain)))

    # dumpxml: only vda paths
    clean_shell.expect_first("virsh dumpxml").returns(_shell_ok(_dumpxml(vda_chain)))

    # ── VM config with two disks ────────────────────────────────────────
    disks = [
        DiskConfig(target="vda", base_image=base_vda),
        DiskConfig(target="vdb", base_image=base_vdb),
    ]
    vm = make_vm_config(name="testvm", disks=disks, snapshot_dir=snap_dir, targets=[])

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=clean_shell)

    result = core.check()

    # vdb has no snapshots → skipped in _verify_active_layer_match.
    # The domblklist mismatch logic should NOT fire for vdb.
    assert result["testvm"].status == "ok", f"Expected status='ok', got {result['testvm'].status!r}"
    # Confirm no broken_snapshots mentioning vdb or any domblklist mismatch
    for issue in result["testvm"].broken_snapshots:
        assert "vdb" not in issue.lower(), f"vdb should be skipped, but found: {issue!r}"
