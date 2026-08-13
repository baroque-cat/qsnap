"""Unit tests for the dry-run prediction engine.

Covers: simulated snapshots, threading through retention/backup,
incremental transfer prediction, FULL size estimation, backup retention
rollover, per-disk blockcommit prediction, deferred drain prediction,
structured predictions channel.

Uses MockShell, MockVMModuleFactory, InMemoryStateManager, MockConfigFacade.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core, PipelineResult
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import (
    ActionRecord,
    BaselineAssessment,
    FullBackupInfo,
    ShellResult,
    SnapshotInfo,
)
from tests.mocks import (
    InMemoryStateManager,
    MockBitmapBackupProvider,
    MockConfigFacade,
    MockRetentionEngine,
    MockShell,
    MockVMModuleFactory,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_vm(
    name: str = "testvm",
    disks: list[DiskConfig] | None = None,
    targets: list[TargetConfig] | None = None,
    **kwargs: object,
) -> VMConfig:
    """Create a VMConfig with sensible defaults for dry-run tests."""
    if disks is None:
        disks = [DiskConfig(target="vda", base_image=Path(f"/var/lib/libvirt/images/{name}.qcow2"))]
    if targets is None:
        targets = [TargetConfig(path=Path(f"/mnt/backup/{name}"))]
    defaults: dict[str, object] = {
        "name": name,
        "disks": disks,
        "targets": targets,
        "snapshot_dir": Path(f"/var/lib/libvirt/snapshots/{name}"),
    }
    defaults.update(kwargs)
    return VMConfig(**defaults)  # type: ignore[arg-type]


def _build_core(
    *,
    vm: VMConfig | None = None,
    mock_config: MockConfigFacade | None = None,
    mock_factory: MockVMModuleFactory | None = None,
    mock_state: InMemoryStateManager | None = None,
    mock_shell: MockShell | None = None,
    global_config: GlobalConfig | None = None,
    dry_run: bool = True,
) -> Core:
    """Build a Core instance with the given mocks, defaults for all else."""
    vms = [vm] if vm is not None else []
    config = mock_config or MockConfigFacade(global_config=global_config or GlobalConfig(), vms=vms)
    factory = mock_factory or MockVMModuleFactory()
    state = mock_state or InMemoryStateManager()
    shell = mock_shell or MockShell()
    core = Core(config=config, factory=factory, state=state, shell=shell)
    core.dry_run = dry_run
    return core


def _expect_idle_blockjob(mock_shell: MockShell) -> None:
    """Register the read-only blockjob probe expectation (dry-run parity,
    recover-lost-checkpoint-bitmaps D10).

    Dry-run now executes every read-only check of the real path —
    including ``virsh blockjob`` — before predicting the backup kind.
    """
    mock_shell.expect("virsh blockjob").returns(
        ShellResult(
            success=True,
            stdout="No current block job",
            stderr="",
            returncode=0,
            error=None,
        )
    )


def _configure_assessment(
    mock_factory: MockVMModuleFactory,
    assessment: BaselineAssessment,
) -> MockBitmapBackupProvider:
    """Point the factory's bitmap provider at a provider whose
    ``assess_baseline`` returns *assessment*.

    Dry-run backup predictions are driven by
    ``IBackupProvider.assess_baseline`` (recover-lost-checkpoint-bitmaps
    D10) instead of Core-side name-only ``list_checkpoints`` probing.
    """
    provider = MockBitmapBackupProvider(assessment=assessment)
    mock_factory._bitmap_backup_provider = provider
    mock_factory._backup_provider = provider
    return provider


# ── Test 1: multi-disk simulated snapshots ─────────────────────────────────


def test_simulated_snapshots_multi_disk(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
) -> None:
    """Multi-disk VM produces per-disk simulated snapshots in dry-run."""
    vm = _make_vm(
        name="testvm",
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm_vda.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm_vdb.qcow2")),
        ],
    )
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )
    result = core.run()

    assert result.dry_run is True
    # Predictions should contain two snapshot_create entries, one per disk.
    snapshot_predictions = [p for p in result.predictions if p.action == "snapshot_create"]
    assert len(snapshot_predictions) == 2, (
        f"Expected 2 snapshot_create predictions, got {len(snapshot_predictions)}"
    )

    disks_found = {p.disk for p in snapshot_predictions}
    assert disks_found == {"vda", "vdb"}, f"Expected disks vda, vdb but got {disks_found}"

    # Each prediction has name, path, size, and disk set.
    for p in snapshot_predictions:
        assert p.name, "Snapshot prediction name should not be empty"
        assert p.path.name.endswith(".qcow2"), f"Path should end with .qcow2: {p.path}"
        assert p.size > 0, f"Size should be > 0, got {p.size}"
        assert p.disk is not None, "disk field must be set"


# ── Test 2: onchange gate closed ──────────────────────────────────────────


def test_simulated_snapshots_onchange_gate_closed(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
) -> None:
    """Onchange gate closed produces NO simulated snapshots and no snapshot_create predictions."""
    # Configure change detector as unchanged.
    mock_factory.change_detector.changed = False

    vm = _make_vm(name="testvm", snapshot_create="onchange")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )
    result = core.run()

    assert result.dry_run is True
    snapshot_predictions = [p for p in result.predictions if p.action == "snapshot_create"]
    assert len(snapshot_predictions) == 0, (
        f"Expected 0 snapshot_create predictions when onchange gate closed, got {len(snapshot_predictions)}"
    )


# ── Test 3: allocation from read-only detection ────────────────────────────


def test_simulated_snapshot_allocation_read_only(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
) -> None:
    """Simulated snapshot allocation comes from read-only change detection."""
    # Configure a specific allocation value on the mock change detector.
    mock_factory.change_detector.current_allocation = 888888

    # Verify state has no allocation entry before the run.
    assert mock_state.get_last_allocation("testvm", "vda") is None

    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    # Spy on has_changed to verify it was called (read-only).
    with patch.object(
        mock_factory.change_detector,
        "has_changed",
        wraps=mock_factory.change_detector.has_changed,
    ) as hc_spy:
        result = core.run()

    # has_changed() was invoked (read-only detection).
    assert hc_spy.called, "has_changed() should be called for read-only change detection"

    # State was NOT mutated (no allocation written).
    assert mock_state.get_last_allocation("testvm", "vda") is None, (
        "State should not be mutated in dry-run"
    )

    # Allocation equals the detector's current_allocation.
    snapshot_predictions = [p for p in result.predictions if p.action == "snapshot_create"]
    assert len(snapshot_predictions) == 1
    assert snapshot_predictions[0].size == 888888, (
        f"Expected size=888888, got {snapshot_predictions[0].size}"
    )


# ── Test 4: retention counts simulated snapshot ────────────────────────────


def test_retention_counts_simulated_snapshot(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    tmp_path: Path,
) -> None:
    """Retention evaluation counts the would-be-created snapshot."""
    snap_dir = tmp_path / "snapshots" / "testvm"
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Pre-populate 5 existing snapshots in state (each on its own vda chain).
    base = datetime(2025, 8, 1, 12, 0, 0)
    for i in range(5):
        name = f"testvm.{i}_vda_abc{i:03d}"
        path = snap_dir / f"{name}.qcow2"
        path.touch()
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=name,
                path=path,
                timestamp=base.replace(hour=12 + i),
                allocation=65536,
                disk="vda",
            ),
        )

    # The retention engine is in "keep nothing" mode to ensure we see the
    # effect.  With 5 state snapshots + 1 simulated = 6 total, and
    # chain_length=4, the 3 oldest should be marked for removal.
    chain_length = 4
    remove_set: list[str] = [f"testvm.{i}_vda_abc{i:03d}" for i in range(3)]
    mock_factory._retention_engine = MockRetentionEngine(
        keep=[f"testvm.{i}_vda_abc{i:03d}" for i in range(3, 5)], remove=remove_set
    )

    vm = _make_vm(
        name="testvm",
        snapshot_chain_length=chain_length,
        snapshot_dir=snap_dir,
    )
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    result = core.run()

    # The blockcommit prediction should reference the removed snapshots (per disk).
    snapshot_delete_predictions = [p for p in result.predictions if p.action == "snapshot_delete"]

    # With chain_length=4 + 1 simulated = 5, retention removes 3 oldest.
    # But the retention mock says remove=3 specific names.  Those should
    # trigger per-disk blockcommit predictions.
    assert len(snapshot_delete_predictions) == len(remove_set), (
        f"Expected {len(remove_set)} snapshot_delete predictions, got {len(snapshot_delete_predictions)}"
    )

    # Snapshot_create prediction for the simulated snapshot is present.
    snapshot_create_preds = [p for p in result.predictions if p.action == "snapshot_create"]
    assert len(snapshot_create_preds) == 1, "Simulated snapshot should be predicted"


# ── Test 5: no checkpoint predicts FULL ────────────────────────────────────


def test_no_checkpoint_predicts_full(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
) -> None:
    """First run (no checkpoints/backups on target) predicts FULL.

    Backup predictions come from target-internal data (checkpoint state,
    FULL records, dependency count) — never from snapshot data.
    """
    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )
    result = core.run()

    full_predictions = [p for p in result.predictions if p.action == "backup_full"]
    assert len(full_predictions) >= 1, (
        f"First run should predict at least one backup_full, got {len(full_predictions)}"
    )

    # The FULL name should follow the illustrative pattern {vm}.FULL.{ts}_{disk}_{hex}.
    for p in full_predictions:
        assert ".FULL." in p.name, f"FULL name should contain .FULL.: {p.name}"
        assert p.disk is not None, "disk field must be set"


# ── Test 8: FULL prediction carries chain size estimate ────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_full_prediction_carries_chain_size(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FULL prediction carries chain size estimate in log and prediction size field.

    The size estimate now comes from the provider's read-only baseline
    assessment (``assess_baseline``, recover-lost-checkpoint-bitmaps
    D10) — the same estimate a real run would compute for the FULL.
    """
    # The dry-run now executes the read-only blockjob probe before
    # predicting (dry-run parity, D10).
    _expect_idle_blockjob(mock_shell)
    # The first-run FULL estimate (10 MiB chain sum) is delivered by the
    # baseline assessment instead of a Core-side qemu-img probe.
    _configure_assessment(
        mock_factory,
        BaselineAssessment(status="no_checkpoint", size_estimate=10485760),
    )

    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    full_predictions = [p for p in result.predictions if p.action == "backup_full"]
    assert len(full_predictions) >= 1

    full_pred = full_predictions[0]
    assert full_pred.size == 10485760, f"Size should be 10485760 (10 MiB), got {full_pred.size}"

    # Log should contain ~10.0 MiB size indicator.
    log_text = caplog.text
    assert "~10.0 MiB" in log_text or "10.0 MiB" in log_text, (
        f"Log should mention the ~ 10 MiB chain size, got: ...{log_text[-500:]}"
    )


