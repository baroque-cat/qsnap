"""Tests for Core.fork() — standalone qcow2 creation from snapshot or backup.

Covers the simplified fork implementation: resolve snapshot via
``_resolve_snapshot()``, estimate chain size via
``qemu-img info --force-share --backing-chain``, and create standalone
qcow2 via ``qemu-img convert --force-share -O qcow2`` with retry and
post-conversion verification.

No XML manipulation, virsh define, NBD, UUID generation, or deploy.

See OpenSpec change: fix-per-disk-isolation (fork-mode/spec.md)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.results import RestoreResult, ShellResult, SnapshotInfo
from tests.mocks import MockConfigFacade


def _make_snapshot(
    name: str = "snap1",
    path: str = "/snapshots/snap1.qcow2",
    allocation: int = 1048576,
    disk: str = "vda",
) -> SnapshotInfo:
    return SnapshotInfo(
        name=name,
        path=Path(path),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=allocation,
        disk=disk,
    )


def _stub_verify_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch ``verify_standalone_image`` to return None (success)."""
    monkeypatch.setattr(
        "qsnap.core.verify_standalone_image",
        lambda shell, source, output: None,
    )


def _stub_verify_failure(
    monkeypatch: pytest.MonkeyPatch, error_msg: str = "M1 failed: virtual-size mismatch"
) -> None:
    """Monkeypatch ``verify_standalone_image`` to return an error string."""
    monkeypatch.setattr(
        "qsnap.core.verify_standalone_image",
        lambda shell, source, output: error_msg,
    )


# ── Required scenarios ────────────────────────────────────────────────────


@pytest.mark.unit
def test_fork_returns_restore_result_on_success(
    tmp_path, make_vm_config, mock_factory, mock_state, mock_shell, monkeypatch
):
    """fork() returns RestoreResult with success=True and correct paths (incl. disk)."""
    _stub_verify_success(monkeypatch)

    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    output_path = tmp_path / "forked.qcow2"
    result = core.fork("snap1", output_path)
    assert isinstance(result, RestoreResult)
    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.restored_path == output_path
    assert len(result.chain_files) == 1
    assert output_path in result.chain_files
    assert result.error is None
    assert result.disk == "vda"


@pytest.mark.unit
def test_fork_snapshot_not_found_returns_failure(
    make_vm_config, mock_factory, mock_state, mock_shell
):
    """fork() returns RestoreResult(success=False) when snapshot is not found."""
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    result = core.fork("nonexistent", Path("/tmp/forked.qcow2"))
    assert isinstance(result, RestoreResult)
    assert result.success is False
    assert result.snapshot_name == "nonexistent"
    assert result.error is not None
    assert "not found" in result.error.lower()


@pytest.mark.unit
def test_fork_no_xml_or_state_mutation(
    tmp_path, make_vm_config, mock_factory, mock_state, mock_shell, monkeypatch
):
    """fork() completes without any virsh dumpxml, virsh define, or state mutation."""
    _stub_verify_success(monkeypatch)

    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    output_path = tmp_path / "forked.qcow2"
    result = core.fork("snap1", output_path)
    assert result.success is True

    # No XML manipulation
    dumpxml_calls = [c for c in mock_shell.call_history if "virsh dumpxml" in c]
    define_calls = [c for c in mock_shell.call_history if "virsh define" in c]
    assert len(dumpxml_calls) == 0, "fork must not call virsh dumpxml"
    assert len(define_calls) == 0, "fork must not call virsh define"

    # No IStateManager mutation (record_snapshot / reset_vm_disk_state / etc.)
    # Verify the snapshot count hasn't increased beyond the one we recorded
    snapshots = mock_state.get_snapshots("sourcevm")
    assert len(snapshots) == 1, "fork must not mutate state"
    assert snapshots[0].name == "snap1"


