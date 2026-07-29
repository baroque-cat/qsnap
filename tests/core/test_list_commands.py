"""Tests for Core informational/listing commands.

Covers ``list_snapshots``, ``list_backups``, ``list_config``,
``list_latest``, ``print_schedule``, and ``check``.

These commands are read-only: they must not mutate state, execute
shell commands, or call lifecycle/backup deletion methods.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from qsnap.core import Core
from qsnap.models.results import (
    DeferredBlockcommit,
    RetentionResult,
    ScheduleResult,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import MockConfigFacade
from tests.helpers import add_deferred_with_since


# ── test_list_snapshots_returns_all_vms_sorted_ascending ──────────────────


def test_list_snapshots_returns_all_vms_sorted_ascending(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``list_snapshots()`` returns all VMs with snapshots sorted ascending."""
    vm1 = make_vm_config(name="vm1")
    vm2 = make_vm_config(name="vm2")
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for i, delta in enumerate([2, 0, 1]):
        snap = SnapshotInfo(
            name=f"vm1_snap{i}",
            path=Path(f"/tmp/vm1_snap{i}.qcow2"),
            timestamp=base + timedelta(hours=delta),
            allocation=1000 * (i + 1),
        )
        mock_state.record_snapshot("vm1", snap)
    for i, delta in enumerate([1, 0]):
        snap = SnapshotInfo(
            name=f"vm2_snap{i}",
            path=Path(f"/tmp/vm2_snap{i}.qcow2"),
            timestamp=base + timedelta(hours=delta),
            allocation=1000 * (i + 1),
        )
        mock_state.record_snapshot("vm2", snap)

    result = core.list_snapshots()

    assert set(result.keys()) == {"vm1", "vm2"}
    assert len(result["vm1"]) == 3
    assert len(result["vm2"]) == 2

    for snaps in result.values():
        timestamps = [s.timestamp for s in snaps]
        assert timestamps == sorted(timestamps)


# ── test_list_snapshots_filtered_vm_returns_only_matching ────────────────


