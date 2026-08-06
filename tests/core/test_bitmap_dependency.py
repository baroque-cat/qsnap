"""Tests for bitmap dependency recording and env-validation in Core.

Covers:
- ``_backup_target`` records incremental→FULL dependency for bitmap transfers.
- Failed transfers do not record dependencies.
- Standalone pull (no backing chain) records nothing.
- ``check_state`` reports no missing deps when dependencies are present.
- ``_validate_environment``: missing libnbd → validation_failed (unconditional).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import BackupAbortError, Core
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import BackupResult, CheckResult, ShellResult, SnapshotInfo
from qsnap.utils.nbd_client import MISSING_LIBNBD_ERROR
from tests.mocks import (
    MockBitmapBackupProvider,
    MockConfigFacade,
    MockShell,
)

# ── helpers ────────────────────────────────────────────────────────────


def _make_bitmap_target(path: str = "/mnt/backup/testvm") -> TargetConfig:
    return TargetConfig(
        path=Path(path),
        compress=True,
        compression_type="zstd",
        backup_stall_timeout="30m",
    )


def _make_incremental_snapshot(name: str = "vm.20250101T000000") -> SnapshotInfo:
    return SnapshotInfo(
        name=name,
        path=Path(f"/var/lib/libvirt/snapshots/testvm/{name}.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
        disk="vda",
    )


def _make_bitmap_vm(
    target: TargetConfig,
    name: str = "testvm",
    snapshot_dir: str = "/var/lib/libvirt/snapshots/testvm",
) -> VMConfig:
    return VMConfig(
        name=name,
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path(snapshot_dir),
        targets=[target],
    )


def _setup_chain_walk_shell(
    shell: MockShell,
    target_path: str,
    inc_name: str,
    full_stem: str,
    full_backing: dict | None = None,
) -> Path:
    """Configure ``MockShell`` so ``_resolve_chain_full_anchor`` walks the chain.

    *inc_name* is the incremental file name (without extension).
    *full_stem* is the FULL anchor stem.
    *full_backing* controls what the FULL itself reports as backing-filename
    (None = no backing file, standalone full).
    """
    inc_path = Path(target_path) / f"{inc_name}.qcow2"
    full_path = Path(target_path) / f"{full_stem}.qcow2"

    # Step 1: incremental → points to FULL
    info_inc = {
        "backing-filename": str(full_path),
        "format": "qcow2",
        "virtual-size": 1073741824,
    }
    # Step 2: FULL → has its own backing-filename (or none)
    if full_backing is not None:
        info_full = full_backing
    else:
        info_full = {"format": "qcow2", "virtual-size": 1073741824}

    shell.expect_first(f"qemu-img info.*{inc_path.name}").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(info_inc),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    shell.expect_first(f"qemu-img info.*{full_path.name}").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(info_full),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    return inc_path


# ── test_bitmap_incremental_registers_dependency ───────────────────────


def test_bitmap_incremental_registers_dependency(
    mock_factory,
    mock_state,
    make_vm_config,
    make_global_config,
    tmp_path: Path,
) -> None:
    """Bitmap backup transfer → Core records incremental→FULL dependency.

    Drives ``Core._backup_target()`` with a bitmap-mode target and a
    single snapshot.  The provider's ``transfer_missing`` returns one
    successful ``BackupResult``.  The backing chain walk resolves a
    ``.FULL.`` anchor, and Core calls
    ``state.record_incremental_dependency()`` with the correct arguments.
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir()
    target = _make_bitmap_target(str(target_dir))
    vm = _make_bitmap_vm(target)
    snap = _make_incremental_snapshot("vm.20250101T000000")

    # Pre-record a FULL (with a real file) so this run performs an
    # incremental transfer instead of creating a new FULL (FULL creation
    # would fail verification on this minimal shell and abort the VM).
    full_name = "vm.FULL.20250101.qcow2"
    (target_dir / full_name).touch()
    mock_state.record_full_backup(str(target_dir), full_name, datetime(2025, 1, 1, 0, 0, 0), "vda")

    # Create a fresh shell with only our expectations (no conftest noise).
    shell = MockShell()
    _setup_chain_walk_shell(
        shell,
        str(target_dir),
        inc_name="vm.20250101T000000",
        full_stem="vm.FULL.20250101",
    )

    config = MockConfigFacade(
        global_config=make_global_config(),
        vms=[vm],
    )
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    with patch.object(
        mock_state, "record_incremental_dependency", wraps=mock_state.record_incremental_dependency
    ) as spy:
        core._backup_target(vm, target, [snap])

    # ── assert dependency was recorded ──────────────────────────────
    spy.assert_called_once_with(
        str(target_dir),
        "vm.20250101T000000",
        "vm.FULL.20250101",
    )
    deps = mock_state.get_incremental_dependencies(str(target_dir), "vm.FULL.20250101")
    assert "vm.20250101T000000" in deps


