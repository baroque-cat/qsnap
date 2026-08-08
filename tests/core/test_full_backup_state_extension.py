"""Core-level tests for the FULL-backup ``.qcow2`` state-name invariant.

The fix (fix-full-backup-state-extension, designs D1/D3/D4):
1. ``Core._backup_target()`` records ``f"{result.snapshot_name}.qcow2"``
   instead of the bare stem (call-site fix).
2. ``JsonStateManager`` (and the ``InMemoryStateManager`` mock) normalize
   recorded names to extended form and derive ``path`` from the
   normalized name (defensive invariant).
3. ``remove_full_backup`` accepts stem lookups — ``_cleanup_backups``
   passes ``BackupInfo.name`` (always a stem, design D5) and must still
   remove the extended record (design D3).

These tests exercise the Core orchestration boundary with zero real I/O:
MockFactory + InMemoryStateManager + MockShell.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.results import (
    BackupInfo,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import MockConfigFacade


def _make_snapshot(snap_name: str, snap_path: Path, ts: datetime) -> SnapshotInfo:
    return SnapshotInfo(
        name=snap_name,
        path=snap_path,
        timestamp=ts,
        allocation=1000,
        disk="vda",
    )


# ── Test 1: call-site fix — Core records the .qcow2 extension ─────────────


@pytest.mark.unit
@pytest.mark.mock
def test_backup_target_records_full_with_qcow2_extension(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A FULL run records ``f"{result.snapshot_name}.qcow2"`` — not the stem.

    Spies on ``record_full_backup`` and asserts the ``name`` argument
    equals the provider's ``BackupResult.snapshot_name`` plus the
    ``.qcow2`` extension (spec scenario 15).
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    backup_provider = mock_factory._backup_provider
    original_run_backup = backup_provider.run_backup
    result_holder: dict[str, object] = {}

    def _capture_result(*args, **kwargs):
        result = original_run_backup(*args, **kwargs)
        result_holder["result"] = result
        return result

    with (
        patch.object(
            backup_provider,
            "run_backup",
            side_effect=_capture_result,
        ) as run_spy,
        patch.object(
            mock_state,
            "record_full_backup",
            wraps=mock_state.record_full_backup,
        ) as rec_spy,
    ):
        core._backup_target(vm, target)

    assert run_spy.called, "run_backup should be called on first (FULL) backup"
    result = result_holder["result"]
    assert result.success, "mock backup should succeed"
    assert rec_spy.called, "record_full_backup should be called after a FULL"

    # The call-site fix (design D1): name argument carries the .qcow2 extension.
    name_arg = rec_spy.call_args.args[1]
    assert name_arg == f"{result.snapshot_name}.qcow2", (
        f"record_full_backup name should be '{result.snapshot_name}.qcow2', got {name_arg!r}"
    )
    assert name_arg.endswith(".qcow2"), f"recorded FULL name should end with .qcow2: {name_arg}"

    # The derived path in state must point at the extended filename.
    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 1, f"Expected exactly one FULL record, got {len(fulls)}"
    assert fulls[0].name == name_arg
    assert fulls[0].path == target.path / name_arg


# ── Test 2: recorded path resolves to the physical file ───────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_recorded_full_path_exists_on_disk(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """After a FULL run, ``get_full_backups()[0].path`` resolves to a real file.

    The mock provider does not write the qcow2 file itself, so the test
    wraps ``run_backup`` to create ``result.target_path`` — mirroring what
    the real bitmap provider does.  The state record must then point at
    that exact physical file (never a phantom extensionless path).
    """
    target = make_target(path=str(tmp_path / "backup"))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    backup_provider = mock_factory._backup_provider
    original_run_backup = backup_provider.run_backup

    def _run_backup_and_touch(*args, **kwargs):
        result = original_run_backup(*args, **kwargs)
        if result.success:
            result.target_path.parent.mkdir(parents=True, exist_ok=True)
            result.target_path.touch()
        return result

    with patch.object(
        backup_provider,
        "run_backup",
        side_effect=_run_backup_and_touch,
    ):
        core._backup_target(vm, target)

    fulls = mock_state.get_full_backups(str(target.path))
    assert len(fulls) == 1, f"Expected exactly one FULL record, got {len(fulls)}"
    full = fulls[0]
    assert full.name.endswith(".qcow2"), f"FULL record name should be extended: {full.name}"
    assert full.path.exists(), (
        f"Recorded FULL path should resolve to the physical file: {full.path}"
    )
    assert full.path.name == full.name, (
        f"Recorded path should be derived from the extended name: {full.path}"
    )


# ── Test 3: second run creates a delta, not a FULL ────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_second_run_creates_delta_not_full_after_recorded_full(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """With an extended record + real file, the next run computes needs_full=False.

    The phantom filter must see the recorded FULL through the extended
    path (design D4), so ``_backup_target`` keeps the record, computes
    ``needs_full=False``, and calls ``run_backup`` WITHOUT ``force_full``.
    No second FULL is recorded — only the seeded record remains.
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Seed one extended FULL record whose file exists on disk.
    full_name = f"{vm.name}.FULL.20250101T000000_vda_a1b2c3.qcow2"
    (target_dir / full_name).touch()
    mock_state.record_full_backup(str(target_dir), full_name, datetime(2025, 1, 1, 0, 0, 0), "vda")
    assert len(mock_state.get_full_backups(str(target_dir))) == 1

    backup_provider = mock_factory._backup_provider
    with patch.object(
        backup_provider,
        "run_backup",
        wraps=backup_provider.run_backup,
    ) as run_spy:
        core._backup_target(vm, target)

    assert run_spy.called, "run_backup should be called for the delta transfer"
    assert run_spy.call_args.kwargs["force_full"] is False, (
        "run_backup must NOT receive force_full=True when a valid FULL record exists"
    )

    # Only the seeded FULL record remains — no second FULL was recorded.
    fulls = mock_state.get_full_backups(str(target_dir))
    assert len(fulls) == 1, f"Expected exactly one FULL record, got {len(fulls)}"
    assert fulls[0].name == full_name, f"Unexpected FULL record after delta run: {fulls[0].name}"
    assert fulls[0].path.exists(), "Seeded FULL file should still exist on disk"


