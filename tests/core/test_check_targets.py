"""Tests for Core.check() — triple-source target verification.

Verifies that ``check()`` correctly cross-references:
(1) state FULLs + incremental deps, (2) disk files (provider.list),
(3) libvirt checkpoints (virsh checkpoint-list).

Each test creates at least one snapshot on-disk + in state so that
the snapshot-verification portion of ``check()`` passes cleanly,
then focuses on the target-verification behaviour implemented in
``_check_target_consistency()``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.results import ShellResult, SnapshotInfo
from tests.mocks import MockConfigFacade

# ── helpers ────────────────────────────────────────────────────────────────

# Module-level result factory for helper functions (not pytest fixtures).
_ok = lambda: ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
_shell_ok = lambda stdout="": ShellResult(success=True, stdout=stdout, stderr="", returncode=0, error=None)


def _single_file_chain(path: Path) -> str:
    """qemu-img info --backing-chain --output=json for a standalone file."""
    return json.dumps([{
        "filename": str(path),
        "format": "qcow2",
        "virtual-size": 10737418240,
        "actual-size": 200704,
    }])


def _multi_file_chain(*paths: Path) -> str:
    """qemu-img info --backing-chain --output=json for a chain
    in top-to-base order, each backing the next."""
    entries: list[dict] = []
    for i, p in enumerate(paths):
        entry: dict = {
            "filename": str(p),
            "format": "qcow2",
            "virtual-size": 10737418240,
            "actual-size": 200704,
        }
        if i + 1 < len(paths):
            entry["backing-filename"] = str(paths[i + 1])
        entries.append(entry)
    return json.dumps(entries)


def _setup_snapshot_check(
    mock_state,
    mock_shell,
    snap_dir: Path,
    vm_name: str,
) -> Path:
    """Create one snapshot on disk + in state and configure the shell
    so that the snapshot-verification portion of ``check()`` passes.

    Returns the snapshot file path (the active layer).
    """
    snap_name = f"{vm_name}.20250713T1400_vda"
    snap_path = snap_dir / f"{snap_name}.qcow2"
    snap_path.touch()  # real file on disk so os.path.exists() passes

    mock_state.record_snapshot(
        vm_name,
        SnapshotInfo(
            name=snap_name,
            path=snap_path,
            timestamp=datetime(2025, 7, 13, 14, 0),
            allocation=1000,
        ),
    )

    # Override the default fixture domblklist to point to *our* snapshot.
    mock_shell.expect_first("virsh domblklist").returns(
        _shell_ok(f"Target   Source\n--------------------------------\nvda   {snap_path}\n"),
    )

    # qemu-img info --backing-chain on the active layer (snapshot chain).
    # Use expect_first so this specific pattern wins over any catch-all.
    mock_shell.expect_first(
        f"--backing-chain.*{re.escape(str(snap_path))}"
    ).returns(_shell_ok(_single_file_chain(snap_path)))

    # virsh dumpxml — return XML that references only the snapshot.
    mock_shell.expect("virsh dumpxml").returns(
        _shell_ok(
            '<domain type="kvm">'
            f'<devices><disk type="file" device="disk">'
            f'<source file="{snap_path}"/>'
            f"</disk></devices>"
            "</domain>"
        ),
    )

    return snap_path


# ── Scenario 1: all consistent ─────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_targets_all_consistent(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    success_result,
):
    """State: 1 FULL + 2 incrementals.  Disk: all exist.  Chain traversable.
    One checkpoint with matching target_hash → status="ok".
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir),
                        targets=[target])
    config = MockConfigFacade(vms=[vm])

    # Snapshot verification passes cleanly.
    _setup_snapshot_check(mock_state, mock_shell, snap_dir, "testvm")

    # ── Target state ──────────────────────────────────────────────────
    full_name = "testvm.FULL.20250712.qcow2"
    full_path = backup_dir / full_name
    full_path.touch()

    inc1_name = "testvm.20250713T1500_vda"
    inc1_path = backup_dir / f"{inc1_name}.qcow2"
    inc1_path.touch()

    inc2_name = "testvm.20250713T1600_vda"
    inc2_path = backup_dir / f"{inc2_name}.qcow2"
    inc2_path.touch()

    mock_state.record_full_backup(
        str(backup_dir), full_name,
        datetime(2025, 7, 12, 10, 0),
    )
    mock_state.record_incremental_dependency(
        str(backup_dir), inc1_name, full_name,
    )
    mock_state.record_incremental_dependency(
        str(backup_dir), inc2_name, full_name,
    )

    # Provider returns all backup files on disk.
    backups = [
        SnapshotInfo(
            name=full_name.rstrip(".qcow2"),
            path=full_path,
            timestamp=datetime(2025, 7, 12, 10, 0),
            allocation=0,
        ),
        SnapshotInfo(
            name=inc1_name,
            path=inc1_path,
            timestamp=datetime(2025, 7, 13, 15, 0),
            allocation=0,
        ),
        SnapshotInfo(
            name=inc2_name,
            path=inc2_path,
            timestamp=datetime(2025, 7, 13, 16, 0),
            allocation=0,
        ),
    ]

    # Chain traversability: qemu-img --backing-chain on inc2 succeeds.
    mock_shell.expect(
        f"--backing-chain.*{re.escape(str(inc2_path))}"
    ).returns(success_result(_multi_file_chain(inc2_path, inc1_path, full_path)))

    # Compute the target_hash for matching checkpoints.
    tgt_hash = mock_factory._bitmap_backup_provider.target_hash(
        str(backup_dir)
    )
    mock_shell.expect("virsh checkpoint-list").returns(
        success_result(f"qsnap-{tgt_hash}-testvm.snap\n"),
    )

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=backups,
    ), patch.object(
        mock_factory._bitmap_backup_provider, "list_checkpoints",
        return_value=[f"qsnap-{tgt_hash}-testvm.snap"],
    ):
        core = Core(
            config=config, factory=mock_factory,
            state=mock_state, shell=mock_shell,
        )
        result = core.check()

    assert result["testvm"].status == "ok"
    assert result["testvm"].broken_snapshots == []