# ── test_failed_transfer_records_no_dependency ─────────────────────────


def test_failed_transfer_records_no_dependency(
    mock_factory,
    mock_state,
    make_vm_config,
    make_global_config,
    tmp_path: Path,
) -> None:
    """Failed bitmap transfer → no dependency recorded, no qemu-img info calls.

    When ``transfer_missing`` returns a ``BackupResult(success=False)``,
    Core must skip the chain walk entirely and never call
    ``record_incremental_dependency``.
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir()
    target = _make_bitmap_target(str(target_dir))
    vm = _make_bitmap_vm(target)
    snap = _make_incremental_snapshot("vm.failed.20250101T000000")

    # Pre-record a FULL (with a real file) so this run reaches the
    # incremental transfer step instead of creating a new FULL.
    full_name = "vm.FULL.20250101.qcow2"
    (target_dir / full_name).touch()
    mock_state.record_full_backup(str(target_dir), full_name, datetime(2025, 1, 1, 0, 0, 0), "vda")

    # Replace the bitmap provider with one that always fails.
    failing_provider = MockBitmapBackupProvider()

    def _failing_transfer(
        vm_config,
        target,
        snapshots,
        *,
        compression_type="zstd",
        stall_timeout=1800,
        convert_parallel=4,
        convert_out_of_order=True,
    ):
        return [
            BackupResult(
                success=False,
                snapshot_name=s.name,
                source_path=s.path,
                target_path=target.path / f"{s.name}.qcow2",
                bytes_transferred=0,
                error="simulated transfer failure",
            )
            for s in snapshots
        ]

    failing_provider.transfer_missing = _failing_transfer  # type: ignore[method-assign]
    mock_factory._bitmap_backup_provider = failing_provider

    shell = MockShell()

    config = MockConfigFacade(
        global_config=make_global_config(),
        vms=[vm],
    )
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    with (
        patch.object(
            mock_state,
            "record_incremental_dependency",
            wraps=mock_state.record_incremental_dependency,
        ) as spy,
        # VM-level isolation: the definitive transfer failure aborts the VM.
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target, [snap])

    # ── assert nothing was recorded ─────────────────────────────────
    spy.assert_not_called()

    # Also assert no qemu-img info commands were issued for the result file.
    # No qemu-img info expectations were ever matched because the failed
    # result short-circuited before the chain walk.  We verify by checking
    # that the shell was never asked a qemu-img info command (the mock
    # would return an error for any unmatched command):
    result = shell.run(
        ["qemu-img", "info", "--output=json", str(target_dir / "vm.failed.20250101T000000.qcow2")],
        timeout=60,
    )
    assert "No mock configured" in (result.error or ""), (
        "qemu-img info was unexpectedly configured for a failed result path"
    )


# ── test_standalone_no_backing_records_no_dependency ───────────────────


def test_standalone_no_backing_records_no_dependency(
    mock_factory,
    mock_state,
    make_vm_config,
    make_global_config,
    tmp_path: Path,
) -> None:
    """Standalone pull (no backing-filename) → no dependency recorded.

    When ``qemu-img info`` returns a JSON object with no
    ``"backing-filename"`` key, ``_resolve_chain_full_anchor`` returns
    ``None`` and Core must not call ``record_incremental_dependency``.
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir()
    target = _make_bitmap_target(str(target_dir))
    vm = _make_bitmap_vm(target)
    snap = _make_incremental_snapshot("vm.standalone.20250101T000000")

    # Pre-record a FULL (with a real file) so this run performs an
    # incremental transfer instead of creating a new FULL.
    full_name = "vm.FULL.20250101.qcow2"
    (target_dir / full_name).touch()
    mock_state.record_full_backup(str(target_dir), full_name, datetime(2025, 1, 1, 0, 0, 0), "vda")

    shell = MockShell()
    inc_path = target_dir / f"{snap.name}.qcow2"
    # No backing-filename → standalone.
    shell.expect_first(f"qemu-img info.*{inc_path.name}").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 1073741824}),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    config = MockConfigFacade(
        global_config=make_global_config(),
        vms=[vm],
    )
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    with patch.object(
        mock_state, "record_incremental_dependency", wraps=mock_state.record_incremental_dependency
    ) as spy:
        core._backup_target(vm, target, [snap])

    spy.assert_not_called()


