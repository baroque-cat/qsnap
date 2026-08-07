"""Unit tests for BitmapBackupProvider (orthogonal ``run_backup`` API).

Tests cover the per-disk ``run_backup(vm_config, target, disk, *, opts)``
entry point (design D3 of orthogonalize-snapshots-and-backups):

- FULL decision: no qsnap checkpoint for the VM+target+disk -> FULL via
  ``qemu-img convert`` (running VM: NBD pull export; stopped VM: direct
  source file) with atomic successor checkpoint creation (checkpoint XML
  as third positional arg of ``virsh backup-begin``).
- Delta decision: a qsnap checkpoint exists -> dirty-block incremental
  via the unified NBD engine (``pread``/``pwrite``), freeze-timestamp
  named ``{vm}.{freeze_ts}_{disk}_{hex6}.qcow2`` (FULL variant has a
  ``.FULL.`` infix).
- Stopped VM + checkpoint -> ``BackupResult(deferred=True)`` with no
  mutation.
- Checkpoint rotation, collision recovery, failure cleanup (partial
  file removed via ``rm -f`` timeout=10, successor checkpoint deleted
  best-effort, prior preserved), ``list()`` returning ``BackupInfo``
  and per-disk result attribution.

All shell calls are intercepted by ``MockShell``; all NBD operations
through ``MockNbdClient`` — zero real I/O.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.interfaces.backup import IBackupProvider
from qsnap.models.config import DiskConfig
from qsnap.models.results import BackupInfo, NbdExtent, NbdResult, ShellResult, SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.utils.retry import is_retryable
from tests.mocks.mock_nbd import MockNbdClient

# ── Helpers ────────────────────────────────────────────────────────────────

# Frozen wall clock + hex suffix so freeze-timestamp backup names and
# successor checkpoint names are deterministic in tests.
_FREEZE_DT = datetime(2026, 8, 8, 3, 0, 0)
_FREEZE_STR = "20260808T030000"
_FREEZE_HEX = "a1b2c3"


def _full_backup_name(vm: str = "testvm", disk: str = "vda") -> str:
    return f"{vm}.FULL.{_FREEZE_STR}_{disk}_{_FREEZE_HEX}"


def _delta_backup_name(vm: str = "testvm", disk: str = "vda") -> str:
    return f"{vm}.{_FREEZE_STR}_{disk}_{_FREEZE_HEX}"


@contextlib.contextmanager
def _frozen_naming():
    """Freeze ``datetime.now()`` and ``secrets.token_hex(3)`` inside the
    bitmap module so freeze-ts names are deterministic."""
    with (
        patch("qsnap.modules.backup.bitmap.datetime") as mock_dt,
        patch("qsnap.modules.backup.bitmap.secrets.token_hex", return_value=_FREEZE_HEX),
    ):
        mock_dt.now.return_value = _FREEZE_DT
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_dt.strptime = datetime.strptime
        mock_dt.min = datetime.min
        yield


def _ok_version_result(version: str = "8.2.0") -> ShellResult:
    """A successful ``virsh --version`` ShellResult."""
    return ShellResult(
        success=True,
        stdout=f"virsh {version}\n",
        stderr="",
        returncode=0,
        error=None,
    )


# Module-level result factory for helper functions (not pytest fixtures).
def _ok() -> ShellResult:
    return ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


def _expect_no_blockjob(mock_shell) -> None:
    """Register the blockjob probe expectation: no active job on the disk."""
    mock_shell.expect("virsh blockjob").returns(
        ShellResult(
            success=True,
            stdout="No current block job\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )


def _setup_incr_expectations(mock_shell, target, prev_data: tuple[Path, str, str]) -> MockNbdClient:
    """Register incremental copy-loop expectations and return a MockNbdClient.

    *prev_data* is ``(prev_backup_path, disk_target, prev_backup_name)``.
    The caller must still register: virsh blockjob, rm -f stale socket,
    checkpoint-list, backup-begin, checkpoint-delete, domjobabort,
    source socket cleanup.  The target directory must exist and contain
    the previous backup file (created by the caller).
    """
    prev_path, disk_target, prev_name = prev_data
    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}-vda.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    nbd = MockNbdClient(size=65536, max_request_size=33554432)
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        f"qemu:dirty-bitmap:backup-{disk_target}": [
            NbdExtent(offset=0, length=65536, data=True),
        ],
    }

    mock_shell.expect("qemu-img info.*" + prev_name).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 65536, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_path}").returns(_ok())
    mock_shell.expect("qemu-img create").returns(_ok())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok())
    mock_shell.expect("qemu-nbd --fork").returns(_ok())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok())
    mock_shell.expect("^mv ").returns(_ok())

    # Step 5b post-transfer validation: chain-to-FULL traversability.
    # qemu-img info --backing-chain on the target file returns a valid
    # JSON array (chain is intact).  Individual tests can override with
    # expect_first for failure scenarios.
    mock_shell.expect(r"qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )
    return nbd


def _add_post_validation_mocks(mock_shell) -> None:
    """Register post-transfer verification expectations for FULL run_backup runs.

    ``verify_full_backup`` (M1 metadata tier) runs ``qemu-img info
    --output=json`` on the transferred file; the FULL file has no
    ``backing-filename`` (standalone) and no corrupt bit.
    """
    mock_shell.expect(r"qemu-img info --output=json").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": 1073741824,
                    "actual-size": 1048576,
                }
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )


def _setup_full_run_expectations(
    mock_shell,
    target,
    vm_name: str = "testvm",
    disk_target: str = "vda",
) -> None:
    """Register expectations for a running-VM FULL ``run_backup``.

    Covers: checkpoint-list (empty -> FULL decision), blockjob probe
    (no active job), stale-socket ``rm -f``, ``virsh backup-begin``
    (checkpoint XML as third positional arg), ``qemu-img convert`` via
    ``run_with_stall_detection``, ``mv .tmp -> final``, the
    ``_full_pull_lifecycle`` finally block (``rm -f .tmp``,
    ``domjobabort``, ``rm -f socket``), post-transfer verification
    (``qemu-img info --output=json``), the rotation checkpoint-list
    (empty -> nothing to delete), and the outer ``run_backup`` finally
    block.  Generic ``rm -f`` / ``domjobabort`` expectations replay for
    every matching call.
    """
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(_ok())  # stale socket + finally cleanups
    mock_shell.expect("backup-begin").returns(_ok())
    # qemu-img convert via run_with_stall_detection
    mock_shell.expect("qemu-img convert").returns(_ok())
    # mv .tmp -> final in _full_pull_lifecycle
    mock_shell.expect("^mv ").returns(_ok())
    # finally block: domjobabort (idempotent — inner + outer)
    mock_shell.expect("virsh domjobabort").returns(_ok())
    # Post-transfer verification (M1): standalone FULL qcow2.
    _add_post_validation_mocks(mock_shell)


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


def test_constructor_rejects_state_manager(mock_shell, mock_state):
    """Provider constructor no longer accepts IStateManager (backup-provider)."""
    with pytest.raises(TypeError):
        BitmapBackupProvider(mock_shell, state=mock_state)  # type: ignore[call-arg]


def test_constructor_works_without_state_manager(mock_shell):
    """Provider works without state manager (nbd is the only optional dep)."""
    provider = BitmapBackupProvider(mock_shell)
    assert provider._nbd is None


# ──────────────────────────────────────────────────────────────────────────
# 1a. _start_write_server signature — compression_type removed (spec D8)
# ──────────────────────────────────────────────────────────────────────────


def test_start_write_server_signature_no_compression_type():
    """_start_write_server no longer accepts compression_type parameter (spec D8)."""
    sig = inspect.signature(BitmapBackupProvider._start_write_server)
    params = sig.parameters
    assert "compression_type" not in params, (
        f"compression_type should NOT be in _start_write_server signature, got: {list(params.keys())}"
    )
    # Verify the expected parameters are present
    assert "target_file" in params
    assert "write_socket" in params
    assert "pid_file" in params
    assert "compress" in params


# ──────────────────────────────────────────────────────────────────────────
# 2. No checkpoints — full NBD export (unified engine, atomic successor)
# ──────────────────────────────────────────────────────────────────────────


def test_run_backup_first_backup_creates_full_with_atomic_checkpoint(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """When no prior checkpoint exists, run_backup performs a FULL export
    via qemu-img convert (design D5) with atomic successor checkpoint.
    No write-side qemu-nbd, no standalone checkpoint-create-as."""
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
    )

    _setup_full_run_expectations(mock_shell, target)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.error is None
    assert result.disk == "vda", f"BackupResult must carry disk='vda', got {result.disk!r}"
    assert result.deferred is False

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        f"backup-begin should receive checkpoint XML 3rd arg, got: {backup_cmds[0]}"
    )

    # qemu-nbd write-side server not started (use startswith to exclude
    # "rm -f /tmp/qsnap-qemu-nbd-<pid>.pid" from outer finally cleanup)
    qemu_nbd_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("qemu-nbd")]
    assert len(qemu_nbd_cmds) == 0, "No write-side qemu-nbd — qemu-img convert replaces it"

    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0, (
        "checkpoint-create-as must NOT be called (atomic via backup-begin)"
    )
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0


def test_full_backup_named_freeze_ts_disk_hex(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """FULL backups are freeze-timestamp named: {vm}.FULL.{ts}_{disk}_{hex6}.qcow2."""
    vm_config = make_vm_config()
    target = make_target(
        path=str(tmp_path / "nonexistent_target"),
        verify="off",
    )

    _setup_full_run_expectations(mock_shell, target)

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run),
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert re.fullmatch(
        r"testvm\.FULL\.\d{8}T\d{6}_vda_[0-9a-f]{6}\.qcow2",
        result.target_path.name,
    ), f"FULL filename must be freeze-ts named, got {result.target_path.name}"


def test_run_backup_result_carries_disk(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """run_backup results carry the source disk (per-disk attribution)."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale socket
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with patch.object(BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 65536)):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.disk == "vda", f"BackupResult must carry disk='vda', got {result.disk!r}"
    assert result.target_path.name.endswith(".qcow2")


# ──────────────────────────────────────────────────────────────────────────
# 3/4. Incremental backup + checkpoint cleanup after successful transfer
# ──────────────────────────────────────────────────────────────────────────


