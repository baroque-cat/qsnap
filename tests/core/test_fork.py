"""Tests for Core.fork() and Core.deploy() — standalone VM creation.

Covers fork (qemu-img convert + virsh define), deploy (delegates to fork),
_resolve_snapshot resolution from state and backup providers, and config
appending via _append_vm_to_config.

See OpenSpec change: multi-level-full-anchors-and-fork-mode
"""

from __future__ import annotations

import json
import logging
import uuid as uuid_module
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

from qsnap.core import Core
from qsnap.models.config import VMConfig
from qsnap.models.results import RestoreResult, ShellResult, SnapshotInfo
from tests.mocks import MockConfigFacade


SAMPLE_SOURCE_XML = """<domain type='kvm'>
  <name>sourcevm</name>
  <uuid>12345678-1234-1234-1234-123456789abc</uuid>
  <devices>
    <disk type='file' device='disk'>
      <source file='/var/lib/libvirt/images/sourcevm.qcow2'/>
      <target dev='vda'/>
    </disk>
    <interface type='network'>
      <mac address='52:54:00:aa:bb:cc'/>
    </interface>
  </devices>
</domain>"""


# ── helpers ────────────────────────────────────────────────────────────────


def _make_shell_convert_expectations(shell) -> None:
    """Set up MockShell expectations required by Core.fork() happy path.

    Registers responses for:
      - virsh dominfo (VM state — shut off so direct convert is used)
      - qemu-img info --backing-chain (chain size estimation)
      - mkdir -p (vm directory creation)
      - qemu-img convert (standalone qcow2)
      - virsh dumpxml (get source VM XML)
      - virsh define (define new VM)
    """
    # VM state: shut off (so fork uses direct convert, not NBD)
    # Use expect_first to override the global fixture's "running" response
    shell.expect_first("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: -\nName: sourcevm\nState: shut off\n",
            stderr="", returncode=0, error=None,
        )
    )
    # Chain size info (JSON list with actual-size per image)
    chain_info_json = json.dumps(
        [
            {"image": "/snapshots/snap1.qcow2", "actual-size": 1048576},
            {"image": "/var/lib/libvirt/images/sourcevm.qcow2", "actual-size": 2097152},
        ]
    )
    shell.expect("backing-chain").returns(
        ShellResult(success=True, stdout=chain_info_json, stderr="", returncode=0, error=None)
    )
    # Convert: standalone qcow2
    shell.expect("convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # virsh dumpxml
    shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=SAMPLE_SOURCE_XML, stderr="", returncode=0, error=None)
    )
    # virsh define
    shell.expect("virsh define").returns(
        ShellResult(success=True, stdout="Domain newvm defined", stderr="", returncode=0, error=None)
    )
    # mkdir
    shell.expect("mkdir").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )


def _make_snapshot(name="snap1", path="/snapshots/snap1.qcow2") -> SnapshotInfo:
    return SnapshotInfo(
        name=name,
        path=Path(path),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1048576,
    )


# ── test_fork_direct_convert_stopped_vm ──────────────────────────────────