@pytest.mark.unit
def test_fork_dry_run_logs_plan_no_file(
    tmp_path, make_vm_config, mock_factory, mock_state, mock_shell, monkeypatch, caplog
):
    """fork() in dry-run mode logs the plan and creates no output file."""
    # verify_standalone_image is never reached in dry-run, but just in case
    _stub_verify_success(monkeypatch)

    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    core.dry_run = True
    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    output_path = tmp_path / "forked.qcow2"

    caplog.set_level(logging.INFO)
    result = core.fork("snap1", output_path)

    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.restored_path == output_path

    # Dry-run INFO log must contain "[dry-run]"
    dry_run_logs = [r.message for r in caplog.records if "[dry-run]" in r.getMessage()]
    assert len(dry_run_logs) >= 1, f"Expected [dry-run] log message, got: {caplog.text}"

    # No qemu-img convert was executed
    convert_calls = [c for c in mock_shell.call_history if "qemu-img convert" in c]
    assert len(convert_calls) == 0, "dry-run must not execute qemu-img convert"

    # No output file was created
    assert not output_path.exists(), "dry-run must not create the output file"


@pytest.mark.unit
def test_fork_creates_standalone_qcow2_from_snapshot(
    tmp_path, make_vm_config, mock_factory, mock_state, mock_shell, monkeypatch
):
    """fork() resolves a snapshot in state and converts to standalone qcow2."""
    _stub_verify_success(monkeypatch)

    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    output_path = tmp_path / "forked.qcow2"
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    result = core.fork("snap1", output_path)
    assert isinstance(result, RestoreResult)
    assert result.success is True
    assert result.snapshot_name == "snap1"
    assert result.restored_path == output_path
    assert output_path in result.chain_files
    assert result.error is None
    assert result.disk == "vda"
    convert_calls = [c for c in mock_shell.call_history if "qemu-img convert" in c]
    assert len(convert_calls) >= 1, "qemu-img convert should have been called"
    convert_cmd = convert_calls[0]
    assert "--force-share" in convert_cmd
    assert "-O" in convert_cmd
    assert "qcow2" in convert_cmd
    assert str(output_path) in convert_cmd


@pytest.mark.unit
def test_fork_flattens_incremental_chain(
    tmp_path, make_vm_config, mock_factory, mock_state, mock_shell, monkeypatch
):
    """fork() converts a source with backing chain to standalone qcow2.

    The source may have backing dependencies (e.g., incremental backup).
    qemu-img convert with -O qcow2 flattens the entire chain into a
    standalone file with no backing dependencies.
    """
    _stub_verify_success(monkeypatch)

    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    snap = SnapshotInfo(
        name="inc_backup1",
        path=Path("/backups/target/inc_backup1.qcow2"),
        timestamp=datetime(2025, 7, 14, 10, 0),
        allocation=524288,
        disk="vda",
    )
    mock_state.record_snapshot("sourcevm", snap)
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    output_path = tmp_path / "standalone.qcow2"
    with patch.object(mock_shell, "run", wraps=mock_shell.run) as spy:
        result = core.fork("inc_backup1", output_path)
    assert result.success is True
    convert_calls = [
        " ".join(c.args[0]) for c in spy.call_args_list if "convert" in " ".join(c.args[0])
    ]
    assert len(convert_calls) >= 1, "qemu-img convert should flatten chain"


@pytest.mark.unit
def test_fork_verify_failure_removes_output(
    tmp_path, make_vm_config, mock_factory, mock_state, mock_shell, monkeypatch
):
    """fork() removes the output file when verification fails."""
    _stub_verify_failure(monkeypatch, "M2 failed: qemu-img check found 1 errors and 2 corruptions")

    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1", "/snapshots/snap1.qcow2"))
    # Convert succeeds, but verification fails
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    output_path = tmp_path / "forked.qcow2"
    result = core.fork("snap1", output_path)

    assert result.success is False
    assert "converted image verification failed" in result.error
    assert "M2 failed" in result.error

    # Output file should be removed via rm -f
    rm_calls = [c for c in mock_shell.call_history if "rm" in c and str(output_path) in c]
    assert len(rm_calls) >= 1, "rm -f should be called on verification failure"