# ── Scenario 2: phantom FULL ───────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_phantom_full(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """State: 1 FULL.  Disk: FULL doesn't exist → phantom FULL detected,
    broken.append("phantom backup: ...").
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir),
                        targets=[target])
    config = MockConfigFacade(vms=[vm])

    _setup_snapshot_check(mock_state, mock_shell, snap_dir, "testvm")

    # FULL in state, but NO file on disk.
    full_name = "testvm.FULL.20250712.qcow2"
    mock_state.record_full_backup(
        str(backup_dir), full_name,
        datetime(2025, 7, 12, 10, 0),
    )

    # Provider returns nothing — no files on disk.
    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=[],
    ), patch.object(
        mock_factory._bitmap_backup_provider, "list_checkpoints",
        return_value=[],
    ):
        core = Core(
            config=config, factory=mock_factory,
            state=mock_state, shell=mock_shell,
        )
        result = core.check()

    assert result["testvm"].status == "broken"
    assert any(
        "phantom backup" in b and full_name.rstrip(".qcow2") in b
        for b in result["testvm"].broken_snapshots
    ), f"Expected phantom backup for {full_name} in broken_snapshots"


# ── Scenario 3: phantom incremental ────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_phantom_incremental(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """State: inc1 → FULL dep.  Disk: inc1 doesn't exist → stale dep,
    broken.append("phantom backup: inc1_name").
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir),
                        targets=[target])
    config = MockConfigFacade(vms=[vm])

    _setup_snapshot_check(mock_state, mock_shell, snap_dir, "testvm")

    # FULL on disk.
    full_name = "testvm.FULL.20250712.qcow2"
    full_path = backup_dir / full_name
    full_path.touch()

    mock_state.record_full_backup(
        str(backup_dir), full_name,
        datetime(2025, 7, 12, 10, 0),
    )
    # Incremental in state but no file on disk.
    inc1_name = "testvm.20250713T1500_vda"
    mock_state.record_incremental_dependency(
        str(backup_dir), inc1_name, full_name,
    )

    # Provider returns only the FULL.
    backups = [
        SnapshotInfo(
            name=full_name.rstrip(".qcow2"),
            path=full_path,
            timestamp=datetime(2025, 7, 12, 10, 0),
            allocation=0,
        ),
    ]

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=backups,
    ), patch.object(
        mock_factory._bitmap_backup_provider, "list_checkpoints",
        return_value=[],
    ):
        core = Core(
            config=config, factory=mock_factory,
            state=mock_state, shell=mock_shell,
        )
        result = core.check()

    assert result["testvm"].status == "broken"
    assert any(
        "phantom backup" in b and inc1_name in b
        for b in result["testvm"].broken_snapshots
    ), f"Expected phantom backup for {inc1_name} in broken_snapshots"


