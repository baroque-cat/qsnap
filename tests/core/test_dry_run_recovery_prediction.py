"""Unit tests for dry-run recovery prediction (recover-lost-checkpoint-bitmaps).

Covers the dry-run parity of the recovery path:

- ``test_dry_run_assessment_zero_mutation`` — dry-run invokes the
  provider's read-only ``assess_baseline`` per disk but mutates nothing
  (state, filesystem, actions all untouched).
- ``test_dry_run_healthy_checkpoint_predicts_single_delta`` — a HEALTHY
  newest checkpoint predicts exactly one delta per disk (``backup_transfer``).
- ``test_dry_run_dead_checkpoint_gates_pass_predicts_recovered_delta`` —
  a DEAD checkpoint with all recovery gates passing predicts a
  recovered-delta transfer (``backup_transfer``, gate-OK wording).
- ``test_dry_run_dead_checkpoint_gate_fail_predicts_full_with_reason`` —
  a DEAD checkpoint with a failed recovery gate predicts a FULL and
  names the failed gate.
- ``test_dry_run_free_space_gate_uses_recovered_delta_estimate`` — the
  free-space gate prediction for a dead/gates-pass disk uses the
  recovered-delta size estimate, not the FULL estimate.

Uses MockShell, MockVMModuleFactory, InMemoryStateManager, MockConfigFacade
— zero real I/O (spec: dry-run-prediction, group dry-run-parity).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import BaselineAssessment
from tests.mocks import (
    InMemoryStateManager,
    MockConfigFacade,
    MockShell,
    MockVMModuleFactory,
)
from tests.mocks.mock_modules import MockBitmapBackupProvider

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_vm(
    name: str = "testvm",
    disks: list[DiskConfig] | None = None,
    targets: list[TargetConfig] | None = None,
    **kwargs: object,
) -> VMConfig:
    """Create a VMConfig with sensible defaults for dry-run recovery tests."""
    if disks is None:
        disks = [DiskConfig(target="vda", base_image=Path(f"/var/lib/libvirt/images/{name}.qcow2"))]
    if targets is None:
        targets = [TargetConfig(path=Path(f"/var/lib/libvirt/snapshots/{name}/backup"))]
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


def _seed_full_backup(
    mock_state: InMemoryStateManager,
    vm: VMConfig,
    disk: str = "vda",
    timestamp: datetime | None = None,
) -> str:
    """Record a FULL backup for *vm* with its file created on disk.

    The phantom-FULL filter in ``Core._backup_target()`` checks
    ``os.path.exists``, so the file must exist or the record is filtered
    out (which would flip the FULL/delta decision).
    """
    target_path = str(vm.targets[0].path)
    full_name = f"{vm.name}.FULL.20250101T000000_{disk}_a1b2c3"
    full_path = vm.targets[0].path / f"{full_name}.qcow2"
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.touch()
    mock_state.record_full_backup(
        target_path,
        full_name,
        timestamp or datetime(2025, 1, 1, 0, 0, 0),
        disk,
    )
    return full_name


def _install_assessment(
    mock_factory: MockVMModuleFactory,
    assessment: BaselineAssessment,
) -> MockBitmapBackupProvider:
    """Replace the factory's backup provider with one returning *assessment*."""
    provider = MockBitmapBackupProvider(assessment=assessment, backup_kind="delta")
    mock_factory._bitmap_backup_provider = provider
    return provider


def _checkpoint_name(vm_name: str = "testvm", disk: str = "vda") -> str:
    """A realistic qsnap checkpoint name for this VM+target+disk."""
    return f"qsnap-cafebabe-{disk}-20250101T000000-aa11bb"


# ── Test 1: assess_baseline is read-only; dry-run mutates nothing ──────────


