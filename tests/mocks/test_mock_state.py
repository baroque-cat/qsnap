"""Mock verification: InMemoryStateManager implements IStateManager."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.interfaces.state import IStateManager
from qsnap.models.results import DeferredBlockcommit, FullBackupInfo, SnapshotInfo
from tests.mocks.mock_state import InMemoryStateManager


def test_inmemory_state_is_istatemanager():
    """InMemoryStateManager passes isinstance against IStateManager and all
    basic state operations (set/get allocation, record/list snapshots) work."""
    state_manager = InMemoryStateManager()
    assert isinstance(state_manager, IStateManager)

    # Allocation: initially None, then set and get.
    assert state_manager.get_last_allocation("testvm") is None

    state_manager.set_last_allocation("testvm", 1048576)
    assert state_manager.get_last_allocation("testvm") == 1048576

    # Snapshots: initially empty, then record and retrieve.
    assert state_manager.get_snapshots("testvm") == []

    info = SnapshotInfo(
        name="testvm.20240101T000000",
        path=Path("/var/lib/libvirt/snapshots/testvm/testvm.20240101T000000.qcow2"),
        timestamp=datetime(2024, 1, 1, 0, 0, 0),
        allocation=65536,
    )
    state_manager.record_snapshot("testvm", info)

    snapshots = state_manager.get_snapshots("testvm")
    assert len(snapshots) == 1
    assert snapshots[0].name == info.name
    assert snapshots[0].allocation == info.allocation


def test_in_memory_state_manager_implements_deferred_operations():
    """InMemoryStateManager is still a valid IStateManager after the
    deferred blockcommit methods were added to the ABC."""
    state_manager = InMemoryStateManager()
    assert isinstance(state_manager, IStateManager)

    # The deferred-operation methods must exist and be callable.
    assert callable(state_manager.add_deferred_blockcommit)
    assert callable(state_manager.get_deferred_operations)
    assert callable(state_manager.clear_deferred_operations)

    # A VM with no deferred operations returns an empty list (never None).
    assert state_manager.get_deferred_operations("testvm") == []


def test_in_memory_state_manager_add_get_clear_deferred():
    """Round-trip: add_deferred_blockcommit → get returns it → clear → get
    returns [].
    """
    state_manager = InMemoryStateManager()

    # Initially empty.
    assert state_manager.get_deferred_operations("testvm") == []

    # Add a deferred blockcommit.
    state_manager.add_deferred_blockcommit(
        "testvm",
        snapshots=["snap1", "snap2"],
        reason="MAC blocks blockcommit while VM is running",
    )

    # Retrieve — should contain exactly one DeferredBlockcommit.
    deferred = state_manager.get_deferred_operations("testvm")
    assert len(deferred) == 1
    assert isinstance(deferred[0], DeferredBlockcommit)
    assert deferred[0].snapshots == ["snap1", "snap2"]
    assert deferred[0].reason == "MAC blocks blockcommit while VM is running"
    assert deferred[0].since is not None

    # Clear and verify empty.
    state_manager.clear_deferred_operations("testvm")
    assert state_manager.get_deferred_operations("testvm") == []


def test_inmemory_state_manager_full_backup_methods():
    """InMemoryStateManager implements get_last_full_backup and
    set_last_full_backup.  set then get returns a FullBackupInfo with the
    same values.  get on an unknown path returns None."""
    state_manager = InMemoryStateManager()

    # Unknown path returns None.
    assert state_manager.get_last_full_backup("/mnt/backup/testvm") is None

    # Set and get.
    ts = datetime.now()
    state_manager.set_last_full_backup("/mnt/backup/testvm", "testvm.FULL.qcow2", ts)

    info = state_manager.get_last_full_backup("/mnt/backup/testvm")
    assert info is not None
    assert isinstance(info, FullBackupInfo)
    assert info.name == "testvm.FULL.qcow2"
    assert info.timestamp == ts
    assert info.path == Path("/mnt/backup/testvm") / "testvm.FULL.qcow2"


def test_inmemory_state_manager_content_hash_persists():
    """record_snapshot with SnapshotInfo(content_hash=...) persists the
    content_hash so that get_snapshots() returns the snapshot with the
    same content_hash value."""
    state_manager = InMemoryStateManager()

    info = SnapshotInfo(
        name="test",
        path=Path("/tmp/test"),
        timestamp=datetime.now(),
        allocation=65536,
        content_hash="abc123",
    )
    state_manager.record_snapshot("testvm", info)

    snapshots = state_manager.get_snapshots("testvm")
    assert len(snapshots) == 1
    assert snapshots[0].content_hash == "abc123"


def test_in_memory_state_manager_deferred_last_warned_at_defaults_none():
    """add_deferred_blockcommit creates entries with last_warned_at=None."""
    state_manager = InMemoryStateManager()

    state_manager.add_deferred_blockcommit("testvm", ["snap1"], "apparmor")

    deferred = state_manager.get_deferred_operations("testvm")
    assert len(deferred) == 1
    assert deferred[0].last_warned_at is None


def test_in_memory_state_manager_update_deferred_warning():
    """update_deferred_warning correctly updates last_warned_at on the right entry."""
    state_manager = InMemoryStateManager()

    state_manager.add_deferred_blockcommit("vm1", ["snap1"], "apparmor")
    state_manager.add_deferred_blockcommit("vm1", ["snap2"], "selinux")

    warned = datetime(2025, 6, 1, 10, 0, 0)
    state_manager.update_deferred_warning("vm1", 1, warned)

    deferred = state_manager.get_deferred_operations("vm1")
    assert len(deferred) == 2
    # Index 0 unchanged.
    assert deferred[0].last_warned_at is None
    # Index 1 updated.
    assert deferred[1].last_warned_at == warned


def test_in_memory_state_manager_get_deferred_carries_last_warned_at():
    """get_deferred_operations returns entries that carry last_warned_at."""
    state_manager = InMemoryStateManager()

    state_manager.add_deferred_blockcommit("vm1", ["snap1"], "apparmor")
    warned = datetime(2025, 6, 1, 10, 0, 0)
    state_manager.update_deferred_warning("vm1", 0, warned)

    deferred = state_manager.get_deferred_operations("vm1")
    assert len(deferred) == 1
    assert deferred[0].last_warned_at == warned


def test_in_memory_state_manager_passes_isinstance():
    """Contract verification: InMemoryStateManager is an IStateManager.

    Assert ``isinstance(InMemoryStateManager(), IStateManager)`` is True
    after all interface changes (e.g. deferred ops, full backups, etc.).
    """
    state_manager = InMemoryStateManager()
    assert isinstance(state_manager, IStateManager), (
        "InMemoryStateManager must satisfy IStateManager contract"
    )


# ── New IStateManager methods (bucket-driven-backup-model) ──────────


def test_mock_state_isinstance_istatemanager():
    """InMemoryStateManager passes isinstance check against IStateManager
    after all new methods (get_full_backups, record_full_backup,
    record_incremental_dependency, get_incremental_dependencies) are added."""
    state_manager = InMemoryStateManager()
    assert isinstance(state_manager, IStateManager)
    # Verify new methods exist as callable attributes.
    assert callable(state_manager.get_full_backups)
    assert callable(state_manager.record_full_backup)
    assert callable(state_manager.record_incremental_dependency)
    assert callable(state_manager.get_incremental_dependencies)


def test_mock_state_get_full_backups():
    """get_full_backups returns a list of FullBackupInfo for a target.

    Unknown target returns an empty list (not None).  After recording
    a full backup, it appears in the returned list.
    """
    state_manager = InMemoryStateManager()

    # Unknown target → empty list.
    result = state_manager.get_full_backups("/mnt/backup/testvm")
    assert result == []
    assert isinstance(result, list)

    # Record one full backup.
    ts = datetime(2025, 1, 15, 12, 0, 0)
    state_manager.record_full_backup(
        "/mnt/backup/testvm", "testvm.FULL.20250115.qcow2", ts, "monthly"
    )

    fulls = state_manager.get_full_backups("/mnt/backup/testvm")
    assert len(fulls) == 1
    assert isinstance(fulls[0], FullBackupInfo)
    assert fulls[0].name == "testvm.FULL.20250115.qcow2"
    assert fulls[0].timestamp == ts
    assert fulls[0].bucket_level == "monthly"
    assert fulls[0].path == Path("/mnt/backup/testvm") / "testvm.FULL.20250115.qcow2"


def test_mock_state_record_full_backup():
    """record_full_backup appends to the list for a target.

    Recording multiple full backups accumulates them.  The returned
    list preserves insertion order (oldest → newest).  bucket_level
    is stored correctly for each entry.
    """
    state_manager = InMemoryStateManager()

    ts1 = datetime(2025, 1, 1, 0, 0, 0)
    ts2 = datetime(2025, 2, 1, 0, 0, 0)
    ts3 = datetime(2025, 3, 1, 0, 0, 0)

    state_manager.record_full_backup(
        "/mnt/backup/testvm", "testvm.FULL.20250101.qcow2", ts1, "monthly"
    )
    state_manager.record_full_backup(
        "/mnt/backup/testvm", "testvm.FULL.20250201.qcow2", ts2, "monthly"
    )
    state_manager.record_full_backup(
        "/mnt/backup/testvm", "testvm.FULL.20250301.qcow2", ts3, "yearly"
    )

    fulls = state_manager.get_full_backups("/mnt/backup/testvm")
    assert len(fulls) == 3
    assert fulls[0].name == "testvm.FULL.20250101.qcow2"
    assert fulls[0].bucket_level == "monthly"
    assert fulls[1].name == "testvm.FULL.20250201.qcow2"
    assert fulls[1].bucket_level == "monthly"
    assert fulls[2].name == "testvm.FULL.20250301.qcow2"
    assert fulls[2].bucket_level == "yearly"

    # Verify get_full_backups returns a copy — mutations don't affect state.
    fulls.append(None)  # type: ignore[arg-type]
    assert len(state_manager.get_full_backups("/mnt/backup/testvm")) == 3


def test_mock_state_record_incremental_dependency():
    """record_incremental_dependency records that an incremental depends on a
    FULL backup.  Duplicate additions are idempotent (no duplicates)."""
    state_manager = InMemoryStateManager()

    # Record a dependency.
    state_manager.record_incremental_dependency(
        "/mnt/backup/testvm", "testvm.INCR.20250115T120000.qcow2", "testvm.FULL.20250101.qcow2"
    )

    deps = state_manager.get_incremental_dependencies(
        "/mnt/backup/testvm", "testvm.FULL.20250101.qcow2"
    )
    assert deps == ["testvm.INCR.20250115T120000.qcow2"]

    # Record the same dependency again — idempotent, no duplicate.
    state_manager.record_incremental_dependency(
        "/mnt/backup/testvm", "testvm.INCR.20250115T120000.qcow2", "testvm.FULL.20250101.qcow2"
    )

    deps = state_manager.get_incremental_dependencies(
        "/mnt/backup/testvm", "testvm.FULL.20250101.qcow2"
    )
    assert deps == ["testvm.INCR.20250115T120000.qcow2"]

    # Add a second incremental depending on the same FULL.
    state_manager.record_incremental_dependency(
        "/mnt/backup/testvm", "testvm.INCR.20250116T120000.qcow2", "testvm.FULL.20250101.qcow2"
    )

    deps = state_manager.get_incremental_dependencies(
        "/mnt/backup/testvm", "testvm.FULL.20250101.qcow2"
    )
    assert len(deps) == 2
    assert "testvm.INCR.20250115T120000.qcow2" in deps
    assert "testvm.INCR.20250116T120000.qcow2" in deps


def test_mock_state_get_incremental_dependencies():
    """get_incremental_dependencies returns the list of incremental names
    that depend on a given full backup.  Unknown full_name → empty list.
    Unknown target_path → empty list."""
    state_manager = InMemoryStateManager()

    # Unknown target_path → empty list.
    deps = state_manager.get_incremental_dependencies("/mnt/backup/nonexistent", "some.FULL.qcow2")
    assert deps == []

    # Record a dependency, then query for the known full.
    state_manager.record_incremental_dependency("/mnt/backup/testvm", "incr1.qcow2", "full1.qcow2")

    # Known full → returns list.
    deps = state_manager.get_incremental_dependencies("/mnt/backup/testvm", "full1.qcow2")
    assert deps == ["incr1.qcow2"]

    # Unknown full (same target) → empty list.
    deps = state_manager.get_incremental_dependencies("/mnt/backup/testvm", "full2.qcow2")
    assert deps == []

    # get_incremental_dependencies returns a copy.
    deps.append("extra")
    assert state_manager.get_incremental_dependencies("/mnt/backup/testvm", "full1.qcow2") == [
        "incr1.qcow2"
    ]


def test_mock_state_multiple_fulls():
    """Multiple FULL backups are tracked per target.  get_last_full_backup
    returns the most recently recorded one (last in insertion order)."""
    state_manager = InMemoryStateManager()

    ts1 = datetime(2025, 1, 1, 0, 0, 0)
    ts2 = datetime(2025, 6, 1, 0, 0, 0)

    state_manager.record_full_backup(
        "/mnt/backup/testvm", "testvm.FULL.20250101.qcow2", ts1, "monthly"
    )
    state_manager.record_full_backup(
        "/mnt/backup/testvm", "testvm.FULL.20250601.qcow2", ts2, "monthly"
    )

    # get_full_backups returns both.
    fulls = state_manager.get_full_backups("/mnt/backup/testvm")
    assert len(fulls) == 2

    # get_last_full_backup returns the last one (most recent).
    last = state_manager.get_last_full_backup("/mnt/backup/testvm")
    assert last is not None
    assert last.name == "testvm.FULL.20250601.qcow2"
    assert last.timestamp == ts2

    # set_last_full_backup delegates to record_full_backup (bucket_level="monthly").
    ts3 = datetime(2025, 7, 1, 0, 0, 0)
    state_manager.set_last_full_backup("/mnt/backup/testvm", "testvm.FULL.20250701.qcow2", ts3)

    fulls = state_manager.get_full_backups("/mnt/backup/testvm")
    assert len(fulls) == 3
    assert fulls[2].name == "testvm.FULL.20250701.qcow2"
    assert fulls[2].bucket_level == "monthly"