# ── Scenario 4: orphan backup file ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_orphan_backup_file(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
    success_result,
):
    """State: 1 FULL + 1 inc.  Disk: FULL + inc1 + inc2 (inc2 not in state).
    Orphan is a WARNING — status stays "ok" (no items added to broken).
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir),
                        targets=[target])
    config = MockConfigFacade(vms=[vm])

    _setup_snapshot_check(mock_state, mock_shell, snap_dir, "testvm")

    # FULL + inc1 in state AND on disk.
    full_name = "testvm.FULL.20250712.qcow2"
    full_path = backup_dir / full_name
    full_path.touch()
    inc1_name = "testvm.20250713T1500_vda"
    inc1_path = backup_dir / f"{inc1_name}.qcow2"
    inc1_path.touch()

    # inc2 on disk but NOT in state (orphan).
    inc2_name = "testvm.20250713T1600_vda"
    inc2_path = backup_dir / f"{inc2_name}.qcow2"
    inc2_path.touch()

    mock_state.record_full_backup(
        str(backup_dir), full_name,
        datetime(2025, 7, 12, 10, 0),
    )
    mock_state.record_incremental_dependency(
        str(backup_dir), inc1_name, full_name,
    )

    # Chain traversability on last incremental (inc1) succeeds.
    mock_shell.expect(
        f"--backing-chain.*{re.escape(str(inc1_path))}"
    ).returns(success_result(_multi_file_chain(inc1_path, full_path)))

    backups = [
        SnapshotInfo(name=full_name.rstrip(".qcow2"), path=full_path,
                     timestamp=datetime(2025, 7, 12, 10, 0), allocation=0),
        SnapshotInfo(name=inc1_name, path=inc1_path,
                     timestamp=datetime(2025, 7, 13, 15, 0), allocation=0),
        SnapshotInfo(name=inc2_name, path=inc2_path,
                     timestamp=datetime(2025, 7, 13, 16, 0), allocation=0),
    ]

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=backups,
    ), patch.object(
        mock_factory._bitmap_backup_provider, "list_checkpoints",
        return_value=[],
    ), caplog.at_level(logging.WARNING):
        core = Core(
            config=config, factory=mock_factory,
            state=mock_state, shell=mock_shell,
        )
        result = core.check()

    # Orphan is a WARNING — not broken.
    assert result["testvm"].status == "ok"
    assert result["testvm"].broken_snapshots == []
    assert any(
        "orphan backup file" in r.message and inc2_path.name in r.message
        for r in caplog.records
    ), "Should log WARNING about orphan backup file"


# ── Scenario 5: broken backup chain (middle file deleted) ──────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_broken_backup_chain(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    failure_result,
):
    """State: FULL + inc1 + inc2.  Disk: FULL + inc2 (inc1 deleted).
    inc1 detected as phantom; qemu-img --backing-chain on inc2 fails.
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir),
                        targets=[target])
    config = MockConfigFacade(vms=[vm])

    _setup_snapshot_check(mock_state, mock_shell, snap_dir, "testvm")

    full_name = "testvm.FULL.20250712.qcow2"
    full_path = backup_dir / full_name
    full_path.touch()

    inc1_name = "testvm.20250713T1500_vda"
    # inc1_path = backup_dir / f"{inc1_name}.qcow2" — NOT created (deleted)

    inc2_name = "testvm.20250713T1600_vda"
    inc2_path = backup_dir / f"{inc2_name}.qcow2"
    inc2_path.touch()

    mock_state.record_full_backup(
        str(backup_dir), full_name,
        datetime(2025, 7, 12, 10, 0),
    )
    mock_state.record_incremental_dependency(
        str(backup_dir), inc1_name, full_name,
    )
    mock_state.record_incremental_dependency(
        str(backup_dir), inc2_name, full_name,
    )

    # Chain traversability check on last incremental (inc2) — FAILS.
    # Use expect_first to override conftest default --backing-chain expectation.
    mock_shell.expect_first(
        f"--backing-chain.*{re.escape(str(inc2_path))}"
    ).returns(failure_result("Could not open backing file"))

    backups = [
        SnapshotInfo(name=full_name.rstrip(".qcow2"), path=full_path,
                     timestamp=datetime(2025, 7, 12, 10, 0), allocation=0),
        SnapshotInfo(name=inc2_name, path=inc2_path,
                     timestamp=datetime(2025, 7, 13, 16, 0), allocation=0),
    ]

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=backups,
    ), patch.object(
        mock_factory._bitmap_backup_provider, "list_checkpoints",
        return_value=[],
    ):
        core = Core(
            config=config, factory=mock_factory,
            state=mock_state, shell=mock_shell,
        )
        result = core.check()

    assert result["testvm"].status == "broken"
    broken_list = result["testvm"].broken_snapshots
    # inc1 is phantom.
    assert any(
        "phantom backup" in b and inc1_name in b
        for b in broken_list
    ), f"Expected phantom backup for {inc1_name}"
    # inc2 chain is broken.
    assert any(
        "backup chain broken" in b and inc2_name in b
        for b in broken_list
    ), f"Expected broken chain at {inc2_name}"