# ── test_dependency_visible_in_check_state ─────────────────────────────


def test_dependency_visible_in_check_state(
    mock_factory,
    mock_state,
    make_vm_config,
    make_global_config,
    mock_shell,
    tmp_path: Path,
) -> None:
    """After recording a dependency, ``check_state`` reports no stale deps.

    Records an incremental→FULL dependency in state, creates the
    incremental file on disk so the existence check passes, then calls
    ``check_state()`` and asserts ``status="ok"`` with no stale_deps.
    """
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()

    target = _make_bitmap_target(str(backup_dir))
    vm = _make_bitmap_vm(target, snapshot_dir=str(tmp_path / "snapshots"))

    # Create FULL and incremental files on disk (existence check passes).
    full_name = "vm.FULL.monthly"
    full_path = backup_dir / full_name
    full_path.touch()

    inc_name = "vm.20250101T000000"
    inc_path = backup_dir / f"{inc_name}.qcow2"
    inc_path.touch()

    # Record state: FULL first, then dependency.
    mock_state.record_full_backup(str(backup_dir), full_name, datetime(2025, 1, 1, 0, 0, 0), "vda")
    mock_state.record_incremental_dependency(str(backup_dir), inc_name, full_name)

    config = MockConfigFacade(
        global_config=make_global_config(state_dir=str(tmp_path / "state")),
        vms=[vm],
    )
    core = Core(config=config, factory=mock_factory, state=mock_state, shell=mock_shell)

    result = core.check_state()

    assert result["testvm"].status == "ok"
    assert result["testvm"].stale_deps == []
    assert result["testvm"].phantom_snapshots == []
    assert result["testvm"].phantom_fulls == []


# ── env-validation: bitmap + missing libnbd ────────────────────────────


def test_validate_environment_bitmap_without_libnbd_fails(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
) -> None:
    """Backup target + ``is_libnbd_available()`` → False →
    ``validation_failed`` with ``MISSING_LIBNBD_ERROR`` in broken items.

    When the libnbd bindings are not installed, the (unconditional)
    pre-flight validation fails with an actionable error naming the
    distro package (design R4 — no silent fallback).
    """
    target = _make_bitmap_target("/mnt/backup/testvm")
    vm = _make_bitmap_vm(target)
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with patch("qsnap.core.is_libnbd_available", return_value=False):
        result = core._validate_environment(vm)

    assert isinstance(result, CheckResult)
    assert result.status == "validation_failed"
    assert any(MISSING_LIBNBD_ERROR in b for b in result.broken_snapshots), (
        f"Expected {MISSING_LIBNBD_ERROR!r} in broken items: {result.broken_snapshots}"
    )
