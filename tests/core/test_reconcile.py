"""Tests for Core.reconcile() — orphan detection, broken chain detection (B6),
and dry-run mode.

Covers the new reconcile features:
- Broken backing-chain detection before orphan classification (B6)
- Dependency record cleanup on orphan deletion (B4)
- Dry-run reporting of broken chains
- Intact chains produce empty broken_chains
- Non-qsnap file skip (integration with broken-chain check)
- Orphan snapshot file removal
- Non-fatal error handling during orphan detection
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.results import (
    FullBackupInfo,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import MockConfigFacade

# ── shared helpers ────────────────────────────────────────────────────────


def _success_result(stdout: str = "") -> ShellResult:
    """Return a successful ShellResult."""
    return ShellResult(
        success=True,
        stdout=stdout,
        stderr="",
        returncode=0,
        error=None,
    )


def _failure_result(stderr: str = "qemu-img: Could not open") -> ShellResult:
    """Return a failed ShellResult."""
    return ShellResult(
        success=False,
        stdout="",
        stderr=stderr,
        returncode=1,
        error="qemu-img failed",
    )


def _anchor_json(orphan_path: Path, full_path: Path) -> str:
    """Return JSON for ``qemu-img info --output=json`` with a FULL backing anchor."""
    return json.dumps({
        "filename": str(orphan_path),
        "format": "qcow2",
        "virtual-size": 10737418240,
        "actual-size": 200704,
        "backing-filename": str(full_path),
    })


# ── Scenario 1: skip non-qsnap files ──────────────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_skips_non_qsnap_files_on_target(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """A ``.qcow2`` file on target not matching ``{vm_name}.*`` pattern
    is NOT deleted and a WARNING is logged.

    The new broken-chain detection (B6) also runs on this file, so
    we mock it to succeed (intact chain).
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # A non-qsnap file on the target.
    non_qsnap = SnapshotInfo(
        name="my-backup",
        path=target.path / "my-backup.qcow2",
        timestamp=datetime.now(),
        allocation=0,
    )

    # Broken-chain detection runs on non-FULL backups before orphan
    # classification.  The non-qsnap file name does NOT contain ".FULL."
    # so it passes through the check.  We mock the chain as intact.
    mock_shell.expect_first("--backing-chain").returns(_success_result())

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=[non_qsnap]
    ), patch.object(
        mock_factory._bitmap_backup_provider, "delete",
        wraps=mock_factory._bitmap_backup_provider.delete,
    ) as delete_spy, caplog.at_level(logging.WARNING):
        result = core.reconcile()

    assert not delete_spy.called, "Non-qsnap file should NOT be deleted"
    assert result["testvm"].orphan_files_removed == 0
    # Verify the WARNING about non-qsnap pattern skip was logged.
    assert any("not qsnap pattern" in r.message for r in caplog.records), (
        "Should log WARNING about non-qsnap file"
    )
    # Non-qsnap file should NOT appear in broken_chains (chain was intact).
    assert result["testvm"].broken_chains == []


# ── Scenario 2: remove orphan snapshot files ──────────────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_removes_orphan_snapshot_files(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """A ``.qcow2`` file on target not tracked in state, matching the
    qsnap pattern ``{vm_name}.*``, is deleted and counted in
    ``orphan_files_removed``.

    The broken-chain check runs first on non-FULL files.  We mock it
    as intact so the file proceeds to orphan classification.
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    orphan_backup = SnapshotInfo(
        name="testvm.20250726T1531_vda",
        path=target.path / "testvm.20250726T1531_vda.qcow2",
        timestamp=datetime.now(),
        allocation=0,
    )

    # Broken-chain detection: mock intact chain.
    mock_shell.expect_first("--backing-chain").returns(_success_result())
    # Anchor resolution (runs during deletion): return no anchor.
    mock_shell.expect("qemu-img info --output=json").returns(_success_result("{}"))

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=[orphan_backup]
    ), patch.object(
        mock_factory._bitmap_backup_provider, "delete",
        wraps=mock_factory._bitmap_backup_provider.delete,
    ) as delete_spy:
        result = core.reconcile()

    assert delete_spy.called, "provider.delete() should be called for orphan file"
    assert result["testvm"].orphan_files_removed == 1
    assert result["testvm"].broken_chains == []


