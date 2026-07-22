"""Unit tests for BitmapBackupProvider (NBD pull-model v3 — atomic checkpoints).

Tests cover NBD pull-model incremental backup via ``virsh backup-begin``
with **atomic** checkpoint creation (checkpoint XML as third positional arg).
All shell calls are intercepted by ``MockShell`` — zero real I/O.

Design decisions verified:
- **D1**: Every ``backup-begin`` receives a checkpoint XML as third positional arg
  — the successor checkpoint is created atomically at the export's freeze point.
  No standalone ``checkpoint-create-as``.
- **D2**: ``_new_checkpoint_name(target_hash)`` → ``qsnap-{hash}-{yyyymmddTHHMMSS}``.
- **D3**: Rotation: after success + verification, delete ALL older
  ``qsnap-{target_hash}-*`` checkpoints; the successor already exists
  atomically.  On failure, successor deleted best-effort, prior preserved.
- **D4**: Removed: no checkpoint-only path, no ``IStateManager`` consultation.
- **Newest-wins discovery**: ``_newest_checkpoint`` via ``virsh checkpoint-list``
  only; legacy names parsed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from qsnap.interfaces.backup import IBackupProvider
from qsnap.models.results import NbdExtent, ShellResult, SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from tests.mocks.mock_nbd import MockNbdClient

# ── Helpers ────────────────────────────────────────────────────────────────


def _ok_version_result(version: str = "8.2.0") -> ShellResult:
    """A successful ``virsh --version`` ShellResult."""
    return ShellResult(
        success=True,
        stdout=f"virsh {version}\n",
        stderr="",
        returncode=0,
        error=None,
    )


def _ok_result() -> ShellResult:
    """A generic successful ShellResult."""
    return ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


def _make_snapshot() -> SnapshotInfo:
    """A standard SnapshotInfo for transfer tests."""
    return SnapshotInfo(
        name="testvm.20250101T000000",
        path=Path("/snapshots/testvm.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )


def _setup_incr_expectations(mock_shell, target, prev_data: tuple[Path, str, str]) -> MockNbdClient:
    """Register incremental copy-loop expectations and return a MockNbdClient.

    *prev_data* is ``(prev_backup_path, disk_target, prev_backup_name)``.
    The caller must still register: virsh --version, rm -f stale socket,
    checkpoint-list, backup-begin, checkpoint-delete, domjobabort,
    source socket cleanup.  The target directory must exist and contain
    the previous backup file (created by the caller).
    """
    prev_path, disk_target, prev_name = prev_data
    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    nbd = MockNbdClient(size=65536, max_request_size=33554432)
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        f"qemu:dirty-bitmap:backup-{disk_target}": [
            NbdExtent(offset=0, length=65536, data=True),
        ],
    }

    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=f"Target   Source\n--------------------------------\n{disk_target}   /var/lib/libvirt/images/testvm.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first("qemu-img info.*" + prev_name).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 65536, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_path}").returns(_ok_result())
    mock_shell.expect("qemu-img create").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("qemu-nbd --fork").returns(_ok_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("^mv ").returns(_ok_result())
    return nbd


# ──────────────────────────────────────────────────────────────────────────
# 1. Constructor
# ──────────────────────────────────────────────────────────────────────────


def test_constructor_accepts_ishell_and_implements_abc(mock_shell):
    """BitmapBackupProvider accepts IShell and is an IBackupProvider."""
    provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient())
    assert isinstance(provider, IBackupProvider)


def test_bitmap_constructor_no_version_check():
    """BitmapBackupProvider.__init__ no longer calls ``_check_libvirt_version()``."""
    from tests.mocks.mock_shell import MockShell

    shell = MockShell()
    provider = BitmapBackupProvider(shell, nbd=MockNbdClient())

    assert isinstance(provider, IBackupProvider)
    assert not hasattr(BitmapBackupProvider, "_check_libvirt_version"), (
        "_check_libvirt_version method should not exist (version check moved to factory)"
    )


def test_bitmap_constructor_stores_nbd() -> None:
    """BitmapBackupProvider stores the injected nbd dependency."""
    from tests.mocks.mock_shell import MockShell

    nbd = MockNbdClient(size=1048576)
    provider = BitmapBackupProvider(MockShell(), nbd=nbd)
    assert provider._nbd is nbd


# ──────────────────────────────────────────────────────────────────────────
# 2. No checkpoints — full NBD export (atomic successor checkpoint)
# ──────────────────────────────────────────────────────────────────────────


def test_no_checkpoints_triggers_full_export(mock_shell, make_vm_config, make_target, tmp_path):
    """When no prior checkpoint exists, a full NBD export is performed with
    an atomic successor checkpoint (D1).  ``virsh backup-begin`` receives
    a checkpoint XML as the third positional argument — no standalone
    ``checkpoint-create-as``."""
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
    )
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].snapshot_name == snapshot.name
    assert results[0].error is None

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        f"backup-begin should receive checkpoint XML 3rd arg, got: {backup_cmds[0]}"
    )

    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]
    assert "-c" in convert_cmds[0]
    assert "-o compression_type=zstd" in convert_cmds[0]

    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0, (
        "checkpoint-create-as must NOT be called (atomic via backup-begin)"
    )
    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0


# ──────────────────────────────────────────────────────────────────────────
# 3. Incremental backup — dirty blocks via NBD checkpoint (atomic)
# ──────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────
# 4. Checkpoint cleanup after successful transfer (atomic model)
# ──────────────────────────────────────────────────────────────────────────


def test_checkpoint_cleanup_after_successful_transfer(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """After successful NBD transfer, rotation deletes superseded checkpoints
    (create-atomically-then-delete-superseded, D3).  No standalone
    ``checkpoint-create-as``."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_incr_expectations(
        mock_shell,
        target,
        (prev_backup, "vda", prev_backup.name),
    )

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # rotation
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    mock_wbxml.assert_called_once()
    _, kwargs = mock_wbxml.call_args
    assert kwargs.get("incremental") == prior_checkpoint

    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0]

    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert "--metadata" in delete_cmds[0]
    assert prior_checkpoint in delete_cmds[0]
    assert vm_config.name in delete_cmds[0]

    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0

    # No qemu-img convert on the incremental path (copy loop used instead)
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, "No qemu-img convert on incremental path"


