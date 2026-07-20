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


# ── test_check_state_orphaned_checkpoint_removed_target ─────────────────


def test_check_state_orphaned_checkpoint_removed_target(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A checkpoint exists whose hash does not match any configured target.

    Simulates a target that was removed from config — its checkpoint
    remains in libvirt but the hash no longer matches any target path.
    """
    import hashlib

    from qsnap.models.results import ShellResult

    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    # Configure one target at a "current" path
    target_path = tmp_path / "backup" / "existing"
    target_path.mkdir(parents=True)
    target = make_target(path=str(target_path))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # Compute hash of a "deleted" target path (not in vm.targets)
    deleted_hash = hashlib.md5(b"/nonexistent/deleted/target").hexdigest()[:8]
    orphan_cp = f"qsnap-{deleted_hash}-snap1"

    # Mock virsh checkpoint-list to return the orphaned checkpoint
    mock_shell.expect("virsh checkpoint-list").returns(
        ShellResult(success=True, stdout=f"{orphan_cp}\n", stderr="", returncode=0, error=None)
    )

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    assert orphan_cp in result["testvm"].orphan_checkpoints
    assert "orphan_checkpoints" in result["testvm"].status
    # Other fields should be clean
    assert result["testvm"].phantom_snapshots == []
    assert result["testvm"].phantom_fulls == []
    assert result["testvm"].stale_deps == []
    assert result["testvm"].corrupt_files == []


# ── test_check_state_orphaned_checkpoint_changed_path ───────────────────


def test_check_state_orphaned_checkpoint_changed_path(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A target path was changed so an existing checkpoint's hash no longer matches.

    The checkpoint was created when the target was at an old path; now
    the target is at a new path with a different hash.
    """
    import hashlib

    from qsnap.models.results import ShellResult

    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    # Target at new (current) path
    target_path = tmp_path / "backup" / "new-path"
    target_path.mkdir(parents=True)
    target = make_target(path=str(target_path))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # Checkpoint with hash of old path
    old_hash = hashlib.md5(b"/old/backup/path").hexdigest()[:8]
    orphan_cp = f"qsnap-{old_hash}-snap1"

    mock_shell.expect("virsh checkpoint-list").returns(
        ShellResult(success=True, stdout=f"{orphan_cp}\n", stderr="", returncode=0, error=None)
    )

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    assert orphan_cp in result["testvm"].orphan_checkpoints
    assert "orphan_checkpoints" in result["testvm"].status
    assert result["testvm"].phantom_snapshots == []
    assert result["testvm"].phantom_fulls == []
    assert result["testvm"].stale_deps == []
    assert result["testvm"].corrupt_files == []


# ── test_check_state_no_orphans_all_match ───────────────────────────────


def test_check_state_no_orphans_all_match(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """All checkpoints match configured targets — orphan_checkpoints is empty."""
    import hashlib

    from qsnap.models.results import ShellResult

    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    target_path = tmp_path / "backup" / "main"
    target_path.mkdir(parents=True)
    target = make_target(path=str(target_path))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # Checkpoint hash matches the configured target
    target_hash = hashlib.md5(str(target_path).encode()).hexdigest()[:8]
    matching_cp = f"qsnap-{target_hash}-snap1"

    mock_shell.expect("virsh checkpoint-list").returns(
        ShellResult(success=True, stdout=f"{matching_cp}\n", stderr="", returncode=0, error=None)
    )

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    assert result["testvm"].orphan_checkpoints == []
    assert "orphan_checkpoints" not in result["testvm"].status
    assert result["testvm"].status == "ok"
    assert result["testvm"].phantom_snapshots == []
    assert result["testvm"].phantom_fulls == []
    assert result["testvm"].stale_deps == []
    assert result["testvm"].corrupt_files == []


# ── test_check_state_checkpoint_list_failure_non_fatal ──────────────────


def test_check_state_checkpoint_list_failure_non_fatal(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """virsh checkpoint-list fails — check_state() does NOT raise, orphans empty."""
    from qsnap.models.results import ShellResult

    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    target_path = tmp_path / "backup"
    target_path.mkdir(parents=True)
    target = make_target(path=str(target_path))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # Mock virsh checkpoint-list to return failure
    mock_shell.expect("virsh checkpoint-list").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: Domain not found",
            returncode=1,
            error="Domain not found",
        )
    )

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Must NOT raise
    result = core.check_state()

    assert result["testvm"].orphan_checkpoints == []
    assert "orphan_checkpoints" not in result["testvm"].status
    assert result["testvm"].status == "ok"


# ── test_check_state_non_qsnap_checkpoints_ignored ──────────────────────


def test_check_state_non_qsnap_checkpoints_ignored(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Checkpoints not matching qsnap-{hash}-{snapshot} naming are silently ignored.

    Non-qsnap checkpoints are filtered by list_checkpoints (startswith("qsnap-")).
    qsnap- checkpoints with only 2 parts (malformed) are skipped by
    _detect_orphan_checkpoints (len(parts) < 3).
    """
    import hashlib

    from qsnap.models.results import ShellResult

    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    target_path = tmp_path / "backup" / "main"
    target_path.mkdir(parents=True)
    target = make_target(path=str(target_path))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # Hash for target — this one should match
    target_hash = hashlib.md5(str(target_path).encode()).hexdigest()[:8]
    # Hash for a different target — this one is truly orphaned
    other_hash = hashlib.md5(b"/other/target").hexdigest()[:8]

    # Mix of: non-qsnap, malformed qsnap, orphaned qsnap, matching qsnap
    checkpoints = [
        "not-ours-checkpoint",  # non-qsnap — filtered by list_checkpoints
        "qsnap-nohash",  # malformed (2 parts after split) → skipped
        f"qsnap-{other_hash}-orphan1",  # valid format, orphaned
        f"qsnap-{target_hash}-valid1",  # valid format, matching
    ]
    mock_shell.expect("virsh checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout="\n".join(checkpoints) + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    # Only the truly orphaned valid-format qsnap checkpoint should appear
    assert f"qsnap-{other_hash}-orphan1" in result["testvm"].orphan_checkpoints
    assert f"qsnap-{target_hash}-valid1" not in result["testvm"].orphan_checkpoints
    assert "qsnap-nohash" not in result["testvm"].orphan_checkpoints
    assert "not-ours-checkpoint" not in result["testvm"].orphan_checkpoints
    assert len(result["testvm"].orphan_checkpoints) == 1
    assert "orphan_checkpoints" in result["testvm"].status


# ── test_check_state_result_includes_orphans ────────────────────────────


def test_check_state_result_includes_orphans():
    """StateCheckResult with orphaned checkpoints preserves them in the field."""
    from qsnap.models.results import StateCheckResult

    orphans = ["qsnap-abc12345-snap1", "qsnap-def67890-snap2"]
    result = StateCheckResult(
        vm_name="testvm",
        status="orphan_checkpoints",
        orphan_checkpoints=orphans,
    )

    assert result.orphan_checkpoints == orphans
    assert result.orphan_checkpoints[0] == "qsnap-abc12345-snap1"
    assert result.orphan_checkpoints[1] == "qsnap-def67890-snap2"
    assert "orphan_checkpoints" in result.status


# ── test_check_state_result_empty_orphans ───────────────────────────────


def test_check_state_result_empty_orphans():
    """StateCheckResult with no orphans defaults to an empty list."""
    from qsnap.models.results import StateCheckResult

    result = StateCheckResult(vm_name="testvm", status="ok")

    assert result.orphan_checkpoints == []
    # Verify defaults for other lists too
    assert result.phantom_snapshots == []
    assert result.phantom_fulls == []
    assert result.stale_deps == []
    assert result.corrupt_files == []