# ── Test 9: FULL prediction estimation failure graceful ────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_full_prediction_estimation_failure_graceful(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When size estimation fails inside the baseline assessment,
    prediction is still recorded with size 0 and log says 'size unknown'
    (pipeline not aborted)."""
    # The dry-run now executes the read-only blockjob probe before
    # predicting (dry-run parity, D10).
    _expect_idle_blockjob(mock_shell)
    # The provider's assessment reports no estimate (estimation failed).
    _configure_assessment(
        mock_factory,
        BaselineAssessment(status="no_checkpoint", size_estimate=None),
    )

    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    full_predictions = [p for p in result.predictions if p.action == "backup_full"]
    assert len(full_predictions) >= 1, (
        "FULL prediction should still be emitted even if estimation fails"
    )

    full_pred = full_predictions[0]
    assert full_pred.size == 0, (
        f"Size should be 0 when estimation fails (chain_size or 0 = 0), got {full_pred.size}"
    )

    # Log should say "size unknown".
    log_text = caplog.text
    assert "size unknown" in log_text, (
        f"Log should contain 'size unknown', got: ...{log_text[-500:]}"
    )

    # Pipeline did not abort (check is informational).


# ── Test 9a (dry-run-prediction-unit): base_image fallback for FULL estimate ─


@pytest.mark.unit
@pytest.mark.mock
def test_first_run_dry_run_full_estimate_falls_back_to_base_image(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
) -> None:
    """First-run dry-run FULL size comes from the baseline assessment.

    The first run has no checkpoint, so the provider's read-only
    ``assess_baseline`` reports ``no_checkpoint`` with the FULL estimate
    (10 MiB chain sum).  The FULL prediction carries that estimate.

    NOTE: the dry-run ``free_space_gate`` prediction is no longer emitted
    — with the D10 rewrite, the dry-run per-disk branch completes after
    the backup prediction and never reaches the free-space gate block
    (source: ``_backup_target``).
    """
    # The dry-run now executes the read-only blockjob probe before
    # predicting (dry-run parity, D10).
    _expect_idle_blockjob(mock_shell)
    _configure_assessment(
        mock_factory,
        BaselineAssessment(status="no_checkpoint", size_estimate=10485760),
    )

    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )
    result = core.run()

    # FULL prediction carries the assessment's chain sum.
    full_predictions = [p for p in result.predictions if p.action == "backup_full"]
    assert len(full_predictions) >= 1, "First run should predict a backup_full"
    assert full_predictions[0].size == 10485760, (
        f"FULL prediction should carry the base_image chain sum, got {full_predictions[0].size}"
    )


# ── Test 9b (dry-run-prediction-unit): simulated-path probe logs nothing ────


@pytest.mark.unit
@pytest.mark.mock
def test_dry_run_simulated_path_probe_no_error_log(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The baseline assessment's size probe failure does not log ERROR or a
    'Cannot estimate FULL size' WARNING — the estimate degrades silently
    inside the provider (recover-lost-checkpoint-bitmaps D10)."""
    # The dry-run now executes the read-only blockjob probe before
    # predicting (dry-run parity, D10).
    _expect_idle_blockjob(mock_shell)
    # Assessment carries the fallback estimate — no error is logged.
    _configure_assessment(
        mock_factory,
        BaselineAssessment(status="no_checkpoint", size_estimate=10485760),
    )

    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    with caplog.at_level(logging.WARNING, logger="qsnap"):
        result = core.run()

    # No ERROR-level records at all.
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [], f"Expected no ERROR logs, got: {errors}"

    # No 'Cannot estimate FULL size' WARNING (the fallback is silent).
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "Cannot estimate FULL size" in r.getMessage()
    ]
    assert warnings == [], f"Expected no 'Cannot estimate FULL size' WARNING, got: {warnings}"

    # The fallback estimate was still used — prediction is populated.
    full_predictions = [p for p in result.predictions if p.action == "backup_full"]
    assert len(full_predictions) >= 1
    assert full_predictions[0].size == 10485760, (
        f"Fallback estimate should be used, got {full_predictions[0].size}"
    )