# ──────────────────────────────────────────────────────────────────────────
# 5. Transfer failure preserves checkpoint, successor deleted best-effort
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_failure_preserves_checkpoint(mock_shell, make_vm_config, make_target, tmp_path):
    """When the copy loop fails, prior checkpoint is NOT deleted,
    successor checkpoint is deleted best-effort, partial file is deleted."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"
    target_file = target.path / f"{snapshot.name}.qcow2"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    nbd = MockNbdClient(size=65536, max_request_size=33554432)
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }
    nbd.fail_pread = "pread I/O error"

    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /var/lib/libvirt/images/testvm.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first("qemu-img info.*" + prev_backup.name).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 65536, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(_ok_result())
    mock_shell.expect("qemu-img create").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("qemu-nbd --fork").returns(_ok_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # .tmp removal in finally

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # successor best-effort
    mock_shell.expect(f"rm -f {target_file}").returns(_ok_result())  # _cleanup_partial_file
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False
    assert "pread" in results[0].error.lower()
    assert results[0].bytes_transferred == 0

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0

    # The final file removal (emit -f <target_file>) must appear.
    # Match precisely: the rm command must end with " <target_file>"
    # (the .tmp removal has " <target_file>.tmp" so trailing-space match
    # avoids the false hit).
    partial_file_cmds = [
        cmd for cmd in all_cmds if cmd.startswith("rm -f") and cmd.endswith(" " + str(target_file))
    ]
    assert len(partial_file_cmds) == 1, (
        f"Expected exactly one rm -f of {target_file}, got:\n" + "\n".join(all_cmds)
    )

    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert "--metadata" in delete_cmds[0]
    assert vm_config.name in delete_cmds[0]
    assert prior_checkpoint not in delete_cmds[0], "prior checkpoint must NOT be deleted"
    assert f"qsnap-{target_hash}-" in delete_cmds[0], "successor (not prior) should be deleted"

    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0


# ──────────────────────────────────────────────────────────────────────────
# 6. Socket cleanup on success and failure
# ──────────────────────────────────────────────────────────────────────────


def test_socket_cleanup_on_success(mock_shell, make_vm_config, make_target, tmp_path):
    """Socket cleanup on success; backup-begin receives checkpoint XML 3rd arg (D1)."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        provider.transfer_missing(vm_config, target, [snapshot])

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]
    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0


def test_socket_cleanup_on_failure(mock_shell, make_vm_config, make_target, tmp_path):
    """Socket cleanup on failure; successor checkpoint deleted best-effort."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(
        ShellResult(
            success=False, stdout="", stderr="NBD read error", returncode=1, error="NBD read error"
        )
    )
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # successor best-effort
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0


# ──────────────────────────────────────────────────────────────────────────
# 7. list_checkpoints filters qsnap- prefix
# ──────────────────────────────────────────────────────────────────────────


def test_list_checkpoints_filters_qsnap_prefix(mock_shell):
    """``list_checkpoints()`` filters by the ``qsnap-`` prefix."""
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout="qsnap-abc123-snap1\nother-checkpoint\nlibvirt-something\nqsnap-xyz789-snap2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        checkpoints = provider.list_checkpoints("testvm")

    assert checkpoints == ["qsnap-abc123-snap1", "qsnap-xyz789-snap2"]
    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_cmds = all_run_cmds + [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    cp_list_cmds = [cmd for cmd in all_cmds if "checkpoint-list" in cmd]
    assert len(cp_list_cmds) == 1
    assert "--name" in cp_list_cmds[0]
    assert "--domain" in cp_list_cmds[0]
    assert "testvm" in cp_list_cmds[0]


# ──────────────────────────────────────────────────────────────────────────
# create_full_backup via NBD full export (atomic checkpoint, D1/D2)
# ──────────────────────────────────────────────────────────────────────────


def test_bitmap_create_full_backup_nbd_succeeds(mock_shell, make_target, tmp_path):
    """create_full_backup uses NBD full export, backup-begin receives
    checkpoint XML 3rd arg (D1).  No standalone checkpoint-create-as."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False, bucket_level="monthly"
        )

    assert result.success is True
    assert result.error is None
    assert result.snapshot_name == snapshot.name
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        f"backup-begin should receive checkpoint XML 3rd arg, got: {backup_cmds[0]}"
    )

    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]
    assert "-c" not in convert_cmds[0]

    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1
    assert "testvm" in abort_cmds[0]

    cp_create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(cp_create_cmds) == 0, "create_full_backup must NOT use checkpoint-create-as"
    cp_delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(cp_delete_cmds) == 0, "create_full_backup should NOT delete checkpoints on success"