# ── Test 4: check()/check_state() report no phantom FULLs ─────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_check_reports_no_phantom_fulls_for_extended_records(
    tmp_path: Path,
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Extended records whose files exist are NOT phantom FULLs.

    ``check_state()`` reports status ``"ok"`` with an empty
    ``phantom_fulls`` list, ``_detect_phantom_fulls`` returns nothing,
    and ``check()`` (triple-source) also reports ``"ok"``.
    """
    snap_dir = tmp_path / "snapshots"
    backup_dir = tmp_path / "backup"
    snap_dir.mkdir()
    backup_dir.mkdir()
    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", snapshot_dir=str(snap_dir), targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Extended FULL record + physical file on disk.
    full_name = f"{vm.name}.FULL.monthly.qcow2"
    full_path = backup_dir / full_name
    full_path.touch()
    mock_state.record_full_backup(str(backup_dir), full_name, datetime(2025, 7, 13, 10, 0), "vda")

    # ── check_state(): no phantoms, status ok ────────────────────────
    state_result = core.check_state()
    assert state_result["testvm"].status == "ok", (
        f"Expected status 'ok', got {state_result['testvm'].status!r}"
    )
    assert state_result["testvm"].phantom_fulls == []
    assert state_result["testvm"].stale_deps == []
    assert core._detect_phantom_fulls(vm) == [], (
        "Extended record with existing file must not be a phantom FULL"
    )

    # ── check(): triple-source pass must also be clean ───────────────
    # Snapshot verification portion: one snapshot on disk + in state.
    snap_name = f"{vm.name}.20250713T1400_vda"
    snap_path = snap_dir / f"{snap_name}.qcow2"
    snap_path.touch()
    mock_state.record_snapshot(
        "testvm", _make_snapshot(snap_name, snap_path, datetime(2025, 7, 13, 14, 0))
    )

    # Override the default fixture domblklist to point at our snapshot.
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=f"Target   Source\n--------------------------------\nvda   {snap_path}\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # qemu-img info --backing-chain on the active layer (single-file chain).
    mock_shell.expect_first(f"--backing-chain.*{re.escape(str(snap_path))}").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(
                [
                    {
                        "filename": str(snap_path),
                        "format": "qcow2",
                        "virtual-size": 10737418240,
                        "actual-size": 200704,
                    }
                ]
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # virsh dumpxml referencing only the snapshot.
    mock_shell.expect("virsh dumpxml").returns(
        ShellResult(
            success=True,
            stdout=(
                '<domain type="kvm"><devices><disk type="file" device="disk">'
                f'<source file="{snap_path}"/>'
                "</disk></devices></domain>"
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Provider lists the FULL file (stem name, extended path — design D5).
    backups = [
        BackupInfo(
            name=full_name.removesuffix(".qcow2"),
            path=full_path,
            timestamp=datetime(2025, 7, 13, 10, 0),
            disk="vda",
        )
    ]
    with (
        patch.object(
            mock_factory._bitmap_backup_provider,
            "list",
            return_value=backups,
        ),
        patch.object(
            mock_factory._bitmap_backup_provider,
            "list_checkpoints",
            return_value=[],
        ),
    ):
        check_result = core.check()
    assert check_result["testvm"].status == "ok", (
        f"Expected check() status 'ok', got {check_result['testvm'].status!r}"
    )
    assert check_result["testvm"].broken_snapshots == []


# ── Test 5: _cleanup_backups removes the record via stem lookup ───────────


@pytest.mark.unit
@pytest.mark.mock
def test_cleanup_backups_removes_record_via_stem_lookup(
    tmp_path: Path,
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``_cleanup_backups`` passes ``BackupInfo.name`` (stem) to
    ``remove_full_backup`` and the extended record is removed (design D3).

    ``provider.list()`` reports backup names as stems (design D5), so
    ``_cleanup_backups`` hands a stem to ``remove_full_backup``; the
    tolerant lookup normalizes it to ``.qcow2`` and removes the extended
    state record.
    """
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    target = make_target(path=str(backup_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(global_config=make_global_config(), vms=[vm])
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    # Extended record in state + physical file on disk.
    full_stem = f"{vm.name}.FULL.monthly"
    full_name = f"{full_stem}.qcow2"
    (backup_dir / full_name).touch()
    mock_state.record_full_backup(str(backup_dir), full_name, datetime(2025, 7, 13, 10, 0), "vda")
    assert len(mock_state.get_full_backups(str(backup_dir))) == 1

    # provider.list() returns BackupInfo whose name is the STEM (design D5).
    backups = [
        BackupInfo(
            name=full_stem,
            path=backup_dir / full_name,
            timestamp=datetime(2025, 7, 13, 10, 0),
            disk="vda",
        )
    ]
    retention = RetentionResult(keep=[], remove=[full_stem])

    # M1/M2 verification passes; deletion is a no-op mock.
    with (
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch.object(
            mock_factory._bitmap_backup_provider,
            "delete",
            wraps=mock_factory._bitmap_backup_provider.delete,
        ) as delete_spy,
    ):
        core._cleanup_backups(vm, target, backups, retention)

    assert delete_spy.called, "provider.delete should be called for the removed FULL"
    remaining = mock_state.get_full_backups(str(backup_dir))
    assert remaining == [], (
        f"Extended FULL record should be removed via stem lookup, got {remaining}"
    )