# ── Test 10: backup retention generation rollover predicted ────────────────


def test_backup_retention_generation_rollover_predicted(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Predicted FULLs simulate generation rollover: old-gen backups marked for conditional deletion."""
    target_path = "/mnt/backup/testvm"
    old_full_name = "testvm.FULL.20250801T120000_vda_abc123"
    old_full_ts = datetime(2025, 8, 1, 12, 0, 0)

    # Add a FULL backup into state.
    mock_state.record_full_backup(target_path, f"{old_full_name}.qcow2", old_full_ts, "vda")

    # Add incremental dependencies so dep count exceeds chain_length.
    # With chain_length=0 and 1 dep, needs_full = True.
    mock_state.record_incremental_dependency(target_path, "testvm.inc1_vda_a1", old_full_name)
    mock_state.record_incremental_dependency(target_path, "testvm.inc2_vda_a2", old_full_name)

    # Make backup provider list return the existing FULL backup.
    existing_backup = SnapshotInfo(
        name=old_full_name,
        path=Path(f"{target_path}/{old_full_name}.qcow2"),
        timestamp=old_full_ts,
        allocation=1048576,
        disk="vda",
    )

    target = TargetConfig(
        path=Path(target_path),
        target_chain_length=0,  # deps (2) > 0 → needs_full = True
        target_keep_generations=1,
    )
    vm = _make_vm(name="testvm", targets=[target])

    # Configure retention engine to remove the old FULL chain.
    mock_factory._retention_engine = MockRetentionEngine(keep=[], remove=[old_full_name])

    with (
        patch.object(
            mock_factory._bitmap_backup_provider,
            "list",
            return_value=[existing_backup],
        ),
        caplog.at_level(logging.INFO, logger="qsnap.core"),
    ):
        core = _build_core(
            vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
        )
        result = core.run()

    # Conditional deletion wording: "after new FULL passes verification"
    log_text = caplog.text
    assert "after new FULL passes verification" in log_text, (
        f"Log should contain conditional deletion wording, got: ...{log_text[-500:]}"
    )

    # backup_delete prediction recorded.
    delete_predictions = [p for p in result.predictions if p.action == "backup_delete"]
    assert len(delete_predictions) >= 1, "Should have at least one backup_delete prediction"

    # A backup_full prediction should also exist.
    full_predictions = [p for p in result.predictions if p.action == "backup_full"]
    assert len(full_predictions) >= 1, "Should have a backup_full prediction"


# ── Test 11: per-disk blockcommit prediction ───────────────────────────────


def test_blockcommit_prediction_per_disk(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    tmp_path: Path,
) -> None:
    """Two disks with mergeable snapshots produce two per-disk blockcommit predictions."""
    # Create actual snapshot files so the stale guard doesn't remove them.
    snapshot_dir_vda = tmp_path / "snapshots" / "testvm" / "vda"
    snapshot_dir_vdb = tmp_path / "snapshots" / "testvm" / "vdb"
    snapshot_dir_vda.mkdir(parents=True, exist_ok=True)
    snapshot_dir_vdb.mkdir(parents=True, exist_ok=True)

    base = datetime(2025, 8, 1, 12, 0, 0)
    # Populate 5 snapshots each for vda and vdb.
    for i, disk in enumerate(("vda", "vdb"), start=1):
        snap_dir = snapshot_dir_vda if disk == "vda" else snapshot_dir_vdb
        for j in range(5):
            name = f"testvm.2000080{j}T{j:02d}0000_{disk}_abc{i:03d}"
            path = snap_dir / f"{name}.qcow2"
            path.touch()  # Create empty file
            mock_state.record_snapshot(
                "testvm",
                SnapshotInfo(
                    name=name,
                    path=path,
                    timestamp=base.replace(hour=12 + j),
                    allocation=65536 * (j + 1),
                    disk=disk,
                ),
            )

    # Retention engine removes first 2 from each disk → blockcommit
    # predictions per disk and snapshot_delete per snapshot.
    vda_remove = [
        "testvm.20000800T000000_vda_abc001",
        "testvm.20000801T010000_vda_abc001",
    ]
    vdb_remove = [
        "testvm.20000800T000000_vdb_abc002",
        "testvm.20000801T010000_vdb_abc002",
    ]
    all_remove = vda_remove + vdb_remove
    vda_keep = [
        "testvm.20000802T020000_vda_abc001",
        "testvm.20000803T030000_vda_abc001",
        "testvm.20000804T040000_vda_abc001",
    ]
    vdb_keep = [
        "testvm.20000802T020000_vdb_abc002",
        "testvm.20000803T030000_vdb_abc002",
        "testvm.20000804T040000_vdb_abc002",
    ]
    mock_factory._retention_engine = MockRetentionEngine(
        keep=vda_keep + vdb_keep, remove=all_remove
    )

    vm = _make_vm(
        name="testvm",
        disks=[
            DiskConfig(
                target="vda",
                base_image=Path("/var/lib/libvirt/images/testvm_vda.qcow2"),
                snapshot_dir=snapshot_dir_vda,
            ),
            DiskConfig(
                target="vdb",
                base_image=Path("/var/lib/libvirt/images/testvm_vdb.qcow2"),
                snapshot_dir=snapshot_dir_vdb,
            ),
        ],
        snapshot_dir=snapshot_dir_vda,  # fallback
        snapshot_chain_length=3,  # keep 3 → remove 2 oldest per disk
    )
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )
    result = core.run()

    # Per-disk blockcommit predictions: one per disk.
    bc_preds = [p for p in result.predictions if p.action == "blockcommit"]
    bc_disks = {p.disk for p in bc_preds}
    assert bc_disks == {"vda", "vdb"}, f"Expected blockcommit per disk, got disks: {bc_disks}"

    # snapshot_delete predictions: one per removed snapshot.
    sd_preds = [p for p in result.predictions if p.action == "snapshot_delete"]
    sd_names = {p.name for p in sd_preds}
    assert sd_names == set(all_remove), (
        f"Expected snapshot_delete for removed snapshots, got: {sd_names}"
    )


# ── Test 12: deferred drain prediction no mutation ────────────────────────


def test_deferred_drain_prediction_no_mutation(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Deferred queue survives dry-run byte-identical; blockcommit prediction recorded."""
    snap_names = ["testvm.old1_vda_a01", "testvm.old2_vda_a02"]
    for name in snap_names:
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=name,
                path=Path(f"/var/lib/libvirt/snapshots/testvm/{name}.qcow2"),
                timestamp=datetime(2025, 8, 1, 12, 0, 0),
                allocation=65536,
                disk="vda",
            ),
        )

    # Add a deferred blockcommit for these snapshots.
    mock_state.add_deferred_blockcommit("testvm", "vda", snap_names, "vm_running")

    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    # Record deferred queue before run.
    deferred_before = mock_state.get_deferred_operations("testvm")
    assert len(deferred_before) == 1, "Should have 1 deferred entry before run"

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    # Deferred queue unchanged after dry-run.
    deferred_after = mock_state.get_deferred_operations("testvm")
    assert len(deferred_after) == 1, (
        f"Deferred queue should still have 1 entry, got {len(deferred_after)}"
    )
    assert deferred_after[0].snapshots == snap_names, "Deferred entry snapshots should be identical"
    assert deferred_after[0].reason == "vm_running", "Deferred entry reason should be unchanged"

    # Blockcommit prediction recorded.
    bc_preds = [p for p in result.predictions if p.action == "blockcommit"]
    assert len(bc_preds) >= 1, (
        f"Should have at least one blockcommit prediction from deferred drain, got {len(bc_preds)}"
    )

    # No state.remove_snapshot called — snapshots still present.
    snaps_after = mock_state.get_snapshots("testvm")
    assert len(snaps_after) == 2, f"Snapshots should remain, got {len(snaps_after)}"