def test_bitmap_create_full_backup_with_compression_succeeds(
    mock_shell, make_target, tmp_path, caplog
):
    """compress=True passes -c to qemu-img convert.  Checkpoint XML 3rd arg."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=True, bucket_level="monthly"
        )

    assert result.success is True
    assert result.snapshot_name == snapshot.name
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]
    assert "-c" in convert_cmds[0]
    assert "-o compression_type=zstd" in convert_cmds[0]

    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1

    compress_ignored_warnings = [
        rec for rec in caplog.records if "compress=True ignored" in rec.getMessage()
    ]
    assert len(compress_ignored_warnings) == 0

    cp_create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(cp_create_cmds) == 0
    cp_delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(cp_delete_cmds) == 0


def test_bitmap_full_backup_does_not_raise_not_implemented(mock_shell, make_target, tmp_path):
    """create_full_backup returns BackupResult, not NotImplementedError."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False, bucket_level="monthly"
        )

    from qsnap.models.results import BackupResult as _BR

    assert isinstance(result, _BR)
    assert result.success is True
    assert result.snapshot_name == snapshot.name


def test_bitmap_full_socket_cleanup(mock_shell, make_target, tmp_path):
    """Socket cleanup on success and failure; checkpoint XML 3rd arg (D1)."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    # ── Success case ──
    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False, bucket_level="monthly"
        )

    assert result.success is True
    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds_s = all_run_cmds + all_stall_cmds

    backup_cmds = [cmd for cmd in all_cmds_s if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    socket_rm = [
        cmd for cmd in all_cmds_s if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm) >= 2
    abort_cmds = [cmd for cmd in all_cmds_s if "domjobabort" in cmd]
    assert len(abort_cmds) == 1
    convert_stall = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_stall) == 1
    cp_create = [cmd for cmd in all_cmds_s if "checkpoint-create-as" in cmd]
    assert len(cp_create) == 0

    # ── Failure case ──
    from tests.mocks.mock_shell import MockShell

    fail_shell = MockShell()
    fail_shell.expect("virsh dominfo").returns(
        ShellResult(success=True, stdout="State: running\n", stderr="", returncode=0, error=None)
    )
    fail_shell.expect("which virsh").returns(
        ShellResult(success=True, stdout="/usr/bin/virsh\n", stderr="", returncode=0, error=None)
    )
    fail_shell.expect("which qemu-img").returns(
        ShellResult(success=True, stdout="/usr/bin/qemu-img\n", stderr="", returncode=0, error=None)
    )
    fail_shell.expect("virsh --version").returns(_ok_version_result())
    fail_shell.expect("rm -f").returns(_ok_result())
    fail_shell.expect("backup-begin").returns(_ok_result())
    fail_shell.expect("qemu-img convert").returns(
        ShellResult(success=False, stdout="", stderr="I/O error", returncode=1, error="I/O error")
    )
    fail_shell.expect("checkpoint-delete").returns(_ok_result())  # successor best-effort
    fail_shell.expect("domjobabort").returns(_ok_result())

    with patch.object(fail_shell, "run", wraps=fail_shell.run) as fail_spy:
        provider_fail = BitmapBackupProvider(fail_shell)
        result_fail = provider_fail.create_full_backup(
            "testvm", snapshot, target, compress=False, bucket_level="monthly"
        )

    assert result_fail.success is False
    all_cmds_fail = [" ".join(call_obj.args[0]) for call_obj in fail_spy.call_args_list]
    socket_rm_fail = [
        cmd for cmd in all_cmds_fail if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm_fail) >= 1
    abort_fail = [cmd for cmd in all_cmds_fail if "domjobabort" in cmd]
    assert len(abort_fail) == 1
    cp_delete_fail = [cmd for cmd in all_cmds_fail if "checkpoint-delete" in cmd]
    assert len(cp_delete_fail) == 1, "Successor checkpoint should be deleted best-effort on failure"


# ── REMOVED: test_bitmap_full_backup_no_checkpoint (replaced by
#    test_bitmap_full_backup_creates_atomic_checkpoint) ──────────────────


def test_bitmap_bucket_driven_full_no_longer_crashes(mock_shell, make_target, tmp_path):
    """create_full_backup works with different bucket_level values; checkpoint XML 3rd arg."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = BitmapBackupProvider(mock_shell)
        for bl in ("monthly", "weekly", "daily", "yearly"):
            result = provider.create_full_backup(
                "testvm", snapshot, target, compress=False, bucket_level=bl
            )
            assert result.success is True, f"create_full_backup failed for bucket_level={bl}"
            assert result.snapshot_name == snapshot.name


def test_bitmap_create_full_backup_returns_standalone_qcow2(mock_shell, make_target, tmp_path):
    """create_full_backup via NBD produces standalone qcow2 with no backing file."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False, bucket_level="monthly"
        )

    assert result.success is True
    mock_shell.expect(r"qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {"format": "qcow2", "virtual-size": 1073741824, "actual-size": 1048576}
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    info_result = mock_shell.run(
        ["qemu-img", "info", "--output=json", str(result.target_path)], timeout=30
    )
    info_data = json.loads(info_result.stdout)
    assert "backing-filename" not in info_data


def test_bitmap_create_full_backup_dotted_vm_name(mock_shell, make_target, tmp_path):
    """Bitmap FULL backup with dotted VM name '3.Projects_opencode'."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    from qsnap.utils.nbd import nbd_full_export as real_nbd_full_export

    with (
        patch("qsnap.modules.backup.bitmap.nbd_full_export", wraps=real_nbd_full_export) as nbd_spy,
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "3.Projects_opencode", snapshot, target, compress=False, bucket_level="monthly"
        )

    assert result.success is True
    assert result.error is None
    assert result.snapshot_name == snapshot.name
    assert result.bytes_transferred == 65536

    nbd_spy.assert_called_once()
    actual_vm_name = nbd_spy.call_args[0][1]
    assert actual_vm_name == "3.Projects_opencode"

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds
    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1
    assert "3.Projects_opencode" in abort_cmds[0]

    result_filename = result.target_path.name
    expected_date = snapshot.timestamp.strftime("%Y%m%d")
    expected_name = f"3.Projects_opencode.FULL.{expected_date}.qcow2"
    assert result_filename == expected_name
    assert result_filename.startswith("3.Projects_opencode.FULL.")
    assert result_filename.endswith(".qcow2")
    assert snapshot.name == "testvm.20250101T000000"
    assert "3.Projects_opencode" not in snapshot.name