def test_list_snapshots_filtered_vm_returns_only_matching(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``list_snapshots(vm_filter="vm1")`` returns only vm1's snapshots."""
    vm1 = make_vm_config(name="vm1")
    vm2 = make_vm_config(name="vm2")
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for name in ("vm1", "vm2"):
        snap = SnapshotInfo(
            name=f"{name}_snap0",
            path=Path(f"/tmp/{name}_snap0.qcow2"),
            timestamp=base,
            allocation=1000,
        )
        mock_state.record_snapshot(name, snap)

    result = core.list_snapshots(vm_filter="vm1")

    assert set(result.keys()) == {"vm1"}


# ── test_list_backups_returns_sorted_backup_infos ────────────────────────


def test_list_backups_returns_sorted_backup_infos(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``list_backups()`` returns backups sorted by timestamp ascending."""
    vm = make_vm_config(name="testvm", targets=[make_target()])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    backups = [
        SnapshotInfo(
            name="b3",
            path=Path("/mnt/backup/b3.qcow2"),
            timestamp=base + timedelta(hours=2),
            allocation=3000,
        ),
        SnapshotInfo(
            name="b1",
            path=Path("/mnt/backup/b1.qcow2"),
            timestamp=base,
            allocation=1000,
        ),
        SnapshotInfo(
            name="b2",
            path=Path("/mnt/backup/b2.qcow2"),
            timestamp=base + timedelta(hours=1),
            allocation=2000,
        ),
    ]

    with patch.object(mock_factory._backup_provider, "list", return_value=backups):
        result = core.list_backups()

    assert "testvm" in result
    assert len(result["testvm"]) == 3
    timestamps = [b.timestamp for b in result["testvm"]]
    assert timestamps == sorted(timestamps)


# ── test_list_backups_empty_when_no_backups_exist ─────────────────────────


def test_list_backups_empty_when_no_backups_exist(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``list_backups()`` returns an empty list per VM when no backups exist."""
    vm = make_vm_config(name="testvm", targets=[make_target()])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.list_backups()

    assert "testvm" in result
    assert result["testvm"] == []


# ── test_list_config_returns_all_vmconfigs_from_facade ────────────────────


def test_list_config_returns_all_vmconfigs_from_facade(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``list_config()`` returns all VMConfigs from the config facade."""
    vm1 = make_vm_config(name="vm1")
    vm2 = make_vm_config(name="vm2")
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.list_config()

    assert len(result) == 2
    names = {vm.name for vm in result}
    assert names == {"vm1", "vm2"}


# ── test_list_latest_returns_newest_snapshot_per_vm ──────────────────────


def test_list_latest_returns_newest_snapshot_per_vm(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``list_latest()`` returns the newest snapshot per VM."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(3):
        snap = SnapshotInfo(
            name=f"snap{i}",
            path=Path(f"/tmp/snap{i}.qcow2"),
            timestamp=base + timedelta(hours=i),
            allocation=1000 * (i + 1),
        )
        mock_state.record_snapshot("testvm", snap)

    result = core.list_latest()

    assert "testvm" in result
    latest = result["testvm"]
    assert latest is not None
    assert latest.name == "snap2"
    assert latest.timestamp == base + timedelta(hours=2)


# ── test_list_latest_returns_none_for_vm_without_snapshots ────────────────


def test_list_latest_returns_none_for_vm_without_snapshots(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``list_latest()`` returns ``None`` for a VM with no snapshots."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.list_latest()

    assert "testvm" in result
    assert result["testvm"] is None


# ── test_print_schedule_shows_keep_remove_counts ──────────────────────────


def test_print_schedule_shows_keep_remove_counts(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``print_schedule()`` returns a RetentionResult with keep and remove lists."""
    vm = make_vm_config(name="testvm", snapshot_chain_length=24)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    base = datetime(2025, 7, 13, 10, 0)
    for i in range(10):
        snap = SnapshotInfo(
            name=f"snap{i}",
            path=Path(f"/tmp/snap{i}.qcow2"),
            timestamp=base + timedelta(hours=i),
            allocation=1000 * (i + 1),
        )
        mock_state.record_snapshot("testvm", snap)

    keep_list = [f"snap{i}" for i in range(7)]
    remove_list = [f"snap{i}" for i in range(7, 10)]
    with patch.object(
        mock_factory._retention_engine,
        "evaluate",
        return_value=RetentionResult(keep=keep_list, remove=remove_list),
    ):
        result = core.print_schedule()

    assert "testvm" in result
    assert len(result["testvm"].snapshots.keep) == 7
    assert len(result["testvm"].snapshots.remove) == 3


# ── test_print_schedule_does_not_call_mutating_shell_commands ─────────────


def test_print_schedule_does_not_call_mutating_shell_commands(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``print_schedule()`` must not call any shell commands."""
    vm = make_vm_config(name="testvm", snapshot_chain_length=24)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        core.print_schedule()

    shell_spy.assert_not_called()


# ── test_print_schedule_with_vm_filter_shows_keep_remove ─────────────────


def test_print_schedule_with_vm_filter_shows_keep_remove(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``print_schedule(vm_filter="vm1")`` returns only vm1's result."""
    vm1 = make_vm_config(name="vm1", snapshot_chain_length=24)
    vm2 = make_vm_config(name="vm2", snapshot_chain_length=24)
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for name in ("vm1", "vm2"):
        snap = SnapshotInfo(
            name=f"{name}_snap1",
            path=Path(f"/tmp/{name}_snap1.qcow2"),
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=1000,
        )
        mock_state.record_snapshot(name, snap)

    result = core.print_schedule(vm_filter="vm1")

    assert set(result.keys()) == {"vm1"}


# ── test_print_schedule_does_not_execute_mutating_commands ───────────────


def test_print_schedule_does_not_execute_mutating_commands(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``print_schedule()`` must not call blockcommit or backup delete."""
    vm = make_vm_config(name="testvm", snapshot_chain_length=24)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    with (
        patch.object(
            mock_factory._lifecycle_manager,
            "blockcommit",
            wraps=mock_factory._lifecycle_manager.blockcommit,
        ) as bc_spy,
        patch.object(
            mock_factory._backup_provider,
            "delete",
            wraps=mock_factory._backup_provider.delete,
        ) as del_spy,
    ):
        core.print_schedule()

    bc_spy.assert_not_called()
    del_spy.assert_not_called()


# ── test_check_healthy_backing_chain_reports_ok ───────────────────────────


def test_check_healthy_backing_chain_reports_ok(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """``check()`` reports ``"ok"`` when qemu-img succeeds for all snapshots."""
    snap_path = tmp_path / "snap1.qcow2"
    snap_path.write_text("")  # create real file so os.path.exists passes

    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=snap_path,
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    # Mock virsh domblklist → active layer is snap1
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=f"Target   Source\n--------------------------------\nvda   {snap_path}\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Mock qemu-img --backing-chain → valid JSON chain
    chain_json = json.dumps(
        [
            {
                "filename": str(snap_path),
                "format": "qcow2",
                "virtual-size": 10737418240,
                "actual-size": 200704,
            }
        ]
    )
    mock_shell.expect_first("--backing-chain").returns(
        ShellResult(
            success=True,
            stdout=chain_json,
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Mock virsh dumpxml → references the snapshot path
    dumpxml = (
        '<domain type="kvm"><devices><disk>'
        f'<source file="{snap_path}"/>'
        "</disk></devices></domain>"
    )
    mock_shell.expect_first("virsh dumpxml").returns(
        ShellResult(
            success=True,
            stdout=dumpxml,
            stderr="",
            returncode=0,
            error=None,
        )
    )

    result = core.check()

    assert "testvm" in result
    assert result["testvm"].status == "ok"
    assert result["testvm"].broken_snapshots == []


# ── test_check_broken_chain_reports_broken_status ────────────────────────


def test_check_broken_chain_reports_broken_status(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``check()`` reports ``"broken"`` when qemu-img fails."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    # Mock virsh domblklist → active layer is snap1.qcow2 (matches state)
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /tmp/snap1.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Override the conftest default for --backing-chain (which returns
    # valid JSON).  Use expect_first so this broken-chain result takes
    # priority over the conftest default.
    mock_shell.expect_first("--backing-chain").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error",
            returncode=1,
            error="backing file not found",
        )
    )

    result = core.check()

    assert "testvm" in result
    assert result["testvm"].status == "broken"
    assert "snap1.qcow2" in result["testvm"].broken_snapshots


# ── test_check_filtered_vm ───────────────────────────────────────────────


def test_check_filtered_vm(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``check(vm_filter="vm1")`` returns only vm1's result."""
    vm1 = make_vm_config(name="vm1")
    vm2 = make_vm_config(name="vm2")
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for name in ("vm1", "vm2"):
        snap = SnapshotInfo(
            name=f"{name}_snap1",
            path=Path(f"/tmp/{name}_snap1.qcow2"),
            timestamp=datetime(2025, 7, 13, 10, 0),
            allocation=1000,
        )
        mock_state.record_snapshot(name, snap)

    mock_shell.expect("qemu-img").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    result = core.check(vm_filter="vm1")

    assert set(result.keys()) == {"vm1"}


# ── test_print_schedule_shows_snapshot_and_backup_retention ──────────────


def test_print_schedule_shows_snapshot_and_backup_retention(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """print_schedule returns ScheduleResult with both .snapshots and .backups keys."""
    vm = make_vm_config(
        name="testvm",
        snapshot_chain_length=24,
        targets=[make_target(target_chain_length=24)],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    backup = SnapshotInfo(
        name="backup1",
        path=Path("/mnt/backup/backup1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )

    with patch.object(mock_factory._backup_provider, "list", return_value=[backup]):
        result = core.print_schedule()

    assert "testvm" in result
    schedule = result["testvm"]
    assert isinstance(schedule, ScheduleResult)

    # Both snapshot and backup retention are evaluated
    assert isinstance(schedule.snapshots, RetentionResult)
    assert len(schedule.snapshots.keep) > 0

    assert isinstance(schedule.backups, dict)
    assert len(schedule.backups) > 0
    target_key = str(vm.targets[0].path)
    assert target_key in schedule.backups
    assert isinstance(schedule.backups[target_key], RetentionResult)
    assert len(schedule.backups[target_key].keep) > 0


# ── test_check_deep_finds_corruption_reports_broken ──────────────────────


def test_check_deep_finds_corruption_reports_broken(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """check(deep=True) runs qemu-img check, finds corruptions>0, reports 'corrupted'."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    mock_shell.expect("qemu-img.*check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions": 1, "errors": 0, "leaks": 0}',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    result = core.check(deep=True)

    assert "testvm" in result
    assert result["testvm"].status == "corrupted"
    assert "snap1" in result["testvm"].broken_snapshots


# ── test_check_deep_clean_image_reports_ok ────────────────────────────────


def test_check_deep_clean_image_reports_ok(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """check(deep=True) runs qemu-img check, finds 0 corruptions/errors/leaks, reports 'ok'."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    # Override domblklist so it matches the test snapshot path (avoids
    # false-positive active layer mismatch from triple-source check).
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /tmp/snap1.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Mock qemu-img info --backing-chain for the triple-source chain check
    # so the file is found and no chain break is reported.
    mock_shell.expect("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                [{"filename": "/tmp/snap1.qcow2", "format": "qcow2"}]
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    mock_shell.expect("qemu-img.*check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions": 0, "errors": 0, "leaks": 0}',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with patch("os.path.exists", return_value=True):
        result = core.check(deep=True)

    assert "testvm" in result
    assert result["testvm"].status == "ok"
    assert result["testvm"].broken_snapshots == []


# ── test_check_deep_errors_detected ──────────────────────────────────────


def test_check_deep_errors_detected(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``check(deep=True)`` detects ``errors > 0`` even when corruptions=0 and leaks=0.

    The ``_deep_check_file`` method checks ``corruptions``, ``errors``,
    AND ``leaks``.  When only ``errors > 0``, the status is ``"corrupted"``.
    """
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    mock_shell.expect("qemu-img.*check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions": 0, "errors": 2, "leaks": 0}',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    result = core.check(deep=True)

    assert "testvm" in result
    assert result["testvm"].status == "corrupted"
    assert "snap1" in result["testvm"].broken_snapshots


# ── test_check_deep_leaks_detected ───────────────────────────────────────


def test_check_deep_leaks_detected(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``check(deep=True)`` detects ``leaks > 0`` even when corruptions=0 and errors=0.

    ``_deep_check_file`` checks all three fields.  When only ``leaks > 0``,
    the status is ``"corrupted"``.
    """
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    mock_shell.expect("qemu-img.*check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions": 0, "errors": 0, "leaks": 5}',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    result = core.check(deep=True)

    assert "testvm" in result
    assert result["testvm"].status == "corrupted"
    assert "snap1" in result["testvm"].broken_snapshots


# ── test_check_deep_image_unreadable ──────────────────────────────────────


def test_check_deep_image_unreadable(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``check(deep=True)`` reports ``"broken"`` when ``qemu-img check`` fails
    (image unreadable / cannot open)."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/tmp/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1000,
    )
    mock_state.record_snapshot("testvm", snap)

    mock_shell.expect("qemu-img.*check").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="Could not open '/tmp/snap1.qcow2'",
            returncode=1,
            error="qemu-img: Could not open",
        )
    )

    result = core.check(deep=True)

    assert "testvm" in result
    assert result["testvm"].status == "broken"
    assert "snap1" in result["testvm"].broken_snapshots


# ── test_list_deferred_returns_all_vm_summaries ───────────────────────────


def test_list_deferred_returns_all_vm_summaries(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``list_deferred()`` returns a summary per VM with deferred operations."""
    vm1 = make_vm_config(name="vm1", disks=["vda"])
    vm2 = make_vm_config(name="vm2", disks=["vda"])
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_state.add_deferred_blockcommit("vm1", ["snap1"], "apparmor")
    mock_state.add_deferred_blockcommit("vm2", ["snap2"], "selinux")

    result = core.list_deferred()

    assert len(result) == 2
    names = {s.vm_name for s in result}
    assert names == {"vm1", "vm2"}
    for s in result:
        assert s.snapshot_count == 1
        assert s.reason in ("apparmor", "selinux")
        assert isinstance(s.age, timedelta)
        assert isinstance(s.since, datetime)


# ── test_list_deferred_with_vm_filter ─────────────────────────────────────


def test_list_deferred_with_vm_filter(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``list_deferred(vm_filter="vm1")`` returns only vm1's summary."""
    vm1 = make_vm_config(name="vm1", disks=["vda"])
    vm2 = make_vm_config(name="vm2", disks=["vda"])
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_state.add_deferred_blockcommit("vm1", ["snap1"], "apparmor")
    mock_state.add_deferred_blockcommit("vm2", ["snap2"], "selinux")

    result = core.list_deferred(vm_filter="vm1")

    assert len(result) == 1
    assert result[0].vm_name == "vm1"


# ── test_list_deferred_no_deferred_operations ──────────────────────────────


def test_list_deferred_no_deferred_operations(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``list_deferred()`` returns an empty list when no deferred ops exist."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.list_deferred()

    assert result == []


# ── test_list_deferred_filtered_by_vm_name ────────────────────────────────


def test_list_deferred_filtered_by_vm_name(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``list_deferred(vm_filter="vm2")`` returns only vm2's summary."""
    vm1 = make_vm_config(name="vm1", disks=["vda"])
    vm2 = make_vm_config(name="vm2", disks=["vda"])
    vm3 = make_vm_config(name="vm3", disks=["vda"])
    config = MockConfigFacade(vms=[vm1, vm2, vm3])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_state.add_deferred_blockcommit("vm1", ["snap1"], "apparmor")
    mock_state.add_deferred_blockcommit("vm2", ["snap2"], "selinux")
    mock_state.add_deferred_blockcommit("vm3", ["snap3"], "apparmor")

    result = core.list_deferred(vm_filter="vm2")

    assert len(result) == 1
    assert result[0].vm_name == "vm2"
    assert result[0].reason == "selinux"


# ── test_list_deferred_returns_per_vm_summaries ───────────────────────────


def test_list_deferred_returns_per_vm_summaries(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    frozen_clock,
):
    """``list_deferred()`` summary fields: vm_name, snapshot_count, reason,
    age, since — all populated correctly."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    frozen_dt = datetime(2025, 7, 13, 15, 31)
    since = frozen_dt - timedelta(hours=3)
    add_deferred_with_since(mock_state, "testvm", ["snap1", "snap2"], "apparmor", since)

    with frozen_clock(frozen_dt):
        result = core.list_deferred()

    assert len(result) == 1
    summary = result[0]
    assert summary.vm_name == "testvm"
    assert summary.snapshot_count == 2
    assert summary.reason == "apparmor"
    assert summary.age == timedelta(hours=3)
    assert summary.since == since


# ── test_check_deferred_apparmor_remediation ───────────────────────────────


def test_check_deferred_apparmor_remediation(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``check()`` with apparmor-deferred ops → remediation mentions AppArmor."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(5):
        mock_state.add_deferred_blockcommit("testvm", [f"snap{i}"], "apparmor")

    result = core.check()

    assert result["testvm"].deferred_count == 5
    assert result["testvm"].deferred_severity == "warning"
    assert result["testvm"].remediation is not None
    assert "AppArmor" in result["testvm"].remediation


# ── test_check_deferred_selinux_remediation ────────────────────────────────


def test_check_deferred_selinux_remediation(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``check()`` with selinux-deferred ops → remediation mentions SELinux."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    for i in range(5):
        mock_state.add_deferred_blockcommit("testvm", [f"snap{i}"], "selinux")

    result = core.check()

    assert result["testvm"].deferred_count == 5
    assert result["testvm"].deferred_severity == "warning"
    assert result["testvm"].remediation is not None
    assert "SELinux" in result["testvm"].remediation


# ── test_check_healthy_vm_no_remediation ──────────────────────────────────


def test_check_healthy_vm_no_remediation(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``check()`` with no deferred ops → deferred_count=0, remediation=None."""
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.check()

    assert result["testvm"].deferred_count == 0
    assert result["testvm"].remediation is None