def test_checkpoint_cleanup_after_successful_transfer(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """After successful NBD transfer, rotation deletes superseded checkpoints
    (create-atomically-then-delete-superseded, D3).  No standalone
    ``checkpoint-create-as``."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_incr_expectations(
        mock_shell,
        target,
        (prev_backup, "vda", prev_backup.name),
    )

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # cleanup

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.disk == "vda"

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    mock_wbxml.assert_called_once()
    _, kwargs = mock_wbxml.call_args
    assert kwargs.get("incremental") == prior_checkpoint

    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0]

    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    # First delete is a FULL delete (no --metadata), with metadata fallback only on failure.
    assert "--metadata" not in delete_cmds[0]
    assert prior_checkpoint in delete_cmds[0]
    assert vm_config.name in delete_cmds[0]

    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0

    # No qemu-img convert on any path (unified engine)
    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, "No qemu-img convert — unified NBD engine for ALL paths"


def test_checkpoint_rotation_after_successful_run_backup(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """After a successful incremental, rotation deletes all older checkpoints
    (D3).  The successor checkpoint, created atomically by backup-begin,
    is NOT deleted.  No standalone checkpoint-create-as."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior = f"qsnap-{target_hash}-vda-20241230T000000-dead01"
    middle = f"qsnap-{target_hash}-vda-20250101T000000-dead02"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_incr_expectations(
        mock_shell,
        target,
        (prev_backup, "vda", prev_backup.name),
    )

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    # Discovery + checkpoint-existence re-list + rotation all replay this.
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=f"{middle}\n{prior}\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with (
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    # prior + middle are both older than the successor -> both deleted.
    assert len(delete_cmds) == 2, (
        f"Both superseded checkpoints should be deleted, got {len(delete_cmds)}: {delete_cmds}"
    )
    deleted = {cmd.rsplit(" ", 1)[-1] for cmd in delete_cmds}
    assert deleted == {prior, middle}, (
        f"Expected deletion of {prior!r} and {middle!r}, got {deleted}"
    )
    assert "--metadata" not in delete_cmds[0]

    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0

    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    # No qemu-img convert
    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0


def test_checkpoint_delete_failure_non_fatal(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    caplog,
    success_result,
):
    """When rotation's checkpoint-delete fails, BackupResult is still
    success=True and a WARNING is logged."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_incr_expectations(
        mock_shell,
        target,
        (prev_backup, "vda", prev_backup.name),
    )

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=prior + "\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="checkpoint not found",
            returncode=1,
            error="checkpoint not found",
        )
    )
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with (
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        "Transfer should succeed even when checkpoint-delete fails (non-fatal)"
    )

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    # Two commands: full delete (no --metadata) fails, then metadata fallback also fails.
    assert len(delete_cmds) == 2
    assert prior in delete_cmds[0]
    assert "--metadata" not in delete_cmds[0], "First delete should be full (no --metadata)"
    assert "--metadata" in delete_cmds[1], "Second delete should be metadata-only fallback"

    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0

    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    warnings = [
        rec
        for rec in caplog.records
        if rec.levelname == "WARNING" and "checkpoint" in rec.getMessage().lower()
    ]
    assert len(warnings) >= 1, "checkpoint-delete failure should log a WARNING"


# ──────────────────────────────────────────────────────────────────────────
# 5. Transfer failure preserves checkpoint, successor deleted best-effort
# ──────────────────────────────────────────────────────────────────────────


def test_run_backup_failure_preserves_prior_checkpoint(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """When the copy loop fails, prior checkpoint is NOT deleted,
    successor checkpoint is deleted best-effort, partial file is deleted."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}-vda.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    nbd = MockNbdClient(size=65536, max_request_size=33554432)
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }
    nbd.fail_pread = "pread I/O error"

    mock_shell.expect("qemu-img info.*" + prev_backup.name).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 65536, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(success_result())
    mock_shell.expect("qemu-img create").returns(success_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(success_result())
    mock_shell.expect("qemu-nbd --fork").returns(success_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(success_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # .tmp removal in finally

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # successor best-effort
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # cleanup

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.error is not None
    assert "pread" in result.error.lower()
    assert result.bytes_transferred == 0
    assert result.disk == "vda"

    target_file = target.path / f"{_delta_backup_name()}.qcow2"

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0

    partial_file_cmds = [
        cmd
        for cmd in all_run_cmds
        if cmd.startswith("rm -f") and cmd.endswith(" " + str(target_file))
    ]
    assert len(partial_file_cmds) == 1, (
        f"Expected exactly one rm -f of {target_file}, got:\n" + "\n".join(all_run_cmds)
    )
    # Partial-file cleanup uses a short fixed timeout (10s).
    rm_calls = [
        c for c in run_spy.call_args_list if " ".join(c.args[0]).endswith(" " + str(target_file))
    ]
    assert rm_calls and rm_calls[0].kwargs.get("timeout") == 10, (
        f"rm -f partial file must use timeout=10, got: {rm_calls[0] if rm_calls else None}"
    )

    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    # First delete is a FULL delete (no --metadata).  The full delete succeeds on the successor
    # (the successor was just created and the VM is still running), so no fallback is issued.
    assert "--metadata" not in delete_cmds[0]
    assert vm_config.name in delete_cmds[0]
    assert prior_checkpoint not in delete_cmds[0], "prior checkpoint must NOT be deleted"
    assert f"qsnap-{target_hash}-vda-" in delete_cmds[0], "successor (not prior) should be deleted"

    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0


# ──────────────────────────────────────────────────────────────────────────
# 6. Socket cleanup on success and failure
# ──────────────────────────────────────────────────────────────────────────


def test_socket_cleanup_on_success(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Socket cleanup on success via qemu-img convert; backup-begin receives
    checkpoint XML 3rd arg (D1).  No write-side qemu-nbd socket."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")

    _setup_full_run_expectations(mock_shell, target)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]
    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0


def test_socket_cleanup_on_failure(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Socket cleanup on convert failure; successor deleted best-effort.
    No write-side qemu-nbd socket — only source NBD socket cleaned up."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")

    # qemu-img convert FAILS
    mock_shell.expect_first("qemu-img convert").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="I/O error",
            returncode=1,
            error="qemu-img convert failed",
        )
    )
    # finally: domjobabort, rm -f .tmp, rm -f socket
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    _setup_full_run_expectations(mock_shell, target)
    mock_shell.expect("checkpoint-delete").returns(success_result())  # successor best-effort

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.disk == "vda"

    target_file = target.path / f"{_full_backup_name()}.qcow2"
    rm_calls = [
        c for c in run_spy.call_args_list if " ".join(c.args[0]).endswith(" " + str(target_file))
    ]
    assert len(rm_calls) == 1, "Failed FULL file must be deleted immediately"
    assert rm_calls[0].kwargs.get("timeout") == 10

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0

    # No write-side qemu-nbd (use startswith to exclude outer finally rm -f cleanup)
    qemu_nbd_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("qemu-nbd")]
    assert len(qemu_nbd_cmds) == 0


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
    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        checkpoints = provider.list_checkpoints("testvm")

    assert checkpoints == ["qsnap-abc123-snap1", "qsnap-xyz789-snap2"]
    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    cp_list_cmds = [cmd for cmd in all_run_cmds if "checkpoint-list" in cmd]
    assert len(cp_list_cmds) == 1
    assert "--name" in cp_list_cmds[0]
    assert "--domain" in cp_list_cmds[0]
    assert "testvm" in cp_list_cmds[0]


# ──────────────────────────────────────────────────────────────────────────
# list() returns BackupInfo (target-world model)
# ──────────────────────────────────────────────────────────────────────────


def test_list_returns_backupinfo_no_snapshotinfo(mock_shell, make_target, tmp_path, success_result):
    """``list(target)`` returns ``list[BackupInfo]`` — never SnapshotInfo."""
    target = make_target(path=str(tmp_path / "target"))
    target.path.mkdir(parents=True, exist_ok=True)
    delta = target.path / "testvm.20260808T030000_vda_a1b2c3.qcow2"
    delta.write_bytes(b"")
    full = target.path / "testvm.FULL.20260701T120000_vda_deadbe.qcow2"
    full.write_bytes(b"")

    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 65536, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    provider = BitmapBackupProvider(mock_shell)
    backups = provider.list(target)

    assert len(backups) == 2
    for b in backups:
        assert isinstance(b, BackupInfo)
        assert not isinstance(b, SnapshotInfo), "list() must never return SnapshotInfo"
    by_name = {b.name: b for b in backups}
    assert by_name["testvm.20260808T030000_vda_a1b2c3"].disk == "vda"
    assert by_name["testvm.20260808T030000_vda_a1b2c3"].is_full is False
    assert by_name["testvm.FULL.20260701T120000_vda_deadbe"].disk == "vda"
    assert by_name["testvm.FULL.20260701T120000_vda_deadbe"].is_full is True


def test_delete_accepts_backupinfo(mock_shell, make_target, tmp_path, success_result):
    """``delete(BackupInfo)`` removes the file via ``rm -f``."""
    backup = BackupInfo(
        name="testvm.20260808T030000_vda_a1b2c3",
        path=tmp_path / "target" / "testvm.20260808T030000_vda_a1b2c3.qcow2",
        timestamp=datetime(2026, 8, 8, 3, 0, 0),
        disk="vda",
        is_full=False,
    )
    mock_shell.expect("rm -f").returns(success_result())
    provider = BitmapBackupProvider(mock_shell)
    result = provider.delete(backup)
    assert result.success is True


# ──────────────────────────────────────────────────────────────────────────
# run_backup FULL via unified NBD engine (atomic checkpoint, D1/D2)
# ──────────────────────────────────────────────────────────────────────────


def test_run_backup_full_unified_engine_succeeds(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """run_backup FULL uses qemu-img convert (design D5), not _start_write_server + _transfer.
    Checkpoint XML 3rd arg (D1).  No standalone checkpoint-create-as."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)

    original_run = mock_shell.run
    original_stall = mock_shell.run_with_stall_detection

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(mock_shell, "run_with_stall_detection", wraps=original_stall) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.error is None
    assert result.disk == "vda"
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]

    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        f"backup-begin should receive checkpoint XML 3rd arg, got: {backup_cmds[0]}"
    )

    # qemu-img convert IS used for FULL (design D5) via run_with_stall_detection
    convert_cmds = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) >= 1, "qemu-img convert should be used for FULL backups (design D5)"

    # No qemu-nbd write-side server started — _start_write_server NOT called
    qemu_nbd_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("qemu-nbd")]
    assert len(qemu_nbd_cmds) == 0, "No qemu-nbd write server — qemu-img convert replaces it"

    abort_cmds = [cmd for cmd in all_run_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) >= 1
    assert "testvm" in abort_cmds[0]

    cp_create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(cp_create_cmds) == 0, "run_backup FULL must NOT use checkpoint-create-as"
    cp_delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(cp_delete_cmds) == 0, "run_backup FULL should NOT delete checkpoints on success"


def test_run_backup_full_with_compression(
    mock_shell, make_vm_config, make_target, tmp_path, caplog, success_result
):
    """target.compress=True uses qemu-img convert -c -o compression_type=zstd (design D5).
    No write-side qemu-nbd.  Checkpoint XML 3rd arg."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=True, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)

    original_run = mock_shell.run
    original_stall = mock_shell.run_with_stall_detection

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(mock_shell, "run_with_stall_detection", wraps=original_stall) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.disk == "vda"
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]

    # qemu-img convert -c IS used (design D5)
    convert_cmds = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) >= 1, (
        f"Expected qemu-img convert via run_with_stall_detection, got: {all_stall_cmds}"
    )
    if convert_cmds:
        assert "-c" in convert_cmds[0], f"compress=True should add -c flag, got: {convert_cmds[0]}"
        assert "compression_type=zstd" in convert_cmds[0], (
            f"compress=True should add -o compression_type=zstd, got: {convert_cmds[0]}"
        )

    # No qemu-nbd write-side server (no --image-opts driver=compress)
    qemu_nbd_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("qemu-nbd")]
    assert len(qemu_nbd_cmds) == 0, "No write-side qemu-nbd — qemu-img convert replaces it"

    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    abort_cmds = [cmd for cmd in all_run_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) >= 1

    compress_ignored_warnings = [
        rec for rec in caplog.records if "compress=True ignored" in rec.getMessage()
    ]
    assert len(compress_ignored_warnings) == 0

    cp_create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(cp_create_cmds) == 0
    cp_delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(cp_delete_cmds) == 0