@pytest.mark.unit
@pytest.mark.mock
def test_dry_run_assessment_zero_mutation(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    tmp_path: Path,
) -> None:
    """Dry-run calls ``assess_baseline`` but mutates nothing.

    Spec (dry-run-prediction): "State and filesystem unchanged after
    dry-run" — the provider's read-only baseline assessment drives the
    prediction channel; no state write, no action, no file appears.
    """
    target_dir = tmp_path / "backup"
    vm = _make_vm(name="testvm", targets=[TargetConfig(path=target_dir)])
    full_name = _seed_full_backup(mock_state, vm)
    mock_state.record_incremental_dependency(str(target_dir), "testvm.inc1_vda_x1", full_name)
    mock_state.set_last_backup_allocation(str(target_dir), "vda", 4096)

    provider = _install_assessment(
        mock_factory,
        BaselineAssessment(
            status="healthy",
            newest_checkpoint=_checkpoint_name(),
            size_estimate=5_242_880,
        ),
    )

    # Capture the full state surface before the run.
    snapshots_before = mock_state.get_snapshots("testvm")
    fulls_before = mock_state.get_full_backups(str(target_dir))
    deps_before = mock_state.get_incremental_dependencies(str(target_dir), full_name)
    alloc_before = mock_state.get_last_backup_allocation(str(target_dir), "vda")
    deferred_before = mock_state.get_deferred_operations("testvm")

    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    # Spy on assess_baseline to prove the dry-run executes the read-only probe.
    with patch.object(provider, "assess_baseline", wraps=provider.assess_baseline) as spy:
        result = core.run()

    # ── Assert: the read-only assessment was executed ────────────────
    assert spy.called, "dry-run must call provider.assess_baseline()"

    # ── Assert: predictions exist and are driven by the assessment ───
    assert result.dry_run is True
    assert result.actions == [], f"No actions may execute in dry-run, got {result.actions}"
    assert len(result.predictions) > 0, "Dry-run must produce predictions"
    assert any(p.action == "backup_transfer" for p in result.predictions), (
        "Healthy assessment should predict a backup_transfer"
    )

    # ── Assert: state is byte-identical ──────────────────────────────
    assert mock_state.get_snapshots("testvm") == snapshots_before
    assert mock_state.get_full_backups(str(target_dir)) == fulls_before
    assert mock_state.get_incremental_dependencies(str(target_dir), full_name) == deps_before
    assert mock_state.get_last_backup_allocation(str(target_dir), "vda") == alloc_before
    assert mock_state.get_deferred_operations("testvm") == deferred_before


# ── Test 2: healthy checkpoint predicts a single delta ─────────────────────


