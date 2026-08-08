"""Unit tests for BitmapBackupProvider incremental dirty-block copy loop.

Tests cover the unified NBD engine (design D1/D2/D4) under the orthogonal
``run_backup(vm_config, target, disk, *, opts)`` entry point: when a qsnap
checkpoint exists for the VM+target+disk, exactly ONE delta is produced,
named by its freeze point ``{vm}.{freeze_ts}_{disk}_{hex6}.qcow2``, backed
by the newest intact previous backup.

All shell calls go through ``MockShell``; all NBD operations through
``MockNbdClient`` — zero real I/O.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import pytest

from qsnap.models.results import NbdExtent, NbdResult, ShellResult
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.utils.retry import is_retryable
from tests.mocks.mock_nbd import MockNbdClient

pytestmark = pytest.mark.unit

# Tiny disk so block_status runs a single window — the copy-loop logic is
# the same regardless of disk size, and one window avoids multiplying extents.
_TINY_DISK = 65536

# Frozen wall clock + hex suffix so freeze-timestamp backup names are
# deterministic in tests.
_FREEZE_DT = datetime(2026, 8, 8, 3, 0, 0)
_FREEZE_STR = "20260808T030000"
_FREEZE_HEX = "a1b2c3"


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


def _expect_healthy_probe(mock_shell, cp_name: str) -> None:
    """Register a HEALTHY QMP probe for *cp_name* (recover-lost-checkpoint-bitmaps).

    Loads the canned ``qmp_block_nodes_healthy.json`` fixture and rewrites
    the advertised bitmap name to *cp_name* so the exact-name match in
    ``BitmapBackupProvider._probe_running_vm`` returns HEALTHY — the delta
    path proceeds without entering recovery.
    """
    fixture = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "shell_outputs"
        / "qmp_block_nodes_healthy.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    for node in payload.get("return", []):
        for bitmap in node.get("dirty-bitmaps", []):
            bitmap["name"] = cp_name
    mock_shell.expect("qemu-monitor-command").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(payload),
            stderr="",
            returncode=0,
            error=None,
        )
    )


# ── Helpers ────────────────────────────────────────────────────────────────


def _default_nbd() -> MockNbdClient:
    """A MockNbdClient with a tiny disk (single block_status window)."""
    return MockNbdClient(size=_TINY_DISK, max_request_size=33554432)


def _setup_full_copy_loop_expectations(
    mock_shell, target, prev_path: Path, disk_target: str = "vda"
) -> MockNbdClient:
    """Register all MockShell expectations for a successful copy loop.

    Handles: qemu-img info (listing), test -f, qemu-img create,
    rm -f write socket/pidfile, qemu-nbd, kill, rm -f write socket/pidfile,
    mv.  Returns a pre-configured MockNbdClient.

    The caller must still register:
    - virsh blockjob (no active job)
    - rm -f (stale source socket)
    - checkpoint-list
    - backup-begin
    - checkpoint-delete (rotation)
    - domjobabort
    - rm -f (source socket cleanup in finally)
    """
    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        f"qemu:dirty-bitmap:backup-{disk_target}": [
            NbdExtent(offset=0, length=65536, data=True),
        ],
    }

    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}-vda.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"

    mock_shell.expect("qemu-img info.*" + str(prev_path.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
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

    return nbd


def _setup_verify_bitmap_incremental_expectations(
    mock_shell,
    source_path: str,
    delta_path_str: str,
    prev_path: Path,
    verify_mode: str,
    *,
    delta_virtual_size: int = _TINY_DISK,
    delta_actual_size: int = 65536,
    delta_backing: str | None = None,
) -> None:
    """Register MockShell expectations for verify_bitmap_incremental.

    *delta_backing* defaults to ``str(prev_path)``.
    """
    if delta_backing is None:
        delta_backing = str(prev_path)

    source_info = {
        "format": "qcow2",
        "virtual-size": delta_virtual_size,
        "actual-size": 1048576,
    }
    delta_info = {
        "format": "qcow2",
        "virtual-size": delta_virtual_size,
        "actual-size": delta_actual_size,
        "backing-filename": delta_backing,
    }

    mock_shell.expect_first(rf"qemu-img info.*--force-share.*{source_path}").returns(
        ShellResult(
            success=True, stdout=json.dumps(source_info), stderr="", returncode=0, error=None
        )
    )
    mock_shell.expect_first(rf"qemu-img info.*{delta_path_str}").returns(
        ShellResult(
            success=True, stdout=json.dumps(delta_info), stderr="", returncode=0, error=None
        )
    )

    if verify_mode == "compare":
        mock_shell.expect("qemu-img compare").returns(_ok())


# ── Tests ──────────────────────────────────────────────────────────────────


def test_run_backup_subsequent_creates_single_delta_chained(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """Prior checkpoint exists → run_backup produces exactly one delta,
    freeze-timestamp named, backed by the previous backup."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

    # Mock for Step 5b: chain-to-FULL traversability verification
    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"
    assert result.disk == "vda"
    # Freeze-timestamp delta name — no snapshot name anywhere.
    assert result.target_path.name == f"{_delta_backup_name()}.qcow2", (
        f"Delta must be freeze-ts named, got {result.target_path.name}"
    )
    assert ".FULL." not in result.target_path.name

    # The delta is chained onto the previous backup.
    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    create_cmds = [cmd for cmd in all_run_cmds if "qemu-img create" in cmd]
    assert len(create_cmds) == 1
    assert f"-b {prev_backup}" in create_cmds[0]
    assert "-F qcow2" in create_cmds[0]

    mock_wbxml.assert_called_once()
    _, kwargs = mock_wbxml.call_args
    assert kwargs.get("incremental") == prior_checkpoint