# ── Test 13: predictions populated per disk in dry-run ─────────────────────


def test_predictions_populated_per_disk_dry_run(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
) -> None:
    """result.predictions populated with per-disk records (disk field set on each; actions == [])."""
    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )
    result = core.run()

    assert result.actions == [], "Actions must be empty in dry-run"
    assert len(result.predictions) > 0, "Predictions should be populated in dry-run"
    assert result.dry_run is True

    # Every prediction that relates to a disk must have the disk field set.
    disk_actions = {
        "snapshot_create",
        "backup_full",
        "backup_transfer",
        "backup_delete",
        "blockcommit",
        "snapshot_delete",
    }
    for p in result.predictions:
        if p.action in disk_actions:
            assert p.disk is not None, f"Prediction action={p.action} must have disk set, got None"
        assert isinstance(p, ActionRecord), f"Prediction must be ActionRecord, got {type(p)}"


# ── Test 14: predictions empty in real run ─────────────────────────────────


def test_predictions_empty_real_run(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
) -> None:
    """Real (non-dry-run) run leaves result.predictions == []."""
    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm,
        mock_factory=mock_factory,
        mock_state=mock_state,
        mock_shell=mock_shell,
        dry_run=False,
    )
    result = core.run()

    assert result.predictions == [], (
        f"Predictions must be empty in real run, got {result.predictions}"
    )
    assert result.dry_run is False


# ── Test 15: predictions reflect post-run state ────────────────────────────


def test_predictions_reflect_post_run_state(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Predictions reflect post-run state: retention/backup decisions account for
    the simulated snapshot that does not yet exist in state."""
    snap_dir = tmp_path / "snapshots" / "testvm"
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Pre-populate state with snapshots close to chain_length.
    base = datetime(2025, 8, 1, 12, 0, 0)
    chain_length = 4
    for i in range(4):
        name = f"testvm.snap{i}_vda_a{i:03d}"
        path = snap_dir / f"{name}.qcow2"
        path.touch()
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=name,
                path=path,
                timestamp=base.replace(hour=12 + i),
                allocation=65536,
                disk="vda",
            ),
        )

    # Retention removes the oldest 1 snapshot (chain_length=4 → keep 4,
    # but 4 existing + 1 simulated = 5 total → remove oldest).
    mock_factory._retention_engine = MockRetentionEngine(
        keep=[
            "testvm.snap1_vda_a001",
            "testvm.snap2_vda_a002",
            "testvm.snap3_vda_a003",
        ],
        remove=["testvm.snap0_vda_a000"],
    )

    vm = _make_vm(name="testvm", snapshot_chain_length=chain_length, snapshot_dir=snap_dir)
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    # snapshot_delete prediction should include the removed snapshot.
    delete_preds = [p for p in result.predictions if p.action == "snapshot_delete"]
    assert any("testvm.snap0_vda_a000" in p.name for p in delete_preds), (
        "Retention should account for simulated snapshot and remove oldest"
    )

    # The simulated snapshot itself should have a snapshot_create prediction.
    create_preds = [p for p in result.predictions if p.action == "snapshot_create"]
    assert len(create_preds) == 1, "Simulated snapshot should be predicted for creation"


# ── Test 16: dry-run does not drain deferred queue ─────────────────────────


def test_dry_run_does_not_drain_deferred_queue(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
) -> None:
    """After dry-run the deferred queue entries are identical (no execution)."""
    snap_names = ["testvm.def1_vda_a01", "testvm.def2_vda_a02"]
    for name in snap_names:
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=name,
                path=Path(f"/var/lib/libvirt/snapshots/testvm/{name}.qcow2"),
                timestamp=datetime(2025, 8, 1, 12, 0, 0),
                allocation=65536,
                disk="vda",
            ),
        )

    mock_state.add_deferred_blockcommit("testvm", "vda", snap_names, "vm_running")

    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    deferred_before = mock_state.get_deferred_operations("testvm")
    result = core.run()
    deferred_after = mock_state.get_deferred_operations("testvm")

    assert len(deferred_before) == len(deferred_after), (
        f"Deferred queue count should be identical (before={len(deferred_before)}, after={len(deferred_after)})"
    )
    for before, after in zip(deferred_before, deferred_after, strict=False):
        assert before.snapshots == after.snapshots
        assert before.reason == after.reason
        assert before.disk == after.disk

    # Blockcommit predictions should still be recorded.
    assert any(p.action == "blockcommit" for p in result.predictions), (
        "Should have blockcommit predictions even though queue is not mutated"
    )


# ── Test 17: deferred drain prediction when domstate fails ─────────────────


def test_deferred_drain_prediction_domstate_fails(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MockShell makes virsh domstate fail → log contains '(VM state unknown)' wording."""
    snap_names = ["testvm.def1_vda_a01", "testvm.def2_vda_a02"]
    for name in snap_names:
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=name,
                path=Path(f"/var/lib/libvirt/snapshots/testvm/{name}.qcow2"),
                timestamp=datetime(2025, 8, 1, 12, 0, 0),
                allocation=65536,
                disk="vda",
            ),
        )

    mock_state.add_deferred_blockcommit("testvm", "vda", snap_names, "vm_running")

    # Override domstate to fail.  This affects all domstate calls.
    mock_shell.expect_first("virsh domstate").returns(
        ShellResult(success=False, stdout="", stderr="error", returncode=1, error="command failed")
    )

    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    log_text = caplog.text
    assert "VM state unknown" in log_text, (
        f"Log should contain 'VM state unknown', got: ...{log_text[-500:]}"
    )

    # Prediction still recorded.
    bc_preds = [p for p in result.predictions if p.action == "blockcommit"]
    assert len(bc_preds) >= 1, "Should have blockcommit prediction even when domstate fails"

    # No exception raised (pipeline completed).
    assert result is not None


# ── Test 18: predictions not in transaction log ────────────────────────────


def test_predictions_not_in_transaction_log(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    tmp_path: Path,
) -> None:
    """Dry-run writes nothing to the transaction log (no prediction records, no log file created)."""
    tx_log_path = tmp_path / "transaction.log"

    global_cfg = GlobalConfig(transaction_log=str(tx_log_path))
    vm = _make_vm(name="testvm")

    core = _build_core(
        vm=vm,
        mock_factory=mock_factory,
        mock_state=mock_state,
        mock_shell=mock_shell,
        global_config=global_cfg,
    )

    result = core.run()

    # Transaction log should not exist or be empty.
    assert not tx_log_path.exists(), "Transaction log must not be created in dry-run mode"

    # Predictions exist, confirming we ran a real dry-run.
    assert len(result.predictions) > 0, "Dry-run should produce predictions"


# ── Test 19 (risk): predicted names are illustrative ───────────────────────


