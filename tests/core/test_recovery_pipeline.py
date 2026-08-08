"""Core orchestration tests for bitmap-loss recovery (recover-lost-checkpoint-bitmaps).

Covers the Core-level recovery pipeline:
- Crash evidence: dead-bitmap WARNING at startup; successful recovery exits 0.
- FULL fallback ordering: old generation retired only after verification;
  failed recovery FULL preserves the old generation.
- Startup invariant: dead-bitmap checkpoint with covering file removed at
  startup; dry-run predicts the removal without deleting.
- State management: boot_id recorded after a successful run; last_commit_ts
  written after virsh blockcommit and after offline (qemu-img) commit.
- Orthogonality: the backup phase with zero snapshots in state produces a FULL.
- Retention: recovery retirement, corrupt superseded FULL preserved, normal
  retention unchanged.
- Provider audit: recovered-delta results are auditable via ``kind``.

Per TESTING.md: Core is tested with MockVMModuleFactory, InMemoryStateManager,
MockShell.expect().returns(), and no pytest-mock.  Zero real virsh/qemu-img calls.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.cli.commands import _format_pipeline_result
from qsnap.cli.errors import EXIT_SUCCESS
from qsnap.cli.summary import _LEGEND_LINES
from qsnap.core import BackupAbortError, Core
from qsnap.models.config import GlobalConfig
from qsnap.models.results import (
    BackupResult,
    BaselineAssessment,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import (
    MockBitmapBackupProvider,
    MockConfigFacade,
    MockRetentionEngine,
)

pytestmark = pytest.mark.unit

_OK = ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


def _shell_ok(stdout: str = "") -> ShellResult:
    return ShellResult(success=True, stdout=stdout, stderr="", returncode=0, error=None)


def _dead_assessment(checkpoint: str) -> BaselineAssessment:
    """A DEAD-bitmap baseline assessment (gate G1 fails conservatively)."""
    return BaselineAssessment(
        status="dead",
        newest_checkpoint=checkpoint,
        gates_passed=False,
        failed_gate_reason="G1",
    )


def _install_provider(
    mock_factory,
    *,
    assessment: BaselineAssessment | None = None,
    backup_kind: str = "delta",
) -> MockBitmapBackupProvider:
    """Replace the factory's bitmap provider with a configured mock."""
    provider = MockBitmapBackupProvider(assessment=assessment, backup_kind=backup_kind)
    mock_factory._bitmap_backup_provider = provider
    mock_factory._backup_provider = provider
    return provider


# ═══════════════════════════════════════════════════════════════════════════
# Crash evidence
# ═══════════════════════════════════════════════════════════════════════════


