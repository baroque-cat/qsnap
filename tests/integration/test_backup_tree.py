"""Integration tests for Core.list_backups(tree=True) — full pipeline through Core.

Tests the backup tree grouping: resolving FULL anchors via
_group_backups_by_chain(), handling orphans under "__orphan__" key,
vm_filter, and multiple targets per VM.

Uses MockShell, MockState with pre-populated backup data via
patched backup provider list() return values.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.results import ShellResult, SnapshotInfo
from tests.mocks import MockConfigFacade


def _make_backup(
    name: str,
    path: str,
    timestamp: datetime | None = None,
    allocation: int = 1048576,
) -> SnapshotInfo:
    """Create a SnapshotInfo representing a backup."""
    if timestamp is None:
        timestamp = datetime(2025, 7, 13, 10, 0)
    return SnapshotInfo(
        name=name,
        path=Path(path),
        timestamp=timestamp,
        allocation=allocation,
        disk="vda",
    )


# ── test_list_backups_tree_full_pipeline ────────────────────────────────────


@pytest.mark.integration
def test_list_backups_tree_full_pipeline(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """list_backups(tree=True) groups backups by FULL anchor chain.

    Creates two FULL anchors, each with incrementals, and verifies
    the nested dict structure.  FULL backup names omit ".qcow2" so that
    _group_backups_by_chain() matches them with the _resolve_chain_full_anchor()
    result (which returns backing_path.stem).
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    base = datetime(2025, 7, 13, 10, 0)
    # FULL backup names: without ".qcow2" extension (matches backing_path.stem)
    full1_name = "testvm.FULL.20250710"
    full2_name = "testvm.FULL.20250712"

    backups = [
        # FULL chain 1: FULL name sans ".qcow2", path with ".qcow2"
        _make_backup(full1_name, f"{tmp_path}/backup/{full1_name}.qcow2", base),
        _make_backup("testvm_inc1.qcow2", f"{tmp_path}/backup/testvm_inc1.qcow2", base + timedelta(hours=1)),
        _make_backup("testvm_inc2.qcow2", f"{tmp_path}/backup/testvm_inc2.qcow2", base + timedelta(hours=2)),
        # FULL chain 2 (newer)
        _make_backup(full2_name, f"{tmp_path}/backup/{full2_name}.qcow2", base + timedelta(days=2)),
        _make_backup("testvm_inc3.qcow2", f"{tmp_path}/backup/testvm_inc3.qcow2", base + timedelta(days=2, hours=1)),
    ]

    # Mock qemu-img info --output=json for _resolve_chain_full_anchor().
    # Incremental backups (no ".FULL." in name) need their backing chain resolved.
    # _resolve_chain_full_anchor() reads backing-filename, finds .FULL., returns stem.
    for bp, full_path in [
        ("inc1", f"{tmp_path}/backup/{full1_name}.qcow2"),
        ("inc2", f"{tmp_path}/backup/{full1_name}.qcow2"),
        ("inc3", f"{tmp_path}/backup/{full2_name}.qcow2"),
    ]:
        mock_shell.expect(f"qemu-img info --output=json.*{bp}").returns(
            ShellResult(
                success=True,
                stdout=json.dumps({
                    "format": "qcow2",
                    "backing-filename": full_path,
                }),
                stderr="",
                returncode=0,
                error=None,
            )
        )

    # Patch the backup provider's list() to return our backups
    with patch.object(mock_factory._backup_provider, "list", return_value=backups):
        result = core.list_backups(tree=True)

    assert "testvm" in result
    target_chains = result["testvm"]

    # Should be a list of (target_path, chains_dict) tuples
    assert len(target_chains) == 1
    target_path, chains = target_chains[0]
    assert target_path == str(target.path)

    # Should have two FULL chains keyed by chain_id (which == FULL name sans .qcow2)
    assert full1_name in chains
    assert full2_name in chains

    # Chain 1 should have FULL + 2 incrementals = 3 backups
    chain1 = chains[full1_name]
    assert len(chain1) == 3, f"Expected 3 backups in chain1, got {len(chain1)}: {[b.name for b in chain1]}"

    # Chain 2 should have FULL + 1 incremental = 2 backups
    chain2 = chains[full2_name]
    assert len(chain2) == 2, f"Expected 2 backups in chain2, got {len(chain2)}: {[b.name for b in chain2]}"

    # Chains should be sorted by timestamp (older FULL first)
    chain_names = list(chains.keys())
    assert chain_names[0] == full1_name
    assert chain_names[1] == full2_name


# ── test_list_backups_tree_with_orphans ─────────────────────────────────────


