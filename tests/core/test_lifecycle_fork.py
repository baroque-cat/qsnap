"""Tests for the adaptive lifecycle fork (design D2, D5).

Covers:
- _plan_blockcommit fork matrix: running+virsh, running+qemu-img,
  shut-off (offline), domstate failure fallback.
- Active layer detection via virsh domblklist (and fallback).
- Race guard: re-check domstate before qemu-img commit.
- Unconditional state.remove_snapshot() after successful commit (D5).
- Sequential domstate responses for the race guard tests use
  ``unittest.mock.patch.object`` on ``MockShell.run`` with a
  ``side_effect`` counter, delegating non-domstate commands to the
  original ``MockShell.run``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qsnap.core import Core
from qsnap.models.results import (
    RetentionResult,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import MockConfigFacade

# ── Helpers ────────────────────────────────────────────────────────────────

_SNAP_DIR = "/var/lib/libvirt/snapshots/testvm"


def _add_two_snapshots(state, vm_name: str = "testvm") -> tuple[SnapshotInfo, SnapshotInfo]:
    """Add snap1 (older) and snap2 (newer) to in-memory state."""
    snap1 = SnapshotInfo(
        name="snap1",
        path=Path(f"{_SNAP_DIR}/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1048576,

        disk="vda",
    )
    snap2 = SnapshotInfo(
        name="snap2",
        path=Path(f"{_SNAP_DIR}/snap2.qcow2"),
        timestamp=datetime(2025, 7, 13, 14, 0),
        allocation=2097152,

        disk="vda",
    )
    state.record_snapshot(vm_name, snap1)
    state.record_snapshot(vm_name, snap2)
    return snap1, snap2


_DOMBLKLIST_TEMPLATE = (
    "Target     Source\n----------------------------------------\nvda        {path}\n"
)


def _domblklist_for(path: str) -> str:
    return _DOMBLKLIST_TEMPLATE.format(path=path)


# ── Test 1: running + lifecycle_mode="virsh" → split ──────────────────────


def test_blockcommit_active_layer_deferred_when_running(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """When VM is running and lifecycle_mode="virsh", the active overlay
    (domblklist source) is deferred with ``"vm_running"``, while older
    non-active snapshots are committed live via virsh blockcommit.
    """
    caplog.set_level(logging.INFO)

    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir=_SNAP_DIR,
        lifecycle_mode="virsh",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap1, snap2 = _add_two_snapshots(mock_state, "testvm")

    # VM is running
    mock_shell.expect_first("virsh domstate").returns(
        ShellResult(success=True, stdout="running\n", stderr="", returncode=0, error=None)
    )
    # domblklist reports snap2 as the active layer
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_domblklist_for(str(snap2.path)),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Support _get_chain_length (called before commit)
    mock_shell.expect(r"qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout="[]", stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=[], remove=["snap1", "snap2"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
        patch.object(
            mock_factory, "create_lifecycle_manager", wraps=mock_factory.create_lifecycle_manager
        ) as clm_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Factory called with virsh mode
    clm_spy.assert_called_once()
    assert clm_spy.call_args[1]["mode"] == "virsh"

    # blockcommit invoked with only the older (non-active) snapshot
    bc_spy.assert_called_once()
    committed = bc_spy.call_args[0][1]  # second positional arg: snapshots_to_merge
    assert len(committed) == 1
    assert committed[0].name == "snap1"

    # Active layer deferred with "vm_running"
    deferred = mock_state.get_deferred_operations("testvm")
    assert len(deferred) == 1
    assert deferred[0].snapshots == ["snap2"]
    assert deferred[0].reason == "vm_running"

    # snap1 removed from state; snap2 still present (deferred, not committed)
    remaining = mock_state.get_snapshots("testvm")
    remaining_names = [s.name for s in remaining]
    assert "snap1" not in remaining_names
    assert "snap2" in remaining_names

    # INFO log records the split
    log_messages = [r.message for r in caplog.records]
    assert any("Deferring blockcommit" in msg and "snap2" in msg for msg in log_messages), (
        "Expected a log message about deferring snap2"
    )


# ── Test 2: lifecycle_mode="qemu-img" + running → defer all ──────────────


def test_blockcommit_qemu_img_mode_defers_when_running(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When lifecycle_mode="qemu-img" and VM is running, NO lifecycle
    manager is invoked and the entire remove set is deferred with
    ``"vm_running"``.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir=_SNAP_DIR,
        lifecycle_mode="qemu-img",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap1, snap2 = _add_two_snapshots(mock_state, "testvm")

    # VM is running
    mock_shell.expect_first("virsh domstate").returns(
        ShellResult(success=True, stdout="running\n", stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=[], remove=["snap1", "snap2"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
        patch.object(
            mock_factory, "create_lifecycle_manager", wraps=mock_factory.create_lifecycle_manager
        ) as clm_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Manager was never created — committable was empty
    clm_spy.assert_not_called()
    bc_spy.assert_not_called()

    # Entire remove set deferred with "vm_running"
    deferred = mock_state.get_deferred_operations("testvm")
    assert len(deferred) == 1
    assert sorted(deferred[0].snapshots) == ["snap1", "snap2"]
    assert deferred[0].reason == "vm_running"


# ── Test 3: shut off → XML tip deferred, older committed via qemu-img ────


def test_blockcommit_xml_tip_deferred_active_layer(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """When VM is shut off, the domblklist-source tip is deferred with
    ``"active_layer"`` while older non-tip snapshots are committed via
    ``mode="qemu-img"``.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir=_SNAP_DIR,
        lifecycle_mode="virsh",  # mode doesn't matter when shut off
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap1, snap2 = _add_two_snapshots(mock_state, "testvm")

    # VM is shut off (conftest default, but make explicit via expect_first)
    mock_shell.expect_first("virsh domstate").returns(
        ShellResult(success=True, stdout="shut off\n", stderr="", returncode=0, error=None)
    )
    # domblklist reports snap2 as the XML-referenced tip
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_domblklist_for(str(snap2.path)),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Support _get_chain_length + race guard re-check uses conftest default ("shut off")
    mock_shell.expect(r"qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout="[]", stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=[], remove=["snap1", "snap2"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
        patch.object(
            mock_factory, "create_lifecycle_manager", wraps=mock_factory.create_lifecycle_manager
        ) as clm_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Factory called with qemu-img mode (offline path)
    clm_spy.assert_called_once()
    assert clm_spy.call_args[1]["mode"] == "qemu-img"

    # blockcommit called with only the older snapshot (tip excluded)
    bc_spy.assert_called_once()
    committed = bc_spy.call_args[0][1]
    assert len(committed) == 1
    assert committed[0].name == "snap1"

    # Tip deferred with "active_layer"
    deferred = mock_state.get_deferred_operations("testvm")
    assert len(deferred) == 1
    assert deferred[0].snapshots == ["snap2"]
    assert deferred[0].reason == "active_layer"

    # snap1 removed from state; snap2 still present (deferred)
    remaining = mock_state.get_snapshots("testvm")
    remaining_names = [s.name for s in remaining]
    assert "snap1" not in remaining_names
    assert "snap2" in remaining_names


# ── Test 4: race guard defers committable when VM starts between checks ───


def test_blockcommit_race_guard_defers_when_vm_started(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Race guard: the plan selects qemu-img (first domstate → "shut off"),
    but the immediate re-check returns "running".  The manager is NOT invoked
    and the committable set is deferred with ``"vm_running"``.

    Implemented by patching ``MockShell.run`` with a side_effect that returns
    "shut off" for the first ``domstate`` call and "running" for the second,
    delegating all other commands to the original ``MockShell.run``.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir=_SNAP_DIR,
        lifecycle_mode="virsh",  # irrelevant when shut off (plan picks qemu-img)
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap1, snap2 = _add_two_snapshots(mock_state, "testvm")
    # Only snap1 is in the remove set; snap2 is the XML tip (domblklist source)
    # and is excluded from committable by the plan but NOT in the remove set,
    # so it is never deferred.
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_domblklist_for(str(snap2.path)),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Support _get_chain_length
    mock_shell.expect(r"qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout="[]", stderr="", returncode=0, error=None)
    )

    retention = RetentionResult(keep=["snap2"], remove=["snap1"])

    # --- Sequential domstate responses ---
    original_run = mock_shell.run
    domstate_count = [0]

    def _patched_run(cmd, timeout, check=False):
        cmd_str = " ".join(cmd)
        if "domstate" in cmd_str:
            domstate_count[0] += 1
            if domstate_count[0] == 1:
                # plan: VM shut off → qemu-img mode
                return ShellResult(
                    success=True, stdout="shut off\n", stderr="", returncode=0, error=None
                )
            else:
                # race guard re-check: VM started → defer
                return ShellResult(
                    success=True, stdout="running\n", stderr="", returncode=0, error=None
                )
        return original_run(cmd, timeout, check)

    manager = mock_factory._lifecycle_manager
    with (
        patch("os.path.exists", return_value=True),
        patch.object(mock_shell, "run", side_effect=_patched_run),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
        patch.object(
            mock_factory, "create_lifecycle_manager", wraps=mock_factory.create_lifecycle_manager
        ) as clm_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Manager was never invoked (race guard triggered)
    clm_spy.assert_not_called()
    bc_spy.assert_not_called()

    # Committable snap1 deferred with "vm_running"
    deferred = mock_state.get_deferred_operations("testvm")
    assert len(deferred) == 1
    assert deferred[0].snapshots == ["snap1"]
    assert deferred[0].reason == "vm_running"

    # State not mutated (snapshots still present)
    remaining = mock_state.get_snapshots("testvm")
    remaining_names = [s.name for s in remaining]
    assert "snap1" in remaining_names
    assert "snap2" in remaining_names


# ── Test 5: state removed even when chain_verify_after_commit=False ───────


def test_blockcommit_state_removed_without_post_verify(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Design D5: when a commit succeeds and ``chain_verify_after_commit``
    is False, committed snapshot entries are still removed from state
    unconditionally.
    """
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,  # <-- the point of this test
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir=_SNAP_DIR,
        lifecycle_mode="virsh",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap1, snap2 = _add_two_snapshots(mock_state, "testvm")

    # VM shut off → qemu-img mode; only snap1 (non-tip) in remove set
    mock_shell.expect_first("virsh domstate").returns(
        ShellResult(success=True, stdout="shut off\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_domblklist_for(str(snap2.path)),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect(r"qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout="[]", stderr="", returncode=0, error=None)
    )

    # Only snap1 in remove set (non-tip); snap1 will be committed
    retention = RetentionResult(keep=["snap2"], remove=["snap1"])

    manager = mock_factory._lifecycle_manager
    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # Commit was invoked
    bc_spy.assert_called_once()

    # snap1 must be removed from state even though post-verify is disabled
    remaining = mock_state.get_snapshots("testvm")
    remaining_names = [s.name for s in remaining]
    assert "snap1" not in remaining_names, (
        "snap1 should be removed from state unconditionally (design D5)"
    )
    assert "snap2" in remaining_names

    # Action record created
    action_names = [a.name for a in core._actions if a.action == "snapshot_delete"]
    assert "snap1" in action_names


# ── Test 6: domblklist failure → fallback to newest state snapshot ────────


def test_blockcommit_active_detection_fallback(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """When ``virsh domblklist`` fails but ``virsh domstate`` succeeds,
    the active layer is assumed to be the newest state snapshot by
    timestamp.  A WARNING is logged and the fork logic still splits
    correctly (shut off → non-tip committed, tip deferred with
    ``"active_layer"``).
    """
    caplog.set_level(logging.WARNING)

    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir=_SNAP_DIR,
        lifecycle_mode="virsh",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap1, snap2 = _add_two_snapshots(mock_state, "testvm")

    # VM shut off
    mock_shell.expect_first("virsh domstate").returns(
        ShellResult(success=True, stdout="shut off\n", stderr="", returncode=0, error=None)
    )
    # domblklist FAILS
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(success=False, stdout="", stderr="error: failed", returncode=1, error="failed")
    )
    mock_shell.expect(r"qemu-img info.*--backing-chain").returns(
        ShellResult(success=True, stdout="[]", stderr="", returncode=0, error=None)
    )

    # Both snapshots in remove set; fallback says snap2 (newest) is active layer
    retention = RetentionResult(keep=[], remove=["snap1", "snap2"])

    manager = mock_factory._lifecycle_manager
    with (
        patch("os.path.exists", return_value=True),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
        patch.object(
            mock_factory, "create_lifecycle_manager", wraps=mock_factory.create_lifecycle_manager
        ) as clm_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    # WARNING logged about domblklist failure
    log_messages = [r.message for r in caplog.records]
    assert any("virsh domblklist failed" in msg and "snap2" in msg for msg in log_messages), (
        "Expected WARNING about domblklist failure with snap2 as active layer"
    )

    # Factory called with qemu-img mode (shut off path)
    clm_spy.assert_called_once()
    assert clm_spy.call_args[1]["mode"] == "qemu-img"

    # Older snapshot (snap1) committed; newer (snap2) deferred as active_layer
    bc_spy.assert_called_once()
    committed = bc_spy.call_args[0][1]
    assert len(committed) == 1
    assert committed[0].name == "snap1"

    deferred = mock_state.get_deferred_operations("testvm")
    assert len(deferred) == 1
    assert deferred[0].snapshots == ["snap2"]
    assert deferred[0].reason == "active_layer"

    # snap1 removed, snap2 deferred
    remaining = mock_state.get_snapshots("testvm")
    remaining_names = [s.name for s in remaining]
    assert "snap1" not in remaining_names
    assert "snap2" in remaining_names