def test_bitmap_nbd_job_terminated_after_transfer(mock_shell, make_target, tmp_path):
    """domjobabort called after NBD transfer via create_full_backup."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False, bucket_level="monthly"
        )

    assert result.success is True
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1
    assert "--domain" in abort_cmds[0]
    assert "testvm" in abort_cmds[0]

    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    convert_stall = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_stall) == 1


def test_bitmap_socket_cleanup_after_job_abort(mock_shell, make_target, tmp_path, caplog):
    """Socket cleanup even when domjobabort fails."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: domain is not running",
            returncode=1,
            error="error: domain is not running",
        )
    )
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False, bucket_level="monthly"
        )

    assert result.success is True
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1
    socket_rm = [cmd for cmd in all_cmds if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd]
    assert len(socket_rm) >= 2

    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    abort_idx = None
    last_rm_idx = None
    for i, cmd in enumerate(all_cmds):
        if "domjobabort" in cmd:
            abort_idx = i
        if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd:
            last_rm_idx = i
    assert abort_idx is not None and last_rm_idx is not None
    assert abort_idx < last_rm_idx

    warnings = [
        rec
        for rec in caplog.records
        if rec.levelname == "WARNING" and "domjobabort" in rec.getMessage().lower()
    ]
    assert len(warnings) >= 1


def test_bitmap_first_full_pull_via_nbd(mock_shell, make_target, tmp_path):
    """create_full_backup via NBD pull-model with atomic checkpoint.
    No standalone checkpoint-create-as."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False, bucket_level="monthly"
        )

    assert result.success is True
    assert result.error is None
    assert result.snapshot_name == snapshot.name
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    assert any(cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd for cmd in all_cmds)
    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        f"backup-begin should receive checkpoint XML 3rd arg, got: {backup_cmds[0]}"
    )
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 1
    assert "nbd:unix:" in convert_cmds[0]
    abort_cmds = [cmd for cmd in all_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) == 1
    socket_rm_count = sum(
        1 for cmd in all_cmds if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    )
    assert socket_rm_count >= 2
    assert not any("checkpoint-create-as" in cmd for cmd in all_cmds), (
        "create_full_backup must NOT use checkpoint-create-as (atomic via backup-begin)"
    )


def test_domjobabort_called_after_successful_transfer(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """Successful transfer — domjobabort called.  Checkpoint XML 3rd arg.  No checkpoint-create-as."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")
    snapshot = _make_snapshot()

    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    abort_cmds = [cmd for cmd in all_cmds if "virsh domjobabort" in cmd]
    assert len(abort_cmds) == 1
    assert "--domain" in abort_cmds[0]
    assert vm_config.name in abort_cmds[0]

    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0


def test_domjobabort_called_after_failed_transfer(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """backup-begin fails — domjobabort still called.  Successor never created, no checkpoint-delete."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")
    snapshot = _make_snapshot()

    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    backup_error = "backup-begin failed: domain is shut off"
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=False, stdout="", stderr=backup_error, returncode=1, error=backup_error)
    )
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error == backup_error

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    abort_cmds = [cmd for cmd in all_cmds if "virsh domjobabort" in cmd]
    assert len(abort_cmds) == 1
    assert "--domain" in abort_cmds[0]
    assert vm_config.name in abort_cmds[0]

    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0, (
        "No checkpoint-delete (backup-begin is atomic — checkpoint never created)"
    )


def test_domjobabort_failure_is_non_fatal(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
):
    """domjobabort fails — transfer still succeeds, WARNING logged.  Checkpoint XML 3rd arg."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")
    snapshot = _make_snapshot()

    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: domain is not running",
            returncode=1,
            error="error: domain is not running",
        )
    )
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    abort_cmds = [cmd for cmd in all_cmds if "virsh domjobabort" in cmd]
    assert len(abort_cmds) == 1

    backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    create_cmds = [cmd for cmd in all_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0

    warnings = [
        rec
        for rec in caplog.records
        if rec.levelname == "WARNING" and "domjobabort" in rec.getMessage().lower()
    ]
    assert len(warnings) >= 1


def test_constructor_accepts_state_manager(mock_shell, mock_state):
    provider = BitmapBackupProvider(mock_shell, state=mock_state)
    assert provider._state is mock_state


def test_constructor_works_without_state_manager(mock_shell):
    provider = BitmapBackupProvider(mock_shell)
    assert provider._state is None


def test_create_full_backup_does_not_self_record(mock_shell, mock_state, make_target, tmp_path):
    """create_full_backup does NOT call state.record_full_backup."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    with patch.object(
        mock_state, "record_full_backup", wraps=mock_state.record_full_backup
    ) as state_spy:
        original_run = mock_shell.run

        def spied_run(cmd, timeout):
            cmd_str = " ".join(cmd)
            if cmd_str.startswith("mv "):
                Path(cmd[-1]).write_bytes(b"\x00" * 65536)
            return original_run(cmd, timeout)

        with patch.object(mock_shell, "run", side_effect=spied_run):
            provider = BitmapBackupProvider(mock_shell, state=mock_state)
            result = provider.create_full_backup(
                "testvm", snapshot, target, compress=False, bucket_level="weekly"
            )
    assert result.success is True
    state_spy.assert_not_called()


def test_create_full_backup_skips_state_when_none(mock_shell, make_target, tmp_path):
    """create_full_backup succeeds without state manager."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False, bucket_level="monthly"
        )

    assert result.success is True
    assert result.bytes_transferred == 65536
    assert result.snapshot_name == snapshot.name


