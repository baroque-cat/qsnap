"""Tests for Core.check_state() — state consistency cross-referencing.

Verifies that ``check_state()`` correctly detects phantom snapshots,
phantom FULLs, stale dependencies, and corrupt state files.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.results import ShellResult, SnapshotInfo
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
        str(backup_dir), full_name, datetime(2025, 7, 13, 10, 0)
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
        str(backup_dir), full_name, datetime(2025, 7, 13, 10, 0)
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
        str(backup_dir), full_name, datetime(2025, 7, 13, 10, 0)
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
        str(backup_dir), full_name, datetime(2025, 7, 13, 10, 0)
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

    # Compute the hash of the current target to return from target_hash().
    current_hash = hashlib.md5(str(target_path).encode()).hexdigest()[:8]

    # Configure the mock backup provider to return the orphan checkpoint
    # and the current target hash.  The factory now routes through
    # create_backup_provider (design D5).
    mock_factory._bitmap_backup_provider.list_checkpoints = lambda vm_name: [orphan_cp]
    mock_factory._bitmap_backup_provider.target_hash = lambda p: current_hash

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

    # Current target hash (different from old hash)
    current_hash = hashlib.md5(str(target_path).encode()).hexdigest()[:8]

    mock_factory._bitmap_backup_provider.list_checkpoints = lambda vm_name: [orphan_cp]
    mock_factory._bitmap_backup_provider.target_hash = lambda p: current_hash

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

    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    target_path = tmp_path / "backup" / "main"
    target_path.mkdir(parents=True)
    target = make_target(path=str(target_path))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # Checkpoint hash matches the configured target
    target_hash = hashlib.md5(str(target_path).encode()).hexdigest()[:8]
    matching_cp = f"qsnap-{target_hash}-snap1"

    mock_factory._bitmap_backup_provider.list_checkpoints = lambda vm_name: [matching_cp]
    mock_factory._bitmap_backup_provider.target_hash = lambda p: hashlib.md5(
        p.encode()
    ).hexdigest()[:8]

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
    """list_checkpoints returns empty — check_state() does NOT raise, orphans empty.

    When the backup provider's list_checkpoints returns an empty list
    (simulating checkpoint-list failure with WARNING logged inside the
    provider), _detect_orphan_checkpoints returns [] and check_state
    reports status="ok".
    """
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    target_path = tmp_path / "backup"
    target_path.mkdir(parents=True)
    target = make_target(path=str(target_path))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # MockBitmapBackupProvider.list_checkpoints already returns [] by
    # default (simulating failure / no checkpoints available).

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

    # list_checkpoints simulates the BitmapBackupProvider filter: only
    # qsnap-prefixed checkpoints are returned.  Non-qsnap checkpoints
    # like "not-ours-checkpoint" are already filtered out by the provider.
    checkpoints = [
        "qsnap-nohash",  # malformed (2 parts after split) → skipped
        f"qsnap-{other_hash}-orphan1",  # valid format, orphaned
        f"qsnap-{target_hash}-valid1",  # valid format, matching
    ]

    mock_factory._bitmap_backup_provider.list_checkpoints = lambda vm_name: checkpoints
    mock_factory._bitmap_backup_provider.target_hash = lambda p: hashlib.md5(
        p.encode()
    ).hexdigest()[:8]

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    # Only the truly orphaned valid-format qsnap checkpoint should appear
    assert f"qsnap-{other_hash}-orphan1" in result["testvm"].orphan_checkpoints
    assert f"qsnap-{target_hash}-valid1" not in result["testvm"].orphan_checkpoints
    assert "qsnap-nohash" not in result["testvm"].orphan_checkpoints
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


# ── test_detect_orphan_checkpoints_no_auto_cleanup_by_default ─────────────


def test_detect_orphan_checkpoints_no_auto_cleanup_by_default(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Call _detect_orphan_checkpoints() without auto_cleanup parameter.

    Orphan checkpoints are detected/reported but no virsh checkpoint-delete
    commands are executed.
    """
    import hashlib

    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    target_path = tmp_path / "backup" / "existing"
    target_path.mkdir(parents=True)
    target = make_target(path=str(target_path))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # Compute hash of a "deleted" target path (not in vm.targets)
    orphan_hash = hashlib.md5(b"/nonexistent/deleted/target").hexdigest()[:8]
    orphan_cp = f"qsnap-{orphan_hash}-snap1"

    # Hash of the current target
    current_hash = hashlib.md5(str(target_path).encode()).hexdigest()[:8]

    mock_factory._bitmap_backup_provider.list_checkpoints = lambda vm_name: [orphan_cp]
    mock_factory._bitmap_backup_provider.target_hash = lambda p: current_hash

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Spy on shell.run to verify no checkpoint-delete is called
    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        orphans = core._detect_orphan_checkpoints(vm)

    # Orphan is detected
    assert orphan_cp in orphans

    # Verify NO virsh checkpoint-delete commands were executed
    checkpoint_delete_calls = [
        c for c in run_spy.call_args_list
        if "checkpoint-delete" in " ".join(c[0][0])
    ]
    assert len(checkpoint_delete_calls) == 0, (
        f"No virsh checkpoint-delete should be called without auto_cleanup, "
        f"got {len(checkpoint_delete_calls)} calls"
    )