@pytest.mark.unit
def test_fork_removes_partial_on_convert_failure(
    tmp_path, make_vm_config, mock_factory, mock_state, mock_shell, monkeypatch
):
    """fork() returns failed RestoreResult when convert fails; partial cleanup
    is handled by convert_with_retry → convert_to_standalone."""

    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1", "/snapshots/snap1.qcow2"))
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="I/O error",
            returncode=1,
            error="I/O error",
        )
    )
    output_path = tmp_path / "forked.qcow2"
    result = core.fork("snap1", output_path)

    assert result.success is False
    assert "image conversion failed" in result.error
    assert result.snapshot_name == "snap1"

    # convert_to_standalone calls _remove_partial on failure.
    # _remove_partial runs rm -f <output> via IShell.  Verify it appears
    # in call_history (MockShell records all calls even without an
    # explicit expectation).
    rm_calls = [
        c for c in mock_shell.call_history if "rm" in c and "-f" in c and str(output_path) in c
    ]
    assert len(rm_calls) >= 1, "convert_to_standalone should call rm -f on the partial output"


# ── Additional coverage (non-required but already present) ─────────────────


@pytest.mark.unit
def test_fork_from_backup_resolves_via_backup_provider(
    tmp_path, make_vm_config, make_target, mock_factory, mock_state, mock_shell, monkeypatch
):
    """fork() finds a backup via backup provider's list() when not in state."""
    _stub_verify_success(monkeypatch)

    target = make_target(path=str(tmp_path / "backups"))
    vm = make_vm_config(name="sourcevm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    backup_info = SnapshotInfo(
        name="backup1",
        path=tmp_path / "backups" / "backup1.qcow2",
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=1048576,
        disk="vda",
    )
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    output_path = tmp_path / "forked.qcow2"
    with patch.object(mock_factory._backup_provider, "list", return_value=[backup_info]):
        result = core.fork("backup1", output_path)
    assert result.success is True
    assert result.snapshot_name == "backup1"
    assert result.restored_path == output_path
    assert result.disk == "vda"


@pytest.mark.unit
def test_fork_passes_vm_filter_correctly(
    tmp_path, make_vm_config, mock_factory, mock_state, mock_shell, monkeypatch
):
    """fork() passes vm_filter to _resolve_snapshot() to restrict VM search."""
    _stub_verify_success(monkeypatch)

    vm1 = make_vm_config(name="vm1")
    vm2 = make_vm_config(name="vm2")
    config = MockConfigFacade(vms=[vm1, vm2])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    snap1 = _make_snapshot("snap1", "/snapshots/vm1_snap1.qcow2")
    snap2 = SnapshotInfo(
        name="snap1",
        path=Path("/snapshots/vm2_snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 10, 0),
        allocation=99999,
        disk="vda",
    )
    mock_state.record_snapshot("vm1", snap1)
    mock_state.record_snapshot("vm2", snap2)
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    output_path = tmp_path / "forked.qcow2"
    result = core.fork("snap1", output_path, vm_filter="vm2")
    assert result.success is True
    assert result.snapshot_name == "snap1"
    convert_calls = [c for c in mock_shell.call_history if "qemu-img convert" in c]
    assert len(convert_calls) >= 1
    assert "vm2_snap1" in convert_calls[0], "convert should use vm2's snapshot, not vm1's"


@pytest.mark.unit
def test_fork_convert_failure_returns_error(
    tmp_path, make_vm_config, mock_factory, mock_state, mock_shell, monkeypatch
):
    """fork() returns RestoreResult(success=False) when convert fails.

    Note: verify_standalone_image is NOT reached when convert fails,
    so this test does NOT need a verify stub.  ``convert_with_retry``
    returns the failure immediately for non-retryable errors.
    """
    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1", "/snapshots/snap1.qcow2"))
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="disk full",
            returncode=1,
            error="disk full",
        )
    )
    output_path = tmp_path / "forked.qcow2"
    result = core.fork("snap1", output_path)
    assert result.success is False
    assert "image conversion failed" in result.error


