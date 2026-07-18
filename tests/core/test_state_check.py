"""Tests for Core.check_state() — state consistency cross-referencing.

Verifies that ``check_state()`` correctly detects phantom snapshots,
phantom FULLs, stale dependencies, and corrupt state files.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.core import Core
from qsnap.models.results import SnapshotInfo
from tests.mocks import MockConfigFacade

# ── test_check_state_all_snapshots_exist_clean ─────────────────────────


def test_check_state_all_snapshots_exist_clean(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When all recorded snapshots, FULLs, and deps have matching files on disk, status="ok"."""
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # Create actual snapshot file on disk
    snap_name = "testvm.20250713T1000_vda"
    snap_path = snap_dir / f"{snap_name}.qcow2"
    snap_path.touch()

    mock_state.record_snapshot(
        "testvm",
        SnapshotInfo(
            name=snap_name,
            path=snap_path,
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=1000,
        ),
    )

    # Create actual FULL backup file on disk (state stores path as target_dir/name)
    full_name = "full.FULL.monthly"
    full_path = backup_dir / full_name
    full_path.touch()

    mock_state.record_full_backup(
        str(backup_dir), full_name, datetime(2025, 7, 13, 10, 0), "monthly"
    )

    # Record incremental dependency with existing file (check_state adds .qcow2)
    inc_name = "inc.20250713T1100_vda"
    inc_path = backup_dir / f"{inc_name}.qcow2"
    inc_path.touch()
    mock_state.record_incremental_dependency(str(backup_dir), inc_name, full_name)

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    assert result["testvm"].status == "ok"
    assert result["testvm"].phantom_snapshots == []
    assert result["testvm"].phantom_fulls == []
    assert result["testvm"].stale_deps == []
    assert result["testvm"].corrupt_files == []


# ── test_check_state_phantom_snapshot_detected ──────────────────────────


def test_check_state_phantom_snapshot_detected(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A snapshot in state whose file is missing → phantom_snapshots populated, status="stale_snapshots"."""
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # Record a snapshot in state with a non-existent file path
    missing_path = snap_dir / "missing_snap.qcow2"
    # Do NOT create the file — this is the phantom

    mock_state.record_snapshot(
        "testvm",
        SnapshotInfo(
            name="missing_snap",
            path=missing_path,
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=1000,
        ),
    )

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    assert result["testvm"].status == "stale_snapshots"
    assert len(result["testvm"].phantom_snapshots) == 1
    assert "missing_snap" in result["testvm"].phantom_snapshots[0]
    assert str(missing_path) in result["testvm"].phantom_snapshots[0]
    assert result["testvm"].phantom_fulls == []
    assert result["testvm"].stale_deps == []
    assert result["testvm"].corrupt_files == []


# ── test_check_state_phantom_full_detected ──────────────────────────────


def test_check_state_phantom_full_detected(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A FULL backup in state whose file is missing → phantom_fulls populated, status="stale_fulls"."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(tmp_path / "snapshots"), targets=[target])

    # Record a FULL backup with a file that does NOT exist
    full_name = "full.FULL.monthly"
    # Do NOT create the file on disk

    mock_state.record_full_backup(
        str(backup_dir), full_name, datetime(2025, 7, 13, 10, 0), "monthly"
    )

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    assert result["testvm"].status == "stale_fulls"
    assert len(result["testvm"].phantom_fulls) == 1
    assert full_name in result["testvm"].phantom_fulls[0]
    assert result["testvm"].phantom_snapshots == []
    assert result["testvm"].stale_deps == []
    assert result["testvm"].corrupt_files == []


# ── test_check_state_orphaned_dependency_detected ───────────────────────


def test_check_state_orphaned_dependency_detected(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """An incremental dependency recorded in state whose .qcow2 file is missing → stale_deps."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(tmp_path / "snapshots"), targets=[target])

    # Create a valid FULL on disk (so the FULL itself is not phantom)
    full_name = "full.FULL.monthly"
    full_path = backup_dir / full_name
    full_path.touch()
    mock_state.record_full_backup(
        str(backup_dir), full_name, datetime(2025, 7, 13, 10, 0), "monthly"
    )

    # Record an incremental dependency but do NOT create its file
    inc_name = "inc.20250713T1100_vda"
    mock_state.record_incremental_dependency(str(backup_dir), inc_name, full_name)

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    assert result["testvm"].status == "stale_deps"
    assert len(result["testvm"].stale_deps) == 1
    assert inc_name in result["testvm"].stale_deps[0]
    assert full_name in result["testvm"].stale_deps[0]
    assert result["testvm"].phantom_snapshots == []
    assert result["testvm"].phantom_fulls == []
    assert result["testvm"].corrupt_files == []


# ── test_check_state_detached_dependency_detected ───────────────────────


def test_check_state_detached_dependency_detected(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A dependency where the FULL itself is phantom causes stale_deps detection.

    When the FULL backup file is missing but there are still incremental
    dependency records referencing it, status includes "stale_deps"
    (because the dependency file won't exist either).
    """
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(tmp_path / "snapshots"), targets=[target])

    # Record a FULL backup whose file does NOT exist (phantom)
    full_name = "full.FULL.monthly"
    # Do NOT create the file
    mock_state.record_full_backup(
        str(backup_dir), full_name, datetime(2025, 7, 13, 10, 0), "monthly"
    )

    # Record an incremental dependency referencing that phantom FULL
    inc_name = "inc.20250713T1100_vda"
    mock_state.record_incremental_dependency(str(backup_dir), inc_name, full_name)

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    # FULL is phantom → stale_fulls, dependency file missing → stale_deps
    assert "stale_fulls" in result["testvm"].status
    assert "stale_deps" in result["testvm"].status
    assert len(result["testvm"].phantom_fulls) >= 1
    assert len(result["testvm"].stale_deps) >= 1
    assert inc_name in result["testvm"].stale_deps[0]
    assert result["testvm"].phantom_snapshots == []
    assert result["testvm"].corrupt_files == []


# ── test_check_state_corrupted_json_detected ────────────────────────────


def test_check_state_corrupted_json_detected(
    tmp_path: Path,
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When the VM's state JSON file contains corrupt content, status="corrupt_state"."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", snapshot_dir=str(tmp_path / "snapshots"), targets=[target])

    # Create a corrupt state JSON file for this VM
    vm_state_file = state_dir / "testvm.json"
    vm_state_file.write_text("this is not valid json {{{")

    config = MockConfigFacade(
        global_config=make_global_config(state_dir=str(state_dir)),
        vms=[vm],
    )
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    assert result["testvm"].status == "corrupt_state"
    assert len(result["testvm"].corrupt_files) == 1
    assert "testvm.json" in result["testvm"].corrupt_files[0]
    assert result["testvm"].phantom_snapshots == []
    assert result["testvm"].phantom_fulls == []
    assert result["testvm"].stale_deps == []