# ── Scenario 3: non-fatal error during orphan detection ───────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_orphan_file_cleanup_non_fatal(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Error during orphan detection (e.g. target dir not accessible)
    is logged as WARNING, recorded in ``errors``, and reconcile continues.
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with patch.object(
        mock_factory._bitmap_backup_provider, "list",
        side_effect=OSError("target directory not accessible"),
    ), caplog.at_level(logging.WARNING):
        result = core.reconcile()

    # Error recorded in the ReconcileResult, no exception raised.
    assert len(result["testvm"].errors) > 0
    assert any("orphan files" in e for e in result["testvm"].errors), (
        "Should record error about orphan files"
    )
    assert result["testvm"].orphan_files_removed == 0
    # Verify WARNING was logged.
    assert any(
        "error checking orphan files" in r.message for r in caplog.records
    ), "Should log WARNING about orphan files error"


# ── Scenario 4: dependency cleanup on orphan deletion (B4) ─────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_cleans_dependency_records_on_orphan_deletion(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Orphan file deleted that has a resolvable FULL anchor →
    ``remove_incremental_dependency`` is called with target_path,
    orphan name, and anchor (fix B4).
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    orphan_name = "testvm.20250726T1531_vda"
    orphan_path = target.path / f"{orphan_name}.qcow2"
    full_name = "testvm.FULL.20250725"
    full_path = target.path / f"{full_name}.qcow2"


    orphan_backup = SnapshotInfo(
        name=orphan_name,
        path=orphan_path,
        timestamp=datetime.now(),
        allocation=0,
    )

    # Pre-populate dependency so we can verify removal.
    mock_state.record_incremental_dependency(
        str(target.path), orphan_name, full_name
    )
    # Verify it was recorded.
    deps_before = mock_state.get_incremental_dependencies(
        str(target.path), full_name
    )
    assert orphan_name in deps_before, "Dependency should be recorded before reconcile"

    # Broken-chain detection: mock intact chain.
    mock_shell.expect_first("--backing-chain").returns(_success_result())
    # Anchor resolution: return JSON with FULL backing.
    mock_shell.expect("qemu-img info --output=json").returns(
        _success_result(_anchor_json(orphan_path, full_path))
    )

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=[orphan_backup]
    ), patch.object(
        mock_factory._bitmap_backup_provider, "delete",
        wraps=mock_factory._bitmap_backup_provider.delete,
    ) as delete_spy:
        result = core.reconcile()

    assert delete_spy.called, "Orphan file should be deleted"
    assert result["testvm"].orphan_files_removed == 1

    # Verify dependency was cleaned (B4).
    deps_after = mock_state.get_incremental_dependencies(
        str(target.path), full_name
    )
    assert orphan_name not in deps_after, (
        f"Dependency {orphan_name} → {full_name} should be removed "
        "on orphan deletion (B4)"
    )
    assert result["testvm"].broken_chains == []


# ── Scenario 5: broken chain detected before orphan (B6) ──────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_detects_broken_chain_before_orphan(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Non-FULL backup has broken backing chain →
    WARNING logged, backup name in ``broken_chains``,
    and file still proceeds through orphan classification (deleted).

    This tests the B6 fix: broken-chain detection runs BEFORE orphan
    classification so that broken chains are logged even when the file
    gets deleted as orphan.
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    orphan_name = "testvm.20250726T1531_vda"
    orphan_path = target.path / f"{orphan_name}.qcow2"
    orphan_backup = SnapshotInfo(
        name=orphan_name,
        path=orphan_path,
        timestamp=datetime.now(),
        allocation=0,
    )

    # Broken-chain detection: mock FAILED chain.
    mock_shell.expect_first("--backing-chain").returns(_failure_result())
    # Anchor resolution: return no anchor (so dependency cleanup is skipped).
    mock_shell.expect("qemu-img info --output=json").returns(_success_result("{}"))

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=[orphan_backup]
    ), patch.object(
        mock_factory._bitmap_backup_provider, "delete",
        wraps=mock_factory._bitmap_backup_provider.delete,
    ) as delete_spy, caplog.at_level(logging.WARNING):
        result = core.reconcile()

    # File is still deleted (broken-chain detection doesn't stop orphan logic).
    assert delete_spy.called, "Orphan file should still be deleted even with broken chain"
    assert result["testvm"].orphan_files_removed == 1

    # Broken chain reported in result (B6).
    assert orphan_name in result["testvm"].broken_chains, (
        f"Broken chain for {orphan_name} should be in broken_chains"
    )
    # WARNING logged about broken chain.
    assert any(
        "broken backing chain" in r.message for r in caplog.records
    ), "Should log WARNING about broken backing chain"