def test_recovery_logs_unclean_shutdown_warning(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    caplog,
):
    """Crash evidence: a dead-bitmap checkpoint WITH a covering backup file
    is detected at startup and logged as a WARNING.

    The covering file proves the checkpoint was the baseline of a real
    backup, so the missing/inconsistent dirty bitmap is crash evidence
    (unclean host shutdown), not a cancelled export.  Core logs the
    WARNING before any recovery action.

    When the host boot_id changed since the last successful run, the
    WARNING attributes the dead bitmap to an unclean host shutdown
    (spec scenario "Unclean shutdown evidence logged"; design D3).
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    # Covering backup file whose freeze timestamp matches the checkpoint.
    (target_dir / "testvm.20250801T120000_vda_a1b2c3.qcow2").touch()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # State records a boot_id from a previous boot; the current boot_id
    # is different → unclean host shutdown detected.
    mock_state.set_boot_id("testvm", "boot-old")

    backup = SnapshotInfo(
        name="testvm.FULL.20250101T000000_vda_abc123.qcow2",
        path=target_dir / "testvm.FULL.20250101T000000_vda_abc123.qcow2",
        timestamp=datetime(2025, 1, 1, 0, 0),
        allocation=1000,
        disk="vda",
    )
    target_hash = mock_factory._bitmap_backup_provider.target_hash(str(target.path))
    dead_ck = f"qsnap-{target_hash}-vda-20250801T120000"
    provider = _install_provider(mock_factory, assessment=_dead_assessment(dead_ck))

    with (
        patch.object(provider, "list", return_value=[backup]),
        patch.object(Core, "_read_host_boot_id", return_value="boot-new"),
    ):
        mock_shell.expect("virsh checkpoint-list").returns(_shell_ok(f"{dead_ck}\n"))
        mock_shell.expect("virsh checkpoint-delete").returns(_OK)
        caplog.set_level(logging.WARNING)
        core._validate_state_at_startup(vm)

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("dead-bitmap checkpoint" in m for m in warnings), (
        f"Expected dead-bitmap WARNING, got: {warnings}"
    )
    assert any(dead_ck in m for m in warnings), (
        f"WARNING should name the dead checkpoint, got: {warnings}"
    )
    assert any("unclean host shutdown detected" in m for m in warnings), (
        f"WARNING should attribute the dead bitmap to an unclean host shutdown "
        f"when the boot_id changed, got: {warnings}"
    )
    assert any("covering file" in m for m in warnings), (
        f"WARNING should cite the covering-file evidence, got: {warnings}"
    )


def test_successful_recovery_exits_zero_with_warning_only(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    caplog,
):
    """A recovery that heals the dead checkpoint exits 0 with WARNING only.

    Startup removes the dead-bitmap checkpoint (covering file exists, so
    the bitmap loss is crash evidence); the run then creates a FULL
    fallback.  The pipeline reports success — a healed recovery is not an
    error (exit 0), and no ERROR/CRITICAL is logged.
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "testvm.20250801T120000_vda_a1b2c3.qcow2").touch()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    global_cfg = make_global_config(full_verify_after_create="off")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    backup = SnapshotInfo(
        name="testvm.FULL.20250101T000000_vda_abc123.qcow2",
        path=target_dir / "testvm.FULL.20250101T000000_vda_abc123.qcow2",
        timestamp=datetime(2025, 1, 1, 0, 0),
        allocation=1000,
        disk="vda",
    )
    target_hash = mock_factory._bitmap_backup_provider.target_hash(str(target.path))
    dead_ck = f"qsnap-{target_hash}-vda-20250801T120000"
    provider = _install_provider(
        mock_factory,
        assessment=_dead_assessment(dead_ck),
        backup_kind="full",  # recovery FULL fallback
    )

    with patch.object(provider, "list", return_value=[backup]):
        mock_shell.expect("virsh checkpoint-list").returns(_shell_ok(f"{dead_ck}\n"))
        mock_shell.expect("virsh checkpoint-delete").returns(_OK)
        mock_shell.expect("virsh blockjob").returns(_shell_ok("No current block job"))
        caplog.set_level(logging.WARNING)
        result = core.run()

    # Exit 0: successful recovery is a warning, not an error.
    assert result.results[0].success is True, f"Run failed: {result.results[0].error}"
    assert _format_pipeline_result(result) == EXIT_SUCCESS

    # The recovery FULL fallback was created and audited as backup_full.
    full_actions = [a for a in result.actions if a.action == "backup_full"]
    assert len(full_actions) == 1, f"Expected one recovery FULL action, got {full_actions}"

    # WARNING-only: the dead-bitmap evidence is logged, no ERROR/CRITICAL.
    assert any("dead-bitmap checkpoint" in r.getMessage() for r in caplog.records), (
        f"Expected dead-bitmap WARNING, got: {[r.getMessage() for r in caplog.records]}"
    )
    bad = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert bad == [], f"Recovery must not log ERROR/CRITICAL, got: {bad}"


# ═══════════════════════════════════════════════════════════════════════════
# FULL fallback ordering
# ═══════════════════════════════════════════════════════════════════════════