def test_run_backup_full_no_compress_driver_when_compress_false(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """target.compress=False → qemu-img convert without -c flag.  No write-side qemu-nbd."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)

    original_run = mock_shell.run
    original_stall = mock_shell.run_with_stall_detection

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run) as run_spy,
        patch.object(mock_shell, "run_with_stall_detection", wraps=original_stall) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]

    # qemu-img convert IS used (no -c flag)
    convert_cmds = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) >= 1
    if convert_cmds:
        assert "-c" not in convert_cmds[0], (
            f"compress=False should NOT add -c flag, got: {convert_cmds[0]}"
        )

    # No qemu-nbd write-side server
    qemu_nbd_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("qemu-nbd")]
    assert len(qemu_nbd_cmds) == 0, "No write-side qemu-nbd for FULL backups"


def test_run_backup_full_atomic_rename_tmp_to_final(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """run_backup FULL writes to .tmp via qemu-img convert then atomically renames to .qcow2."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)

    original_run = mock_shell.run

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    # Target path ends in .qcow2, not .tmp
    assert result.target_path.suffix == ".qcow2"
    assert ".tmp" not in result.target_path.name

    # Running-VM FULL reports its checkpoint name (design D1): the
    # successor was created atomically by backup-begin.
    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    assert result.checkpoint is not None
    assert re.fullmatch(
        rf"qsnap-{target_hash}-vda-\d{{8}}T\d{{6}}-[0-9a-f]{{6}}",
        result.checkpoint,
    ), f"checkpoint {result.checkpoint!r} must match qsnap-{target_hash}-vda-<ts>-<hex>"

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    mv_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 1, (
        f"Expected exactly one mv command after qemu-img convert, got: {mv_cmds}"
    )
    assert ".tmp" in mv_cmds[0]
    assert ".qcow2" in mv_cmds[0]


def test_run_backup_full_failure_removes_tmp(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """On qemu-img convert failure, .tmp is removed, no final file created."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    # qemu-img convert FAILS via run_with_stall_detection
    mock_shell.expect_first("qemu-img convert").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="I/O error",
            returncode=1,
            error="qemu-img convert failed",
        )
    )
    # finally: rm -f .tmp, domjobabort, rm -f socket
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # rm -f .tmp
    mock_shell.expect("rm -f").returns(success_result())  # rm -f socket_path

    _setup_full_run_expectations(mock_shell, target)
    mock_shell.expect("checkpoint-delete").returns(success_result())  # successor best-effort

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.error is not None
    assert "qemu-img convert failed" in result.error
    assert result.disk == "vda"

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    # .tmp removed in finally block of _full_pull_lifecycle
    tmp_rm = [cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and ".tmp" in cmd]
    assert len(tmp_rm) >= 1, "Expected rm -f of .tmp file in finally"

    # mv never called (convert failed)
    mv_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 0, "mv should not be called on failure"


def test_run_backup_full_socket_cleanup(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Socket cleanup on success and failure; checkpoint XML 3rd arg (D1).
    No write-side qemu-nbd socket — only source NBD socket cleaned up."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    # ── Success case ──
    _setup_full_run_expectations(mock_shell, target)

    original_run = mock_shell.run

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    socket_rm = [
        cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm) >= 2  # stale socket + finally cleanup
    abort_cmds = [cmd for cmd in all_run_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) >= 1
    cp_create = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
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
    fail_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    fail_shell.expect("virsh blockjob").returns(
        ShellResult(
            success=True, stdout="No current block job\n", stderr="", returncode=0, error=None
        )
    )
    fail_shell.expect("rm -f").returns(success_result())  # stale socket
    fail_shell.expect("backup-begin").returns(success_result())

    # qemu-img convert FAILS
    fail_shell.expect_first("qemu-img convert").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="I/O error",
            returncode=1,
            error="qemu-img convert failed",
        )
    )
    # finally cleanup
    fail_shell.expect("domjobabort").returns(success_result())
    fail_shell.expect("rm -f").returns(success_result())  # rm -f .tmp
    fail_shell.expect("rm -f").returns(success_result())  # rm -f socket
    fail_shell.expect("checkpoint-delete").returns(success_result())  # successor best-effort

    with (
        _frozen_naming(),
        patch.object(fail_shell, "run", wraps=fail_shell.run) as fail_spy,
    ):
        provider_fail = BitmapBackupProvider(fail_shell, nbd=None)
        result_fail = provider_fail.run_backup(vm_config, target, vm_config.disks[0])

    assert result_fail.success is False
    assert result_fail.disk == "vda"
    all_cmds_fail = [" ".join(call_obj.args[0]) for call_obj in fail_spy.call_args_list]
    socket_rm_fail = [
        cmd for cmd in all_cmds_fail if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm_fail) >= 1  # source socket cleaned up
    abort_fail = [cmd for cmd in all_cmds_fail if "domjobabort" in cmd]
    assert len(abort_fail) >= 1
    cp_delete_fail = [cmd for cmd in all_cmds_fail if "checkpoint-delete" in cmd]
    assert len(cp_delete_fail) == 1, "Successor checkpoint should be deleted best-effort on failure"


def test_run_backup_full_returns_standalone_qcow2(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """run_backup FULL via qemu-img convert produces standalone qcow2 with no backing file."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)

    original_run = mock_shell.run

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

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


def test_run_backup_full_dotted_vm_name_passed_untruncated(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Bitmap FULL backup passes dotted VM name '3.Projects_opencode' untruncated
    via qemu-img convert and names the target file with the VM name."""
    vm_config = make_vm_config(name="3.Projects_opencode")
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)

    original_run = mock_shell.run

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.error is None
    assert result.disk == "vda"
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    abort_cmds = [cmd for cmd in all_run_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) >= 1
    assert "3.Projects_opencode" in abort_cmds[0]

    # Freeze-timestamp FULL naming with the VM name, not a snapshot name.
    result_filename = result.target_path.name
    assert re.fullmatch(
        r"3\.Projects_opencode\.FULL\.\d{8}T\d{6}_vda_[0-9a-f]{6}\.qcow2",
        result_filename,
    ), f"FULL filename must be freeze-ts named, got {result_filename!r}"


# ──────────────────────────────────────────────────────────────────────────
# shared _full_pull_lifecycle helper — the single FULL path uses it (design D7)
# ──────────────────────────────────────────────────────────────────────────


def test_run_backup_full_delegates_to_full_pull_lifecycle(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """Both the running-VM FULL path and the stopped-VM offline FULL path
    delegate to the shared _full_pull_lifecycle helper (design D7)."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    # ── Part 1: running-VM FULL path ──
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale socket
    mock_shell.expect("backup-begin").returns(success_result())
    # run_backup finally cleanup
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with patch.object(
        BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 0)
    ) as mock_helper:
        provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient())
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert mock_helper.call_count == 1, (
        f"run_backup() FULL should call _full_pull_lifecycle once, got {mock_helper.call_count}"
    )

    # ── Part 2: stopped-VM offline FULL path ──
    from tests.mocks.mock_shell import MockShell

    shell2 = MockShell()
    shell2.expect("virsh dominfo").returns(
        ShellResult(success=True, stdout="State: shut off\n", stderr="", returncode=0, error=None)
    )
    shell2.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    shell2.expect("qemu-img convert").returns(success_result())
    shell2.expect("^mv ").returns(success_result())
    # _full_pull_lifecycle finally: rm .tmp + domjobabort
    shell2.expect("domjobabort").returns(success_result())
    shell2.expect("rm -f").returns(success_result())
    # run_backup stopped finally: rm .tmp
    shell2.expect("rm -f").returns(success_result())

    with patch.object(
        BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 0)
    ) as mock_helper2:
        provider2 = BitmapBackupProvider(shell2, nbd=MockNbdClient())
        result2 = provider2.run_backup(vm_config, target, vm_config.disks[0])

    assert result2.success is True
    assert mock_helper2.call_count == 1, (
        f"stopped-VM FULL should call _full_pull_lifecycle once, got {mock_helper2.call_count}"
    )


def test_run_backup_nbd_job_terminated_after_transfer(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """domjobabort called after qemu-img convert via run_backup FULL."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)

    original_run = mock_shell.run

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    abort_cmds = [cmd for cmd in all_run_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) >= 1
    assert "--domain" in abort_cmds[0]
    assert "testvm" in abort_cmds[0]

    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]


def test_run_backup_socket_cleanup_after_job_abort(
    mock_shell, make_vm_config, make_target, tmp_path, caplog, success_result
):
    """Socket cleanup even when domjobabort fails.  No write-side qemu-nbd socket."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)
    # Override helper's domjobabort expectation so we get failure + WARNING
    mock_shell.expect_first("virsh domjobabort").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: domain is not running",
            returncode=1,
            error="error: domain is not running",
        )
    )

    original_run = mock_shell.run

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    abort_cmds = [cmd for cmd in all_run_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) >= 1
    socket_rm = [
        cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    ]
    assert len(socket_rm) >= 2

    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    abort_idx = None
    last_rm_idx = None
    for i, cmd in enumerate(all_run_cmds):
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