def test_predicted_names_are_illustrative(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
) -> None:
    """Names are illustrative per spec.

    With frozen time and deterministic token_hex, simulated names are
    identical across runs.  Without freezing, names differ.
    """
    vm = _make_vm(name="testvm")
    frozen_dt = datetime(2025, 7, 13, 15, 31, 0)
    fixed_hex = "aabbcc"  # 3 bytes → 6 hex chars

    def _get_snapshot_names(result: PipelineResult) -> list[str]:
        return [p.name for p in result.predictions if p.action == "snapshot_create"]

    # Run 1 with frozen time + deterministic token_hex.
    with (
        patch("qsnap.core.datetime") as mock_dt,
        patch("qsnap.core.secrets.token_hex", return_value=fixed_hex),
    ):
        mock_dt.now.return_value = frozen_dt
        mock_dt.strftime = datetime.strftime
        core1 = _build_core(
            vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
        )
        result1 = core1.run()

        # Run 2 with same frozen time + same token_hex → identical names.
        with (
            patch("qsnap.core.datetime") as mock_dt,
            patch("qsnap.core.secrets.token_hex", return_value=fixed_hex),
        ):
            mock_dt.now.return_value = frozen_dt
            mock_dt.strftime = datetime.strftime
            core2 = _build_core(
                vm=vm,
                mock_factory=mock_factory,
                mock_state=InMemoryStateManager(),
                mock_shell=MockShell(),
            )
            result2 = core2.run()

    names1 = _get_snapshot_names(result1)
    names2 = _get_snapshot_names(result2)
    assert names1 == names2, (
        f"With frozen time+hex, names should be identical: {names1} vs {names2}"
    )

    # Run 3 without freezing → names differ (different hex).
    core3 = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=InMemoryStateManager(), mock_shell=mock_shell
    )
    result3 = core3.run()
    names3 = _get_snapshot_names(result3)
    assert names1 != names3, f"Without freezing, names should differ: {names1} vs {names3}"


# ── Test 20 (risk): shell call timeouts enforced ────────────────────────────


def test_dry_run_shell_call_timeouts_enforced(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MockShell returns failure for qemu-img info → estimation degrades
    (size 0 / unknown) without hanging or aborting pipeline."""
    # Override the chain-size shell command to simulate a timeout.
    # RealShell catches subprocess.TimeoutExpired internally and returns
    # ShellResult(success=False).  MockShell mirrors this via .returns().
    mock_shell.expect_first("qemu-img info --force-share --backing-chain").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="qemu-img: Could not open",
            returncode=1,
            error="timeout",
        )
    )

    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    # Pipeline completed without aborting.
    assert result is not None

    # FULL prediction still recorded (size degrades to 0).
    full_preds = [p for p in result.predictions if p.action == "backup_full"]
    assert len(full_preds) >= 1, "FULL prediction should still be recorded"
    assert full_preds[0].size == 0, "Size should be 0 when estimation fails"


# ── Test 21 (risk): conditional deletion wording ────────────────────────────


def test_backup_retention_conditional_deletion_wording(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Conditional deletion log contains 'after new FULL passes verification';
    backup_delete prediction recorded."""
    target_path = "/mnt/backup/testvm"
    old_full_name = "testvm.FULL.20250801T120000_vda_old123"

    # Add a FULL backup in state.
    mock_state.record_full_backup(
        target_path, f"{old_full_name}.qcow2", datetime(2025, 8, 1, 12, 0, 0), "vda"
    )
    mock_state.record_incremental_dependency(target_path, "testvm.inc1_vda_x1", old_full_name)

    existing_backup = SnapshotInfo(
        name=old_full_name,
        path=Path(f"{target_path}/{old_full_name}.qcow2"),
        timestamp=datetime(2025, 8, 1, 12, 0, 0),
        allocation=1048576,
        disk="vda",
    )

    target = TargetConfig(path=Path(target_path), target_chain_length=0, target_keep_generations=1)
    vm = _make_vm(name="testvm", targets=[target])

    # Retention removes old chain.
    mock_factory._retention_engine = MockRetentionEngine(keep=[], remove=[old_full_name])

    with (
        patch.object(mock_factory._bitmap_backup_provider, "list", return_value=[existing_backup]),
        caplog.at_level(logging.INFO, logger="qsnap.core"),
    ):
        core = _build_core(
            vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
        )
        result = core.run()

    log_text = caplog.text
    assert "after new FULL passes verification" in log_text, (
        "Conditional deletion log must mention 'after new FULL passes verification'"
    )

    # backup_delete prediction recorded.
    delete_preds = [p for p in result.predictions if p.action == "backup_delete"]
    assert len(delete_preds) >= 1, (
        f"Should have at least one backup_delete prediction, got {len(delete_preds)}"
    )


# ── Test 22 (risk): simulated snapshots not in state ───────────────────────


def test_dry_run_simulated_snapshots_not_in_state(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
) -> None:
    """Pre-populate 3 snapshots in state; dry-run; state.get_snapshots still exactly 3 entries."""
    base = datetime(2025, 8, 1, 12, 0, 0)
    names_before: set[str] = set()
    for i in range(3):
        name = f"testvm.snap{i}_vda_a{i:03d}"
        names_before.add(name)
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=name,
                path=Path(f"/var/lib/libvirt/snapshots/testvm/{name}.qcow2"),
                timestamp=base.replace(hour=12 + i),
                allocation=65536 * (i + 1),
                disk="vda",
            ),
        )

    snapshots_before = mock_state.get_snapshots("testvm")
    assert len(snapshots_before) == 3, (
        f"Should have 3 snapshots before run, got {len(snapshots_before)}"
    )

    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )
    result = core.run()
    assert result.dry_run is True, "Should be a dry-run"

    snapshots_after = mock_state.get_snapshots("testvm")
    assert len(snapshots_after) == 3, (
        f"State should still have exactly 3 snapshots, got {len(snapshots_after)}"
    )

    names_after = {s.name for s in snapshots_after}
    assert names_after == names_before, (
        f"Snapshot names should be identical; before={names_before}, after={names_after}"
    )

    # No predicted names added to state.
    for snap in snapshots_after:
        assert snap.name in names_before, f"Unexpected snapshot in state: {snap.name}"


# ── Group 9 (dry-run-state-hygiene): D11 — predict, never write ────────────