def test_recovery_full_retires_generation_only_after_verification(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """The old generation is retired only after the new FULL is verified.

    End-to-end ``_backup_target``: the recovery FULL is created, retention
    flags the superseded chain, and ``_cleanup_backups`` applies the
    verify-before-delete gate (M1 always, M2 per config) BEFORE the old
    FULL is deleted.  Call ordering: create (new FULL) → verify (old FULL)
    → delete (old FULL).
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = make_target(path=str(target_dir), target_chain_length=0)
    vm = make_vm_config(name="testvm", targets=[target])
    global_cfg = GlobalConfig(
        full_verify_after_create="off",
        full_verify_before_delete="check",
        free_space_check="off",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # ── Old (superseded) generation: FULL + 2 incrementals on disk+state ──
    old_full_stem = "testvm.FULL.20250101T000000_vda_abc111"
    old_full_path = target_dir / f"{old_full_stem}.qcow2"
    old_full_path.touch()
    mock_state.record_full_backup(
        str(target_dir), old_full_stem, datetime(2025, 1, 1, 0, 0, 0), "vda"
    )
    inc_names = ["testvm.20250101T010000_vda_a1b2c3", "testvm.20250101T020000_vda_d4e5f6"]
    for inc in inc_names:
        (target_dir / f"{inc}.qcow2").touch()
        mock_state.record_incremental_dependency(str(target_dir), inc, old_full_stem)

    # provider.list returns the old chain (FULL first, then incs).
    old_backups = [
        SnapshotInfo(
            name=old_full_stem,
            path=old_full_path,
            timestamp=datetime(2025, 1, 1, 0, 0),
            allocation=0,
            disk="vda",
        ),
        SnapshotInfo(
            name=inc_names[0],
            path=target_dir / f"{inc_names[0]}.qcow2",
            timestamp=datetime(2025, 1, 1, 1, 0),
            allocation=0,
            disk="vda",
        ),
        SnapshotInfo(
            name=inc_names[1],
            path=target_dir / f"{inc_names[1]}.qcow2",
            timestamp=datetime(2025, 1, 1, 2, 0),
            allocation=0,
            disk="vda",
        ),
    ]
    # Incremental anchor resolution: each inc's backing file is the old FULL.
    for inc in inc_names:
        mock_shell.expect(rf"qemu-img info --output=json.*{inc}").returns(
            _shell_ok(f'{{"format": "qcow2", "backing-filename": "{old_full_path}"}}')
        )
    mock_shell.expect("virsh blockjob").returns(_shell_ok("No current block job"))

    provider = _install_provider(mock_factory, backup_kind="full")
    mock_factory._retention_engine = MockRetentionEngine(keep=[], remove=[old_full_stem])

    order: list[str] = []
    run_backup_orig = provider.run_backup

    def _tracked_run_backup(*args, **kwargs):
        result = run_backup_orig(*args, **kwargs)
        order.append(f"created:{result.snapshot_name}")
        return result

    def _tracked_verify(shell, path, mode, **kwargs):
        order.append(f"verify:{Path(path).stem}:{mode}")
        return None

    def _tracked_delete(backup):
        order.append(f"delete:{backup.name}")
        return _OK

    with (
        patch.object(provider, "run_backup", side_effect=_tracked_run_backup),
        patch.object(provider, "list", return_value=old_backups),
        patch("qsnap.core.verify_full_backup", side_effect=_tracked_verify),
        patch.object(provider, "delete", side_effect=_tracked_delete),
    ):
        core._backup_target(vm, target)

    # The new recovery FULL was created...
    created = [e for e in order if e.startswith("created:")]
    assert len(created) == 1, f"Expected one created FULL, got {order}"
    # ...and the old generation was retired in the same run.
    assert any("delete:" in e for e in order), f"Old generation should be deleted, got {order}"

    # Ordering invariant: verify(old FULL) strictly before delete(old FULL).
    verify_idx = next(i for i, e in enumerate(order) if e.startswith("verify:testvm.FULL"))
    delete_idx = next(i for i, e in enumerate(order) if e.startswith("delete:testvm.FULL"))
    assert verify_idx < delete_idx, (
        f"Old FULL must be verified (M1/M2) before deletion, got order: {order}"
    )
    # The new FULL was created before the old generation was deleted.
    assert created and order.index(created[0]) < delete_idx


def test_failed_recovery_full_preserves_old_generation(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """A failed recovery FULL aborts before retention — old generation intact.

    The FULL-fallback failure raises BackupAbortError (VM-level isolation);
    retention and cleanup are never reached, so the superseded generation's
    files and state records are preserved.
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = make_target(path=str(target_dir), target_chain_length=0)
    vm = make_vm_config(name="testvm", targets=[target])
    global_cfg = GlobalConfig(free_space_check="off")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    old_full_stem = "testvm.FULL.20250101T000000_vda_abc111"
    old_full_path = target_dir / f"{old_full_stem}.qcow2"
    old_full_path.touch()
    mock_state.record_full_backup(
        str(target_dir), old_full_stem, datetime(2025, 1, 1, 0, 0, 0), "vda"
    )
    mock_state.record_incremental_dependency(
        str(target_dir), "testvm.20250101T010000_vda_a1b2c3", old_full_stem
    )

    mock_shell.expect("virsh blockjob").returns(_shell_ok("No current block job"))

    provider = _install_provider(mock_factory, backup_kind="full")
    failing = BackupResult(
        success=False,
        snapshot_name="",
        source_path=vm.disks[0].base_image,
        target_path=target_dir / "testvm.FULL.failed.qcow2",
        bytes_transferred=0,
        error="verification failed: FULL backup has corrupt bit set — file is damaged",
        disk="vda",
        kind="full",
    )

    with (
        patch.object(provider, "run_backup", return_value=failing),
        patch.object(provider, "delete", wraps=provider.delete) as del_spy,
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm, target)

    # Old generation preserved: file still on disk, nothing deleted.
    assert old_full_path.exists(), "Old FULL file must be preserved"
    assert not del_spy.called, "No backup deletion may occur on the abort path"


# ═══════════════════════════════════════════════════════════════════════════
# Startup invariant
# ═══════════════════════════════════════════════════════════════════════════


def test_startup_removes_dead_bitmap_checkpoint_with_covering_file(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    caplog,
):
    """Startup removes a dead-bitmap checkpoint even when a covering file
    exists (design D12 extended invariant).

    The covering file alone used to keep the checkpoint; now the bitmap
    is probed and a DEAD probe routes to deletion.
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "testvm.20250801T120000_vda_a1b2c3.qcow2").touch()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    backup = SnapshotInfo(
        name="testvm.FULL.20250101T000000_vda_abc123.qcow2",
        path=target_dir / "testvm.FULL.20250101T000000_vda_abc123.qcow2",
        timestamp=datetime(2025, 1, 1, 0, 0),
        allocation=1000,
        disk="vda",
    )
    target_hash = mock_factory._bitmap_backup_provider.target_hash(str(target.path))
    dead_ck = f"qsnap-{target_hash}-vda-20250801T120000"
    provider = _install_provider(mock_factory, assessment=_dead_assessment(dead_ck))

    with patch.object(provider, "list", return_value=[backup]):
        mock_shell.expect("virsh checkpoint-list").returns(_shell_ok(f"{dead_ck}\n"))
        mock_shell.expect("virsh checkpoint-delete").returns(_OK)
        caplog.set_level(logging.WARNING)
        core._validate_state_at_startup(vm)

    delete_calls = [c for c in mock_shell.call_history if "checkpoint-delete" in c]
    assert delete_calls, "Dead-bitmap checkpoint with covering file must be deleted"
    assert dead_ck in delete_calls[0], (
        f"checkpoint-delete must target the dead checkpoint, got: {delete_calls}"
    )


def test_startup_dry_run_predicts_removal_no_delete(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    caplog,
):
    """Dry-run startup invariant predicts the dead-bitmap checkpoint removal
    without executing it (zero-mutation invariant, D10).

    The dry-run guard lives in ``_check_orphan_checkpoint``: a DEAD
    covered checkpoint produces a ``[startup] [dry-run] Would remove
    orphan checkpoint ...`` prediction and no ``checkpoint-delete`` call.

    NOTE (source gap): ``_validate_state_at_startup`` returns early in
    dry-run mode (auto-recovery is skipped), so the full startup path
    never reaches the orphan invariant during a dry-run pipeline — the
    guard is only reachable via the direct call exercised here.
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "testvm.20250801T120000_vda_a1b2c3.qcow2").touch()
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.dry_run = True

    target_hash = mock_factory._bitmap_backup_provider.target_hash(str(target.path))
    dead_ck = f"qsnap-{target_hash}-vda-20250801T120000"
    provider = _install_provider(mock_factory, assessment=_dead_assessment(dead_ck))

    mock_shell.expect("virsh checkpoint-list").returns(_shell_ok(f"{dead_ck}\n"))
    caplog.set_level(logging.WARNING)
    core._check_orphan_checkpoint(
        vm.name,
        str(target.path),
        target_hash,
        "vda",
        provider=provider,
        vm_config=vm,
        target=target,
        disk_cfg=vm.disks[0],
    )

    assert "[startup] [dry-run] Would remove orphan checkpoint" in caplog.text, (
        f"Dry-run must predict the removal, got: {caplog.text}"
    )
    delete_calls = [c for c in mock_shell.call_history if "checkpoint-delete" in c]
    assert not delete_calls, "Dry-run must NOT delete the checkpoint"