# ── test_detect_orphan_checkpoints_auto_cleanup_deletes ───────────────────


def test_detect_orphan_checkpoints_auto_cleanup_deletes(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Call _detect_orphan_checkpoints(auto_cleanup=True).

    Orphan checkpoints are detected and virsh checkpoint-delete commands
    are executed via mock_shell for each orphan. Success is logged.
    """
    import hashlib

    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    target_path = tmp_path / "backup" / "existing"
    target_path.mkdir(parents=True)
    target = make_target(path=str(target_path))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # Two orphan checkpoints from a deleted target
    orphan_hash = hashlib.md5(b"/nonexistent/deleted/target").hexdigest()[:8]
    current_hash = hashlib.md5(str(target_path).encode()).hexdigest()[:8]
    orphan_cp1 = f"qsnap-{orphan_hash}-snap1"
    orphan_cp2 = f"qsnap-{orphan_hash}-snap2"

    mock_factory._bitmap_backup_provider.list_checkpoints = lambda vm_name: [
        orphan_cp1, orphan_cp2,
    ]
    mock_factory._bitmap_backup_provider.target_hash = lambda p: current_hash

    # Register expectation for checkpoint-delete to succeed
    mock_shell.expect("checkpoint-delete").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    caplog.set_level(logging.INFO)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        orphans = core._detect_orphan_checkpoints(vm, auto_cleanup=True)

    # Both orphans detected
    assert orphan_cp1 in orphans
    assert orphan_cp2 in orphans
    assert len(orphans) == 2

    # Verify virsh checkpoint-delete was called for each orphan
    checkpoint_delete_calls = [
        c for c in run_spy.call_args_list
        if "checkpoint-delete" in " ".join(c[0][0])
    ]
    assert len(checkpoint_delete_calls) == 2, (
        f"Should have 2 checkpoint-delete calls, got {len(checkpoint_delete_calls)}"
    )

    # Verify success was logged for each orphan
    info_logs = [r for r in caplog.records if r.levelno == logging.INFO]
    deleted_logs = [
        r for r in info_logs if "deleted orphan checkpoint" in r.message
    ]
    assert len(deleted_logs) == 2, (
        f"Should have 2 INFO logs about deleted orphan checkpoint, "
        f"got {len(deleted_logs)}"
    )


# ── test_detect_orphan_checkpoints_auto_cleanup_non_fatal ─────────────────


def test_detect_orphan_checkpoints_auto_cleanup_non_fatal(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Call _detect_orphan_checkpoints(auto_cleanup=True) with failing shell.

    When virsh checkpoint-delete fails, the failure is logged as WARNING
    but does not raise an exception. The method continues processing other
    orphans.
    """
    import hashlib

    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    target_path = tmp_path / "backup" / "existing"
    target_path.mkdir(parents=True)
    target = make_target(path=str(target_path))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # Two orphan checkpoints from a deleted target
    orphan_hash = hashlib.md5(b"/nonexistent/deleted/target").hexdigest()[:8]
    current_hash = hashlib.md5(str(target_path).encode()).hexdigest()[:8]
    orphan_cp1 = f"qsnap-{orphan_hash}-snap1"
    orphan_cp2 = f"qsnap-{orphan_hash}-snap2"

    mock_factory._bitmap_backup_provider.list_checkpoints = lambda vm_name: [
        orphan_cp1, orphan_cp2,
    ]
    mock_factory._bitmap_backup_provider.target_hash = lambda p: current_hash

    # Register expectation for checkpoint-delete to FAIL
    mock_shell.expect("checkpoint-delete").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="checkpoint not found",
            returncode=1,
            error="Domain checkpoint not found",
        )
    )

    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    caplog.set_level(logging.WARNING)

    # Must NOT raise
    orphans = core._detect_orphan_checkpoints(vm, auto_cleanup=True)

    assert orphan_cp1 in orphans
    assert orphan_cp2 in orphans
    assert len(orphans) == 2

    # Both deletion attempts should fail and log WARNING
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    failed_delete_logs = [
        r for r in warnings if "failed to delete orphan checkpoint" in r.message
    ]
    assert len(failed_delete_logs) == 2, (
        f"Should have 2 WARNING logs about failed deletion, "
        f"got {len(failed_delete_logs)}"
    )

    # Verify no CRITICAL or ERROR logs (non-fatal)
    critical_or_error = [
        r for r in caplog.records
        if r.levelno >= logging.ERROR
    ]
    assert len(critical_or_error) == 0, (
        f"Should not have CRITICAL/ERROR logs, got {len(critical_or_error)}"
    )