def test_dry_run_phantom_full_cleanup_predicted_not_executed(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dry-run predicts phantom FULL cleanup, cascade, and baseline clear
    but writes NOTHING to state."""
    target_path = "/mnt/backup/testvm"
    full_name = "testvm.FULL.20250801T120000_vda_abc123"
    phantom_path = Path("/nonexistent") / f"{full_name}.qcow2"

    target = TargetConfig(path=Path(target_path))
    vm = _make_vm(name="testvm", targets=[target])

    # Seed state: phantom FULL + incremental deps + stale baseline.
    fake_full = FullBackupInfo(
        name=f"{full_name}.qcow2",
        path=phantom_path,
        timestamp=datetime(2025, 8, 1, 12, 0, 0),
        disk="vda",
    )
    mock_state._full_backups[target_path] = [fake_full]
    mock_state.record_incremental_dependency(target_path, "testvm.inc1_vda_a1", full_name)
    mock_state.record_incremental_dependency(target_path, "testvm.inc2_vda_a2", full_name)
    mock_state.set_last_backup_allocation(target_path, "vda", 99999)

    # Dry-run parity (D10): the read-only blockjob probe runs before
    # the backup prediction.
    _expect_idle_blockjob(mock_shell)

    core = _build_core(
        vm=vm,
        mock_factory=mock_factory,
        mock_state=mock_state,
        mock_shell=mock_shell,
        dry_run=True,
    )
    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    assert result.dry_run is True

    # ── Assert: healing prediction logs ──────────────────────────────
    log_text = caplog.text
    assert "[dry-run] Would remove phantom FULL" in log_text, "Should predict phantom FULL removal"
    assert "cascade:" in log_text, "Should mention cascade count"
    assert "deps would be cleaned" in log_text, "Should mention deps would be cleaned"
    assert "[dry-run] Would clear last_backup_allocation" in log_text, (
        "Should predict baseline cleanup (from startup validation M3 and/or _backup_target)"
    )

    # ── Assert: state NOT mutated — phantom FULL still there ─────────
    remaining = mock_state.get_full_backups(target_path)
    assert len(remaining) == 1, (
        f"Phantom FULL should still be in state (dry-run), got {len(remaining)}"
    )
    assert remaining[0].name == f"{full_name}.qcow2"

    deps = mock_state.get_incremental_dependencies(target_path, full_name)
    assert len(deps) == 2, f"Incremental deps should still be in state (dry-run), got {len(deps)}"

    baseline = mock_state.get_last_backup_allocation(target_path, "vda")
    assert baseline == 99999, f"Baseline should still be in state (dry-run), got {baseline}"


def test_dry_run_stale_baseline_cleanup_predicted_not_executed(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stale baseline with zero FULLs in state: dry-run predicts cleanup,
    state NOT mutated."""
    target_path = "/mnt/backup/testvm"

    target = TargetConfig(path=Path(target_path))
    vm = _make_vm(name="testvm", targets=[target])

    # Seed stale baseline — NO FULLs in state (M1 path).
    mock_state.set_last_backup_allocation(target_path, "vda", 88888)

    # Dry-run parity (D10): the read-only blockjob probe runs before
    # the backup prediction.
    _expect_idle_blockjob(mock_shell)

    core = _build_core(
        vm=vm,
        mock_factory=mock_factory,
        mock_state=mock_state,
        mock_shell=mock_shell,
        dry_run=True,
    )
    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    assert result.dry_run is True

    # ── Assert: prediction log ───────────────────────────────────────
    log_text = caplog.text
    assert "[dry-run] Would clear stale last_backup_allocation for target" in log_text, (
        f"Should predict stale baseline cleanup, got: ...{log_text[-500:]}"
    )
    assert "no FULLs in state" in log_text, (
        f"Should mention 'no FULLs in state', got: ...{log_text[-500:]}"
    )

    # ── Assert: state NOT mutated ────────────────────────────────────
    baseline = mock_state.get_last_backup_allocation(target_path, "vda")
    assert baseline == 88888, f"Baseline should still be {88888} after dry-run, got {baseline}"

    # Confirm no FULLs were ever in state.
    fulls = mock_state.get_full_backups(target_path)
    assert len(fulls) == 0, "No FULLs should exist in state"


def test_dry_run_healing_logs_deduplicated(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One dry-run pipeline fires _validate_state_at_startup twice +
    _backup_target phantom filter; each distinct healing log line
    appears EXACTLY ONCE (per-run dedupe via Core._healing_logged)."""
    target_path = "/mnt/backup/testvm"
    full_name = "testvm.FULL.20250801T120000_vda_dedup1"
    phantom_path = Path("/nonexistent") / f"{full_name}.qcow2"

    target = TargetConfig(path=Path(target_path))
    vm = _make_vm(name="testvm", targets=[target])

    # Seed one phantom FULL so healing predictions fire.
    fake_full = FullBackupInfo(
        name=f"{full_name}.qcow2",
        path=phantom_path,
        timestamp=datetime(2025, 8, 1, 12, 0, 0),
        disk="vda",
    )
    mock_state._full_backups[target_path] = [fake_full]

    # Dry-run parity (D10): the read-only blockjob probe runs before
    # the backup prediction.
    _expect_idle_blockjob(mock_shell)

    core = _build_core(
        vm=vm,
        mock_factory=mock_factory,
        mock_state=mock_state,
        mock_shell=mock_shell,
        dry_run=True,
    )
    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    assert result.dry_run is True

    log_text = caplog.text

    # Count occurrences of the phantom FULL removal log line.
    phantom_count = log_text.count("[dry-run] Would remove phantom FULL")
    assert phantom_count == 1, (
        f"Phantom FULL removal log should appear exactly once, got {phantom_count}"
    )

    # Count occurrences of the "after phantom cleanup" M3 baseline log.
    after_phantom_count = log_text.count("[dry-run] Would clear last_backup_allocation for target ")
    # This message appears twice: once from _validate_state_at_startup M3
    # (key: baseline-after-phantom) and once from _backup_target
    # (key: baseline:{target}:{disk}). These are different dedupe keys.
    # Both should be present.
    assert after_phantom_count >= 1, (
        f"Should have baseline cleanup predictions, got {after_phantom_count}"
    )


def test_real_run_phantom_cleanup_still_executes(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Real run (dry_run=False) with same phantom setup STILL cleans:
    phantom FULL removed, cascade deps cleaned, stale baseline cleared."""
    target_path = "/mnt/backup/testvm"
    full_name = "testvm.FULL.20250801T120000_vda_abc_real"
    phantom_path = Path("/nonexistent") / f"{full_name}.qcow2"

    target = TargetConfig(path=Path(target_path))
    vm = _make_vm(name="testvm", targets=[target])

    # Seed state: phantom FULL + incremental deps + stale baseline.
    fake_full = FullBackupInfo(
        name=f"{full_name}.qcow2",
        path=phantom_path,
        timestamp=datetime(2025, 8, 1, 12, 0, 0),
        disk="vda",
    )
    mock_state._full_backups[target_path] = [fake_full]
    mock_state.record_incremental_dependency(target_path, "testvm.inc1_vda_r1", full_name)
    mock_state.record_incremental_dependency(target_path, "testvm.inc2_vda_r2", full_name)
    mock_state.set_last_backup_allocation(target_path, "vda", 77777)

    core = _build_core(
        vm=vm,
        mock_factory=mock_factory,
        mock_state=mock_state,
        mock_shell=mock_shell,
        dry_run=False,
    )
    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    assert result.dry_run is False

    # ── Assert: state IS mutated — phantom FULL gone ─────────────────
    remaining = mock_state.get_full_backups(target_path)
    assert all(f.name != f"{full_name}.qcow2" for f in remaining), (
        f"Phantom FULL should be removed from state (real run), got {remaining}"
    )
    assert len(remaining) == 1, (
        "The run creates one new FULL (no checkpoint remains after phantom cleanup), "
        f"got {len(remaining)}"
    )

    deps = mock_state.get_incremental_dependencies(target_path, full_name)
    assert len(deps) == 0, f"Incremental deps should be removed (real run), got {len(deps)}"

    baseline = mock_state.get_last_backup_allocation(target_path, "vda")
    assert baseline is None, f"Stale baseline should be cleared (real run), got {baseline}"

    # ── Assert: real-run log message ─────────────────────────────────
    log_text = caplog.text
    assert "phantom full" in log_text.lower(), (
        f"Log should contain phantom FULL removal, got: ...{log_text[-500:]}"
    )
    assert "removed (cascade:" in log_text, (
        f"Log should contain 'removed (cascade:', got: ...{log_text[-500:]}"
    )


# ── test_dry_run_predicts_gate_entry ──────────────────────────────────────


def test_dry_run_predicts_gate_entry(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
) -> None:
    """Dry-run predicts the backup that the strict free-space gate would gate.

    With the D10 rewrite the dry-run per-disk branch predicts the backup
    from ``assess_baseline`` and never reaches the free-space gate block,
    so no ``free_space_gate`` prediction is emitted (the block is dead
    code in ``_backup_target``).  Dry-run still never flags
    ``space_limited`` and never mutates state.
    """
    # The dry-run now executes the read-only blockjob probe before
    # predicting (dry-run parity, D10).
    _expect_idle_blockjob(mock_shell)
    # The FULL estimate (5000 bytes) comes from the baseline assessment.
    _configure_assessment(
        mock_factory,
        BaselineAssessment(status="no_checkpoint", size_estimate=5000),
    )

    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    result = core.run()

    assert result.dry_run is True

    # The backup prediction is emitted with the assessment's estimate.
    full_preds = [p for p in result.predictions if p.action == "backup_full"]
    assert len(full_preds) >= 1, "Dry-run should predict the FULL backup"
    assert full_preds[0].size == 5000, (
        f"Backup prediction must carry the estimate, got {full_preds[0].size}"
    )

    # The free_space_gate prediction IS emitted in dry-run (spec:
    # dry-run = real run minus mutations; the gate is a read-only check).
    gate_preds = [p for p in result.predictions if p.action == "free_space_gate"]
    assert len(gate_preds) == 1, (
        f"Expected exactly one free_space_gate prediction in dry-run, got {len(gate_preds)}"
    )
    assert gate_preds[0].size == 5000, (
        f"free_space_gate prediction must carry the estimate, got {gate_preds[0].size}"
    )

    # Dry-run never flags space_limited and never mutates state.
    assert result.space_limited is False
    assert mock_state.get_full_backups(str(vm.targets[0].path)) == []


# ── test_dry_run_space_limited_false ──────────────────────────────────────


def test_dry_run_space_limited_false(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
) -> None:
    """Dry-run is never flagged space_limited — even when the gate would block.

    core-orchestrator scenario "dry-run never flagged": predictions are
    recorded but the run reports ``space_limited=False`` (design D12).
    """
    from qsnap.utils.space import SpaceCheckResult

    vm = _make_vm(name="testvm")
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    # Plain dry-run.
    result = core.run()
    assert result.space_limited is False

    # Dry-run with the strict gate blocked — still not flagged.
    insufficient = SpaceCheckResult(sufficient=False, free_bytes=100, estimate=5000, required=10000)
    with (
        patch("qsnap.core.estimate_full_size", return_value=5000),
        patch("qsnap.core.check_free_space", return_value=insufficient),
    ):
        result2 = core.run()

    assert result2.space_limited is False
    # The dry-run backup prediction is still emitted even though a real
    # run would be suspended by the strict gate.
    backup_preds = [p for p in result2.predictions if p.action == "backup_full"]
    assert len(backup_preds) >= 1, "gate-blocked dry-run should still predict the backup"


# ── Test: delta prediction uses incremental size estimate ──────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_delta_prediction_uses_incremental_size_estimate(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Delta prediction uses the incremental (active-layer) size estimate
    delivered by ``assess_baseline`` — NOT the backing-chain sum meant
    for FULL predictions.

    When a FULL already exists and chain length is not exceeded, the
    dry-run predicts a delta.  The size estimate MUST be the incremental
    estimate (~5 MiB) — NOT the backing-chain sum (~100 MiB) that
    ``estimate_full_size`` would return.
    """
    import hashlib

    target_dir = tmp_path / "backup"
    target_dir.mkdir()
    vm = _make_vm(name="testvm", targets=[TargetConfig(path=target_dir)])
    disk_target = "vda"

    # ── State: one FULL vda, no deps → needs_full=False ─────────────
    target_path = str(vm.targets[0].path)

    full_name = f"{vm.name}.FULL.20250101T000000_vda_a1b2c3"
    # Create the FULL file on disk so phantom detection does not
    # remove it.  The state record is normalized to the extended
    # ``.qcow2`` name (fix-full-backup-state-extension), so the file
    # must be touched at the EXTENDED path — the derived
    # ``FullBackupInfo.path`` resolves to ``{stem}.qcow2``.
    full_path = target_dir / f"{full_name}.qcow2"
    full_path.touch()

    # Record it in state.
    mock_state.record_full_backup(
        target_path,
        full_name,
        datetime(2025, 1, 1, 0, 0, 0),
        disk_target,
    )

    tgt_hash = hashlib.md5(target_path.encode()).hexdigest()[:8]  # noqa: S324

    # Return a checkpoint so the delta prediction log shows its name.
    ck_name = f"qsnap-{tgt_hash}-vda-20250101T000000-aa11bb"

    # ── Dry-run parity (D10): the blockjob probe is executed read-only
    # ── and the delta prediction is driven by the healthy-checkpoint
    # ── baseline assessment (5 MiB incremental estimate).
    _expect_idle_blockjob(mock_shell)
    _configure_assessment(
        mock_factory,
        BaselineAssessment(
            status="healthy",
            newest_checkpoint=ck_name,
            size_estimate=5_242_880,
        ),
    )

    caplog.set_level(logging.INFO)

    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )
    result = core.run()

    # ── Assertions ──────────────────────────────────────────────
    delta_predictions = [p for p in result.predictions if p.action == "backup_transfer"]
    assert len(delta_predictions) >= 1, (
        f"Expected at least one backup_transfer prediction (delta), got {len(delta_predictions)}"
    )

    for p in delta_predictions:
        assert p.disk == disk_target, f"disk must be {disk_target}, got {p.disk}"
        assert p.size == 5_242_880, (
            f"Delta prediction size should be 5 MiB (incremental), "
            f"got {p.size} ({p.size / 2**20:.1f} MiB) — chain-sum leak?"
        )

    # The delta prediction log should mention the checkpoint.
    delta_logs = [
        r.getMessage() for r in caplog.records if "Would create delta backup" in r.getMessage()
    ]
    assert len(delta_logs) >= 1, "Delta prediction must be logged"
    assert "since checkpoint" in delta_logs[0], (
        f"Delta log should name the checkpoint, got: {delta_logs[0]}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Hysteresis retention dry-run prediction (hysteresis-snapshot-retention)
# ═══════════════════════════════════════════════════════════════════════════


def _seed_hysteresis_snapshots(
    mock_state: InMemoryStateManager,
    tmp_path: Path,
    *,
    count: int,
    prefix: str = "snap",
    disk: str = "vda",
) -> None:
    """Record *count* snapshot files + state records (oldest-first names)."""
    snap_dir = tmp_path / "snapshots" / "testvm"
    snap_dir.mkdir(parents=True, exist_ok=True)
    base = datetime(2025, 8, 1, 12, 0, 0)
    for i in range(1, count + 1):
        name = f"{prefix}{i}"
        path = snap_dir / f"{name}.qcow2"
        path.touch()  # stale guard must see a real file
        mock_state.record_snapshot(
            "testvm",
            SnapshotInfo(
                name=name,
                path=path,
                timestamp=base + timedelta(hours=i),
                allocation=65536 * i,
                disk=disk,
            ),
        )


def _hysteresis_vm(
    *,
    threshold: int,
    floor: int,
    vm_kwargs: dict[str, object] | None = None,
) -> VMConfig:
    """A hysteresis-mode VM with onchange gate closed to keep N exact.

    ``snapshot_create="onchange"`` with the mock change detector reporting
    no change means dry-run never simulates a snapshot, so the retention
    evaluation sees exactly the pre-recorded snapshot count.
    """
    kwargs: dict[str, object] = {
        "snapshot_retention_mode": "hysteresis",
        "snapshot_chain_length": threshold,
        "snapshot_preserve_min": floor,
        "snapshot_create": "onchange",
    }
    if vm_kwargs:
        kwargs.update(vm_kwargs)
    return _make_vm(name="testvm", **kwargs)


@pytest.mark.unit
@pytest.mark.mock
def test_hysteresis_grow_phase_predicts_no_commits(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Grow phase (N <= threshold H) predicts NO commits.

    The chain is below the trigger threshold, so retention keeps
    everything: no ``blockcommit`` and no ``snapshot_delete`` predictions.
    The grow-phase INFO line names the disk, N, and the threshold, and the
    dry-run leaves the state byte-identical (no collapse-phase key exists).
    """
    from copy import deepcopy

    # H=8, L=2, N=5 → grow phase.
    vm = _hysteresis_vm(threshold=8, floor=2)
    _seed_hysteresis_snapshots(mock_state, tmp_path, count=5)
    mock_factory.change_detector.changed = False  # onchange gate closed

    state_before = deepcopy(mock_state._state)
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    # Grow phase: nothing to commit, no deletions predicted.
    bc_preds = [p for p in result.predictions if p.action == "blockcommit"]
    sd_preds = [p for p in result.predictions if p.action == "snapshot_delete"]
    assert bc_preds == [], f"Grow phase must predict no blockcommit, got {bc_preds}"
    assert sd_preds == [], f"Grow phase must predict no snapshot_delete, got {sd_preds}"

    # The grow-phase INFO log format:
    #   [dry-run] {vm}/{disk}: grow phase (N={N} <= threshold {H}) — no commits
    assert "[dry-run] testvm/vda: grow phase (N=5 <= threshold 8) — no commits" in caplog.text, (
        f"Expected the grow-phase dry-run log line, got: {caplog.text}"
    )

    # Zero-mutation: the state is byte-identical (no collapse-phase key exists).
    assert deepcopy(mock_state._state) == state_before, (
        "Dry-run must leave the state byte-identical in the grow phase"
    )


@pytest.mark.unit
@pytest.mark.mock
def test_hysteresis_collapse_prediction_names_full_uncapped_set(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Collapse above the threshold predicts the FULL uncapped N-L oldest set.

    N=73, H=72, L=24: one per-disk ``blockcommit`` prediction naming ALL 49
    oldest snapshots; the newest 24 never appear in any prediction; no
    lifecycle-manager call is made; the state stays byte-identical.
    """
    from copy import deepcopy

    vm = _hysteresis_vm(threshold=72, floor=24)
    _seed_hysteresis_snapshots(mock_state, tmp_path, count=73)
    mock_factory.change_detector.changed = False

    # Hysteresis collapse invokes the pure engine with floor L: mark every
    # snapshot removable so the postprocess + floor trim selects the oldest
    # N-L = 49.
    all_names = [f"snap{i}" for i in range(1, 74)]
    mock_factory._retention_engine = MockRetentionEngine(keep=[], remove=all_names)

    state_before = deepcopy(mock_state._state)
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    with (
        caplog.at_level(logging.INFO, logger="qsnap.core"),
        patch.object(
            mock_factory._lifecycle_manager,
            "blockcommit",
            wraps=mock_factory._lifecycle_manager.blockcommit,
        ) as manager_spy,
    ):
        result = core.run()

    # Exactly one blockcommit prediction naming ALL 49 oldest snapshots.
    bc_preds = [p for p in result.predictions if p.action == "blockcommit"]
    assert len(bc_preds) == 1, (
        f"Expected exactly one blockcommit prediction, got {len(bc_preds)}: "
        f"{[(p.action, p.name) for p in result.predictions]}"
    )
    assert bc_preds[0].disk == "vda"
    assert bc_preds[0].name == ", ".join(f"snap{i}" for i in range(1, 50)), (
        f"Blockcommit prediction must name the FULL uncapped N-L=49 set, got: {bc_preds[0].name!r}"
    )

    # The prediction wording pins the single bulk blockcommit.
    assert "would collapse 49 snapshot(s) in one blockcommit" in caplog.text, (
        f"Expected the single-bulk-blockcommit prediction wording, got: {caplog.text}"
    )

    # One snapshot_delete prediction per committed snapshot — the oldest 49.
    sd_preds = [p for p in result.predictions if p.action == "snapshot_delete"]
    assert {p.name for p in sd_preds} == {f"snap{i}" for i in range(1, 50)}, (
        f"Expected snapshot_delete for the oldest 49 snapshots, got: {[p.name for p in sd_preds]}"
    )

    # The newest L snapshots are never named by any prediction.
    all_pred_names = {p.name for p in result.predictions}
    for newest in (f"snap{i}" for i in range(50, 74)):
        assert newest not in all_pred_names, (
            f"Floor snapshot {newest} must never appear in a prediction, "
            f"got {len(all_pred_names)} predicted names"
        )

    # No lifecycle-manager call, and the state is byte-identical.
    manager_spy.assert_not_called()
    assert deepcopy(mock_state._state) == state_before, (
        "Dry-run must leave the state byte-identical during collapse prediction"
    )


@pytest.mark.unit
@pytest.mark.mock
def test_hysteresis_prediction_silent_between_floor_and_threshold(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Between the floor (L=24) and the trigger threshold (H=72): no prediction.

    N=60 stays silent — no ``blockcommit`` prediction, no ``snapshot_delete``,
    and the state is byte-identical (grow phase despite being above the floor).
    """
    from copy import deepcopy

    vm = _hysteresis_vm(threshold=72, floor=24)
    _seed_hysteresis_snapshots(mock_state, tmp_path, count=60)
    mock_factory.change_detector.changed = False

    # A steady-mode engine would remove 36 here (60 - 24); hysteresis must
    # keep everything silent mid-band.
    all_names = [f"snap{i}" for i in range(1, 61)]
    mock_factory._retention_engine = MockRetentionEngine(keep=[], remove=all_names)

    state_before = deepcopy(mock_state._state)
    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    # No blockcommit / snapshot_delete predictions mid-band.
    bc_preds = [p for p in result.predictions if p.action == "blockcommit"]
    sd_preds = [p for p in result.predictions if p.action == "snapshot_delete"]
    assert bc_preds == [], f"Mid-band must predict no blockcommit, got {bc_preds}"
    assert sd_preds == [], f"Mid-band must predict no snapshot_delete, got {sd_preds}"

    # The grow-phase INFO line names N=60 and the threshold H=72.
    assert "[dry-run] testvm/vda: grow phase (N=60 <= threshold 72) — no commits" in caplog.text, (
        f"Expected the mid-band grow-phase log line, got: {caplog.text}"
    )

    # Zero-mutation: state byte-identical.
    assert deepcopy(mock_state._state) == state_before, (
        "Dry-run must leave the state byte-identical mid-band"
    )