def test_run_backup_first_full_pull_via_unified_engine(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """run_backup FULL via qemu-img convert with atomic checkpoint.
    No standalone checkpoint-create-as."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)

    original_run = mock_shell.run

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.error is None
    assert result.disk == "vda"
    assert result.bytes_transferred == 65536

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    assert any(cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd for cmd in all_run_cmds)
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        f"backup-begin should receive checkpoint XML 3rd arg, got: {backup_cmds[0]}"
    )
    abort_cmds = [cmd for cmd in all_run_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) >= 1
    socket_rm_count = sum(
        1 for cmd in all_run_cmds if cmd.startswith("rm -f") and "/tmp/qsnap-backup-" in cmd
    )
    assert socket_rm_count >= 2
    assert not any("checkpoint-create-as" in cmd for cmd in all_run_cmds), (
        "run_backup FULL must NOT use checkpoint-create-as (atomic via backup-begin)"
    )


def test_run_backup_domjobabort_called_after_successful_transfer(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """Successful full-pull transfer — domjobabort called twice (inner + outer finally).

    domjobabort is called twice: once by _full_pull_lifecycle's finally
    (inner cleanup) and once by run_backup's outer finally
    (idempotent — domjobabort is always safe to call multiple times)."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")

    _setup_full_run_expectations(mock_shell, target)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    abort_cmds = [cmd for cmd in all_run_cmds if "virsh domjobabort" in cmd]
    # domjobabort is called twice: _full_pull_lifecycle finally + run_backup outer finally
    assert len(abort_cmds) == 2
    for abort_cmd in abort_cmds:
        assert "--domain" in abort_cmd
        assert vm_config.name in abort_cmd

    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0


def test_run_backup_domjobabort_called_after_failed_transfer(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """backup-begin fails — domjobabort still called.  Successor never created, no checkpoint-delete."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    backup_error = "backup-begin failed: domain is shut off"
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=False, stdout="", stderr=backup_error, returncode=1, error=backup_error)
    )
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.error == backup_error
    assert result.disk == "vda"

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    abort_cmds = [cmd for cmd in all_run_cmds if "virsh domjobabort" in cmd]
    assert len(abort_cmds) == 1
    assert "--domain" in abort_cmds[0]
    assert vm_config.name in abort_cmds[0]

    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0, (
        "No checkpoint-delete (backup-begin is atomic — checkpoint never created)"
    )


def test_run_backup_domjobabort_failure_is_non_fatal(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    caplog,
    success_result,
):
    """domjobabort fails — transfer still succeeds via qemu-img convert, WARNING logged.

    domjobabort is called twice (inner + outer finally, both idempotent).
    Both calls fail, two WARNINGs are logged, and the transfer still succeeds."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")

    _setup_full_run_expectations(mock_shell, target)
    # Override helper's domjobabort expectation so we get failure + WARNING
    mock_shell.expect_first("virsh domjobabort").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: domain is not running",
            returncode=1,
            error="error: domain is not running",
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    abort_cmds = [cmd for cmd in all_run_cmds if "virsh domjobabort" in cmd]
    # domjobabort is called twice: _full_pull_lifecycle finally + run_backup outer finally
    assert len(abort_cmds) == 2

    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "qsnap-checkpoint-" in backup_cmds[0]

    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0

    warnings = [
        rec
        for rec in caplog.records
        if rec.levelname == "WARNING" and "domjobabort" in rec.getMessage().lower()
    ]
    assert len(warnings) >= 1


# ──────────────────────────────────────────────────────────────────────────
# Failed file deletion + successor checkpoint best-effort
# ──────────────────────────────────────────────────────────────────────────