# ──────────────────────────────────────────────────────────────────────────
# Failed file deletion + successor checkpoint best-effort
# ──────────────────────────────────────────────────────────────────────────


def test_transfer_failure_deletes_partial_file(mock_shell, make_vm_config, make_target, tmp_path):
    """Copy loop failure → partial file deleted, successor checkpoint deleted best-effort."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()
    target_file = target.path / f"{snapshot.name}.qcow2"

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    nbd = MockNbdClient(size=65536, max_request_size=33554432)
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }
    nbd.fail_pread = "I/O error"

    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /var/lib/libvirt/images/testvm.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first("qemu-img info.*" + prev_backup.name).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 65536, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(_ok_result())
    mock_shell.expect("qemu-img create").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("qemu-nbd --fork").returns(_ok_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # _cleanup_partial_file
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # successor best-effort
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # socket cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False
    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    partial_file_cmds = [
        cmd for cmd in all_cmds if cmd.startswith("rm -f") and cmd.endswith(" " + str(target_file))
    ]
    assert len(partial_file_cmds) == 1
    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint not in delete_cmds[0]
    assert f"qsnap-{target_hash}-" in delete_cmds[0]


def test_bitmap_verify_failure_deletes_file(mock_shell, make_vm_config, make_target, tmp_path):
    """Verify failure → partial file deleted, successor checkpoint deleted best-effort."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="metadata")
    snapshot = _make_snapshot()
    target_file = target.path / f"{snapshot.name}.qcow2"

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    nbd = MockNbdClient(size=65536, max_request_size=33554432)
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }

    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout="Target   Source\n--------------------------------\nvda   /var/lib/libvirt/images/testvm.qcow2\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first("qemu-img info.*" + prev_backup.name).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 65536, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(_ok_result())
    mock_shell.expect("qemu-img create").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("qemu-nbd --fork").returns(_ok_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok_result())
    mock_shell.expect("^mv ").returns(_ok_result())

    # Verification: source info fails
    mock_shell.expect_first(r"qemu-img info.*--force-share").returns(
        ShellResult(success=False, stdout="", stderr="I/O error", returncode=1, error="I/O error")
    )

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # _cleanup_partial_file
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # successor best-effort
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # socket cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False
    assert "verification failed" in results[0].error

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    all_cmds = all_run_cmds + all_stall_cmds

    partial_file_cmds = [
        cmd for cmd in all_cmds if cmd.startswith("rm -f") and cmd.endswith(" " + str(target_file))
    ]
    assert len(partial_file_cmds) == 1
    delete_cmds = [cmd for cmd in all_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint not in delete_cmds[0]
    assert f"qsnap-{target_hash}-" in delete_cmds[0]


def test_bitmap_full_zstd_compression(mock_shell, make_target, tmp_path):
    """create_full_backup passes compression_type='zstd' to nbd_full_export.  checkpoint_name also passed."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    from qsnap.utils.nbd import nbd_full_export as real_nbd_full_export

    with (
        patch("qsnap.modules.backup.bitmap.nbd_full_export", wraps=real_nbd_full_export) as nbd_spy,
        patch.object(mock_shell, "run", side_effect=spied_run),
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ),
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=True,
            compression_type="zstd",
            bucket_level="monthly",
        )

    assert result.success is True
    nbd_spy.assert_called_once()
    _, kwargs = nbd_spy.call_args
    assert kwargs.get("compression_type") == "zstd"
    assert kwargs.get("checkpoint_name") is not None, (
        "checkpoint_name must be passed to nbd_full_export (D1)"
    )
    assert kwargs["checkpoint_name"].startswith("qsnap-"), (
        f"checkpoint_name should start with qsnap-, got {kwargs['checkpoint_name']!r}"
    )


def test_bitmap_full_zlib_compression(mock_shell, make_target, tmp_path):
    """create_full_backup passes compression_type='zlib' to nbd_full_export.  checkpoint_name also passed."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    from qsnap.utils.nbd import nbd_full_export as real_nbd_full_export

    with (
        patch("qsnap.modules.backup.bitmap.nbd_full_export", wraps=real_nbd_full_export) as nbd_spy,
        patch.object(mock_shell, "run", side_effect=spied_run),
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ),
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm",
            snapshot,
            target,
            compress=True,
            compression_type="zlib",
            bucket_level="monthly",
        )

    assert result.success is True
    nbd_spy.assert_called_once()
    _, kwargs = nbd_spy.call_args
    assert kwargs.get("compression_type") == "zlib"
    assert kwargs.get("checkpoint_name") is not None, (
        "checkpoint_name must be passed to nbd_full_export (D1)"
    )
    assert kwargs["checkpoint_name"].startswith("qsnap-")