# ── Scenario 6: broken chain — FULL missing ────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_broken_backup_chain_full_missing(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    failure_result,
):
    """State: FULL + inc1.  Disk: inc1 (FULL deleted).
    FULL detected as phantom; qemu-img --backing-chain on inc1 fails.
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir),
                        targets=[target])
    config = MockConfigFacade(vms=[vm])

    _setup_snapshot_check(mock_state, mock_shell, snap_dir, "testvm")

    full_name = "testvm.FULL.20250712.qcow2"
    # full_path NOT created — FULL deleted from disk.

    inc1_name = "testvm.20250713T1500_vda"
    inc1_path = backup_dir / f"{inc1_name}.qcow2"
    inc1_path.touch()

    mock_state.record_full_backup(
        str(backup_dir), full_name,
        datetime(2025, 7, 12, 10, 0),
    )
    mock_state.record_incremental_dependency(
        str(backup_dir), inc1_name, full_name,
    )

    # Chain traversability on inc1 fails (backing FULL missing).
    # Use expect_first to override conftest default --backing-chain expectation.
    mock_shell.expect_first(
        f"--backing-chain.*{re.escape(str(inc1_path))}"
    ).returns(failure_result("Could not open backing file"))

    backups = [
        SnapshotInfo(name=inc1_name, path=inc1_path,
                     timestamp=datetime(2025, 7, 13, 15, 0), allocation=0),
    ]

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=backups,
    ), patch.object(
        mock_factory._bitmap_backup_provider, "list_checkpoints",
        return_value=[],
    ):
        core = Core(
            config=config, factory=mock_factory,
            state=mock_state, shell=mock_shell,
        )
        result = core.check()

    assert result["testvm"].status == "broken"
    broken_list = result["testvm"].broken_snapshots
    # FULL is phantom.
    assert any(
        "phantom backup" in b and full_name.rstrip(".qcow2") in b
        for b in broken_list
    ), f"Expected phantom backup for {full_name}"
    # inc1 chain is broken.
    assert any(
        "backup chain broken" in b and inc1_name in b
        for b in broken_list
    ), f"Expected broken chain at {inc1_name}"


# ── Scenario 7: orphan checkpoint ──────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_orphan_checkpoint(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """checkpoint-list has entry with wrong target_hash → WARNING logged,
    but status stays "ok" (orphan checkpoints are warnings, not broken).
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir),
                        targets=[target])
    config = MockConfigFacade(vms=[vm])

    _setup_snapshot_check(mock_state, mock_shell, snap_dir, "testvm")

    # FULL on disk + in state.
    full_name = "testvm.FULL.20250712.qcow2"
    full_path = backup_dir / full_name
    full_path.touch()
    mock_state.record_full_backup(
        str(backup_dir), full_name,
        datetime(2025, 7, 12, 10, 0),
    )

    backups = [
        SnapshotInfo(name=full_name.rstrip(".qcow2"), path=full_path,
                     timestamp=datetime(2025, 7, 12, 10, 0), allocation=0),
    ]

    # Compute the CORRECT target hash for the configured target.
    tgt_hash = mock_factory._bitmap_backup_provider.target_hash(
        str(backup_dir)
    )
    # Provide a checkpoint with a WRONG (different) hash.
    wrong_hash = "deadbeef"

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=backups,
    ), patch.object(
        mock_factory._bitmap_backup_provider, "list_checkpoints",
        return_value=[
            f"qsnap-{wrong_hash}-testvm.snap",    # wrong hash → orphan
            f"qsnap-{tgt_hash}-testvm.snap",      # correct hash — ok
        ],
    ), caplog.at_level(logging.WARNING):
        core = Core(
            config=config, factory=mock_factory,
            state=mock_state, shell=mock_shell,
        )
        result = core.check()

    # Orphan checkpoints are WARNING — not broken.
    assert result["testvm"].status == "ok", (
        "Orphan checkpoints should not break status — they are WARNING only"
    )
    assert result["testvm"].broken_snapshots == []
    assert any(
        "orphan checkpoint" in r.message
        for r in caplog.records
    ), f"Should log WARNING about orphan checkpoint, got: {[r.message for r in caplog.records]}"