def test_run_backup_failure_deletes_partial_file(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Copy loop failure → partial file deleted, successor checkpoint deleted best-effort."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}-vda.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    nbd = MockNbdClient(size=65536, max_request_size=33554432)
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }
    nbd.fail_pread = "I/O error"

    mock_shell.expect("qemu-img info.*" + prev_backup.name).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 65536, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(success_result())
    mock_shell.expect("qemu-img create").returns(success_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(success_result())
    mock_shell.expect("qemu-nbd --fork").returns(success_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(success_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("rm -f").returns(success_result())  # stale socket
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # successor best-effort
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # socket cleanup

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.disk == "vda"

    target_file = target.path / f"{_delta_backup_name()}.qcow2"
    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    partial_file_cmds = [
        cmd
        for cmd in all_run_cmds
        if cmd.startswith("rm -f") and cmd.endswith(" " + str(target_file))
    ]
    assert len(partial_file_cmds) == 1
    rm_calls = [
        c for c in run_spy.call_args_list if " ".join(c.args[0]).endswith(" " + str(target_file))
    ]
    assert rm_calls and rm_calls[0].kwargs.get("timeout") == 10, (
        "partial-file rm -f must use timeout=10"
    )
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint not in delete_cmds[0]
    assert f"qsnap-{target_hash}-vda-" in delete_cmds[0]

    # No qemu-img convert
    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0


def test_run_backup_verify_failure_deletes_file(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Verify failure → partial file deleted, successor checkpoint deleted best-effort."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="metadata")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}-vda.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    nbd = MockNbdClient(size=65536, max_request_size=33554432)
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }

    mock_shell.expect("qemu-img info.*" + prev_backup.name).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 65536, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(success_result())
    mock_shell.expect("qemu-img create").returns(success_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(success_result())
    mock_shell.expect("qemu-nbd --fork").returns(success_result())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(success_result())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(success_result())
    mock_shell.expect("^mv ").returns(success_result())

    # Verification: source info fails
    mock_shell.expect_first(r"qemu-img info.*--force-share").returns(
        ShellResult(success=False, stdout="", stderr="I/O error", returncode=1, error="I/O error")
    )
    # Backing-chain validation: the --backing-chain pattern is more
    # specific than --force-share, so it must be inserted AFTER the
    # --force-share expect_first to land at the front of the list and
    # match first for _validate_backing_chain.
    mock_shell.expect_first(r"qemu-img info.*--backing-chain").returns(success_result())

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("rm -f").returns(success_result())  # stale socket
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # successor best-effort
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # socket cleanup

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.error is not None
    assert "verification failed" in result.error

    target_file = target.path / f"{_delta_backup_name()}.qcow2"
    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]

    partial_file_cmds = [
        cmd
        for cmd in all_run_cmds
        if cmd.startswith("rm -f") and cmd.endswith(" " + str(target_file))
    ]
    assert len(partial_file_cmds) == 1
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint not in delete_cmds[0]
    assert f"qsnap-{target_hash}-vda-" in delete_cmds[0]

    # No qemu-img convert
    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0


# ══════════════════════════════════════════════════════════════════════════
# ATOMIC CHECKPOINT TESTS
# ══════════════════════════════════════════════════════════════════════════


def test_atomic_full_export_passes_checkpoint_xml(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """run_backup with no prior checkpoint: backup-begin receives 3rd
    positional arg (checkpoint XML).  No standalone checkpoint-create-as.
    Full-pull via qemu-img convert (design D5)."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")

    _setup_full_run_expectations(mock_shell, target)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        f"backup-begin must have checkpoint XML as 3rd arg (D1), got: {backup_cmds[0]}"
    )

    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0, (
        "checkpoint-create-as must NOT be called (atomic via backup-begin)"
    )

    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0


def test_atomic_incremental_passes_checkpoint_xml_and_incremental(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """run_backup with prior checkpoint: write_backup_xml receives
    incremental=prior, backup-begin receives 3 positional args (D1).  No
    checkpoint-create-as.  Rotation deletes prior after success."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_incr_expectations(
        mock_shell,
        target,
        (prev_backup, "vda", prev_backup.name),
    )

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with (
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True

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

    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0

    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint in delete_cmds[0]
    # First delete is a FULL delete (no --metadata); metadata fallback only on failure.
    assert "--metadata" not in delete_cmds[0]

    # No qemu-img convert
    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0


def test_run_backup_backup_begin_failure_preserves_prior_checkpoint(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """When backup-begin fails, prior checkpoint is preserved (no
    checkpoint-delete for it) and no data transfer is attempted.
    Successor was never created (backup-begin is atomic), so no
    successor cleanup needed."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    backup_error = "backup-begin failed: domain is shut off"
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=False, stdout="", stderr=backup_error, returncode=1, error=backup_error)
    )
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.error == backup_error
    assert result.disk == "vda"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0

    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 0, (
        "No checkpoint-delete should be called: prior must be preserved, successor never existed"
    )

    abort_cmds = [cmd for cmd in all_run_cmds if "virsh domjobabort" in cmd]
    assert len(abort_cmds) == 1


def test_prior_discovery_newest_wins(mock_shell):
    """_newest_checkpoint returns newest by embedded timestamp.
    Non-qsnap checkpoints are ignored.  New-format timestamps compared correctly."""
    target_hash = "abc12345"
    newest = "qsnap-abc12345-vda-20260721T010000"
    middle = "qsnap-abc12345-vda-20260720T120000"
    oldest = "qsnap-abc12345-vda-20260719T080000"
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
        prior = provider._newest_checkpoint("testvm", target_hash, "vda")

    assert prior == newest, f"Expected newest checkpoint {newest!r}, got {prior!r}"

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
        prior = provider._newest_checkpoint("testvm", target_hash, "vda")

    assert prior is None, (
        f"Legacy name {legacy!r} should not be found by per-disk filter, got {prior!r}"
    )

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    cp_list_cmds = [cmd for cmd in all_run_cmds if "checkpoint-list" in cmd]
    assert len(cp_list_cmds) == 1


def test_newest_checkpoint_unchanged_contract(mock_shell):
    """_newest_checkpoint(vm_name, target_hash, disk) still returns the newest
    checkpoint by embedded timestamp."""
    target_hash = "xyz12345"
    newest = f"qsnap-{target_hash}-vda-20260721T120000"
    older = f"qsnap-{target_hash}-vda-20260720T080000"

    mock_shell.expect("virsh --version").returns(_ok_version_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=f"{older}\n{newest}\n", stderr="", returncode=0, error=None
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        prior = provider._newest_checkpoint("testvm", target_hash, "vda")

    assert prior == newest, f"Expected {newest!r}, got {prior!r}"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    cp_list_cmds = [cmd for cmd in all_run_cmds if "checkpoint-list" in cmd]
    assert len(cp_list_cmds) == 1
    assert "testvm" in cp_list_cmds[0]


# ══════════════════════════════════════════════════════════════════════════
# CHECKPOINT NAME COLLISION TESTS
# ══════════════════════════════════════════════════════════════════════════


def test_new_checkpoint_name_bumps_on_collision():
    """When the seconds-resolution candidate with hex suffix is already in
    *taken*, the timestamp is bumped forward one second at a time until unique."""
    target_hash = "abc12345"
    frozen_time = datetime(2026, 7, 21, 10, 30, 0)

    with patch("qsnap.modules.backup.bitmap.datetime") as mock_dt:
        mock_dt.now.return_value = frozen_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_dt.strptime = datetime.strptime
        mock_dt.min = datetime.min

        # Mock secrets.token_hex to return a predictable value so the
        # first-iteration name collides with the taken set, forcing a bump.
        with patch("qsnap.modules.backup.bitmap.secrets.token_hex", return_value="deadbe"):
            collision_candidate = (
                f"qsnap-{target_hash}-vda-{frozen_time.strftime('%Y%m%dT%H%M%S')}-deadbe"
            )
            bumped = BitmapBackupProvider._new_checkpoint_name(
                target_hash, "vda", taken={collision_candidate}
            )

    assert bumped != collision_candidate, (
        f"Bumped name {bumped!r} must differ from collision candidate {collision_candidate!r}"
    )

    # New format: qsnap-{target_hash}-vda-YYYYMMDDTHHMMSS-{6_hex_chars}
    assert re.fullmatch(rf"qsnap-{target_hash}-vda-\d{{8}}T\d{{6}}-[0-9a-f]{{6}}", bumped), (
        f"Bumped name {bumped!r} must match qsnap-{target_hash}-vda-YYYYMMDDTHHMMSS-XXXXXX"
    )

    # With predictable hex=deadbe, the bump shifts timestamp by +1 second
    expected_bumped = (
        f"qsnap-{target_hash}-vda-"
        f"{(frozen_time + timedelta(seconds=1)).strftime('%Y%m%dT%H%M%S')}"
        f"-deadbe"
    )
    assert bumped == expected_bumped, f"Expected bumped name {expected_bumped!r}, got {bumped!r}"


def test_new_checkpoint_name_includes_uuid_suffix():
    """Checkpoint name includes UUID hex suffix.
    Format: qsnap-{target_hash}-vda-{yyyymmddTHHMMSS}-{6_hex_chars}"""
    target_hash = "abc12345"
    name = BitmapBackupProvider._new_checkpoint_name(target_hash, "vda", taken=set())

    pattern = rf"^qsnap-{re.escape(target_hash)}-vda-\d{{8}}T\d{{6}}-[0-9a-f]{{6}}$"
    assert re.fullmatch(pattern, name), (
        f"Name {name!r} must match qsnap-{target_hash}-YYYYMMDDTHHMMSS-XXXXXX"
    )
    # The hex suffix should be exactly 6 characters.
    hex_suffix = name.rsplit("-", 1)[-1]
    assert len(hex_suffix) == 6, (
        f"Hex suffix should be 6 chars, got {hex_suffix!r} ({len(hex_suffix)})"
    )


def test_parse_checkpoint_timestamp_with_uuid_suffix():
    """_parse_checkpoint_timestamp handles both old format (no suffix)
    and new format (with hex suffix)."""
    target_hash = "abc12345"

    # Old format: no suffix
    old_name = f"qsnap-{target_hash}-vda-20240101T120000"
    old_ts = BitmapBackupProvider._parse_checkpoint_timestamp(old_name, target_hash, "vda")
    assert old_ts is not None, f"Failed to parse old-format name {old_name!r}"
    assert old_ts == datetime(2024, 1, 1, 12, 0, 0), f"Expected 2024-01-01T12:00:00, got {old_ts}"

    # New format: with hex suffix
    new_name = f"qsnap-{target_hash}-vda-20240101T120000-a1b2c3"
    new_ts = BitmapBackupProvider._parse_checkpoint_timestamp(new_name, target_hash, "vda")
    assert new_ts is not None, f"Failed to parse new-format name {new_name!r}"
    assert new_ts == datetime(2024, 1, 1, 12, 0, 0), f"Expected 2024-01-01T12:00:00, got {new_ts}"

    # Both parse to the same timestamp
    assert old_ts == new_ts


def test_checkpoint_collision_force_cleanup_and_retry(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """Bitmap collision triggers _force_cleanup_checkpoints, then retries
    backup-begin with a fresh successor.  The retry succeeds."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    target_hash = BitmapBackupProvider.target_hash(str(target.path))

    # Pre-register basic expectations.  Discovery sees NO checkpoint
    # (FULL decision); after the collision force-clean the re-list also
    # returns empty, so the retry proceeds as a FULL.
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale socket
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # socket cleanup

    # Custom run: first backup-begin fails with collision,
    # second (retry) succeeds.
    backup_call_count = [0]
    original_run = mock_shell.run

    def collision_run(cmd, timeout, check=False):
        cmd_str = " ".join(cmd)
        if "backup-begin" in cmd_str:
            backup_call_count[0] += 1
            if backup_call_count[0] == 1:
                return ShellResult(
                    success=False,
                    stdout="",
                    stderr="Bitmap already exists",
                    returncode=1,
                    error="Bitmap already exists",
                )
            return success_result()
        return original_run(cmd, timeout, check)

    with (
        patch.object(mock_shell, "run", side_effect=collision_run) as run_spy,
        patch.object(BitmapBackupProvider, "_force_cleanup_checkpoints") as mock_force_cleanup,
        patch.object(BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 65536)),
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, f"Expected retry to succeed, got error: {result.error}"
    assert result.disk == "vda"

    # _force_cleanup_checkpoints was called with correct args.
    mock_force_cleanup.assert_called_once_with(vm_config.name, target_hash, "vda")

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 2, (
        f"Expected 2 backup-begin calls (first failed + retry), got {len(backup_cmds)}"
    )


def test_force_cleanup_checkpoints_deletes_all(mock_shell, success_result):
    """_force_cleanup_checkpoints deletes ALL qsnap checkpoints for the
    VM+target via _delete_checkpoint_best_effort."""
    vm_name = "testvm"
    target_hash = "abc12345"
    prefix = f"qsnap-{target_hash}-vda-"

    checkpoints = [
        f"{prefix}20240101T120000",
        f"{prefix}20240102T120000",
        f"{prefix}20240103T120000-deadbe",
    ]

    # checkpoint-list returns all three
    mock_shell.expect_first("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout="\n".join(checkpoints) + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Each checkpoint will be deleted via _delete_checkpoint_best_effort
    # which issues a full delete (no --metadata).
    for _ in checkpoints:
        mock_shell.expect("checkpoint-delete").returns(success_result())

    # virsh --version for list_checkpoints
    mock_shell.expect("virsh --version").returns(_ok_version_result())

    with (
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch.object(BitmapBackupProvider, "_delete_checkpoint_best_effort") as mock_delete,
    ):
        provider = BitmapBackupProvider(mock_shell)
        provider._force_cleanup_checkpoints(vm_name, target_hash, "vda")

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    # checkpoint-list was called for the listing
    cp_list_cmds = [cmd for cmd in all_run_cmds if "checkpoint-list" in cmd]
    assert len(cp_list_cmds) == 1
    assert vm_name in cp_list_cmds[0]

    # _delete_checkpoint_best_effort called for each checkpoint
    assert mock_delete.call_count == len(checkpoints), (
        f"Expected delete for each of {len(checkpoints)} checkpoints, "
        f"got {mock_delete.call_count} calls"
    )
    called_names = {c.args[0] for c in mock_delete.call_args_list}
    assert called_names == {vm_name}, "All calls should use the same VM name"
    called_checkpoints = {c.args[1] for c in mock_delete.call_args_list}
    assert called_checkpoints == set(checkpoints), (
        f"Expected deletion of {set(checkpoints)}, got {called_checkpoints}"
    )


# ══════════════════════════════════════════════════════════════════════════
# STOPPED VM + run_backup: deferral and offline FULL
# ══════════════════════════════════════════════════════════════════════════


def test_stopped_vm_with_checkpoint_defers_no_mutation(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Stopped VM + existing checkpoint → BackupResult(deferred=True).
    No backup-begin, no transfer, no checkpoint mutation, no files."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(success=True, stdout="State: shut off\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient())
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.deferred is True, "Stopped VM with checkpoint must defer"
    assert result.disk == "vda"
    assert result.bytes_transferred == 0

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    assert len(all_run_cmds) == 2, (
        f"Only checkpoint-list + dominfo expected (no mutation), got: {all_run_cmds}"
    )
    assert not any("backup-begin" in cmd for cmd in all_run_cmds)
    assert not any("checkpoint-delete" in cmd for cmd in all_run_cmds)
    # No backup file created on the target.
    assert list(target.path.glob("*.qcow2")) == []


def test_active_blockjob_defers_run_backup(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Running VM with an active blockjob on the disk → deferred (design D9)."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True, stdout=prior_checkpoint + "\n", stderr="", returncode=0, error=None
        )
    )
    # dominfo from conftest → running; blockjob probe reports an active job.
    mock_shell.expect("virsh blockjob").returns(
        ShellResult(
            success=True,
            stdout="Block job: active\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient())
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.deferred is True, "Active blockjob must defer the disk backup"
    assert result.disk == "vda"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    assert not any("backup-begin" in cmd for cmd in all_run_cmds)
    assert not any("checkpoint-delete" in cmd for cmd in all_run_cmds)
    assert list(target.path.glob("*.qcow2")) == []


def test_run_backup_stopped_vm_returns_error_never_happens(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Stopped VM + NO checkpoint → offline FULL (no backup-begin).  The
    old 'backup-begin fails on stopped VM' path no longer exists."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(success=True, stdout="State: shut off\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("qemu-img convert").returns(success_result())
    mock_shell.expect("^mv ").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.deferred is False
    assert result.disk == "vda"
    assert result.checkpoint is None, "Offline FULL creates no checkpoint (design D1)"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 0, "Stopped VM should NOT call virsh backup-begin"
    assert not any("nbd:unix:" in cmd for cmd in all_run_cmds), (
        "Stopped VM FULL must read from the source file, not NBD"
    )


def test_run_backup_stopped_vm_custom_flags(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Stopped VM: direct qemu-img convert from source qcow2 with custom parallel + out_of_order."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(success=True, stdout="State: shut off\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("qemu-img convert").returns(success_result())
    mock_shell.expect("^mv ").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    original_run = mock_shell.run
    original_stall = mock_shell.run_with_stall_detection

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run),
        patch.object(mock_shell, "run_with_stall_detection", wraps=original_stall) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(
            vm_config,
            target,
            vm_config.disks[0],
            convert_parallel=2,
            convert_out_of_order=False,
        )

    assert result.success is True

    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    convert_cmds = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) >= 1
    # Stopped VM: source = file path, not nbd:unix:
    assert "nbd:unix:" not in convert_cmds[0], (
        f"Stopped VM should use file path, got NBD URI: {convert_cmds[0]}"
    )
    assert "-m 2" in convert_cmds[0], f"Expected -m 2, got: {convert_cmds[0]}"
    assert "-W" not in convert_cmds[0]


def test_run_backup_stopped_vm_no_compression(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Stopped VM: direct convert without compression flags."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), compress=False, verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(success=True, stdout="State: shut off\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("qemu-img convert").returns(success_result())
    mock_shell.expect("^mv ").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    original_run = mock_shell.run
    original_stall = mock_shell.run_with_stall_detection

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run),
        patch.object(mock_shell, "run_with_stall_detection", wraps=original_stall) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True

    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    convert_cmds = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) >= 1
    assert "-c" not in convert_cmds[0], (
        f"compress=False should omit -c flag, got: {convert_cmds[0]}"
    )


def test_run_backup_stopped_vm_direct_convert(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Stopped VM: qemu-img convert reads from file path, not NBD socket."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(success=True, stdout="State: shut off\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("qemu-img convert").returns(success_result())
    mock_shell.expect("^mv ").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    original_run = mock_shell.run
    original_stall = mock_shell.run_with_stall_detection

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run),
        patch.object(mock_shell, "run_with_stall_detection", wraps=original_stall) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True

    # Stopped-VM FULL creates no checkpoint — BackupResult.checkpoint
    # must be None (design D1).
    assert result.checkpoint is None

    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    convert_cmds = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) >= 1
    assert "nbd:unix:" not in convert_cmds[0], (
        f"Stopped VM should read from file, got: {convert_cmds[0]}"
    )
    assert "/var/lib/libvirt/images/testvm.qcow2" in convert_cmds[0], (
        f"Expected source path in command, got: {convert_cmds[0]}"
    )


def test_run_backup_running_vm_reports_checkpoint_name(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Running-VM FULL reports its checkpoint name (design D1)."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)

    original_run = mock_shell.run

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.checkpoint is not None

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    assert re.fullmatch(
        rf"qsnap-{target_hash}-vda-\d{{8}}T\d{{6}}-[0-9a-f]{{6}}",
        result.checkpoint,
    ), f"checkpoint {result.checkpoint!r} must match qsnap-{target_hash}-vda-<ts>-<hex>"


def test_run_backup_stopped_vm_reports_no_checkpoint(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """Stopped-VM FULL reports no checkpoint (design D1)."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    mock_shell.expect_first("virsh dominfo").returns(
        ShellResult(success=True, stdout="State: shut off\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("qemu-img convert").returns(success_result())
    mock_shell.expect("^mv ").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    original_run = mock_shell.run
    original_stall = mock_shell.run_with_stall_detection

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run),
        patch.object(mock_shell, "run_with_stall_detection", wraps=original_stall),
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.checkpoint is None


def test_run_backup_backup_begin_failure_reports_no_checkpoint(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """backup-begin failure reports no checkpoint (design D1).

    ``virsh backup-begin`` is atomic — when it fails, the successor
    checkpoint was never created, so ``BackupResult.checkpoint`` must
    be ``None`` (Core's exact-name rollback must delete nothing)."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    # The mock_shell fixture's dominfo returns running → running-VM path.
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale socket
    backup_error = "backup-begin failed: domain is shut off"
    mock_shell.expect("backup-begin").returns(
        ShellResult(success=False, stdout="", stderr=backup_error, returncode=1, error=backup_error)
    )
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    provider = BitmapBackupProvider(mock_shell, nbd=None)
    result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.error == backup_error
    assert result.checkpoint is None


# ── Custom convert flags / stall detection (running-VM FULL) ──────────────


def test_run_backup_full_defaults_to_qemu_img_convert(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """run_backup FULL defaults to the qemu-img-convert engine."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())

    original_run = mock_shell.run

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with patch.object(mock_shell, "run", side_effect=spied_run):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.bytes_transferred == 65536


def test_full_pull_lifecycle_always_qemu_img_convert(mock_shell, tmp_path, caplog, success_result):
    """_full_pull_lifecycle always calls _qemu_img_convert_transfer."""
    tmp_file = tmp_path / "test.qcow2.tmp"
    final_file = tmp_path / "test.qcow2"

    provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient(size=65536))

    with patch.object(
        provider, "_qemu_img_convert_transfer", return_value=(None, 65536)
    ) as mock_convert:
        mock_shell.expect("^mv ").returns(success_result())
        mock_shell.expect("domjobabort").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())
        error, dirty = provider._full_pull_lifecycle(
            vm_name="testvm",
            tmp_file=tmp_file,
            final_file=final_file,
            socket_path="/tmp/test.sock",
            source_path=None,
            compress=False,
            compression_type="zstd",
            stall_timeout=1800,
            backup_xml_path=None,
            checkpoint_xml_path=None,
            disk_target="vda",
            convert_parallel=4,
            convert_out_of_order=True,
        )
    assert error is None
    assert dirty == 65536
    mock_convert.assert_called_once()


def test_run_backup_full_custom_convert_parallel(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """convert_parallel=2 → qemu-img convert -m 2."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())

    original_run = mock_shell.run
    original_stall = mock_shell.run_with_stall_detection

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run),
        patch.object(mock_shell, "run_with_stall_detection", wraps=original_stall) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0], convert_parallel=2)

    assert result.success is True

    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    convert_cmds = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) >= 1
    # With compress=False: qemu-img convert -O qcow2 -m 2 -p <src> <dst>
    assert "-m 2" in convert_cmds[0], f"Expected -m 2 in command, got: {convert_cmds[0]}"


def test_run_backup_full_convert_out_of_order_disabled(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """convert_out_of_order=False → no -W flag in qemu-img convert."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())

    original_run = mock_shell.run
    original_stall = mock_shell.run_with_stall_detection

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run),
        patch.object(mock_shell, "run_with_stall_detection", wraps=original_stall) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(
            vm_config, target, vm_config.disks[0], convert_out_of_order=False
        )

    assert result.success is True

    all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
    convert_cmds = [cmd for cmd in all_stall_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) >= 1
    assert "-W" not in convert_cmds[0], (
        f"convert_out_of_order=False should omit -W flag, got: {convert_cmds[0]}"
    )


def test_run_backup_qemu_img_convert_uses_stall_detection(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """run_backup FULL uses run_with_stall_detection for qemu-img convert."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())

    original_run = mock_shell.run
    original_stall = mock_shell.run_with_stall_detection

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run),
        patch.object(mock_shell, "run_with_stall_detection", wraps=original_stall) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0], stall_timeout=1800)

    assert result.success is True
    assert stall_spy.call_count >= 1
    # Verify the stall_timeout parameter is passed
    stall_call = stall_spy.call_args_list[-1]
    assert stall_call.kwargs.get("stall_timeout") == 1800


def test_run_backup_stall_timeout_disabled(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """stall_timeout=0 disables stall detection (stall_timeout=0 passed through)."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "backups"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    _setup_full_run_expectations(mock_shell, target)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())

    original_run = mock_shell.run
    original_stall = mock_shell.run_with_stall_detection

    def spied_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("mv "):
            Path(cmd[-1]).write_bytes(b"\x00" * 65536)
        return original_run(cmd, timeout)

    with (
        patch.object(mock_shell, "run", side_effect=spied_run),
        patch.object(mock_shell, "run_with_stall_detection", wraps=original_stall) as stall_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=None)
        result = provider.run_backup(vm_config, target, vm_config.disks[0], stall_timeout=0)

    assert result.success is True
    # run_with_stall_detection still called, but stall_timeout=0
    stall_call = stall_spy.call_args_list[-1]
    assert stall_call.kwargs.get("stall_timeout") == 0


# ── Libnbd zero_skip ──────────────────────────────────────────────────


def test_libnbd_full_zero_skip_all_zero(mock_shell, tmp_path):
    """_transfer with zero_skip=True and all-zero data → zero bytes pwritten."""
    write_socket = f"/tmp/qsnap-write-{os.getpid()}.sock"

    nbd = MockNbdClient(size=65536, max_request_size=65536)
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
    }
    # All-zero data
    nbd.pread_handler = lambda o, length: NbdResult(
        success=True, payload=b"\x00" * length, error=None
    )

    provider = BitmapBackupProvider(mock_shell, nbd=nbd)

    error, dirty_bytes = provider._transfer(
        socket_path=f"/tmp/qsnap-backup-{os.getpid()}.sock",
        write_socket=write_socket,
        disk_target="vda",
        meta_contexts=["base:allocation"],
        zero_skip=True,
    )

    assert error is None
    # Zero chunks are skipped, so no pwrite calls
    write_calls = [c for c in nbd.calls if c[0] == "pwrite"]
    assert len(write_calls) == 0, (
        f"All-zero data should be skipped, got pwrite calls: {write_calls}"
    )
    assert nbd.bytes_written == 0
    # dirty_bytes counts total extent length even if not written
    assert dirty_bytes == 65536


def test_libnbd_full_zero_skip_non_zero(mock_shell, tmp_path):
    """_transfer with zero_skip=True and non-zero data → data is written (transfer succeeds)."""
    write_socket = f"/tmp/qsnap-write-{os.getpid()}.sock"

    nbd = MockNbdClient(size=65536, max_request_size=65536)
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
    }
    # Non-zero data
    nbd.pread_handler = lambda o, length: NbdResult(
        success=True, payload=b"\x01" * length, error=None
    )

    provider = BitmapBackupProvider(mock_shell, nbd=nbd)

    error, dirty_bytes = provider._transfer(
        socket_path=f"/tmp/qsnap-backup-{os.getpid()}.sock",
        write_socket=write_socket,
        disk_target="vda",
        meta_contexts=["base:allocation"],
        zero_skip=True,
    )

    assert error is None
    # Non-zero chunks should complete transfer successfully
    assert dirty_bytes == 65536


# ── Libnbd compress driver ─────────────────────────────────────────────


def test_libnbd_full_compress_driver_enabled(mock_shell, tmp_path, success_result):
    """_start_write_server with compress=True uses --image-opts driver=compress."""
    target_file = tmp_path / "target.qcow2"
    target_file.write_bytes(b"")
    write_socket = f"/tmp/qsnap-write-{os.getpid()}.sock"
    pid_file = tmp_path / "qemu-nbd.pid"

    # Expect the qemu-nbd --fork command with compress driver
    mock_shell.expect("qemu-nbd --fork.*--image-opts.*driver=compress").returns(success_result())

    provider = BitmapBackupProvider(mock_shell)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        result = provider._start_write_server(
            target_file=target_file,
            write_socket=write_socket,
            pid_file=pid_file,
            compress=True,
        )

    assert result.success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    qemu_nbd_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("qemu-nbd")]
    assert len(qemu_nbd_cmds) >= 1
    compress_cmd = [cmd for cmd in qemu_nbd_cmds if "--image-opts" in cmd]
    assert len(compress_cmd) >= 1, f"Expected --image-opts driver=compress, got: {qemu_nbd_cmds}"
    assert "driver=compress" in compress_cmd[0]


def test_libnbd_full_no_compress_driver_when_compress_false(mock_shell, tmp_path, success_result):
    """_start_write_server with compress=False uses --format=qcow2, no --image-opts."""
    target_file = tmp_path / "target.qcow2"
    target_file.write_bytes(b"")
    write_socket = f"/tmp/qsnap-write-{os.getpid()}.sock"
    pid_file = tmp_path / "qemu-nbd.pid"

    # Expect the qemu-nbd --fork command WITHOUT compress driver
    mock_shell.expect("qemu-nbd --fork.*--format=qcow2").returns(success_result())

    provider = BitmapBackupProvider(mock_shell)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        result = provider._start_write_server(
            target_file=target_file,
            write_socket=write_socket,
            pid_file=pid_file,
            compress=False,
        )

    assert result.success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    qemu_nbd_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("qemu-nbd")]
    assert len(qemu_nbd_cmds) >= 1
    no_image_opts = [
        cmd for cmd in qemu_nbd_cmds if "--image-opts" not in cmd and "--format=qcow2" in cmd
    ]
    assert len(no_image_opts) >= 1, (
        f"Expected --format=qcow2 without --image-opts, got: {qemu_nbd_cmds}"
    )


# ── Atomic checkpoint with libnbd ─────────────────────────────────────


def test_atomic_full_export_libnbd_with_checkpoint(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """run_backup FULL — backup-begin receives checkpoint XML 3rd arg.
    No standalone checkpoint-create-as."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "nonexistent_target"), verify="off")

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())
    # run_backup finally cleanup
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with (
        patch.object(BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 65536)),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient())
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True

    all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
    assert "--incremental" not in backup_cmds[0]
    assert "qsnap-checkpoint-" in backup_cmds[0], (
        f"backup-begin should receive checkpoint XML 3rd arg, got: {backup_cmds[0]}"
    )
    create_cmds = [cmd for cmd in all_run_cmds if "checkpoint-create-as" in cmd]
    assert len(create_cmds) == 0, (
        "checkpoint-create-as must NOT be called (atomic via backup-begin)"
    )


# ══════════════════════════════════════════════════════════════════════════
# BACKING-CHAIN VALIDATION TESTS (fix-broken-backing-chain)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
@pytest.mark.mock
def test_validate_backing_chain_valid_returns_true(mock_shell, tmp_path, success_result):
    """_validate_backing_chain returns True when backing chain is intact."""
    path = tmp_path / "snapshot.qcow2"
    path.write_bytes(b"")
    mock_shell.expect_first(r"qemu-img info.*--backing-chain").returns(success_result())
    provider = BitmapBackupProvider(mock_shell)
    assert provider._validate_backing_chain(path) is True


@pytest.mark.unit
@pytest.mark.mock
def test_validate_backing_chain_broken_returns_false(mock_shell, tmp_path):
    """_validate_backing_chain returns False when backing chain is broken."""
    path = tmp_path / "snapshot.qcow2"
    path.write_bytes(b"")
    mock_shell.expect_first(r"qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="image: Could not open backing file: No such file",
            returncode=1,
            error="",
        )
    )
    provider = BitmapBackupProvider(mock_shell)
    assert provider._validate_backing_chain(path) is False


@pytest.mark.unit
@pytest.mark.mock
def test_validate_backing_chain_standalone_full_returns_true(mock_shell, tmp_path, success_result):
    """_validate_backing_chain returns True for standalone FULL (no backing)."""
    path = tmp_path / "vm.FULL.20250101.qcow2"
    path.write_bytes(b"")
    mock_shell.expect_first(r"qemu-img info.*--backing-chain").returns(success_result())
    provider = BitmapBackupProvider(mock_shell)
    assert provider._validate_backing_chain(path) is True


@pytest.mark.unit
@pytest.mark.mock
def test_previous_backup_vanished_retryable_failure(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """Previous backup vanishes between walk and (1b) re-check → retryable error.

    Walk finds previous (test -f succeeds, chain validated).  The (1b)
    re-check test -f fails → error mentions 'vanished' and 'eof',
    is_retryable(error) returns True."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    backups = [
        BackupInfo(
            name=prev_backup.stem,
            path=prev_backup,
            timestamp=datetime(2024, 12, 30, 0, 0, 0),
            disk="vda",
            is_full=False,
        ),
    ]

    # Custom run: first test -f on prev_backup succeeds (walk),
    # second fails ((1b) re-check).
    test_f_count = [0]
    original_run = mock_shell.run

    def counting_run(cmd, timeout, **kwargs):
        cmd_str = " ".join(cmd)
        if cmd_str == f"test -f {prev_backup}":
            test_f_count[0] += 1
            if test_f_count[0] >= 2:
                return ShellResult(success=False, stdout="", stderr="", returncode=1, error="")
        return original_run(cmd, timeout, **kwargs)

    # Mock backing-chain validation for the non-FULL incremental
    mock_shell.expect_first(r"qemu-img info.*--backing-chain.*20241230").returns(success_result())

    nbd = MockNbdClient(size=65536)

    with (
        patch.object(mock_shell, "run", side_effect=counting_run),
        patch.object(BitmapBackupProvider, "list", return_value=backups),
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider._copy_dirty_blocks(
            vm_name=vm_config.name,
            target=target,
            target_file=target_path / "testvm.20250101T000000_vda_a1b2c3.qcow2",
            socket_path=f"/tmp/qsnap-backup-{os.getpid()}.sock",
            write_socket=f"/tmp/qsnap-write-{os.getpid()}.sock",
            pid_file=tmp_path / "qemu-nbd.pid",
            disk_target="vda",
            stall_timeout=1800,
        )

    assert result.error is not None
    assert "vanished" in result.error
    assert "eof" in result.error.lower()
    assert is_retryable(result.error) is True


@pytest.mark.unit
@pytest.mark.mock
def test_broken_chain_newest_backup_skipped_walk_to_valid(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """Walk skips newest backup with broken chain, selects next-newest intact.

    reversed(backups) starts with newest: test -f ok, chain broken → skip.
    Next: test -f ok, chain intact → selected as previous.
    Transfer proceeds with the older intact backup."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    older_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    older_backup.write_bytes(b"")
    newer_backup = target_path / "testvm.20250101T000000_vda_a1b2c3.qcow2"
    newer_backup.write_bytes(b"")

    # Sorted ascending by timestamp: older first, newer last.
    # reversed(backups): newer first, older second.
    backups = [
        BackupInfo(
            name=older_backup.stem,
            path=older_backup,
            timestamp=datetime(2024, 12, 30, 0, 0, 0),
            disk="vda",
            is_full=False,
        ),
        BackupInfo(
            name=newer_backup.stem,
            path=newer_backup,
            timestamp=datetime(2025, 1, 1, 0, 0, 0),
            disk="vda",
            is_full=False,
        ),
    ]

    # Newest has broken chain
    mock_shell.expect_first(r"qemu-img info.*--backing-chain.*20250101").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="Could not open backing file",
            returncode=1,
            error="",
        )
    )
    # Older has intact chain
    mock_shell.expect_first(r"qemu-img info.*--backing-chain.*20241230").returns(success_result())

    nbd = MockNbdClient(size=65536)

    with (
        patch.object(BitmapBackupProvider, "list", return_value=backups),
        patch.object(BitmapBackupProvider, "_start_write_server", return_value=success_result()),
        patch.object(BitmapBackupProvider, "_transfer", return_value=(None, 65536)),
        patch.object(BitmapBackupProvider, "_terminate_qemu_nbd"),
    ):
        # Post-transfer shell calls
        mock_shell.expect("qemu-img create").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())
        mock_shell.expect(r"^mv ").returns(success_result())

        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        target_file = target_path / "testvm.20250101T000000_vda_a1b2c3.qcow2"
        result = provider._copy_dirty_blocks(
            vm_name=vm_config.name,
            target=target,
            target_file=target_file,
            socket_path=f"/tmp/qsnap-backup-{os.getpid()}.sock",
            write_socket=f"/tmp/qsnap-write-{os.getpid()}.sock",
            pid_file=tmp_path / "qemu-nbd.pid",
            disk_target="vda",
            stall_timeout=1800,
        )

    assert result.error is None
    assert result.previous_path == older_backup, (
        f"Expected previous to be older intact backup {older_backup}, got {result.previous_path}"
    )
    assert result.dirty_bytes == 65536