def test_nbd_full_export_uses_stall_detection(mock_shell, make_target, tmp_path):
    """nbd_full_export uses run_with_stall_detection; checkpoint_name passed."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    from qsnap.utils.nbd import nbd_full_export as real_nbd_full_export

    with (
        patch("qsnap.modules.backup.bitmap.nbd_full_export", wraps=real_nbd_full_export) as nbd_spy,
        patch.object(mock_shell, "run", side_effect=spied_run),
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=True, bucket_level="monthly"
        )

    assert result.success is True
    nbd_spy.assert_called_once()
    _, kwargs = nbd_spy.call_args
    assert kwargs.get("stall_timeout", 0) > 0
    assert kwargs.get("checkpoint_name") is not None, (
        "checkpoint_name must be passed to nbd_full_export (D1)"
    )
    assert kwargs["checkpoint_name"].startswith("qsnap-")

    convert_stall = [
        c for c in stall_spy.call_args_list if "qemu-img convert" in " ".join(c.args[0])
    ]
    assert len(convert_stall) == 1


def test_nbd_full_tmp_rename(mock_shell, make_target, tmp_path):
    """.tmp file used in nbd_full_export, atomically renamed.  checkpoint_name passed."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    from qsnap.utils.nbd import nbd_full_export as real_nbd_full_export

    with (
        patch("qsnap.modules.backup.bitmap.nbd_full_export", wraps=real_nbd_full_export) as nbd_spy,
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(
            mock_shell, "run_with_stall_detection", wraps=mock_shell.run_with_stall_detection
        ) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=True, bucket_level="monthly"
        )

    assert result.success is True
    assert result.target_path.suffix == ".qcow2"

    nbd_spy.assert_called_once()
    target_arg = nbd_spy.call_args[0][2]
    assert str(target_arg).endswith(".tmp")
    _, nbd_kwargs = nbd_spy.call_args
    assert nbd_kwargs.get("checkpoint_name") is not None, (
        "checkpoint_name must be passed to nbd_full_export (D1)"
    )
    assert nbd_kwargs["checkpoint_name"].startswith("qsnap-")

    stall_call = stall_spy.call_args_list[0]
    _, stall_kwargs = stall_call
    output_file = stall_kwargs.get("output_file")
    assert output_file is not None
    assert str(output_file).endswith(".tmp")

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    mv_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 1
    assert ".tmp" in mv_cmds[0]
    assert ".qcow2" in mv_cmds[0]


# ══════════════════════════════════════════════════════════════════════════
# NEW TESTS: atomic-backup-checkpoints (bitmap-unit)
# ══════════════════════════════════════════════════════════════════════════


def test_atomic_full_export_passes_checkpoint_xml(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """transfer_missing with no prior checkpoint: backup-begin receives 3rd
    positional arg (checkpoint XML).  No standalone checkpoint-create-as."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        f"backup-begin must have checkpoint XML as 3rd arg (D1), got: {backup_cmds[0]}"
    )

    # No standalone checkpoint-create-as
    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0, (
        "checkpoint-create-as must NOT be called (atomic via backup-begin)"
    )

    # No checkpoint-delete on first full export (nothing to rotate)
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0


def test_atomic_incremental_passes_checkpoint_xml_and_incremental(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """transfer_missing with prior checkpoint: write_backup_xml receives
    incremental=prior, backup-begin receives 3 positional args (D1).  No
    checkpoint-create-as.  Rotation deletes prior after success."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_incr_expectations(
        mock_shell,
        target,
        (prev_backup, "vda", prev_backup.name),
    )

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # rotation
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    # write_backup_xml received incremental=prior
    mock_wbxml.assert_called_once()
    _, kwargs = mock_wbxml.call_args
    assert kwargs.get("incremental") == prior_checkpoint

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        f"backup-begin must have checkpoint XML as 3rd arg (D1), got: {backup_cmds[0]}"
    )

    # No standalone checkpoint-create-as
    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0

    # Rotation: checkpoint-delete for prior
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint in delete_cmds[0]
    assert "--metadata" in delete_cmds[0]


def test_backup_begin_failure_preserves_prior_checkpoint(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When backup-begin fails, prior checkpoint is preserved (no
    checkpoint-delete for it) and no data transfer is attempted.
    Successor was never created (backup-begin is atomic), so no
    successor cleanup needed."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-old_snap"

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    backup_error = "backup-begin failed: domain is shut off"
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=False, stdout="", stderr=backup_error, returncode=1, error=backup_error)
    )
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error == backup_error

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    # No qemu-img convert attempted
    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0

    # No checkpoint-delete (prior preserved, successor never created)
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0, (
        "No checkpoint-delete should be called: prior must be preserved, successor never existed"
    )

    # domjobabort still called in finally
    abort_cmds = [cmd for cmd in all_run_cmds if "virsh domjobabort" in cmd]
    assert len(abort_cmds) == 1


def test_prior_discovery_newest_wins(mock_shell):
    """_newest_checkpoint returns newest by embedded timestamp.
    Non-qsnap checkpoints are ignored.  New-format timestamps compared correctly."""
    target_hash = "abc12345"
    # Three checkpoints: oldest, middle, newest (by embedded timestamp)
    newest = "qsnap-abc12345-20260721T010000"
    middle = "qsnap-abc12345-20260720T120000"
    oldest = "qsnap-abc12345-20260719T080000"
    foreign = "manual-one"

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=f"{oldest}\n{foreign}\n{newest}\n{middle}\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        prior = provider._newest_checkpoint("testvm", target_hash)

    assert prior == newest, f"Expected newest checkpoint {newest!r}, got {prior!r}"

    # Verify virsh checkpoint-list was called
    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    cp_list_cmds = [cmd for cmd in all_run_cmds if "checkpoint-list" in cmd]
    assert len(cp_list_cmds) == 1
    assert "testvm" in cp_list_cmds[0]


def test_prior_discovery_legacy_name_parsed(mock_shell):
    """_newest_checkpoint parses legacy checkpoint name with embedded
    timestamp (e.g. qsnap-h-3.Projects_opencode.20260721T0018_vda)."""
    target_hash = "abc12345"
    legacy = f"qsnap-{target_hash}-3.Projects_opencode.20260721T0018_vda"

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=legacy + "\n", stderr="", returncode=0, error=None)
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        prior = provider._newest_checkpoint("testvm", target_hash)

    assert prior == legacy, f"Legacy checkpoint should be recognized, got {prior!r}"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    cp_list_cmds = [cmd for cmd in all_run_cmds if "checkpoint-list" in cmd]
    assert len(cp_list_cmds) == 1