# ── test_check_state_broken_backing_chain_detected ──────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_state_broken_backing_chain_detected(
    tmp_path: Path,
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A non-FULL backup with a broken backing chain is detected and reported.

    When ``qemu-img info --backing-chain`` fails for a non-FULL backup,
    the backup is added to ``broken_chains`` and ``"broken_chains"``
    appears in the status.
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    state_dir = tmp_path / "state"
    snap_dir.mkdir()
    backup_dir.mkdir()
    state_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # A non-FULL incremental backup file
    non_full_name = "inc.20250713T1000_vda"
    non_full_path = backup_dir / f"{non_full_name}.qcow2"
    non_full_path.touch()

    # Override the mock backup provider's list() to return our backup
    mock_factory._bitmap_backup_provider.list = lambda target: [
        SnapshotInfo(
            name=non_full_name,
            path=non_full_path,
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=1000,
        ),
    ]

    # Simulate a broken backing chain: qemu-img info returns failure
    mock_shell.expect(r"qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="qemu-img: Could not open backing file",
            returncode=1,
            error="backing chain broken",
        )
    )

    config = MockConfigFacade(
        global_config=make_global_config(state_dir=str(state_dir)),
        vms=[vm],
    )
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    expected_entry = f"{non_full_name} (target: {str(backup_dir)})"
    assert result["testvm"].broken_chains == [expected_entry]
    assert "broken_chains" in result["testvm"].status