@pytest.mark.unit
@pytest.mark.mock
def test_all_non_full_broken_fall_back_to_full(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """All non-FULL backups have broken chains → walk selects FULL as previous.

    FULLs are standalone (no backing) and always valid — the check
    ``is_full`` short-circuits without calling _validate_backing_chain."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    full_backup = target_path / "testvm.FULL.20241201_vda_a1b2c3.qcow2"
    full_backup.write_bytes(b"")
    incr1 = target_path / "testvm.20241215T000000_vda_a1b2c3.qcow2"
    incr1.write_bytes(b"")
    incr2 = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    incr2.write_bytes(b"")

    # Sorted ascending by timestamp: FULL first, then incr1, then incr2.
    # reversed(backups): incr2, incr1, FULL.
    backups = [
        BackupInfo(
            name=full_backup.stem,
            path=full_backup,
            timestamp=datetime(2024, 12, 1, 0, 0, 0),
            disk="vda",
            is_full=True,
        ),
        BackupInfo(
            name=incr1.stem,
            path=incr1,
            timestamp=datetime(2024, 12, 15, 0, 0, 0),
            disk="vda",
            is_full=False,
        ),
        BackupInfo(
            name=incr2.stem,
            path=incr2,
            timestamp=datetime(2024, 12, 30, 0, 0, 0),
            disk="vda",
            is_full=False,
        ),
    ]

    # Both incrementals have broken chains.
    # Registered second → checked first by MockShell (inserted at [0]).
    mock_shell.expect_first(r"qemu-img info.*--backing-chain.*20241215").returns(
        ShellResult(success=False, stdout="", stderr="", returncode=1, error="")
    )
    mock_shell.expect_first(r"qemu-img info.*--backing-chain.*20241230").returns(
        ShellResult(success=False, stdout="", stderr="", returncode=1, error="")
    )

    nbd = MockNbdClient(size=65536)

    with (
        patch.object(BitmapBackupProvider, "list", return_value=backups),
        patch.object(BitmapBackupProvider, "_start_write_server", return_value=success_result()),
        patch.object(BitmapBackupProvider, "_transfer", return_value=(None, 65536)),
        patch.object(BitmapBackupProvider, "_terminate_qemu_nbd"),
    ):
        mock_shell.expect("qemu-img create").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())
        mock_shell.expect(r"^mv ").returns(success_result())

        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        target_file = target_path / "testvm.20250101T000000_vda_a1b2c3.qcow2"
        result = provider._copy_dirty_blocks(
            vm_name=vm_config.name,
            target=target,
            target_file=target_file,
            socket_path=f"/tmp/qsnap-backup-{os.getpid()}.sock",
            write_socket=f"/tmp/qsnap-write-{os.getpid()}.sock",
            pid_file=tmp_path / "qemu-nbd.pid",
            disk_target="vda",
            stall_timeout=1800,
        )

    assert result.error is None
    assert result.previous_path == full_backup, (
        f"Expected previous to fall back to FULL {full_backup}, got {result.previous_path}"
    )
    assert result.dirty_bytes == 65536


@pytest.mark.unit
@pytest.mark.mock
def test_no_valid_backup_found_error_with_guidance(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """No backup with intact chain (all incrementals broken, no FULL).

    Error directs user to ``qsnap check --deep`` and ``qsnap reconcile``."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    incr = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    incr.write_bytes(b"")

    backups = [
        BackupInfo(
            name=incr.stem,
            path=incr,
            timestamp=datetime(2024, 12, 30, 0, 0, 0),
            disk="vda",
            is_full=False,
        ),
    ]

    # Incremental has broken chain
    mock_shell.expect_first(r"qemu-img info.*--backing-chain.*20241230").returns(
        ShellResult(success=False, stdout="", stderr="", returncode=1, error="")
    )

    nbd = MockNbdClient(size=65536)

    with patch.object(BitmapBackupProvider, "list", return_value=backups):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider._copy_dirty_blocks(
            vm_name=vm_config.name,
            target=target,
            target_file=target_path / "testvm.20250101T000000_vda_a1b2c3.qcow2",
            socket_path=f"/tmp/qsnap-backup-{os.getpid()}.sock",
            write_socket=f"/tmp/qsnap-write-{os.getpid()}.sock",
            pid_file=tmp_path / "qemu-nbd.pid",
            disk_target="vda",
            stall_timeout=1800,
        )

    assert result.error is not None
    assert "no valid previous backup" in result.error.lower()
    assert "qsnap check --deep" in result.error
    assert "qsnap reconcile" in result.error


# ══════════════════════════════════════════════════════════════════════════
# G3: CHECKPOINT LIFECYCLE UNIT TESTS (chain-aware-retention-recovery)
# ══════════════════════════════════════════════════════════════════════════


def test_checkpoint_full_delete_succeeds(mock_shell, success_result):
    """Full checkpoint delete succeeds via _delete_checkpoint_best_effort.
    First command is virsh checkpoint-delete WITHOUT --metadata."""
    vm_name = "testvm"
    checkpoint_name = "qsnap-abc12345-test-checkpoint"

    # Full delete (no --metadata) succeeds → no fallback needed.
    mock_shell.expect_first("checkpoint-delete.*--metadata").returns(
        ShellResult(success=False, stdout="", stderr="no metadata needed", returncode=1, error="")
    )
    mock_shell.expect("checkpoint-delete").returns(success_result())

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        provider._delete_checkpoint_best_effort(vm_name, checkpoint_name)

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1, (
        f"Expected exactly 1 checkpoint-delete (full, no metadata), got {len(delete_cmds)}: {delete_cmds}"
    )
    assert "--metadata" not in delete_cmds[0], (
        f"Full delete should NOT have --metadata, got: {delete_cmds[0]}"
    )
    assert checkpoint_name in delete_cmds[0]
    assert vm_name in delete_cmds[0]


def test_checkpoint_full_delete_fallback_metadata(mock_shell, caplog, success_result):
    """Fallback to metadata-only when VM shut off (full delete fails).
    First delete (no --metadata) fails, second (--metadata) succeeds."""
    vm_name = "testvm"
    checkpoint_name = "qsnap-abc12345-test-checkpoint"

    # Full delete FAILS, metadata fallback SUCCEEDS.
    # expect_first for --metadata catches the second call first.
    mock_shell.expect_first("checkpoint-delete.*--metadata").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="domain is not running",
            returncode=1,
            error="domain is not running",
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        provider = BitmapBackupProvider(mock_shell)
        provider._delete_checkpoint_best_effort(vm_name, checkpoint_name)

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 2, (
        f"Expected 2 commands (full + metadata fallback), got {len(delete_cmds)}: {delete_cmds}"
    )
    assert "--metadata" not in delete_cmds[0], (
        f"First delete should be full (no --metadata), got: {delete_cmds[0]}"
    )
    assert "--metadata" in delete_cmds[1], (
        f"Second delete should be metadata fallback, got: {delete_cmds[1]}"
    )
    assert checkpoint_name in delete_cmds[0]
    assert checkpoint_name in delete_cmds[1]


# ══════════════════════════════════════════════════════════════════════════
# POST-TRANSFER VALIDATION (Step 5b) — incremental backups
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
@pytest.mark.mock
def test_incremental_chain_to_full_traversable(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """After a successful incremental transfer, Step 5b validates that:
    1. ``qemu-img info --backing-chain`` on the target file succeeds
       (chain-to-FULL is traversable).
    2. ``list_checkpoints()`` returns at least one qsnap- checkpoint.

    Both pass → ``BackupResult(success=True)``."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_incr_expectations(
        mock_shell,
        target,
        (prev_backup, "vda", prev_backup.name),
    )

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale socket
    # checkpoint-list returns prior checkpoint (qsnap- prefix) —
    # used for discovery AND Step 5b checkpoint existence check + rotation.
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # cleanup

    provider = BitmapBackupProvider(mock_shell, nbd=nbd)
    result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.error is None
    assert result.disk == "vda"


@pytest.mark.unit
@pytest.mark.mock
def test_incremental_chain_to_full_broken(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """When Step 5b's chain-to-FULL check fails (``qemu-img info
    --backing-chain`` returns non-zero exit), ``run_backup()`` returns
    ``BackupResult(success=False, error="chain-to-FULL not traversable")``.

    The partial file and successor checkpoint are cleaned up."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_incr_expectations(
        mock_shell,
        target,
        (prev_backup, "vda", prev_backup.name),
    )

    # Override the chain-to-FULL check to FAIL.  The target file is the
    # freeze-ts delta under test (frozen clock).
    with _frozen_naming():
        mock_shell.expect_first(rf"qemu-img info.*--backing-chain.*{_FREEZE_STR}").returns(
            ShellResult(
                success=False,
                stdout="",
                stderr="qemu-img: Could not open ...",
                returncode=1,
                error="qemu-img info --backing-chain failed",
            )
        )

        _expect_no_blockjob(mock_shell)
        mock_shell.expect("rm -f").returns(success_result())  # stale socket
        mock_shell.expect("checkpoint-list").returns(
            ShellResult(
                success=True,
                stdout=prior_checkpoint + "\n",
                stderr="",
                returncode=0,
                error=None,
            )
        )
        mock_shell.expect("backup-begin").returns(success_result())
        # _cleanup_partial_file
        mock_shell.expect(f"rm -f {target.path / (_delta_backup_name() + '.qcow2')}").returns(
            success_result()
        )
        # successor checkpoint deletion best-effort
        mock_shell.expect("checkpoint-delete").returns(success_result())
        mock_shell.expect("domjobabort").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())  # cleanup

        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.error is not None
    assert "chain-to-FULL not traversable" in result.error
    assert result.bytes_transferred == 0
    assert result.disk == "vda"