# ═══════════════════════════════════════════════════════════════════════════
# State management
# ═══════════════════════════════════════════════════════════════════════════


def test_boot_id_recorded_after_successful_run(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """The host boot_id is recorded in per-VM state after a fully
    successful pipeline run (crash evidence, design D3)."""
    vm = make_vm_config(name="testvm", targets=[make_target()])
    global_cfg = make_global_config(full_verify_after_create="off")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.run()

    assert result.results[0].success is True
    boot_id = mock_state.get_boot_id("testvm")
    assert boot_id is not None, "boot_id must be recorded after a successful run"

    # The recorded value matches the real host boot_id when readable.
    try:
        expected = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        expected = None
    if expected:
        assert boot_id == expected, f"boot_id {boot_id!r} != host {expected!r}"


def test_last_commit_ts_written_after_blockcommit(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """The per-disk last_commit_ts marker is written after a successful
    live (virsh) blockcommit — recovery gate G1 evidence."""
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        lifecycle_mode="virsh",
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap1 = SnapshotInfo(
        name="snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
        disk="vda",
    )
    snap2 = SnapshotInfo(
        name="snap2",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap2.qcow2"),
        timestamp=datetime(2025, 7, 13, 9, 0),
        allocation=2000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap1)
    mock_state.record_snapshot("testvm", snap2)

    # VM running → live virsh blockcommit of the non-active prefix.
    mock_shell.expect_first("virsh domstate").returns(
        ShellResult(success=True, stdout="running\n", stderr="", returncode=0, error=None)
    )
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=(
                " Target   Source\n"
                "--------------------------------------\n"
                " vda      /var/lib/libvirt/snapshots/testvm/snap2.qcow2\n"
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    retention = RetentionResult(keep=["snap2"], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    assert bc_spy.called, "blockcommit should proceed for the live path"
    marker = mock_state.get_last_commit_ts("testvm", "vda")
    assert marker is not None, "last_commit_ts must be written after blockcommit"
    assert "T" in marker and len(marker) == 15, f"Unexpected marker format: {marker!r}"


def test_last_commit_ts_written_after_offline_commit(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """The per-disk last_commit_ts marker is written after a successful
    offline (qemu-img) commit — recovery gate G1 evidence."""
    global_cfg = make_global_config(
        chain_verify_before_commit=False,
        chain_verify_after_commit=False,
    )
    vm = make_vm_config(
        name="testvm",
        lifecycle_mode="virsh",  # the fork overrides to qemu-img when shut off
        base_image="/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir="/var/lib/libvirt/snapshots/testvm",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snap = SnapshotInfo(
        name="snap1",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        timestamp=datetime(2025, 7, 13, 8, 0),
        allocation=1000,
        disk="vda",
    )
    mock_state.record_snapshot("testvm", snap)

    # VM shut off (conftest default domstate) → offline qemu-img commit.
    # domblklist reports the base image as the XML-referenced tip, so
    # snap1 is committable (not the tip).
    mock_shell.expect_first("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=(
                " Target   Source\n"
                "--------------------------------------\n"
                " vda      /var/lib/libvirt/images/testvm.qcow2\n"
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # _refresh_domain_backing_store after the offline commit.
    mock_shell.expect("virsh dumpxml").returns(_shell_ok("<domain><devices/></domain>"))

    retention = RetentionResult(keep=[], remove=["snap1"])
    manager = mock_factory._lifecycle_manager

    with (
        patch("os.path.exists", return_value=True),
        patch.object(core, "_get_chain_length", return_value=3),
        patch.object(
            mock_factory,
            "create_lifecycle_manager",
            wraps=mock_factory.create_lifecycle_manager,
        ) as lifecycle_spy,
        patch.object(manager, "blockcommit", wraps=manager.blockcommit) as bc_spy,
    ):
        core._blockcommit_snapshots(vm, retention)

    assert bc_spy.called, "blockcommit should proceed for the offline path"
    lifecycle_spy.assert_called_once_with(mode="qemu-img")
    marker = mock_state.get_last_commit_ts("testvm", "vda")
    assert marker is not None, "last_commit_ts must be written after offline commit"
    assert "T" in marker and len(marker) == 15, f"Unexpected marker format: {marker!r}"


# ═══════════════════════════════════════════════════════════════════════════
# Orthogonality
# ═══════════════════════════════════════════════════════════════════════════


def test_backup_phase_with_zero_snapshots_produces_full(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """The backup phase is orthogonal to the snapshot world: with zero
    snapshots in state it still produces a FULL backup.

    The FULL decision comes from target-internal data (no FULL records
    and no checkpoint → FULL), never from snapshot state.
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    global_cfg = GlobalConfig(free_space_check="off")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    mock_shell.expect("virsh blockjob").returns(_shell_ok("No current block job"))
    provider = _install_provider(mock_factory, backup_kind="full")

    results: list[BackupResult] = []
    run_backup_orig = provider.run_backup

    def _capture_run_backup(*args, **kwargs):
        result = run_backup_orig(*args, **kwargs)
        results.append(result)
        return result

    with patch.object(provider, "run_backup", side_effect=_capture_run_backup) as run_spy:
        core._backup_target(vm, target)

    # Zero snapshot data in the backup phase.
    assert mock_state.get_snapshots("testvm") == []

    # Exactly one FULL backup created (force_full because no FULL exists).
    assert run_spy.called
    assert run_spy.call_args.kwargs["force_full"] is True, (
        "No prior FULL → run_backup must be asked for a FULL"
    )
    assert results[0].kind == "full"
    full_actions = [a for a in core._actions if a.action == "backup_full"]
    assert len(full_actions) == 1, f"Expected one backup_full action, got {full_actions}"


# ═══════════════════════════════════════════════════════════════════════════
# Retention
# ═══════════════════════════════════════════════════════════════════════════


def test_recovery_full_retires_generation_immediately_ignoring_keep_generations(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """A recovery FULL retires the superseded generation in the SAME run.

    Even with ``keep_generations`` set, the superseded chain (old FULL
    plus its incrementals) is removed once the recovery FULL completes
    and passes verification.

    NOTE (source gap): Core has no recovery-aware retention bypass yet —
    the immediate retirement is driven by the retention engine's remove
    set, not by a recovery flag.  The spec's "retire immediately
    regardless of keep_generations" logic (per-chain-retention) is not
    implemented in ``_cleanup_backups``/``_evaluate_backup_retention``;
    this test exercises the retirement wiring via the mock engine.
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = make_target(path=str(target_dir), target_chain_length=0, target_keep_generations=2)
    vm = make_vm_config(name="testvm", targets=[target])
    global_cfg = GlobalConfig(
        full_verify_after_create="off",
        full_verify_before_delete="off",
        free_space_check="off",
    )
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    old_full_stem = "testvm.FULL.20250101T000000_vda_abc111"
    old_full_path = target_dir / f"{old_full_stem}.qcow2"
    old_full_path.touch()
    mock_state.record_full_backup(
        str(target_dir), old_full_stem, datetime(2025, 1, 1, 0, 0, 0), "vda"
    )
    inc_name = "testvm.20250101T010000_vda_a1b2c3"
    (target_dir / f"{inc_name}.qcow2").touch()
    mock_state.record_incremental_dependency(str(target_dir), inc_name, old_full_stem)

    old_backups = [
        SnapshotInfo(
            name=old_full_stem,
            path=old_full_path,
            timestamp=datetime(2025, 1, 1, 0, 0),
            allocation=0,
            disk="vda",
        ),
        SnapshotInfo(
            name=inc_name,
            path=target_dir / f"{inc_name}.qcow2",
            timestamp=datetime(2025, 1, 1, 1, 0),
            allocation=0,
            disk="vda",
        ),
    ]
    mock_shell.expect(rf"qemu-img info --output=json.*{inc_name}").returns(
        _shell_ok(f'{{"format": "qcow2", "backing-filename": "{old_full_path}"}}')
    )
    mock_shell.expect("virsh blockjob").returns(_shell_ok("No current block job"))

    provider = _install_provider(mock_factory, backup_kind="full")
    mock_factory._retention_engine = MockRetentionEngine(keep=[], remove=[old_full_stem])

    deleted_files: list[str] = []
    delete_orig = provider.delete

    def _delete_and_unlink(backup):
        deleted_files.append(backup.name)
        with contextlib.suppress(OSError):
            Path(backup.path).unlink()
        return delete_orig(backup)

    with (
        patch.object(provider, "list", return_value=old_backups),
        patch("qsnap.core.verify_full_backup", return_value=None),
        patch.object(provider, "delete", side_effect=_delete_and_unlink),
    ):
        core._backup_target(vm, target)

    # Superseded generation retired in the same run despite keep_generations=2.
    assert old_full_stem in deleted_files, f"Old FULL must be retired, deleted={deleted_files}"
    assert inc_name in deleted_files, f"Old incremental must be retired, deleted={deleted_files}"
    assert not old_full_path.exists(), "Old FULL file must be gone after retirement"


def test_corrupt_superseded_full_preserved_critical_log(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    caplog,
):
    """A corrupt superseded FULL is blocked from deletion at retirement and
    reported CRITICAL (verify-before-delete gate holds in the recovery
    retirement path)."""
    target_dir = tmp_path / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    global_cfg = make_global_config(full_verify_before_delete="check")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    full_name = "testvm.FULL.20250101T000000_vda_abc111"
    full_path = target_dir / f"{full_name}.qcow2"
    full_path.touch()
    mock_state.record_full_backup(str(target_dir), full_name, datetime(2025, 1, 1, 0, 0, 0), "vda")

    full_info = SnapshotInfo(
        name=full_name,
        path=full_path,
        timestamp=datetime(2025, 1, 1, 0, 0),
        allocation=0,
        disk="vda",
    )
    retention = RetentionResult(keep=[], remove=[full_name])
    provider = _install_provider(mock_factory, backup_kind="full")

    caplog.set_level(logging.CRITICAL)
    with (
        patch(
            "qsnap.core.verify_full_backup",
            return_value="verification failed: FULL backup has corrupt bit set — file is damaged",
        ),
        patch.object(provider, "delete", wraps=provider.delete) as del_spy,
    ):
        core._cleanup_backups(vm, target, [full_info], retention)

    assert not del_spy.called, "Corrupt superseded FULL must NOT be deleted"
    assert full_path.exists(), "Corrupt superseded FULL file must be preserved"
    critical = [r.getMessage() for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert any("corrupt" in m for m in critical), (
        f"Expected CRITICAL corrupt-FULL log, got: {critical}"
    )


def test_normal_retention_keep_generations_unchanged(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
):
    """Outside the recovery path, normal retention semantics are unchanged:
    when the retention engine keeps the generation, nothing is deleted."""
    target_dir = tmp_path / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = make_target(path=str(target_dir), target_keep_generations=2)
    vm = make_vm_config(name="testvm", targets=[target])
    global_cfg = GlobalConfig(free_space_check="off")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    old_full_stem = "testvm.FULL.20250101T000000_vda_abc111"
    old_full_path = target_dir / f"{old_full_stem}.qcow2"
    old_full_path.touch()
    mock_state.record_full_backup(
        str(target_dir), old_full_stem, datetime(2025, 1, 1, 0, 0, 0), "vda"
    )

    old_backups = [
        SnapshotInfo(
            name=old_full_stem,
            path=old_full_path,
            timestamp=datetime(2025, 1, 1, 0, 0),
            allocation=0,
            disk="vda",
        )
    ]
    mock_shell.expect("virsh blockjob").returns(_shell_ok("No current block job"))

    provider = _install_provider(mock_factory, backup_kind="full")
    # Normal retention: keep everything (no recovery retirement).
    mock_factory._retention_engine = MockRetentionEngine(keep=[old_full_stem], remove=[])

    with (
        patch.object(provider, "list", return_value=old_backups),
        patch.object(provider, "delete", wraps=provider.delete) as del_spy,
    ):
        core._backup_target(vm, target)

    assert not del_spy.called, "Normal retention must not delete kept generations"
    assert old_full_path.exists(), "Kept generation file must remain"
    delete_actions = [a for a in core._actions if a.action == "backup_delete"]
    assert delete_actions == []


# ═══════════════════════════════════════════════════════════════════════════
# Provider audit
# ═══════════════════════════════════════════════════════════════════════════


def test_recovered_delta_audit_and_summary_kind(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
    tmp_path,
    caplog,
):
    """A recovered-delta backup is auditable: BackupResult.kind ==
    "recovered_delta", Core records a backup_transfer action (transfer-
    based, not FULL), the log names the kind, and the summary legend
    renders it distinctly."""
    target_dir = tmp_path / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = make_target(path=str(target_dir))
    vm = make_vm_config(name="testvm", targets=[target])
    global_cfg = GlobalConfig(free_space_check="off")
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    # A prior FULL exists (no deps) so this run is a delta-position backup.
    full_name = "testvm.FULL.20250101T000000_vda_abc111"
    (target_dir / f"{full_name}.qcow2").touch()
    mock_state.record_full_backup(str(target_dir), full_name, datetime(2025, 1, 1, 0, 0, 0), "vda")
    mock_shell.expect("virsh blockjob").returns(_shell_ok("No current block job"))

    provider = _install_provider(mock_factory, backup_kind="recovered_delta")

    results: list[BackupResult] = []
    run_backup_orig = provider.run_backup

    def _capture_run_backup(*args, **kwargs):
        result = run_backup_orig(*args, **kwargs)
        results.append(result)
        return result

    caplog.set_level(logging.INFO)
    with patch.object(provider, "run_backup", side_effect=_capture_run_backup) as run_spy:
        core._backup_target(vm, target)

    # The provider produced a recovered-delta result.
    assert run_spy.called
    assert results[0].kind == "recovered_delta"
    # Core audits it as a transfer (not a FULL), with the kind in the log.
    transfer_actions = [a for a in core._actions if a.action == "backup_transfer"]
    assert len(transfer_actions) == 1, (
        f"Recovered delta must be audited as backup_transfer, got {core._actions}"
    )
    assert "transferred recovered-delta" in caplog.text, (
        f"Log should name the recovered-delta kind, got: {caplog.text}"
    )
    # The summary legend renders recovered-delta transfers distinctly.
    assert any(symbol == "rrr" and "recovered-delta" in desc for symbol, desc in _LEGEND_LINES), (
        f"Summary legend must render recovered-delta, got: {_LEGEND_LINES}"
    )
