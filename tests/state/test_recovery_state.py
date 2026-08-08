"""Unit tests for crash-evidence state fields (recover-lost-checkpoint-bitmaps).

Covers the additive optional per-VM state fields ``boot_id`` (host boot
identifier) and per-disk ``last_commit_ts`` (timestamp of the most recent
successful blockcommit / ``qemu-img commit``) in both concrete
``IStateManager`` implementations (state-management spec).

Semantics under test:

- ``boot_id`` persists per VM and a change (host restart) is observable.
- Absence of either field (pre-feature state files, first run) is
  "unknown": readers receive ``None``, never an exception.  No migration
  of existing state files is required.
- ``last_commit_ts`` round-trips per (vm, disk) independently.
- Legacy state files without the new fields load fine, and reads never
  rewrite the file (additive optional fields only).
- ``reset_vm_state`` / ``reset_vm_disk_state`` / ``reset_target_disk_state``
  leave the new fields coherent.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.interfaces.state import IStateManager
from qsnap.models.results import SnapshotInfo
from qsnap.state.json_manager import JsonStateManager
from tests.mocks.mock_state import InMemoryStateManager

STATE_MANAGER_CLASSES = [JsonStateManager, InMemoryStateManager]


def _make_state_manager(mgr_cls, tmp_path) -> IStateManager:
    """Construct the manager under test (mock parity convention)."""
    if mgr_cls is JsonStateManager:
        return mgr_cls(state_dir=tmp_path)
    return mgr_cls()


# ── boot_id: persistence and change across a crash ────────────────────────


@pytest.mark.parametrize("mgr_cls", STATE_MANAGER_CLASSES)
def test_boot_id_change_detected_across_crash(mgr_cls, tmp_path) -> None:
    """A boot_id change is observable: state holds A, current host is B.

    ``set_boot_id`` persists the identifier; after the host restarts
    (simulated by writing a different boot_id), readers observe the new
    value and can conclude the host restarted since the last successful
    run (state-management scenario "Boot id change detected across a
    crash").
    """
    manager = _make_state_manager(mgr_cls, tmp_path)

    # First successful run records boot-A.
    manager.set_boot_id("testvm", "boot-A")
    assert manager.get_boot_id("testvm") == "boot-A"

    # Host restarts — next run records boot-B.
    manager.set_boot_id("testvm", "boot-B")

    recorded = manager.get_boot_id("testvm")
    assert recorded == "boot-B"
    assert recorded != "boot-A", "consumers must be able to detect the change"


@pytest.mark.parametrize("mgr_cls", STATE_MANAGER_CLASSES)
def test_boot_id_round_trips_persistently(mgr_cls, tmp_path) -> None:
    """boot_id survives across manager instances for JsonStateManager.

    Mock parity: the in-memory manager holds the same value for the
    lifetime of the object.  The file-backed manager must persist the
    field so a fresh instance reads the same boot_id.
    """
    manager = _make_state_manager(mgr_cls, tmp_path)
    manager.set_boot_id("testvm", "boot-A")
    assert manager.get_boot_id("testvm") == "boot-A"

    if mgr_cls is not JsonStateManager:
        return

    # Fresh instance over the same state directory (JSON) — value survives.
    reloaded = _make_state_manager(mgr_cls, tmp_path)
    assert reloaded.get_boot_id("testvm") == "boot-A"

    # The field is serialized as an optional key in the per-VM JSON file.
    state_file = tmp_path / "testvm.json"
    assert state_file.exists()
    with open(state_file, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data.get("boot_id") == "boot-A"


@pytest.mark.parametrize("mgr_cls", STATE_MANAGER_CLASSES)
def test_missing_boot_id_returns_none_not_error(mgr_cls, tmp_path) -> None:
    """Absent boot_id (pre-feature state, first run) reads as None.

    No exception, no default value, no migration — ``None`` means
    "unknown" for crash-evidence wording (state-management scenario
    "Missing boot id is unknown, not an error").
    """
    manager = _make_state_manager(mgr_cls, tmp_path)

    # VM with no state file at all.
    assert manager.get_boot_id("nonexistent_vm") is None

    # VM with state, but no boot_id recorded (pre-feature file).
    manager.set_last_allocation("testvm", "vda", 4096)
    assert manager.get_boot_id("testvm") is None

    # Still no exception and no file-side effect from the read.
    if mgr_cls is JsonStateManager:
        assert (tmp_path / "nonexistent_vm.json").exists() is False


# ── last_commit_ts: per-disk marker round-trip ────────────────────────────


@pytest.mark.parametrize("mgr_cls", STATE_MANAGER_CLASSES)
def test_last_commit_ts_persistence(mgr_cls, tmp_path) -> None:
    """Per-disk last_commit_ts markers round-trip independently.

    ``set_last_commit_ts(vm, disk, ts)`` then
    ``get_last_commit_ts(vm, disk)`` returns the marker.  Markers for
    different disks of the same VM are independent (state-management
    scenario "Marker written after successful blockcommit").
    """
    manager = _make_state_manager(mgr_cls, tmp_path)
    marker_vda = "20260808T160000"
    marker_vdb = "20260808T170000"

    manager.set_last_commit_ts("testvm", "vda", marker_vda)
    manager.set_last_commit_ts("testvm", "vdb", marker_vdb)

    assert manager.get_last_commit_ts("testvm", "vda") == marker_vda
    assert manager.get_last_commit_ts("testvm", "vdb") == marker_vdb

    # Per-disk independence: overwriting vda leaves vdb untouched.
    manager.set_last_commit_ts("testvm", "vda", "20260809T000000")
    assert manager.get_last_commit_ts("testvm", "vda") == "20260809T000000"
    assert manager.get_last_commit_ts("testvm", "vdb") == marker_vdb

    # Disks with no recorded commit read as None (absent marker is
    # conservative — gate G1 treats unknown as failed).
    assert manager.get_last_commit_ts("testvm", "vdc") is None
    assert manager.get_last_commit_ts("othervm", "vda") is None


@pytest.mark.parametrize("mgr_cls", STATE_MANAGER_CLASSES)
def test_last_commit_ts_round_trips_persistently(mgr_cls, tmp_path) -> None:
    """last_commit_ts survives across manager instances (JSON round-trip)."""
    manager = _make_state_manager(mgr_cls, tmp_path)
    manager.set_last_commit_ts("testvm", "vda", "20260808T160000")
    manager.set_last_commit_ts("testvm", "vdb", "20260808T170000")
    assert manager.get_last_commit_ts("testvm", "vda") == "20260808T160000"
    assert manager.get_last_commit_ts("testvm", "vdb") == "20260808T170000"

    if mgr_cls is not JsonStateManager:
        return

    reloaded = _make_state_manager(mgr_cls, tmp_path)
    assert reloaded.get_last_commit_ts("testvm", "vda") == "20260808T160000"
    assert reloaded.get_last_commit_ts("testvm", "vdb") == "20260808T170000"

    state_file = tmp_path / "testvm.json"
    with open(state_file, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data.get("last_commit_ts") == {
        "vda": "20260808T160000",
        "vdb": "20260808T170000",
    }


# ── legacy state files: additive optional fields, no migration ────────────


def test_legacy_state_files_load_without_new_fields(tmp_path: Path) -> None:
    """A pre-feature state file (no boot_id / last_commit_ts) loads fine.

    The new fields are additive and optional: readers receive ``None``,
    no migration is required, and the read path must not rewrite the
    file (state-management spec: "No migration of existing state files
    SHALL be required").
    """
    state_file = tmp_path / "testvm.json"
    legacy_data = {
        "last_allocation": {"vda": 4096},
        "snapshots": [],
        "deferred_operations": [],
    }
    state_file.write_text(json.dumps(legacy_data), encoding="utf-8")

    with open(state_file, encoding="utf-8") as fh:
        original_raw = fh.read()

    manager = JsonStateManager(state_dir=tmp_path)

    # Absent fields read as None — never an error.
    assert manager.get_boot_id("testvm") is None
    assert manager.get_last_commit_ts("testvm", "vda") is None

    # Existing legacy fields still work.
    assert manager.get_last_allocation("testvm", "vda") == 4096

    # Reads must not rewrite the legacy file (no migration pass).
    with open(state_file, encoding="utf-8") as fh:
        after_raw = fh.read()
    assert after_raw == original_raw, "reading a legacy state file must not rewrite it"


@pytest.mark.parametrize("mgr_cls", STATE_MANAGER_CLASSES)
def test_setting_new_fields_on_legacy_state_is_additive(mgr_cls, tmp_path) -> None:
    """Writing the new fields onto legacy state keeps all legacy fields."""
    manager = _make_state_manager(mgr_cls, tmp_path)
    manager.set_last_allocation("testvm", "vda", 4096)
    manager.record_snapshot(
        "testvm",
        SnapshotInfo(
            name="testvm.20260101T000000_vda_a1b2c3",
            path=Path("/tmp/snap.qcow2"),
            timestamp=datetime(2026, 1, 1, 0, 0, 0),
            allocation=1024,
            disk="vda",
        ),
    )

    manager.set_boot_id("testvm", "boot-A")
    manager.set_last_commit_ts("testvm", "vda", "20260808T160000")

    # New fields readable on the writing instance.
    assert manager.get_boot_id("testvm") == "boot-A"
    assert manager.get_last_commit_ts("testvm", "vda") == "20260808T160000"
    # Legacy fields untouched.
    assert manager.get_last_allocation("testvm", "vda") == 4096
    assert len(manager.get_snapshots("testvm")) == 1

    if mgr_cls is not JsonStateManager:
        return

    # File-backed: the combined state survives a reload.
    reloaded = _make_state_manager(mgr_cls, tmp_path)
    assert reloaded.get_boot_id("testvm") == "boot-A"
    assert reloaded.get_last_commit_ts("testvm", "vda") == "20260808T160000"
    assert reloaded.get_last_allocation("testvm", "vda") == 4096
    assert len(reloaded.get_snapshots("testvm")) == 1


# ── reset coherence: new fields survive state resets ──────────────────────


@pytest.mark.parametrize("mgr_cls", STATE_MANAGER_CLASSES)
def test_reset_vm_state_leaves_new_fields_coherent(mgr_cls, tmp_path) -> None:
    """reset_vm_state preserves boot_id and last_commit_ts structure.

    The reset clears the legacy per-VM state (snapshots,
    last_allocation, deferred_operations) but must leave the
    crash-evidence fields coherent: ``boot_id`` (host-level evidence,
    not disk/VM-restore related) and the per-disk ``last_commit_ts``
    marker map stay readable and intact (state-management spec:
    additive optional fields).
    """
    manager = _make_state_manager(mgr_cls, tmp_path)

    manager.set_boot_id("testvm", "boot-A")
    manager.set_last_commit_ts("testvm", "vda", "20260808T160000")
    manager.set_last_commit_ts("testvm", "vdb", "20260808T170000")
    manager.set_last_allocation("testvm", "vda", 4096)
    manager.add_deferred_blockcommit("testvm", "vda", ["snap.qcow2"], "apparmor")

    manager.reset_vm_state("testvm")

    # New fields are coherent and preserved.
    assert manager.get_boot_id("testvm") == "boot-A"
    assert manager.get_last_commit_ts("testvm", "vda") == "20260808T160000"
    assert manager.get_last_commit_ts("testvm", "vdb") == "20260808T170000"

    # Legacy per-VM state was cleared.
    assert manager.get_last_allocation("testvm", "vda") is None
    assert manager.get_deferred_operations("testvm") == []

    # The on-disk structure remains valid JSON with the new fields.
    if mgr_cls is JsonStateManager:
        with open(tmp_path / "testvm.json", encoding="utf-8") as fh:
            data = json.load(fh)
        assert data.get("boot_id") == "boot-A"
        assert data.get("last_commit_ts") == {
            "vda": "20260808T160000",
            "vdb": "20260808T170000",
        }


@pytest.mark.parametrize("mgr_cls", STATE_MANAGER_CLASSES)
def test_reset_vm_disk_state_keeps_new_fields_coherent(mgr_cls, tmp_path) -> None:
    """reset_vm_disk_state leaves boot_id and last_commit_ts readable.

    Per-disk reset removes the disk's snapshots/allocation/deferred
    state; the crash-evidence fields (host boot_id and the per-disk
    commit markers) must remain structurally intact and readable for
    every disk (state-management spec: additive optional fields).
    """
    manager = _make_state_manager(mgr_cls, tmp_path)

    manager.set_boot_id("testvm", "boot-A")
    manager.set_last_commit_ts("testvm", "vda", "20260808T160000")
    manager.set_last_commit_ts("testvm", "vdb", "20260808T170000")
    manager.set_last_allocation("testvm", "vda", 1000)
    manager.set_last_allocation("testvm", "vdb", 2000)

    manager.reset_vm_disk_state("testvm", "vda")

    # boot_id (host evidence) untouched.
    assert manager.get_boot_id("testvm") == "boot-A"

    # last_commit_ts markers remain readable for both disks.
    assert manager.get_last_commit_ts("testvm", "vda") == "20260808T160000"
    assert manager.get_last_commit_ts("testvm", "vdb") == "20260808T170000"

    # Legacy per-disk state was cleared for the reset disk only.
    assert manager.get_last_allocation("testvm", "vda") is None
    assert manager.get_last_allocation("testvm", "vdb") == 2000


@pytest.mark.parametrize("mgr_cls", STATE_MANAGER_CLASSES)
def test_reset_target_disk_state_does_not_touch_vm_new_fields(mgr_cls, tmp_path) -> None:
    """reset_target_disk_state leaves per-VM boot_id/last_commit_ts alone.

    Target resets operate on the per-target files
    (``_full_backups.json``, ``_dependencies.json``,
    ``_target_state.json``); the per-VM crash-evidence fields must not
    be affected.
    """
    manager = _make_state_manager(mgr_cls, tmp_path)

    manager.set_boot_id("testvm", "boot-A")
    manager.set_last_commit_ts("testvm", "vda", "20260808T160000")
    manager.record_full_backup(
        "/mnt/backup/shared",
        "testvm.FULL.20260808T030000_vda_a1b2c3.qcow2",
        datetime(2026, 8, 8, 3, 0, 0),
        "vda",
    )

    manager.reset_target_disk_state("/mnt/backup/shared", "testvm", "vda")

    # Per-VM crash-evidence fields are untouched.
    assert manager.get_boot_id("testvm") == "boot-A"
    assert manager.get_last_commit_ts("testvm", "vda") == "20260808T160000"