@pytest.mark.unit
@pytest.mark.mock
def test_incremental_checkpoint_missing(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """When Step 5b's chain-to-FULL check passes but ``list_checkpoints()``
    returns empty (no qsnap- checkpoint), ``run_backup()`` returns
    ``BackupResult(success=False, error="checkpoint missing — next
    incremental impossible")``.

    The partial file and successor checkpoint are cleaned up."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_incr_expectations(
        mock_shell,
        target,
        (prev_backup, "vda", prev_backup.name),
    )

    with _frozen_naming():
        _expect_no_blockjob(mock_shell)
        mock_shell.expect("rm -f").returns(success_result())  # stale socket
        # Step 2 discovery: checkpoint-list returns prior (needed for incremental)
        mock_shell.expect("checkpoint-list").returns(
            ShellResult(
                success=True,
                stdout=prior_checkpoint + "\n",
                stderr="",
                returncode=0,
                error=None,
            )
        )
        mock_shell.expect("backup-begin").returns(success_result())
        # _cleanup_partial_file
        mock_shell.expect(f"rm -f {target.path / (_delta_backup_name() + '.qcow2')}").returns(
            success_result()
        )
        # successor checkpoint deletion best-effort
        mock_shell.expect("checkpoint-delete").returns(success_result())
        mock_shell.expect("domjobabort").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())  # cleanup

        # Patch list_checkpoints: first call returns a non-empty checkpoint
        # (for Step 2 discovery via _list_checkpoints_for_target), second
        # call returns empty (for Step 5b post-transfer validation).
        with patch.object(
            BitmapBackupProvider,
            "list_checkpoints",
            side_effect=[[prior_checkpoint], []],
        ):
            provider = BitmapBackupProvider(mock_shell, nbd=nbd)
            result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.error is not None
    assert "checkpoint missing" in result.error.lower()
    assert result.bytes_transferred == 0


# ══════════════════════════════════════════════════════════════════════════
# PER-DISK ISOLATION — BackupResult.disk (fix-per-disk-isolation)
# ══════════════════════════════════════════════════════════════════════════


def test_multi_disk_run_returns_per_disk_results(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """One run_backup per disk → each returned BackupResult carries its own disk."""
    vm_config = make_vm_config(
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm-vdb.qcow2")),
        ]
    )
    assert [d.target for d in vm_config.disks] == ["vda", "vdb"]
    target = make_target(path=str(tmp_path / "target"), verify="off")

    # Both disks have no prior checkpoints → FULL exports.
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("virsh blockjob").returns(
        ShellResult(
            success=True, stdout="No current block job\n", stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())

    nbd = MockNbdClient()

    with (
        patch.object(BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 65536)),
        patch.object(BitmapBackupProvider, "_delete_superseded_checkpoints"),
        patch.object(BitmapBackupProvider, "_terminate_qemu_nbd"),
        patch.object(mock_shell, "run", wraps=mock_shell.run),
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        results = [provider.run_backup(vm_config, target, disk) for disk in vm_config.disks]

    assert len(results) == 2
    assert results[0].success is True
    assert results[0].disk == "vda", (
        f"First result should carry disk='vda', got {results[0].disk!r}"
    )
    assert results[1].success is True
    assert results[1].disk == "vdb", (
        f"Second result should carry disk='vdb', got {results[1].disk!r}"
    )


def test_failed_run_backup_still_carries_disk(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """Force a backup-begin failure → the failed BackupResult still has
    .disk populated."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")

    backup_error = "backup-begin failed: domain is shut off"

    # Stale socket cleanup before backup-begin.
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    # backup-begin FAILS.
    mock_shell.expect("backup-begin").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=backup_error,
            returncode=1,
            error=backup_error,
        )
    )
    # Finally block cleanup: domjobabort.
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with (
        patch.object(BitmapBackupProvider, "_terminate_qemu_nbd"),
        patch.object(mock_shell, "run", wraps=mock_shell.run),
    ):
        provider = BitmapBackupProvider(mock_shell)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.disk == "vda", f"Failed BackupResult must carry disk='vda', got {result.disk!r}"