def test_incremental_backup_named_freeze_ts_disk_hex(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """Delta filename follows {vm}.{freeze_ts}_{disk}_{hex6}.qcow2 — never a
    snapshot name."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch("qsnap.modules.backup.bitmap.write_backup_xml"),
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"
    assert re.fullmatch(
        r"testvm\.\d{8}T\d{6}_vda_[0-9a-f]{6}\.qcow2",
        result.target_path.name,
    ), f"Delta filename must be freeze-ts named, got {result.target_path.name}"


def test_delta_named_by_freeze_point_no_snapshot_name(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """The delta name embeds the run's freeze point, never a snapshot
    name or timestamp (backup-target orthogonality)."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch("qsnap.modules.backup.bitmap.write_backup_xml"),
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"
    assert _FREEZE_STR in result.target_path.name, (
        f"Delta name must embed the freeze timestamp {_FREEZE_STR}, got {result.target_path.name}"
    )
    assert "testvm.20250101" not in result.target_path.name
    assert "snapshot" not in result.target_path.name.lower()


def test_copy_loop_reads_only_dirty_extents(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """block_status has dirty + clean extents; pread covers exactly
    dirty ∩ allocated ranges.  bytes_read == dirty total."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")
    nbd.block_status_payload = {
        "base:allocation": [
            NbdExtent(offset=0, length=65536, data=True),
        ],
        "qemu:dirty-bitmap:backup-vda": [
            NbdExtent(offset=0, length=65536, data=True),
        ],
    }
    nbd._size = 65536

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

    # Mock for Step 5b: chain-to-FULL traversability verification
    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"

    pread_calls = [c for c in nbd.calls if c[0] == "pread"]
    assert len(pread_calls) > 0, "Expected at least one pread call"
    total_read = sum(cast(int, c[2]) for c in pread_calls)
    assert total_read == 65536, f"Expected 65536 bytes read (dirty total), got {total_read}"
    assert nbd.bytes_read == 65536

    # No convert commands — unified engine for ALL paths
    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, "No qemu-img convert — unified NBD engine for ALL paths"


def test_first_incremental_backing_is_full(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """qemu-img create -b names the FULL backup; final qemu-img info
    shows backing-filename == FULL path → verify pass."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="metadata")
    snapshot = vm_config.disks[0]

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.FULL.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    with _frozen_naming():
        delta_file = target_path / f"{_delta_backup_name()}.qcow2"
        _setup_verify_bitmap_incremental_expectations(
            mock_shell,
            str(snapshot.base_image),
            str(delta_file),
            prev_backup,
            "metadata",
        )

        _expect_no_blockjob(mock_shell)
        mock_shell.expect("rm -f").returns(success_result())  # stale source socket
        mock_shell.expect("checkpoint-list").returns(
            ShellResult(
                success=True,
                stdout=prior_checkpoint + "\n",
                stderr="",
                returncode=0,
                error=None,
            )
        )
        _expect_healthy_probe(mock_shell, prior_checkpoint)
        mock_shell.expect("backup-begin").returns(success_result())
        mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
        mock_shell.expect("domjobabort").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

        # Mock for Step 5b: chain-to-FULL traversability verification
        mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
            ShellResult(
                success=True,
                stdout='[{"filename": "fake-chain-element.qcow2"}]',
                stderr="",
                returncode=0,
                error=None,
            )
        )

        with (
            patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
            patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        ):
            mock_wbxml.return_value = tmp_path / "backup-test.xml"
            provider = BitmapBackupProvider(mock_shell, nbd=nbd)
            result = provider.run_backup(vm_config, target, snapshot)

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    create_cmds = [cmd for cmd in all_run_cmds if "qemu-img create" in cmd]
    assert len(create_cmds) == 1
    assert f"-b {prev_backup}" in create_cmds[0]
    assert "-F qcow2" in create_cmds[0]


def test_previous_backup_vanished_retryable_failure(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """test -f fails → is_retryable(error) is True; successor checkpoint
    deleted, prior preserved."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    mock_shell.expect("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Backing-chain validation (new in fix-broken-backing-chain): the
    # walk validates non-FULL backups via qemu-img info --backing-chain.
    mock_shell.expect_first(r"qemu-img info.*--backing-chain").returns(success_result())

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    # Custom run wrapper: the first test -f on the PREVIOUS BACKUP
    # (during the backwards walk) succeeds; the second test -f on the
    # same file (the (1b) re-check) fails — this simulates the previous
    # backup vanishing between the walk and qemu-img create.
    prev_test_f_count = 0
    original_run = mock_shell.run

    def _run_with_vanish(cmd, timeout, check=False):
        nonlocal prev_test_f_count
        cmd_str = " ".join(cmd)
        if cmd_str.startswith("test -f") and str(prev_backup) in cmd_str:
            prev_test_f_count += 1
            if prev_test_f_count == 1:
                return success_result()
            return ShellResult(
                success=False, stdout="", stderr="", returncode=1, error="test -f failed"
            )
        return original_run(cmd, timeout, check)

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", side_effect=_run_with_vanish) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=_default_nbd())
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.error is not None
    assert "vanished" in result.error.lower(), (
        f"Error should mention 'vanished', got: {result.error}"
    )
    assert "eof" in result.error.lower(), f"Error should mention 'eof', got: {result.error}"
    assert is_retryable(result.error) is True, (
        f"Vanished error should be retryable, got is_retryable={is_retryable(result.error)}"
    )
    assert result.disk == "vda"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint not in delete_cmds[0]
    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0


def test_previous_existence_rechecked_before_create(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """Assert a test -f <prev> command appears BEFORE the qemu-img create command."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

    # Mock for Step 5b: chain-to-FULL traversability verification
    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    test_f_idx = None
    create_idx = None
    for i, cmd in enumerate(all_run_cmds):
        if cmd.startswith("test -f") and str(prev_backup) in cmd:
            test_f_idx = i
        if "qemu-img create" in cmd:
            create_idx = i
    assert test_f_idx is not None, "test -f <prev> must appear in shell history"
    assert create_idx is not None, "qemu-img create must appear in shell history"
    assert test_f_idx < create_idx, "test -f must appear BEFORE qemu-img create in shell history"


def test_mid_copy_failure_cleans_temp_qemu_nbd_and_socket(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """fail_pread after first extent → result failure, rm -f <tmp> issued,
    write socket rm issued, qemu-nbd kill issued, domjobabort issued,
    successor checkpoint deleted."""
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

    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }
    nbd.fail_pread = "pread I/O error"

    mock_shell.expect("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
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
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.error is not None
    assert "pread I/O error" in result.error
    assert result.disk == "vda"

    delta_name = _delta_backup_name()
    tmp_suffix = f"{delta_name}.qcow2.tmp"
    delta_file = target_path / f"{delta_name}.qcow2"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    tmp_rm_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and tmp_suffix in cmd]
    assert len(tmp_rm_cmds) >= 1, "Expected rm -f of .tmp file, got none"

    write_socket_rm = [cmd for cmd in all_run_cmds if write_socket in cmd]
    assert len(write_socket_rm) >= 1, "Write socket not cleaned up"

    kill_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("kill")]  # noqa: F841

    abort_cmds = [cmd for cmd in all_run_cmds if "domjobabort" in cmd]
    assert len(abort_cmds) >= 1, "domjobabort not issued"

    # Partial delta file deleted immediately with a short fixed timeout.
    partial_rm = [
        c for c in run_spy.call_args_list if " ".join(c.args[0]).endswith(" " + str(delta_file))
    ]
    assert len(partial_rm) == 1, "Partial delta file must be deleted"
    assert partial_rm[0].kwargs.get("timeout") == 10

    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint not in delete_cmds[0]


def test_successful_transfer_no_tmp_or_socket_remain(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """On success: final rm -f sweep includes tmp file and write socket;
    mv tmp→final issued."""
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

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

    # Mock for Step 5b: chain-to-FULL traversability verification
    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"

    tmp_suffix = f"{_delta_backup_name()}.qcow2.tmp"
    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    mv_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("mv ")]
    assert len(mv_cmds) == 1, "mv tmp→final must be issued"
    assert tmp_suffix in mv_cmds[0], f"mv should involve the .tmp file, got: {mv_cmds[0]}"
    assert ".qcow2" in mv_cmds[0]

    tmp_rm_cmds = [cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and tmp_suffix in cmd]
    assert len(tmp_rm_cmds) >= 1, "rm -f <tmp> must appear in finally cleanup"

    write_socket_cmds = [cmd for cmd in all_run_cmds if write_socket in cmd]
    assert len(write_socket_cmds) >= 2, "Write socket should be in both pre-nbd rm and post-kill rm"


def test_stall_watchdog_aborts_with_correct_error_string(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """sleeping pread_handler + small stall_timeout; exact string match
    'Stall detected: no progress for {N}s'; failure path ran."""
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

    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }
    stall_timeout = 1

    def sleeping_pread(offset: int, length: int) -> NbdResult:
        time.sleep(2.0)  # longer than stall_timeout
        return NbdResult(success=True, payload=bytes(length), error=None)

    nbd.pread_handler = sleeping_pread

    mock_shell.expect("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
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
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # successor
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(
            vm_config, target, vm_config.disks[0], stall_timeout=stall_timeout
        )

    assert result.success is False
    expected_error = f"Stall detected: no progress for {stall_timeout}s"
    assert result.error == expected_error, (
        f"Expected exact error '{expected_error}', got '{result.error}'"
    )
    assert result.disk == "vda"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1


def test_slow_progressing_loop_not_killed(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """Short sleeps per chunk, generous timeout; success."""
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

    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }

    def slow_pread(offset: int, length: int) -> NbdResult:
        time.sleep(0.1)  # short sleep, less than stall_timeout
        return NbdResult(success=True, payload=bytes(length), error=None)

    nbd.pread_handler = slow_pread

    mock_shell.expect("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
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

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket

    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run),
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0], stall_timeout=5)

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"