# ── Scenario 8: missing checkpoint ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_missing_checkpoint(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
    success_result,
):
    """State: FULL + inc1 should have a checkpoint, but checkpoint-list
    is empty → WARNING logged, status stays "ok".
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir),
                        targets=[target])
    config = MockConfigFacade(vms=[vm])

    _setup_snapshot_check(mock_state, mock_shell, snap_dir, "testvm")

    # FULL + inc1 in state AND on disk.
    full_name = "testvm.FULL.20250712.qcow2"
    full_path = backup_dir / full_name
    full_path.touch()

    inc1_name = "testvm.20250713T1500_vda"
    inc1_path = backup_dir / f"{inc1_name}.qcow2"
    inc1_path.touch()

    mock_state.record_full_backup(
        str(backup_dir), full_name,
        datetime(2025, 7, 12, 10, 0),
    )
    mock_state.record_incremental_dependency(
        str(backup_dir), inc1_name, full_name,
    )

    # Chain traversability on inc1 succeeds.
    mock_shell.expect(
        f"--backing-chain.*{re.escape(str(inc1_path))}"
    ).returns(success_result(_multi_file_chain(inc1_path, full_path)))

    backups = [
        SnapshotInfo(name=full_name.rstrip(".qcow2"), path=full_path,
                     timestamp=datetime(2025, 7, 12, 10, 0), allocation=0),
        SnapshotInfo(name=inc1_name, path=inc1_path,
                     timestamp=datetime(2025, 7, 13, 15, 0), allocation=0),
    ]

    # checkpoint-list returns empty — no checkpoints at all.
    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=backups,
    ), patch.object(
        mock_factory._bitmap_backup_provider, "list_checkpoints",
        return_value=[],
    ), caplog.at_level(logging.WARNING):
        core = Core(
            config=config, factory=mock_factory,
            state=mock_state, shell=mock_shell,
        )
        result = core.check()

    # Missing checkpoint is a WARNING — not broken.
    assert result["testvm"].status == "ok"
    assert result["testvm"].broken_snapshots == []
    assert any(
        "no checkpoint" in r.message
        for r in caplog.records
    ), f"Should log WARNING about missing checkpoint, got: {[r.message for r in caplog.records]}"


# ── Scenario 9: multiple checkpoints for same target ───────────────────────


@pytest.mark.unit
@pytest.mark.mock
@pytest.mark.xfail(reason="Implementation gap: multiple checkpoints per target not yet detected")
def test_check_multiple_checkpoints(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
    success_result,
):
    """checkpoint-list has 2 checkpoints for the same target →
    current implementation does NOT detect this (no code for it),
    so the test verifies no crash and no false-positive broken.
    This is a gap in the implementation relative to the spec.
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir),
                        targets=[target])
    config = MockConfigFacade(vms=[vm])

    _setup_snapshot_check(mock_state, mock_shell, snap_dir, "testvm")

    # FULL + inc1 in state AND on disk.
    full_name = "testvm.FULL.20250712.qcow2"
    full_path = backup_dir / full_name
    full_path.touch()

    inc1_name = "testvm.20250713T1500_vda"
    inc1_path = backup_dir / f"{inc1_name}.qcow2"
    inc1_path.touch()

    mock_state.record_full_backup(
        str(backup_dir), full_name,
        datetime(2025, 7, 12, 10, 0),
    )
    mock_state.record_incremental_dependency(
        str(backup_dir), inc1_name, full_name,
    )

    # Chain traversability on inc1 succeeds.
    mock_shell.expect(
        f"--backing-chain.*{re.escape(str(inc1_path))}"
    ).returns(success_result(_multi_file_chain(inc1_path, full_path)))

    backups = [
        SnapshotInfo(name=full_name.rstrip(".qcow2"), path=full_path,
                     timestamp=datetime(2025, 7, 12, 10, 0), allocation=0),
        SnapshotInfo(name=inc1_name, path=inc1_path,
                     timestamp=datetime(2025, 7, 13, 15, 0), allocation=0),
    ]

    # 2 checkpoints for the same target.
    tgt_hash = mock_factory._bitmap_backup_provider.target_hash(
        str(backup_dir)
    )
    checkpoints = [
        f"qsnap-{tgt_hash}-testvm.snap1",
        f"qsnap-{tgt_hash}-testvm.snap2",
    ]

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=backups,
    ), patch.object(
        mock_factory._bitmap_backup_provider, "list_checkpoints",
        return_value=checkpoints,
    ):
        core = Core(
            config=config, factory=mock_factory,
            state=mock_state, shell=mock_shell,
        )
        result = core.check()

    # The current implementation does NOT detect multiple checkpoints.
    # Verify no crash and no false broken signals.
    assert result["testvm"].status == "ok", (
        "Multiple checkpoints should not cause a false broken status"
    )
    assert result["testvm"].broken_snapshots == []