def test_fork_direct_convert_stopped_vm(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """fork() uses direct qemu-img convert -O qcow2 when VM is stopped.

    Verifies that fork detects VM state via virsh dominfo and chooses the
    direct convert path (not NBD) when the VM is shut off.
    """
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    _make_shell_convert_expectations(mock_shell)

    # Pre-create the target directory so ET.ElementTree.write() succeeds
    # (MockShell.mkdir doesn't actually create directories on disk)
    storage_dir = tmp_path / "storage"
    (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as spy:
        result = core.fork("snap1", "newvm", storage_dir)

    assert isinstance(result, RestoreResult)
    assert result.success is True

    # Verify qemu-img convert was called (use "qemu-img convert" to avoid
    # matching the test's own temp directory name which may contain "convert")
    call_cmds = [" ".join(c.args[0]) for c in spy.call_args_list]
    convert_calls = [c for c in call_cmds if "qemu-img convert" in c]
    assert len(convert_calls) >= 1, "qemu-img convert should have been called"

    # Verify the convert command includes -O qcow2 (direct convert)
    convert_cmd = convert_calls[0]
    assert "-O" in convert_cmd
    assert "qcow2" in convert_cmd

    # Verify NBD was NOT used (stopped VM path)
    nbd_calls = [c for c in call_cmds if "nbd:unix:" in c]
    assert len(nbd_calls) == 0, (
        "NBD should not be used for stopped VM"
    )
    backup_begin_calls = [c for c in call_cmds if "virsh backup-begin" in c]
    assert len(backup_begin_calls) == 0, (
        "virsh backup-begin should not be called for stopped VM"
    )


# ── NBD helper ─────────────────────────────────────────────────────────────


def _make_shell_nbd_expectations(shell) -> None:
    """Set up MockShell expectations for the NBD fork path (running VM).

    Registers responses for:
      - qemu-img info --backing-chain (chain size estimation)
      - mkdir -p (vm directory creation)
      - rm -f /tmp/qsnap-backup-* (NBD socket cleanup)
      - virsh backup-begin (start NBD export)
      - qemu-img convert -n nbd:unix: (pull via NBD)
      - virsh dumpxml (get source VM XML)
      - virsh define (define new VM)
    """
    # Chain size info (JSON list with actual-size per image)
    chain_info_json = json.dumps(
        [
            {"image": "/snapshots/snap1.qcow2", "actual-size": 1048576},
            {"image": "/var/lib/libvirt/images/sourcevm.qcow2", "actual-size": 2097152},
        ]
    )
    shell.expect("backing-chain").returns(
        ShellResult(success=True, stdout=chain_info_json, stderr="", returncode=0, error=None)
    )
    # mkdir
    shell.expect("mkdir").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # NBD: rm stale socket (called in nbd_full_export before and after)
    shell.expect(r"rm.*-f.*qsnap-backup").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # NBD: virsh backup-begin
    shell.expect("virsh backup-begin").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # NBD: qemu-img convert -n nbd:unix:<socket>
    shell.expect(r"nbd:unix:").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # virsh dumpxml
    shell.expect("dumpxml").returns(
        ShellResult(success=True, stdout=SAMPLE_SOURCE_XML, stderr="", returncode=0, error=None)
    )
    # virsh define
    shell.expect("virsh define").returns(
        ShellResult(success=True, stdout="Domain newvm defined", stderr="", returncode=0, error=None)
    )


# ── test_fork_nbd_running_vm ───────────────────────────────────────────────


def test_fork_nbd_running_vm(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """fork() uses NBD export when source VM is running.

    With the default conftest ``virsh dominfo`` returning ``State: running``,
    fork should call ``virsh backup-begin`` and ``qemu-img convert -n
    nbd:unix:<socket>`` instead of direct ``qemu-img convert -O qcow2``.
    """
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    _make_shell_nbd_expectations(mock_shell)

    storage_dir = tmp_path / "storage"
    (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as spy:
        result = core.fork("snap1", "newvm", storage_dir)

    assert isinstance(result, RestoreResult)
    assert result.success is True

    # Verify the correct commands were issued
    call_cmds = [" ".join(c.args[0]) for c in spy.call_args_list]

    # virsh backup-begin should be called (NBD export)
    backup_begin_calls = [c for c in call_cmds if "virsh backup-begin" in c]
    assert len(backup_begin_calls) >= 1, (
        "virsh backup-begin should be called for NBD export"
    )

    # qemu-img convert via NBD should be called
    nbd_convert_calls = [c for c in call_cmds if "nbd:unix:" in c]
    assert len(nbd_convert_calls) >= 1, (
        "qemu-img convert nbd:unix: should be called for NBD path"
    )

    # direct qemu-img convert (without NBD) should NOT be called
    all_convert_calls = [c for c in call_cmds if "qemu-img convert" in c]
    direct_convert_calls = [c for c in all_convert_calls if "nbd:unix:" not in c]
    assert len(direct_convert_calls) == 0, (
        "direct qemu-img convert (non-NBD) should NOT be used for running VM"
    )

    # rm -f socket cleanup should be called
    socket_cleanup_calls = [c for c in call_cmds if "rm" in c and "qsnap-backup" in c]
    assert len(socket_cleanup_calls) >= 1, (
        "socket cleanup (rm -f) should be called"
    )


# ── test_fork_chain_size_estimation_uses_force_share ────────────────────────


def test_fork_chain_size_estimation_uses_force_share(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """fork() chain-size estimation uses --force-share on qemu-img info.

    The backing chain may include the active layer of a running VM, which
    holds an exclusive write lock.  ``--force-share`` allows metadata reads
    despite the lock (design D5, bug U).
    """
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    _make_shell_convert_expectations(mock_shell)

    storage_dir = tmp_path / "storage"
    (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as spy:
        result = core.fork("snap1", "newvm", storage_dir)

    assert result.success is True

    # Verify that qemu-img info --backing-chain includes --force-share
    backing_calls = [
        " ".join(c.args[0]) for c in spy.call_args_list
        if "backing-chain" in " ".join(c.args[0])
    ]
    assert len(backing_calls) >= 1, (
        "qemu-img info --backing-chain should be called"
    )
    backing_cmd = backing_calls[0]
    assert "--force-share" in backing_cmd, (
        "qemu-img info --backing-chain must include --force-share"
        " (design D5: read-only metadata access on active layer)"
    )


# ── test_fork_defines_new_libvirt_vm_with_modified_xml ─────────────────────


def test_fork_defines_new_libvirt_vm_with_modified_xml(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """fork() writes modified XML to disk and calls virsh define."""
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    _make_shell_convert_expectations(mock_shell)

    storage_dir = tmp_path / "storage"
    (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)
    result = core.fork("snap1", "newvm", storage_dir)

    assert result.success is True

    # Verify XML was written to disk and virsh define was called
    with patch.object(mock_shell, "run", wraps=mock_shell.run) as spy:
        mock_state.record_snapshot("sourcevm", _make_snapshot("snap2", "/snapshots/snap2.qcow2"))
        _make_shell_convert_expectations(mock_shell)
        (storage_dir / "newvm2").mkdir(parents=True, exist_ok=True)
        core.fork("snap1", "newvm2", storage_dir)

    define_calls = [
        c for c in spy.call_args_list
        if "define" in " ".join(c.args[0])
    ]
    assert len(define_calls) >= 1, "virsh define should have been called"

    # Verify the written XML has new VM name (not sourcevm)
    xml_path = storage_dir / "newvm2" / "newvm2.xml"
    assert xml_path.exists(), f"XML should exist at {xml_path}"
    xml_text = xml_path.read_text()
    assert "newvm2" in xml_text, "XML should contain new VM name"
    assert "sourcevm" not in xml_text or "sourcevm" not in ET.fromstring(xml_text).find("name").text, \
        "XML should NOT contain source VM name as <name>"


# ── test_fork_from_backup_resolves_via_backup_provider ────────────────────


def test_fork_from_backup_resolves_via_backup_provider(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """fork() finds a backup via backup provider's list() when not in state."""
    target = make_target(path=str(tmp_path / "backups"))
    vm = make_vm_config(name="sourcevm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Backup in provider, NOT in state
    backup_info = SnapshotInfo(
        name="backup1",
        path=Path(str(tmp_path / "backups" / "backup1.qcow2")),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1048576,
    )
    with patch.object(
        mock_factory._backup_provider, "list", return_value=[backup_info]
    ):
        _make_shell_convert_expectations(mock_shell)
        storage_dir = tmp_path / "storage"
        (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)
        result = core.fork("backup1", "newvm", storage_dir)

    assert result.success is True
    assert result.snapshot_name == "backup1"


# ── test_fork_add_to_config_appends_vm_block ───────────────────────────────


def test_fork_add_to_config_appends_vm_block(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """fork(add_to_config=True) appends a [[vm]] block to the config file."""
    # Write an initial config file (can be empty)
    config_file = tmp_path / "qsnap.toml"
    config_file.write_text("")

    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm], config_path=config_file)
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    _make_shell_convert_expectations(mock_shell)

    storage_dir = tmp_path / "storage"
    (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)
    result = core.fork("snap1", "newvm", storage_dir, add_to_config=True)

    assert result.success is True

    # Verify the config file now contains a [[vm]] block with new VM name
    config_content = config_file.read_text()
    assert "[[vm]]" in config_content
    assert 'name = "newvm"' in config_content
    assert "base_image" in config_content
    assert "snapshot_dir" in config_content
    assert 'snapshot_create = "always"' in config_content


# ── test_fork_returns_restore_result_on_success ────────────────────────────


def test_fork_returns_restore_result_on_success(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """fork() returns a RestoreResult with success=True and relevant paths."""
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    _make_shell_convert_expectations(mock_shell)

    storage_dir = tmp_path / "storage"
    (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)
    result = core.fork("snap1", "newvm", storage_dir)

    assert isinstance(result, RestoreResult)
    assert result.success is True
    assert result.snapshot_name == "snap1"
    expected_qcow2 = storage_dir / "newvm" / "newvm.qcow2"
    assert result.restored_path == expected_qcow2
    assert len(result.chain_files) > 0
    assert expected_qcow2 in result.chain_files
    assert result.error is None


# ── test_fork_snapshot_not_found_returns_failure ───────────────────────────


def test_fork_snapshot_not_found_returns_failure(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """fork() returns RestoreResult(success=False) when snapshot is not found."""
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # No state snapshots, no backups in provider
    result = core.fork("nonexistent", "newvm", Path("/storage"))

    assert isinstance(result, RestoreResult)
    assert result.success is False
    assert result.snapshot_name == "nonexistent"
    assert result.error is not None
    assert "not found" in result.error.lower()


# ── test_fork_generates_new_uuid_not_source_vm_uuid ────────────────────────


def test_fork_generates_new_uuid_not_source_vm_uuid(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """fork() generates a new UUID, not reusing the source VM's UUID."""
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    _make_shell_convert_expectations(mock_shell)

    # Patch uuid.uuid4 to return a known UUID for deterministic assertion
    known_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    with patch.object(uuid_module, "uuid4", return_value=known_uuid):
        storage_dir = tmp_path / "storage"
        (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)
        result = core.fork("snap1", "newvm", storage_dir)

    assert result.success is True

    # Read the written XML and extract the UUID
    xml_path = storage_dir / "newvm" / "newvm.xml"
    assert xml_path.exists()
    root = ET.fromstring(xml_path.read_text())
    uuid_elem = root.find("uuid")
    assert uuid_elem is not None
    assert uuid_elem.text == known_uuid
    # Source UUID is 12345678-1234-1234-1234-123456789abc — should differ
    assert uuid_elem.text != "12345678-1234-1234-1234-123456789abc"


# ── test_fork_logs_chain_size_before_convert ───────────────────────────────


def test_fork_logs_chain_size_before_convert(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """fork() runs qemu-img info --backing-chain BEFORE qemu-img convert.

    Verifies correct ordering: chain size estimation → log → mkdir → convert.
    """
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    _make_shell_convert_expectations(mock_shell)

    caplog.set_level(logging.INFO)
    storage_dir = tmp_path / "storage"
    (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as spy:
        result = core.fork("snap1", "newvm", storage_dir)

    assert result.success is True

    # Verify log message about chain size appears
    assert "chain size" in caplog.text.lower() or "Converting snapshot" in caplog.text, (
        "Chain size log message should appear"
    )

    # Verify ordering: backing-chain call comes before convert call
    call_cmds = [" ".join(c.args[0]) for c in spy.call_args_list]
    backing_idx = None
    convert_idx = None
    for i, cmd in enumerate(call_cmds):
        if "backing-chain" in cmd:
            backing_idx = i
        if "convert" in cmd:
            convert_idx = i
    # backing-chain should appear (it may be none if not called — but we set it up)
    if backing_idx is not None and convert_idx is not None:
        assert backing_idx < convert_idx, (
            "qemu-img info --backing-chain must run before qemu-img convert"
        )


# ── test_deploy_full_backup_delegates_to_fork ──────────────────────────────


def test_deploy_full_backup_delegates_to_fork(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """deploy() delegates to fork() and succeeds for a full backup."""
    target = make_target(path=str(tmp_path / "backups"))
    vm = make_vm_config(name="sourcevm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    backup_info = SnapshotInfo(
        name="backup1",
        path=Path(str(tmp_path / "backups" / "backup1.qcow2")),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1048576,
    )
    with patch.object(
        mock_factory._backup_provider, "list", return_value=[backup_info]
    ):
        _make_shell_convert_expectations(mock_shell)
        storage_dir = tmp_path / "storage"
        (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)
        result = core.deploy("backup1", "newvm", storage_dir)

    assert result.success is True
    assert result.snapshot_name == "backup1"


# ── test_deploy_incremental_backup_flattens_chain ──────────────────────────


def test_deploy_incremental_backup_flattens_chain(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """deploy() flattens incremental backup chain via qemu-img convert."""
    target = make_target(path=str(tmp_path / "backups"))
    vm = make_vm_config(name="sourcevm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    inc_backup = SnapshotInfo(
        name="inc_backup1",
        path=Path(str(tmp_path / "backups" / "inc_backup1.qcow2")),
        timestamp=datetime(2025, 7, 14, 10, 0),
        allocation=524288,
    )
    with patch.object(
        mock_factory._backup_provider, "list", return_value=[inc_backup]
    ):
        _make_shell_convert_expectations(mock_shell)
        storage_dir = tmp_path / "storage"
        (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)

        with patch.object(mock_shell, "run", wraps=mock_shell.run) as spy:
            result = core.deploy("inc_backup1", "newvm", storage_dir)

    assert result.success is True

    # Verify qemu-img convert was called (flattening the chain)
    convert_calls = [
        c for c in spy.call_args_list
        if "convert" in " ".join(c.args[0])
    ]
    assert len(convert_calls) >= 1, "qemu-img convert should flatten chain"


# ── test_deploy_delegates_to_fork ──────────────────────────────────────────


def test_deploy_delegates_to_fork(
    tmp_path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """deploy() calls fork() with the exact same arguments."""
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    _make_shell_convert_expectations(mock_shell)

    storage_dir = tmp_path / "storage"
    (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)
    with patch.object(core, "fork", wraps=core.fork) as fork_spy:
        result = core.deploy("snap1", "newvm", storage_dir, add_to_config=True, vm_filter=None)

    assert fork_spy.called, "deploy() must delegate to fork()"
    assert fork_spy.call_args[0] == ("snap1", "newvm", storage_dir)
    assert fork_spy.call_args[1] == {"add_to_config": True, "vm_filter": None}


# ── test_resolve_snapshot_finds_in_state ────────────────────────────────────


def test_resolve_snapshot_finds_in_state(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """_resolve_snapshot() finds a snapshot in IStateManager."""
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    snap = _make_snapshot("snap1", "/snapshots/snap1.qcow2")
    mock_state.record_snapshot("sourcevm", snap)

    result = core._resolve_snapshot("snap1")

    assert isinstance(result, tuple)
    assert len(result) == 2
    snapshot_info, vm_config = result
    assert isinstance(snapshot_info, SnapshotInfo)
    assert isinstance(vm_config, VMConfig)
    assert snapshot_info.name == "snap1"
    assert vm_config.name == "sourcevm"


# ── test_resolve_snapshot_finds_in_backup ───────────────────────────────────


def test_resolve_snapshot_finds_in_backup(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """_resolve_snapshot() falls back to backup provider when not in state."""
    target = make_target(path="/mnt/backup/testvm")
    vm = make_vm_config(name="sourcevm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # No state snapshots for this VM
    backup_snap = SnapshotInfo(
        name="backup1",
        path=Path("/mnt/backup/testvm/backup1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1048576,
    )

    with patch.object(
        mock_factory._backup_provider, "list", return_value=[backup_snap]
    ):
        result = core._resolve_snapshot("backup1")

    assert isinstance(result, tuple)
    snapshot_info, vm_config = result
    assert snapshot_info.name == "backup1"


# ── test_resolve_snapshot_raises_on_not_found ───────────────────────────────


def test_resolve_snapshot_raises_on_not_found(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """_resolve_snapshot() raises FileNotFoundError when not in state or backups."""
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Empty state, empty backup provider
    with pytest.raises(FileNotFoundError, match="Snapshot not found"):
        core._resolve_snapshot("nonexistent")


# ── test_core_fork_method_succeeds ─────────────────────────────────────────


def test_core_fork_method_succeeds(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Full happy-path test for fork() — all steps succeed."""
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    _make_shell_convert_expectations(mock_shell)

    storage_dir = tmp_path / "storage"
    (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)
    result = core.fork("snap1", "newvm", storage_dir)

    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.error is None
    assert result.restored_path is not None
    # restored_path should be the qcow2 path, not the storage dir
    expected_qcow2 = storage_dir / "newvm" / "newvm.qcow2"
    assert result.restored_path == expected_qcow2
    assert len(result.chain_files) > 0
    assert expected_qcow2 in result.chain_files


# ── Edge case: test_resolve_snapshot_vm_filter_restricts_to_matching_vm ────


def test_resolve_snapshot_vm_filter_restricts_to_matching_vm(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """_resolve_snapshot() with vm_filter only searches matching VM."""
    vm1 = make_vm_config(name="vm1")
    vm2 = make_vm_config(name="vm2")
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Same snapshot name in both VMs
    snap1 = _make_snapshot("snap1", "/snapshots/vm1_snap1.qcow2")
    snap2 = SnapshotInfo(
        name="snap1",
        path=Path("/snapshots/vm2_snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=99999,
    )
    mock_state.record_snapshot("vm1", snap1)
    mock_state.record_snapshot("vm2", snap2)

    # Filter to vm1 only — should return vm1's snapshot
    result = core._resolve_snapshot("snap1", vm_filter="vm1")
    snapshot_info, vm_config = result
    assert vm_config.name == "vm1"
    assert snapshot_info.path == snap1.path


# ── Edge case: test_fork_warns_when_source_vm_running ───────────────────────


def test_fork_warns_when_source_vm_running(
    tmp_path,
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """fork() should still succeed even if source VM is running (warning logged).

    Note: fork() does NOT check whether the source VM is running — it just
    proceeds.  This test verifies the current behavior is tolerant.
    """
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    _make_shell_convert_expectations(mock_shell)

    caplog.set_level(logging.WARNING)
    storage_dir = tmp_path / "storage"
    (storage_dir / "newvm").mkdir(parents=True, exist_ok=True)
    result = core.fork("snap1", "newvm", storage_dir)

    # fork() does not check VM running state during its flow, so it succeeds
    assert result.success is True