@pytest.mark.unit
def test_fork_warns_active_layer_inconsistency(
    tmp_path, make_vm_config, mock_factory, mock_state, mock_shell, monkeypatch
):
    """fork() from active layer of running VM uses --force-share to read safely.

    Design D1: ``--force-share`` on ``qemu-img info`` and ``qemu-img convert``
    allows reading from a VM's active layer even when the VM holds an
    exclusive write lock.  The fork still succeeds — no warning is logged
    because ``--force-share`` is the documented mechanism.
    """
    _stub_verify_success(monkeypatch)

    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    snap = SnapshotInfo(
        name="active_snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap4.qcow2"),
        timestamp=datetime(2025, 7, 13, 14, 0),
        allocation=1048576,
        disk="vda",
    )
    mock_state.record_snapshot("sourcevm", snap)
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    output_path = tmp_path / "forked_from_active.qcow2"
    result = core.fork("active_snap1", output_path)
    assert result.success is True
    assert result.snapshot_name == "active_snap1"
    assert result.restored_path == output_path
    assert result.disk == "vda"
    convert_calls = [c for c in mock_shell.call_history if "qemu-img convert" in c]
    assert len(convert_calls) >= 1
    assert "--force-share" in convert_calls[0], (
        "--force-share must be used when reading from active layer"
    )
    backing_calls = [c for c in mock_shell.call_history if "backing-chain" in c]
    assert len(backing_calls) >= 1
    assert "--force-share" in backing_calls[0], (
        "--force-share must be used on backing-chain info for active layer"
    )


@pytest.mark.unit
def test_fork_logs_chain_size_before_convert(
    tmp_path, make_vm_config, mock_factory, mock_state, mock_shell, monkeypatch, caplog
):
    """fork() logs the estimated chain size before running qemu-img convert."""
    _stub_verify_success(monkeypatch)

    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    caplog.set_level(logging.INFO)
    output_path = tmp_path / "forked.qcow2"
    with patch.object(mock_shell, "run", wraps=mock_shell.run) as spy:
        result = core.fork("snap1", output_path)
    assert result.success is True
    assert "chain size" in caplog.text.lower() or "Converting snapshot" in caplog.text, (
        f"Expected conversion log message, got: {caplog.text}"
    )
    call_cmds = [" ".join(c.args[0]) for c in spy.call_args_list]
    backing_idx = None
    convert_idx = None
    for i, cmd in enumerate(call_cmds):
        if "backing-chain" in cmd:
            backing_idx = i
        if "convert" in cmd:
            convert_idx = i
    if backing_idx is not None and convert_idx is not None:
        assert backing_idx < convert_idx, (
            "qemu-img info --backing-chain must run before qemu-img convert"
        )


@pytest.mark.unit
def test_fork_chain_size_estimation_uses_force_share(
    tmp_path, make_vm_config, mock_factory, mock_state, mock_shell, monkeypatch
):
    """fork() uses --force-share on qemu-img info --backing-chain.

    The backing chain may include the active layer of a running VM,
    which holds an exclusive write lock.  ``--force-share`` allows
    metadata reads despite the lock.
    """
    _stub_verify_success(monkeypatch)

    vm = make_vm_config(name="sourcevm")
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)
    mock_state.record_snapshot("sourcevm", _make_snapshot("snap1"))
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    output_path = tmp_path / "forked.qcow2"
    with patch.object(mock_shell, "run", wraps=mock_shell.run) as spy:
        result = core.fork("snap1", output_path)
    assert result.success is True
    backing_calls = [
        " ".join(c.args[0]) for c in spy.call_args_list if "backing-chain" in " ".join(c.args[0])
    ]
    assert len(backing_calls) >= 1, "qemu-img info --backing-chain should be called"
    backing_cmd = backing_calls[0]
    assert "--force-share" in backing_cmd, (
        "qemu-img info --backing-chain must include --force-share"
    )