def test_atomic_rotation_deletes_older_after_success(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """After successful incremental, rotation deletes all older checkpoints
    (D3).  The successor checkpoint, created atomically by backup-begin,
    is NOT deleted.  No standalone checkpoint-create-as."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_incr_expectations(
        mock_shell,
        target,
        (prev_backup, "vda", prev_backup.name),
    )

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=prior + "\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    # Rotation: checkpoint-delete for prior only (successor preserved)
    mock_shell.expect("checkpoint-delete").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1, (
        "Exactly one checkpoint-delete expected (prior is oldest, superseded)"
    )
    assert prior in delete_cmds[0], f"Prior checkpoint {prior!r} should be deleted"
    assert "--metadata" in delete_cmds[0]

    # No standalone checkpoint-create-as
    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0

    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]


def test_checkpoint_delete_failure_non_fatal(
    mock_shell, make_vm_config, make_target, tmp_path, caplog
):
    """When rotation's checkpoint-delete fails, BackupResult is still
    success=True and a WARNING is logged."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior = f"qsnap-{target_hash}-old_snap"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_incr_expectations(
        mock_shell,
        target,
        (prev_backup, "vda", prev_backup.name),
    )

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=prior + "\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    # checkpoint-delete FAILS (but is non-fatal)
    mock_shell.expect("checkpoint-delete").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="checkpoint not found",
            returncode=1,
            error="checkpoint not found",
        )
    )
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with (
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    # Transfer still succeeds despite checkpoint-delete failure
    assert len(results) == 1
    assert results[0].success is True, (
        "Transfer should succeed even when checkpoint-delete fails (non-fatal)"
    )

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior in delete_cmds[0]

    # No standalone checkpoint-create-as
    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0

    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    # WARNING logged for failed checkpoint-delete
    warnings = [
        rec
        for rec in caplog.records
        if rec.levelname == "WARNING" and "checkpoint" in rec.getMessage().lower()
    ]
    assert len(warnings) >= 1, "checkpoint-delete failure should log a WARNING"


def test_bitmap_full_backup_creates_atomic_checkpoint(mock_shell, make_target, tmp_path):
    """create_full_backup passes a checkpoint_name to nbd_full_export,
    so backup-begin receives checkpoint XML 3rd arg (D1).  No standalone
    checkpoint-create-as.  No state recording."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("qemu-img convert").returns(_ok_result())
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    mock_shell.expect(r"^mv ").returns(_ok_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    from qsnap.utils.nbd import nbd_full_export as real_nbd_full_export

    with (
        patch("qsnap.modules.backup.bitmap.nbd_full_export", wraps=real_nbd_full_export) as nbd_spy,
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False, bucket_level="monthly"
        )

    assert result.success is True
    assert result.bytes_transferred == 65536

    # nbd_full_export received checkpoint_name
    nbd_spy.assert_called_once()
    _, nbd_kwargs = nbd_spy.call_args
    assert nbd_kwargs.get("checkpoint_name") is not None, (
        "checkpoint_name must be passed to nbd_full_export (D1)"
    )
    checkpoint_name = nbd_kwargs["checkpoint_name"]
    assert checkpoint_name.startswith("qsnap-"), (
        f"checkpoint_name should start with qsnap-, got {checkpoint_name!r}"
    )

    # backup-begin received checkpoint XML 3rd arg
    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        f"backup-begin should receive checkpoint XML 3rd arg, got: {backup_cmds[0]}"
    )

    # No standalone checkpoint-create-as
    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0, (
        "create_full_backup must NOT use checkpoint-create-as (atomic via backup-begin)"
    )

    # No checkpoint-delete on success path
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0


# ══════════════════════════════════════════════════════════════════════════
# NEW TESTS: checkpoint-name collision fix (design D2 uniqueness)
# ══════════════════════════════════════════════════════════════════════════


def test_new_checkpoint_name_bumps_on_collision():
    """When the seconds-resolution candidate is already in *taken*, the
    timestamp is bumped forward one second at a time until unique.

    Verifies the returned name differs from the taken one and still
    matches ``qsnap-{hash}-\\d{8}T\\d{6}``."""
    target_hash = "abc12345"
    frozen_time = datetime(2026, 7, 21, 10, 30, 0)

    with patch("qsnap.modules.backup.bitmap.datetime") as mock_dt:
        mock_dt.now.return_value = frozen_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        # Let datetime.strptime still work through the real implementation
        mock_dt.strptime = datetime.strptime
        mock_dt.min = datetime.min

        # The candidate at the frozen time
        collision_candidate = f"qsnap-{target_hash}-{frozen_time.strftime('%Y%m%dT%H%M%S')}"

        # Pass taken containing the collision candidate → must bump
        bumped = BitmapBackupProvider._new_checkpoint_name(target_hash, taken={collision_candidate})

    # Bumped name must differ from the collision candidate
    assert bumped != collision_candidate, (
        f"Bumped name {bumped!r} must differ from collision candidate {collision_candidate!r}"
    )

    # Still matches the expected format (not microsecond fallback)
    import re

    assert re.fullmatch(rf"qsnap-{target_hash}-\d{{8}}T\d{{6}}", bumped), (
        f"Bumped name {bumped!r} must match qsnap-{target_hash}-YYYYMMDDTHHMMSS"
    )

    # The bumped timestamp should be one second forward
    expected_bumped = (
        f"qsnap-{target_hash}-{(frozen_time + timedelta(seconds=1)).strftime('%Y%m%dT%H%M%S')}"
    )
    assert bumped == expected_bumped, f"Expected bumped name {expected_bumped!r}, got {bumped!r}"


def test_transfer_missing_collision_successor_differs_from_prior(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """When the prior checkpoint name equals the current second's candidate,
    transfer_missing invokes backup-begin with a DIFFERENT successor
    checkpoint name (bump behavior end-to-end at the provider level).

    The prior appears in the candidate list (from checkpoint-list), so
    _new_checkpoint_name(taken=set(candidates)) bumps past it.  The
    successor XML name therefore does NOT equal the prior, and the
    ``<incremental>`` element still names the prior checkpoint."""
    # Freeze time to a known second
    frozen_time = datetime(2026, 7, 21, 15, 30, 0)

    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")
    snapshot = _make_snapshot()

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    # The prior checkpoint has the SAME name as the frozen-time candidate
    prior_checkpoint = f"qsnap-{target_hash}-{frozen_time.strftime('%Y%m%dT%H%M%S')}"

    prev_backup = target_path / "testvm.20241230T000000.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_incr_expectations(
        mock_shell,
        target,
        (prev_backup, "vda", prev_backup.name),
    )

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # stale socket
    # checkpoint-list returns the colliding prior (only one candidate)
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("backup-begin").returns(_ok_result())
    mock_shell.expect("checkpoint-delete").returns(_ok_result())  # rotation
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())  # cleanup

    with (
        patch("qsnap.modules.backup.bitmap.datetime") as mock_dt,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch("qsnap.modules.backup.bitmap.write_checkpoint_xml") as mock_wcxml,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        mock_dt.now.return_value = frozen_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_dt.strptime = datetime.strptime
        mock_dt.min = datetime.min

        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        mock_wcxml.return_value = tmp_path / "qsnap-checkpoint-test.xml"

        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = provider.transfer_missing(vm_config, target, [snapshot])

    assert len(results) == 1
    assert results[0].success is True

    # ── write_backup_xml received the PRIOR checkpoint (not bumped) ──
    mock_wbxml.assert_called_once()
    _, wbxml_kwargs = mock_wbxml.call_args
    assert wbxml_kwargs.get("incremental") == prior_checkpoint, (
        f"write_backup_xml should receive incremental={prior_checkpoint!r}, "
        f"got {wbxml_kwargs.get('incremental')!r}"
    )

    # ── write_checkpoint_xml received a DIFFERENT (bumped) successor ──
    mock_wcxml.assert_called_once()
    wcxml_args, wcxml_kwargs = mock_wcxml.call_args
    successor_name = wcxml_args[0]
    assert successor_name != prior_checkpoint, (
        f"Successor checkpoint name {successor_name!r} must differ from prior {prior_checkpoint!r}"
    )
    assert successor_name.startswith(f"qsnap-{target_hash}-"), (
        f"Successor {successor_name!r} should be a qsnap-{target_hash}- name"
    )
    # Should be one second bumped
    expected_successor = (
        f"qsnap-{target_hash}-{(frozen_time + timedelta(seconds=1)).strftime('%Y%m%dT%H%M%S')}"
    )
    assert successor_name == expected_successor, (
        f"Expected successor {expected_successor!r} (one second bump), got {successor_name!r}"
    )

    # ── backup-begin command line ──────────────────────────────────────
    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]


def test_newest_checkpoint_unchanged_contract(mock_shell):
    """_newest_checkpoint(vm_name, target_hash) still returns the newest
    checkpoint by embedded timestamp, delegating to _select_newest
    after listing via virsh checkpoint-list.  No collision-aware
    behavior changes the selection logic."""
    target_hash = "xyz12345"
    newest = f"qsnap-{target_hash}-20260721T120000"
    older = f"qsnap-{target_hash}-20260720T080000"

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=f"{older}\n{newest}\n", stderr="", returncode=0, error=None
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        prior = provider._newest_checkpoint("testvm", target_hash)

    assert prior == newest, f"Expected {newest!r}, got {prior!r}"

    # Verify checkpoint-list was called exactly once
    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    cp_list_cmds = [cmd for cmd in all_run_cmds if "checkpoint-list" in cmd]
    assert len(cp_list_cmds) == 1
    assert "testvm" in cp_list_cmds[0]


# ══════════════════════════════════════════════════════════════════════════
# NEW TESTS: remove-rsync-filecopy — stopped VM error handling
# ══════════════════════════════════════════════════════════════════════════


def test_create_full_backup_stopped_vm_returns_error(mock_shell, make_target, tmp_path):
    """When the VM is stopped, backup-begin fails with 'domain not running'.
    ``create_full_backup`` returns ``BackupResult(success=False)`` with an
    error message and does NOT fall back to ``qemu-img convert`` on the
    snapshot file."""
    target = make_target(path=str(tmp_path / "backups"))
    target.path.mkdir(parents=True, exist_ok=True)
    snapshot = _make_snapshot()

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("rm -f").returns(_ok_result())
    # backup-begin fails: domain is not running
    mock_shell.expect("backup-begin").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: domain is not running",
            returncode=1,
            error="error: domain is not running",
        )
    )
    mock_shell.expect("domjobabort").returns(_ok_result())
    mock_shell.expect("rm -f").returns(_ok_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.create_full_backup(
            "testvm", snapshot, target, compress=False, bucket_level="monthly"
        )

    assert result.success is False
    assert result.error is not None
    assert "not running" in result.error.lower()

    # No qemu-img convert fallback on the snapshot file
    all_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    convert_cmds = [cmd for cmd in all_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, (
        "No qemu-img convert fallback on snapshot file when VM is stopped"
    )