# ── Scenario 10: check after retention cleanup ──────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_after_retention_cleanup(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    success_result,
):
    """After retention removes old backups (FULL + incrementals),
    check() should report ``status="ok"`` with no phantom/orphan entries.

    State after retention: 1 FULL + 1 incremental (the oldest incremental
    was removed by retention).  Disk files match.  Chain traversable.
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir),
                        targets=[target])
    config = MockConfigFacade(vms=[vm])

    _setup_snapshot_check(mock_state, mock_shell, snap_dir, "testvm")

    # ── Post-retention target state: FULL + inc2 only (inc1 removed) ──
    full_name = "testvm.FULL.20250712.qcow2"
    full_path = backup_dir / full_name
    full_path.touch()

    # inc1 was removed by retention — NOT in state, NOT on disk.

    inc2_name = "testvm.20250713T1600_vda"
    inc2_path = backup_dir / f"{inc2_name}.qcow2"
    inc2_path.touch()

    mock_state.record_full_backup(
        str(backup_dir), full_name,
        datetime(2025, 7, 12, 10, 0),
    )
    mock_state.record_incremental_dependency(
        str(backup_dir), inc2_name, full_name,
    )

    # Provider returns only FULL + inc2 (inc1 was deleted).
    backups = [
        SnapshotInfo(
            name=full_name.rstrip(".qcow2"),
            path=full_path,
            timestamp=datetime(2025, 7, 12, 10, 0),
            allocation=0,
        ),
        SnapshotInfo(
            name=inc2_name,
            path=inc2_path,
            timestamp=datetime(2025, 7, 13, 16, 0),
            allocation=0,
        ),
    ]

    # Chain traversability: qemu-img --backing-chain on inc2 succeeds
    # (inc2 → FULL — inc1 is no longer in the chain).
    mock_shell.expect(
        f"--backing-chain.*{re.escape(str(inc2_path))}"
    ).returns(success_result(_multi_file_chain(inc2_path, full_path)))

    tgt_hash = mock_factory._bitmap_backup_provider.target_hash(
        str(backup_dir)
    )
    mock_shell.expect("virsh checkpoint-list").returns(
        success_result(f"qsnap-{tgt_hash}-testvm.snap\n"),
    )

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=backups,
    ), patch.object(
        mock_factory._bitmap_backup_provider, "list_checkpoints",
        return_value=[f"qsnap-{tgt_hash}-testvm.snap"],
    ):
        core = Core(
            config=config, factory=mock_factory,
            state=mock_state, shell=mock_shell,
        )
        result = core.check()

    assert result["testvm"].status == "ok", (
        f"Expected status ok after retention cleanup, got {result['testvm'].status}"
    )
    assert result["testvm"].broken_snapshots == []


# ── Scenario 11: check after force-full ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_after_force_full(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    success_result,
):
    """After a forced FULL backup replaces the old chain,
    check() should report ``status="ok"``.

    State after force-full: new FULL only (old FULL + incremental removed).
    Disk has the new FULL.  Chain traversable.
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir),
                        targets=[target])
    config = MockConfigFacade(vms=[vm])

    _setup_snapshot_check(mock_state, mock_shell, snap_dir, "testvm")

    # ── Post-force-full target state: new FULL only ────────────────────
    new_full_name = "testvm.FULL.20250713.qcow2"
    new_full_path = backup_dir / new_full_name
    new_full_path.touch()

    mock_state.record_full_backup(
        str(backup_dir), new_full_name,
        datetime(2025, 7, 13, 14, 0),
    )

    # Provider returns only the new FULL.
    backups = [
        SnapshotInfo(
            name=new_full_name.rstrip(".qcow2"),
            path=new_full_path,
            timestamp=datetime(2025, 7, 13, 14, 0),
            allocation=0,
        ),
    ]

    # Chain traversability: qemu-img --backing-chain on new_full succeeds
    # (standalone file, no backing).
    mock_shell.expect(
        f"--backing-chain.*{re.escape(str(new_full_path))}"
    ).returns(success_result(_single_file_chain(new_full_path)))

    tgt_hash = mock_factory._bitmap_backup_provider.target_hash(
        str(backup_dir)
    )
    mock_shell.expect("virsh checkpoint-list").returns(
        success_result(f"qsnap-{tgt_hash}-testvm.snap\n"),
    )

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=backups,
    ), patch.object(
        mock_factory._bitmap_backup_provider, "list_checkpoints",
        return_value=[f"qsnap-{tgt_hash}-testvm.snap"],
    ):
        core = Core(
            config=config, factory=mock_factory,
            state=mock_state, shell=mock_shell,
        )
        result = core.check()

    assert result["testvm"].status == "ok", (
        f"Expected status ok after force-full, got {result['testvm'].status}"
    )
    assert result["testvm"].broken_snapshots == []