# ── test_check_state_all_backing_chains_intact ──────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_state_all_backing_chains_intact(
    tmp_path: Path,
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When all non-FULL backups have intact backing chains, nothing is reported.

    ``qemu-img info --backing-chain`` succeeds for every non-FULL backup,
    so ``broken_chains`` is empty and ``"broken_chains"`` does NOT appear
    in the status.
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    state_dir = tmp_path / "state"
    snap_dir.mkdir()
    backup_dir.mkdir()
    state_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # Two non-FULL incremental backups with intact backing chains
    inc1_name = "inc.20250713T1000_vda"
    inc1_path = backup_dir / f"{inc1_name}.qcow2"
    inc1_path.touch()
    inc2_name = "inc.20250713T1100_vda"
    inc2_path = backup_dir / f"{inc2_name}.qcow2"
    inc2_path.touch()

    mock_factory._bitmap_backup_provider.list = lambda target: [
        SnapshotInfo(
            name=inc1_name,
            path=inc1_path,
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=1000,
        ),
        SnapshotInfo(
            name=inc2_name,
            path=inc2_path,
            timestamp=datetime(2025, 7, 13, 11, 0),
            allocation=2000,
        ),
    ]

    # All backing chains are intact: qemu-img info succeeds
    mock_shell.expect(r"qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "disk.qcow2"}]\n',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    config = MockConfigFacade(
        global_config=make_global_config(state_dir=str(state_dir)),
        vms=[vm],
    )
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    assert result["testvm"].broken_chains == []
    assert "broken_chains" not in result["testvm"].status


# ── test_check_state_full_backups_skipped_in_chain_validation ───────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_state_full_backups_skipped_in_chain_validation(
    tmp_path: Path,
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """FULL backups are NOT checked for backing-chain integrity.

    ``check_state()`` skips any backup whose name contains ``".FULL."`` —
    only non-FULL backups trigger ``qemu-img info --backing-chain``.
    The FULL backup never appears in ``broken_chains`` even though the
    mock shell returns failure for all ``qemu-img info`` calls.
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    state_dir = tmp_path / "state"
    snap_dir.mkdir()
    backup_dir.mkdir()
    state_dir.mkdir()

    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])

    # A FULL backup (standalone — no backing chain to validate)
    full_name = "testvm.FULL.monthly"
    full_path = backup_dir / full_name
    full_path.touch()

    # A non-FULL incremental backup
    non_full_name = "inc.20250713T1000_vda"
    non_full_path = backup_dir / f"{non_full_name}.qcow2"
    non_full_path.touch()

    mock_factory._bitmap_backup_provider.list = lambda target: [
        SnapshotInfo(
            name=full_name,
            path=full_path,
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=50000,
        ),
        SnapshotInfo(
            name=non_full_name,
            path=non_full_path,
            timestamp=datetime(2025, 7, 13, 11, 0),
            allocation=1000,
        ),
    ]

    # All qemu-img info calls return failure — the FULL is skipped so
    # it never hits this mock; only the non-FULL triggers it.
    mock_shell.expect(r"qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="qemu-img: Could not open backing file",
            returncode=1,
            error="backing chain broken",
        )
    )

    config = MockConfigFacade(
        global_config=make_global_config(state_dir=str(state_dir)),
        vms=[vm],
    )
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        result = core.check_state()

    # Only the non-FULL backup should be reported as broken
    assert len(result["testvm"].broken_chains) == 1
    assert non_full_name in result["testvm"].broken_chains[0]
    assert full_name not in result["testvm"].broken_chains[0]
    assert "broken_chains" in result["testvm"].status

    # Verify that qemu-img info --backing-chain was called exactly once
    # (for the non-FULL backup only — FULL is skipped entirely).
    qemu_img_calls = [
        c
        for c in run_spy.call_args_list
        if "qemu-img info" in " ".join(c[0][0])
        and "--backing-chain" in " ".join(c[0][0])
    ]
    assert len(qemu_img_calls) == 1, (
        f"Expected exactly 1 qemu-img info --backing-chain call, "
        f"got {len(qemu_img_calls)}"
    )
    assert str(non_full_path) in " ".join(qemu_img_calls[0][0][0])