@pytest.mark.integration
def test_list_backups_tree_with_orphans(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """list_backups(tree=True) groups orphaned backups under '__orphan__' key.

    Orphan backups are those without a .FULL. anchor in their backing chain.
    _resolve_chain_full_anchor() returns None → _group_backups_by_chain()
    puts them under "__orphan__".
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    base = datetime(2025, 7, 13, 10, 0)
    backups = [
        # A FULL anchor
        _make_backup("testvm.FULL.20250710", f"{tmp_path}/backup/testvm.FULL.20250710.qcow2", base),
        # Orphan incrementals (no .FULL. in backing chain)
        _make_backup("orphan_inc1.qcow2", f"{tmp_path}/backup/orphan_inc1.qcow2", base + timedelta(hours=1)),
        _make_backup("orphan_inc2.qcow2", f"{tmp_path}/backup/orphan_inc2.qcow2", base + timedelta(hours=2)),
    ]

    # Mock qemu-img info for orphan incrementals — backing chain has no .FULL.
    # Without a backing-filename field, _resolve_chain_full_anchor() returns None.
    mock_shell.expect("qemu-img info --output=json.*orphan_inc").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2"}),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with patch.object(mock_factory._backup_provider, "list", return_value=backups):
        result = core.list_backups(tree=True)

    assert "testvm" in result
    _, chains = result["testvm"][0]

    # Should have FULL chain and __orphan__ key
    assert "testvm.FULL.20250710" in chains
    assert "__orphan__" in chains

    # FULL chain has 1 backup
    assert len(chains["testvm.FULL.20250710"]) == 1

    # Orphan group has 2 backups
    assert len(chains["__orphan__"]) == 2

    # Orphan backups listed in the orphans group
    orphan_names = [b.name for b in chains["__orphan__"]]
    assert "orphan_inc1.qcow2" in orphan_names
    assert "orphan_inc2.qcow2" in orphan_names

    # Orphans should be last in the sorted dict (after FULL chains)
    chain_names = list(chains.keys())
    assert chain_names[-1] == "__orphan__"


# ── test_list_backups_tree_with_vm_filter ───────────────────────────────────


@pytest.mark.integration
def test_list_backups_tree_with_vm_filter(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """list_backups(vm_filter="vm1", tree=True) returns only vm1's backup tree."""
    target = make_target(path=str(tmp_path / "backup"))
    vm1 = make_vm_config(name="vm1", targets=[target])
    vm2 = make_vm_config(name="vm2", targets=[target])
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    base = datetime(2025, 7, 13, 10, 0)
    vm1_backups = [
        _make_backup("vm1.FULL.20250710", f"{tmp_path}/backup/vm1.FULL.20250710.qcow2", base),
    ]
    vm2_backups = [
        _make_backup("vm2.FULL.20250710", f"{tmp_path}/backup/vm2.FULL.20250710.qcow2", base),
    ]

    # Patch list() to return different backups per call
    call_count = [0]

    def _mock_list(target):
        call_count[0] += 1
        if call_count[0] == 1:
            return vm1_backups
        return vm2_backups

    with patch.object(mock_factory._backup_provider, "list", side_effect=_mock_list):
        result = core.list_backups(vm_filter="vm1", tree=True)

    # Should only have vm1
    assert set(result.keys()) == {"vm1"}
    assert len(result["vm1"]) == 1
    _, chains = result["vm1"][0]
    assert "vm1.FULL.20250710" in chains


# ── test_list_backups_tree_multiple_targets ──────────────────────────────────


@pytest.mark.integration
def test_list_backups_tree_multiple_targets(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """list_backups(tree=True) handles multiple targets per VM."""
    target1 = make_target(path=str(tmp_path / "backup1"))
    target2 = make_target(path=str(tmp_path / "backup2"))
    vm = make_vm_config(name="testvm", targets=[target1, target2])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    base = datetime(2025, 7, 13, 10, 0)
    target1_backups = [
        _make_backup("testvm.FULL.20250710", f"{tmp_path}/backup1/testvm.FULL.20250710.qcow2", base),
    ]
    target2_backups = [
        _make_backup("testvm.FULL.20250712", f"{tmp_path}/backup2/testvm.FULL.20250712.qcow2", base + timedelta(days=2)),
    ]

    # Patch list() to return different backups per target
    call_count = [0]

    def _mock_list(target):
        call_count[0] += 1
        if call_count[0] == 1:
            return target1_backups
        return target2_backups

    with patch.object(mock_factory._backup_provider, "list", side_effect=_mock_list):
        result = core.list_backups(tree=True)

    assert "testvm" in result
    target_chains = result["testvm"]

    # Should have 2 target entries
    assert len(target_chains) == 2

    # Each target's path should appear
    target_paths = [t[0] for t in target_chains]
    assert str(target1.path) in target_paths
    assert str(target2.path) in target_paths

    # Verify both targets have chains
    _, chains1 = target_chains[0]
    _, chains2 = target_chains[1]
    assert len(chains1) > 0
    assert len(chains2) > 0