@pytest.mark.unit
@pytest.mark.mock
def test_dry_run_healthy_checkpoint_predicts_single_delta(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A HEALTHY newest checkpoint predicts exactly one delta per disk.

    Spec scenario: "Gate open with healthy checkpoint predicts one delta
    per disk" — the prediction is ``backup_transfer`` with the target and
    an approximate size, and the log names the checkpoint.
    """
    target_dir = tmp_path / "backup"
    vm = _make_vm(name="testvm", targets=[TargetConfig(path=target_dir)])
    _seed_full_backup(mock_state, vm)  # existing FULL → delta is due, not FULL

    estimate = 5_242_880  # 5 MiB incremental estimate
    _install_assessment(
        mock_factory,
        BaselineAssessment(
            status="healthy",
            newest_checkpoint=_checkpoint_name(),
            size_estimate=estimate,
        ),
    )

    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    # Exactly one backup_transfer prediction, no backup_full.
    transfers = [p for p in result.predictions if p.action == "backup_transfer"]
    fulls = [p for p in result.predictions if p.action == "backup_full"]
    assert len(transfers) == 1, (
        f"Expected exactly one backup_transfer prediction, got {len(transfers)}: "
        f"{[(p.action, p.name) for p in result.predictions]}"
    )
    assert fulls == [], f"No FULL may be predicted for a healthy checkpoint, got {fulls}"

    pred = transfers[0]
    assert pred.disk == "vda"
    assert pred.size == estimate, (
        f"Delta prediction must carry the incremental estimate, got {pred.size}"
    )
    assert ".FULL." not in pred.name, (
        f"Delta prediction name must not be a FULL name, got {pred.name}"
    )

    # Log names the checkpoint and the delta kind.
    log_text = caplog.text
    assert "Would create delta backup" in log_text, (
        f"Log should predict a delta backup, got: ...{log_text[-500:]}"
    )
    assert "since checkpoint" in log_text, (
        f"Log should name the newest checkpoint, got: ...{log_text[-500:]}"
    )


# ── Test 3: dead checkpoint + gates pass → recovered-delta prediction ──────


@pytest.mark.unit
@pytest.mark.mock
def test_dry_run_dead_checkpoint_gates_pass_predicts_recovered_delta(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DEAD bitmap + all recovery gates passing → recovered-delta prediction.

    Spec scenario: "Dead checkpoint with passing gates predicts recovered
    delta" — the prediction is ``backup_transfer`` (transfer-based, NOT a
    FULL), carries the allocation-superset size estimate, and the log
    reports gate OK.
    """
    target_dir = tmp_path / "backup"
    vm = _make_vm(name="testvm", targets=[TargetConfig(path=target_dir)])
    _seed_full_backup(mock_state, vm)  # existing FULL → delta is due, not FULL

    recovered_estimate = 10_485_760  # 10 MiB copy-set upper bound
    _install_assessment(
        mock_factory,
        BaselineAssessment(
            status="dead",
            newest_checkpoint=_checkpoint_name(),
            gates_passed=True,
            failed_gate_reason=None,
            size_estimate=recovered_estimate,
        ),
    )

    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    # A recovered delta is a transfer, never a FULL.
    transfers = [p for p in result.predictions if p.action == "backup_transfer"]
    fulls = [p for p in result.predictions if p.action == "backup_full"]
    assert len(transfers) == 1, (
        f"Expected exactly one backup_transfer prediction (recovered delta), "
        f"got {len(transfers)}: {[(p.action, p.name) for p in result.predictions]}"
    )
    assert fulls == [], f"No FULL may be predicted when gates pass, got {fulls}"

    pred = transfers[0]
    assert pred.disk == "vda"
    assert pred.size == recovered_estimate, (
        f"Recovered-delta prediction must carry the copy-set estimate, got {pred.size}"
    )
    assert ".FULL." not in pred.name

    # Log identifies the recovered-delta kind and the gate outcome.
    log_text = caplog.text
    assert "recovered-delta" in log_text, (
        f"Log should predict a recovered-delta backup, got: ...{log_text[-500:]}"
    )
    assert "gate OK" in log_text, f"Log should report gate OK, got: ...{log_text[-500:]}"
    assert "recovery" in log_text, f"Log should mark the recovery path, got: ...{log_text[-500:]}"


# ── Test 4: dead checkpoint + gate fail → FULL prediction with reason ──────


@pytest.mark.unit
@pytest.mark.mock
def test_dry_run_dead_checkpoint_gate_fail_predicts_full_with_reason(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DEAD bitmap + failed recovery gate → FULL prediction naming the gate.

    Spec scenario: "Dead checkpoint with failed gate predicts FULL with
    reason" — the prediction is ``backup_full`` and the log names the
    failed gate (e.g. "G1").
    """
    target_dir = tmp_path / "backup"
    vm = _make_vm(name="testvm", targets=[TargetConfig(path=target_dir)])
    _seed_full_backup(mock_state, vm)

    full_estimate = 20_971_520  # 20 MiB FULL chain-sum estimate
    _install_assessment(
        mock_factory,
        BaselineAssessment(
            status="dead",
            newest_checkpoint=_checkpoint_name(),
            gates_passed=False,
            failed_gate_reason="G1",
            size_estimate=full_estimate,
        ),
    )

    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        result = core.run()

    # Gate failure forces a FULL even though a FULL anchor exists.
    fulls = [p for p in result.predictions if p.action == "backup_full"]
    transfers = [p for p in result.predictions if p.action == "backup_transfer"]
    assert len(fulls) == 1, (
        f"Expected exactly one backup_full prediction, got {len(fulls)}: "
        f"{[(p.action, p.name) for p in result.predictions]}"
    )
    assert transfers == [], f"No transfer may be predicted when a gate fails, got {transfers}"

    pred = fulls[0]
    assert pred.disk == "vda"
    assert ".FULL." in pred.name, f"FULL prediction name must contain .FULL., got {pred.name}"
    assert pred.size == full_estimate, (
        f"FULL prediction must carry the FULL estimate, got {pred.size}"
    )

    # Log names the failed gate.
    log_text = caplog.text
    assert "recovery gate failed: G1" in log_text, (
        f"Log should name the failed gate (G1), got: ...{log_text[-500:]}"
    )
    assert "FULL (recovery)" in log_text, (
        f"Log should mark the recovery FULL, got: ...{log_text[-500:]}"
    )


# ── Test 5: free-space gate uses the recovered-delta estimate ──────────────


@pytest.mark.unit
@pytest.mark.mock
def test_dry_run_free_space_gate_uses_recovered_delta_estimate(
    mock_factory: MockVMModuleFactory,
    mock_state: InMemoryStateManager,
    mock_shell: MockShell,
    tmp_path: Path,
) -> None:
    """The free-space gate prediction uses the recovered-delta estimate.

    Spec (size-estimation): "Estimate feeds the free-space gate in
    dry-run" — for a DEAD checkpoint with passing gates the gate must be
    evaluated against the recovered-delta copy-set estimate, NOT the FULL
    chain-sum estimate, so the operator is told the true transfer size.
    """
    from qsnap.utils.space import SpaceCheckResult

    target_dir = tmp_path / "backup"
    target_dir.mkdir(exist_ok=True)
    vm = _make_vm(
        name="testvm",
        targets=[TargetConfig(path=target_dir)],
        free_space_check="strict",
    )
    _seed_full_backup(mock_state, vm)

    recovered_estimate = 8_388_608  # 8 MiB — smaller than the FULL estimate
    full_estimate = 33_554_432  # 32 MiB — the estimate a FULL would need
    _install_assessment(
        mock_factory,
        BaselineAssessment(
            status="dead",
            newest_checkpoint=_checkpoint_name(),
            gates_passed=True,
            failed_gate_reason=None,
            size_estimate=recovered_estimate,
        ),
    )

    core = _build_core(
        vm=vm, mock_factory=mock_factory, mock_state=mock_state, mock_shell=mock_shell
    )

    # Deterministic gate outcome: sufficient for the recovered-delta
    # estimate (8 MiB) but would be insufficient for the FULL estimate
    # (32 MiB) — proving the gate consumed the recovered-delta estimate.
    sufficient = SpaceCheckResult(
        sufficient=True,
        free_bytes=16_777_216,  # 16 MiB free
        estimate=recovered_estimate,
        required=recovered_estimate,
    )
    with patch("qsnap.core.check_free_space", return_value=sufficient):
        result = core.run()

    gate_preds = [p for p in result.predictions if p.action == "free_space_gate"]
    assert len(gate_preds) == 1, (
        f"Expected exactly one free_space_gate prediction, got {len(gate_preds)}: "
        f"{[(p.action, p.name) for p in result.predictions]}"
    )

    pred = gate_preds[0]
    assert pred.size == recovered_estimate, (
        "The free-space gate MUST be evaluated against the recovered-delta "
        f"estimate ({recovered_estimate}), got {pred.size} "
        f"(FULL chain-sum leak: {full_estimate}?)"
    )
    assert pred.size != full_estimate, "Gate must not use the FULL chain-sum estimate"
    assert pred.error is None, f"Sufficient gate must carry no error, got {pred.error}"