# ── Scenario 6: intact chains produce empty broken_chains ─────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_intact_chains_no_broken_chains(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """All non-FULL backups have intact backing chains →
    ``broken_chains`` is an empty list.

    The backup file is tracked in state so it is NOT treated as an orphan.
    Files must actually exist on disk so the phantom-full detection
    in steps 1-4 does not prematurely remove them from state.
    """
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # A FULL backup in state AND on disk.
    full_name = "testvm.FULL.20250725.qcow2"
    full_stem = "testvm.FULL.20250725"
    full_path = target_dir / full_name
    full_path.write_text("")  # create real file so it's not phantom
    full_info = FullBackupInfo(
        name=full_name,
        path=full_path,
        timestamp=datetime.now(),

    )
    mock_state._full_backups[str(target.path)] = [full_info]

    inc_name = "testvm.20250726T1531_vda"
    inc_path = target_dir / f"{inc_name}.qcow2"
    inc_path.write_text("")  # create real file
    mock_state.record_incremental_dependency(
        str(target.path), inc_name, full_name
    )

    # provider.list returns both — both tracked, so no orphans.
    incremental_info = SnapshotInfo(
        name=inc_name,
        path=inc_path,
        timestamp=datetime.now(),
        allocation=0,
    )
    full_snap_info = SnapshotInfo(
        name=full_stem,
        path=full_path,
        timestamp=datetime.now(),
        allocation=0,
    )

    # Broken-chain detection runs on non-FULL files only.  The
    # incremental has an intact chain.
    mock_shell.expect_first("--backing-chain").returns(_success_result())

    with patch.object(
        mock_factory._bitmap_backup_provider, "list",
        return_value=[incremental_info, full_snap_info],
    ), patch.object(
        mock_factory._bitmap_backup_provider, "delete",
        wraps=mock_factory._bitmap_backup_provider.delete,
    ) as delete_spy:
        result = core.reconcile()

    # Neither file is orphan — both tracked in state.
    assert not delete_spy.called, "Tracked files should NOT be deleted"
    assert result["testvm"].orphan_files_removed == 0
    # No broken chains reported (chains are intact).
    assert result["testvm"].broken_chains == [], (
        "broken_chains should be empty when all chains are intact"
    )


# ── Scenario 7: dry-run reports broken chains, no deletion ────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_reconcile_dry_run_reports_broken_chains_no_deletion(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """``--dry-run`` mode: broken chain detected and reported in
    ``broken_chains``, but the file is NOT deleted.

    The broken-chain detection runs before orphan classification and
    populates ``broken_chains``.  In dry-run mode the orphan file is
    counted but not deleted.
    """
    target = make_target()
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.dry_run = True

    orphan_name = "testvm.20250726T1531_vda"
    orphan_path = target.path / f"{orphan_name}.qcow2"
    orphan_backup = SnapshotInfo(
        name=orphan_name,
        path=orphan_path,
        timestamp=datetime.now(),
        allocation=0,
    )

    # Broken-chain detection: mock FAILED chain.
    mock_shell.expect_first("--backing-chain").returns(_failure_result())

    with patch.object(
        mock_factory._bitmap_backup_provider, "list", return_value=[orphan_backup]
    ), patch.object(
        mock_factory._bitmap_backup_provider, "delete",
        wraps=mock_factory._bitmap_backup_provider.delete,
    ) as delete_spy, caplog.at_level(logging.WARNING):
        result = core.reconcile()

    # File NOT deleted in dry-run mode.
    assert not delete_spy.called, (
        "provider.delete() should NOT be called in dry-run mode"
    )
    # Orphan is counted (dry-run counts what WOULD happen).
    assert result["testvm"].orphan_files_removed == 1, (
        "orphan_files_removed should be counted in dry-run mode"
    )
    # Broken chain reported (B6).
    assert orphan_name in result["testvm"].broken_chains, (
        f"Broken chain for {orphan_name} should be in broken_chains "
        "even in dry-run mode"
    )
    # WARNING logged about broken chain.
    assert any(
        "broken backing chain" in r.message for r in caplog.records
    ), "Should log WARNING about broken backing chain in dry-run mode"