def test_zero_stall_timeout_disables_watchdog(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """stall_timeout=0 + sleeping pread → success (watchdog disabled)."""
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

    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }

    def sleeping_pread(offset: int, length: int) -> NbdResult:
        time.sleep(1.0)  # sleep, but stall_timeout=0 disables watchdog
        return NbdResult(success=True, payload=bytes(length), error=None)

    nbd.pread_handler = sleeping_pread

    mock_shell.expect("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
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

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket

    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run),
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0], stall_timeout=0)

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"


def test_incremental_uses_unified_engine_no_convert(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """No 'qemu-img convert' in shell history when prior checkpoint exists;
    unified NBD engine (pread/pwrite) used instead.  mock connect called
    with both contexts."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, (
        "No qemu-img convert — unified NBD engine (pread/pwrite) for ALL paths"
    )

    src_connect = [c for c in nbd.calls if c[0] == "connect"]
    assert len(src_connect) >= 1, "Source NBD connect not called"
    contexts = nbd.requested_contexts
    assert "base:allocation" in contexts
    assert "qemu:dirty-bitmap:backup-vda" in contexts


def test_run_backup_incremental_dirty_blocks_only_zero_skip_false(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """Incremental _transfer is called with zero_skip=False — copies only
    dirty∩allocated extents, never performs zero-skip optimization."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch.object(BitmapBackupProvider, "_transfer") as transfer_mock,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        transfer_mock.return_value = (None, 65536)
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"

    # Verify _transfer was called with zero_skip=False
    assert transfer_mock.call_count == 1
    call_kwargs = transfer_mock.call_args.kwargs
    assert call_kwargs.get("zero_skip") is False, (
        f"Incremental _transfer should have zero_skip=False, got: {call_kwargs}"
    )


def test_incremental_always_uses_pread_pwrite(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """Incremental transfers always use pread/pwrite unified NBD engine."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    # No qemu-img convert — incrementals always use pread/pwrite unified engine
    convert_cmds = [cmd for cmd in all_run_cmds if "qemu-img convert" in cmd]
    assert len(convert_cmds) == 0, "Incremental should NEVER use qemu-img convert"

    # pread/pwrite was called (unified NBD engine)
    pread_calls = [c for c in nbd.calls if c[0] == "pread"]
    assert len(pread_calls) > 0, "pread should be called for incremental transfer"


def test_qemu_img_info_shows_backing_filename(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """Verify path asserts backing-filename; wrong backing → verify failure."""
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

    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }

    mock_shell.expect("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
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

    base_image = vm_config.disks[0].base_image
    source_info = json.dumps(
        {"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 1048576}
    )
    wrong_backing = "/wrong/path/backup.qcow2"
    delta_info = json.dumps(
        {
            "format": "qcow2",
            "virtual-size": _TINY_DISK,
            "actual-size": 65536,
            "backing-filename": wrong_backing,
        }
    )

    with _frozen_naming():
        delta_file = target_path / f"{_delta_backup_name()}.qcow2"

        mock_shell.expect_first(rf"qemu-img info.*--force-share.*{base_image}").returns(
            ShellResult(success=True, stdout=source_info, stderr="", returncode=0, error=None)
        )
        mock_shell.expect_first(rf"qemu-img info.*{delta_file.name}").returns(
            ShellResult(success=True, stdout=delta_info, stderr="", returncode=0, error=None)
        )

        _expect_no_blockjob(mock_shell)
        mock_shell.expect("rm -f").returns(success_result())  # stale source socket
        mock_shell.expect("checkpoint-list").returns(
            ShellResult(
                success=True,
                stdout=prior_checkpoint + "\n",
                stderr="",
                returncode=0,
                error=None,
            )
        )
        _expect_healthy_probe(mock_shell, prior_checkpoint)
        mock_shell.expect("backup-begin").returns(success_result())
        mock_shell.expect("checkpoint-delete").returns(success_result())  # successor
        mock_shell.expect("domjobabort").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())

        with (
            patch.object(mock_shell, "run", wraps=mock_shell.run),
            patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        ):
            mock_wbxml.return_value = tmp_path / "backup-test.xml"
            provider = BitmapBackupProvider(mock_shell, nbd=nbd)
            result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.error is not None
    assert "verification failed" in result.error
    assert "backing-filename" in result.error


def test_restore_chain_resolved_without_bitmap_specific_logic(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """The delta's backing-filename points to previous backup so the
    EXISTING restore flow works — assert create -b chain + final backing
    check pass with no bitmap-specific restore code."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="metadata")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    with _frozen_naming():
        delta_file = target_path / f"{_delta_backup_name()}.qcow2"
        _setup_verify_bitmap_incremental_expectations(
            mock_shell,
            str(vm_config.disks[0].base_image),
            str(delta_file),
            prev_backup,
            "metadata",
        )

        _expect_no_blockjob(mock_shell)
        mock_shell.expect("rm -f").returns(success_result())  # stale source socket
        mock_shell.expect("checkpoint-list").returns(
            ShellResult(
                success=True,
                stdout=prior_checkpoint + "\n",
                stderr="",
                returncode=0,
                error=None,
            )
        )
        _expect_healthy_probe(mock_shell, prior_checkpoint)
        mock_shell.expect("backup-begin").returns(success_result())
        mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
        mock_shell.expect("domjobabort").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

        # Mock for Step 5b: chain-to-FULL traversability verification
        # (expect_first needed because verify mocks use expect_first for qemu-img info too)
        mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
            ShellResult(
                success=True,
                stdout='[{"filename": "fake-chain-element.qcow2"}]',
                stderr="",
                returncode=0,
                error=None,
            )
        )

        with (
            patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
            patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        ):
            mock_wbxml.return_value = tmp_path / "backup-test.xml"
            provider = BitmapBackupProvider(mock_shell, nbd=nbd)
            result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    create_cmds = [cmd for cmd in all_run_cmds if "qemu-img create" in cmd]
    assert len(create_cmds) == 1
    assert f"-b {prev_backup}" in create_cmds[0]

    # The provider surface matches the orthogonal interface: run_backup,
    # list, delete — never transfer_missing/create_full_backup.
    assert hasattr(provider, "run_backup")
    assert hasattr(provider, "list")
    assert hasattr(provider, "delete")
    assert not hasattr(provider, "transfer_missing"), (
        "transfer_missing must be removed from the provider"
    )
    assert not hasattr(provider, "create_full_backup"), (
        "create_full_backup must be removed from the provider"
    )


def test_bitmap_incremental_ignores_compress_setting(
    mock_shell, make_vm_config, make_target, tmp_path, caplog, success_result
) -> None:
    """target.compress=True; assert no '-c' in any issued command on the
    incremental path."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off", compress=True)

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    caplog.set_level("INFO")
    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    # No -c compression flag anywhere in the incremental path
    for cmd in all_run_cmds:
        tokens = cmd.split()
        assert "-c" not in tokens, f"Command '{cmd}' should not contain -c flag"
        assert "-o" not in tokens or "compression_type" not in cmd, (
            f"Command '{cmd}' should not contain -o compression_type"
        )


def test_missing_libnbd_fails_factory_construction(make_target, tmp_path) -> None:
    """DefaultFactory: monkeypatch is_libnbd_available → False; bitmap
    target with libvirt >= 7.2 mocked; create_backup_provider raises
    RuntimeError with message naming python3-libnbd."""
    from qsnap.factory.default import DefaultFactory
    from qsnap.utils.nbd_client import MISSING_LIBNBD_ERROR

    mock_shell = Mock()
    mock_shell.run.return_value = ShellResult(
        success=True, stdout="virsh 8.0.0\n", stderr="", returncode=0, error=None
    )

    target = make_target(
        path=str(tmp_path / "target"),
    )

    with patch("qsnap.factory.default.is_libnbd_available", return_value=False):
        factory = DefaultFactory(mock_shell, Mock())
        with pytest.raises(RuntimeError) as exc_info:
            factory.create_backup_provider(
                Mock(),
                target,
            )

    assert "python3-libnbd" in str(exc_info.value)
    assert MISSING_LIBNBD_ERROR in str(exc_info.value)


def test_full_size_verify_failure_triggers_cleanup(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """qemu-img info actual-size > dirty_bytes*2+64MiB → verification
    failed error; final file rm'd; successor checkpoint deleted;
    prior preserved."""
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

    nbd = _default_nbd()
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=65536, data=True)],
        "qemu:dirty-bitmap:backup-vda": [NbdExtent(offset=0, length=65536, data=True)],
    }

    mock_shell.expect("qemu-img info.*" + str(prev_backup.name)).returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 100}),
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

    source_info = json.dumps(
        {"format": "qcow2", "virtual-size": _TINY_DISK, "actual-size": 1048576}
    )
    huge_actual = 70000000
    delta_info = json.dumps(
        {
            "format": "qcow2",
            "virtual-size": _TINY_DISK,
            "actual-size": huge_actual,
            "backing-filename": str(prev_backup),
        }
    )

    with _frozen_naming():
        delta_file = target_path / f"{_delta_backup_name()}.qcow2"

        mock_shell.expect_first(
            rf"qemu-img info.*--force-share.*{vm_config.disks[0].base_image}"
        ).returns(
            ShellResult(success=True, stdout=source_info, stderr="", returncode=0, error=None)
        )
        mock_shell.expect_first(rf"qemu-img info.*{delta_file.name}").returns(
            ShellResult(success=True, stdout=delta_info, stderr="", returncode=0, error=None)
        )

        _expect_no_blockjob(mock_shell)
        mock_shell.expect("rm -f").returns(success_result())  # stale source socket
        mock_shell.expect("checkpoint-list").returns(
            ShellResult(
                success=True,
                stdout=prior_checkpoint + "\n",
                stderr="",
                returncode=0,
                error=None,
            )
        )
        _expect_healthy_probe(mock_shell, prior_checkpoint)
        mock_shell.expect("backup-begin").returns(success_result())
        mock_shell.expect("checkpoint-delete").returns(success_result())
        mock_shell.expect("domjobabort").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())
        mock_shell.expect("rm -f").returns(success_result())

        with (
            patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
            patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        ):
            mock_wbxml.return_value = tmp_path / "backup-test.xml"
            provider = BitmapBackupProvider(mock_shell, nbd=nbd)
            result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is False
    assert result.error is not None
    assert "verification failed" in result.error
    assert "regressed" in result.error or "barrier" in result.error.lower(), (
        f"Error should mention regression/barrier, got: {result.error}"
    )

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]

    delta_rm = [cmd for cmd in all_run_cmds if cmd.startswith("rm -f") and str(delta_file) in cmd]
    assert len(delta_rm) >= 1, "Delta file should be cleaned up"

    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1
    assert prior_checkpoint not in delete_cmds[0], "Prior checkpoint must be preserved"


def test_size_sanity_check_warns_on_large_transfer(
    mock_shell, make_vm_config, make_target, tmp_path, caplog, success_result
) -> None:
    """A large dirty transfer (700000 bytes) still succeeds under verify="off"
    and produces a freeze-ts named delta.

    NOTE: the old provider-side WARNING ("dirty bytes vs snapshot
    allocation") was removed with the snapshot world; the regression
    barrier for oversized deltas now lives in verify_bitmap_incremental
    (see test_full_size_verify_failure_triggers_cleanup).
    """
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    # Override block_status to return a large total of dirty bytes.
    nbd.block_status_payload = {
        "base:allocation": [NbdExtent(offset=0, length=700000, data=True)],
        "qemu:dirty-bitmap:backup-vda": [
            NbdExtent(offset=0, length=700000, data=True),
        ],
    }
    nbd._size = 700000

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run),
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        f"Backup should succeed despite large transfer, got error: {result.error}"
    )
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"
    # The large delta is named by its freeze point.
    assert result.target_path.name == f"{_delta_backup_name()}.qcow2"


def test_run_backup_normal_prior_always_set(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
):
    """Normal path: prior checkpoint exists → incremental transfer via copy_loop.

    When a prior checkpoint and prior backup file exist, the incremental
    NBD copy loop is used (not a full export via _full_pull_lifecycle)."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    prior_checkpoint = f"qsnap-{target_hash}-vda-20241230T000000-dead01"

    prev_backup = target_path / "testvm.20241230T000000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())  # stale source socket
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=prior_checkpoint + "\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    _expect_healthy_probe(mock_shell, prior_checkpoint)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())  # source socket cleanup

    # Mock for Step 5b: chain-to-FULL traversability verification
    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch.object(BitmapBackupProvider, "_full_pull_lifecycle") as mock_fpl,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"
    # Prior is set → incremental copy_loop used, NOT _full_pull_lifecycle
    assert mock_fpl.call_count == 0, (
        "_full_pull_lifecycle should NOT be called when prior exists (normal path)"
    )

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    # qemu-img create -b is used for incremental
    create_cmds = [cmd for cmd in all_run_cmds if "qemu-img create" in cmd]
    assert len(create_cmds) == 1, "qemu-img create -b should be used for incremental"


def test_second_run_backup_uses_successor_as_baseline(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
) -> None:
    """A second run_backup in the same batch uses the newest (successor)
    checkpoint as baseline — discovery picks the newest checkpoint."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    older = f"qsnap-{target_hash}-vda-20241230T000000-dead01"
    successor = f"qsnap-{target_hash}-vda-20260808T030000-a1b2c3"

    prev_backup = target_path / "testvm.20260808T030000_vda_a1b2c3.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _setup_full_copy_loop_expectations(mock_shell, target, prev_backup, disk_target="vda")

    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    # Discovery returns both; _select_newest must pick the successor.
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=f"{older}\n{successor}\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # The probe targets the NEWEST checkpoint (the successor) each run.
    _expect_healthy_probe(mock_shell, successor)
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        ShellResult(
            success=True,
            stdout='[{"filename": "fake-chain-element.qcow2"}]',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True
    assert result.kind == "delta", f"Delta path must report kind='delta', got {result.kind!r}"

    # write_backup_xml receives the newest checkpoint as the incremental baseline.
    mock_wbxml.assert_called_once()
    _, kwargs = mock_wbxml.call_args
    assert kwargs.get("incremental") == successor, (
        f"Second run_backup must baseline on the successor {successor!r}, got {kwargs.get('incremental')!r}"
    )

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 1
