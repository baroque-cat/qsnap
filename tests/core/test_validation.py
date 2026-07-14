"""Tests for Core._validate_environment() pre-flight checks.

Covers:
- All validation checks pass → CheckResult(status="ok").
- Individual check failures: snapshot_dir missing, virsh not in PATH,
  VM not defined in libvirt.
- Pipeline-level behaviour: validation passes → pipeline continues;
  validation fails → VMRunResult(success=False).
- ondemand mode with no reachable target → snapshot skipped (validation
  still passes).
- always mode with validation failure → VMRunResult(success=False).
"""

from __future__ import annotations

from unittest.mock import patch

from qsnap.core import Core, VMRunResult
from qsnap.models.results import CheckResult, ShellResult
from tests.mocks import MockConfigFacade, MockShell

_OK = ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
_FAIL = ShellResult(
    success=False, stdout="", stderr="", returncode=1, error="not found"
)


def _override(shell: MockShell, pattern: str, result: ShellResult) -> None:
    """Replace an existing MockShell expectation for *pattern* with *result*.

    MockShell matches expectations in insertion order (first match wins).
    To override a default success expectation, we remove the old entry
    and append a new one so it becomes the sole match for that pattern.
    """
    shell._expectations = [
        e for e in shell._expectations if e.pattern != pattern
    ]
    shell.expect(pattern).returns(result)


# ── test_validate_environment_all_pass ────────────────────────────────────


def test_validate_environment_all_pass(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """All shell checks succeed → CheckResult(status="ok")."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core._validate_environment(vm)

    assert isinstance(result, CheckResult)
    assert result.status == "ok"
    assert result.broken_snapshots == []
    assert result.vm_name == "testvm"


# ── test_validate_environment_snapshot_dir_missing ─────────────────────────


def test_validate_environment_snapshot_dir_missing(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``test -d`` fails → CheckResult(status="validation_failed")."""
    _override(mock_shell, "test -d", _FAIL)
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core._validate_environment(vm)

    assert result.status == "validation_failed"
    assert any("snapshot_dir not found" in b for b in result.broken_snapshots)


# ── test_validate_environment_virsh_not_in_path ───────────────────────────


def test_validate_environment_virsh_not_in_path(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``which virsh`` fails → validation_failed with 'virsh not in PATH'."""
    _override(mock_shell, "which virsh", _FAIL)
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core._validate_environment(vm)

    assert result.status == "validation_failed"
    assert any("virsh not in PATH" in b for b in result.broken_snapshots)


# ── test_validate_environment_vm_not_defined ──────────────────────────────


def test_validate_environment_vm_not_defined(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``virsh dominfo`` fails → validation_failed with 'VM not defined'."""
    _override(mock_shell, "virsh dominfo", _FAIL)
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core._validate_environment(vm)

    assert result.status == "validation_failed"
    assert any("VM not defined" in b for b in result.broken_snapshots)


# ── test_validate_environment_ondemand_target_missing_skipped ──────────────


def test_validate_environment_ondemand_target_missing_skipped(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """ondemand mode, no reachable target → validation passes, snapshot skipped.

    The pipeline should still pass environment validation, but because no
    target directory exists, the snapshot is skipped (ondemand semantics).
    """
    vm = make_vm_config(
        name="testvm",
        snapshot_create="ondemand",
        targets=[],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider

    with (
        patch.object(
            core,
            "_validate_environment",
            wraps=core._validate_environment,
        ) as validate_spy,
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
    ):
        result = core.run()

    # Validation was called (pipeline did not short-circuit).
    assert validate_spy.called

    # Snapshot was NOT created (ondemand with no reachable target).
    assert not create_spy.called

    # Pipeline succeeded — validation passed (no "validation failed" error).
    assert result.success is True
    assert result.results[0].error is None


# ── test_validate_environment_always_mode_target_missing_error ─────────────


def test_validate_environment_always_mode_target_missing_error(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """always mode, validation fails → VMRunResult(success=False)."""
    _override(mock_shell, "test -d", _FAIL)
    vm = make_vm_config(name="testvm", snapshot_create="always")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.run()

    assert len(result.results) == 1
    vm_result = result.results[0]
    assert isinstance(vm_result, VMRunResult)
    assert vm_result.success is False
    assert vm_result.error is not None
    assert "snapshot_dir" in vm_result.error


# ── test_pipeline_continues_after_validation_pass ──────────────────────────


def test_pipeline_continues_after_validation_pass(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Validation passes → pipeline continues normally (snapshot created)."""
    vm = make_vm_config(name="testvm", snapshot_create="always", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider

    with (
        patch.object(
            core,
            "_validate_environment",
            wraps=core._validate_environment,
        ) as validate_spy,
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
    ):
        result = core.run()

    # Validation was called and passed (pipeline continued to snapshot).
    assert validate_spy.called

    # Pipeline continued — snapshot was created.
    assert create_spy.called

    # Pipeline succeeded.
    assert result.success is True


# ── test_pipeline_returns_failure_on_missing_snapshot_dir ──────────────────


def test_pipeline_returns_failure_on_missing_snapshot_dir(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Validation fails (snapshot_dir missing) → VMRunResult(success=False,
    error contains 'validation failed')."""
    _override(mock_shell, "test -d", _FAIL)
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider

    with (
        patch.object(
            core,
            "_validate_environment",
            wraps=core._validate_environment,
        ) as validate_spy,
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
    ):
        result = core.run()

    # Validation was called and failed (pipeline stopped).
    assert validate_spy.called

    # Snapshot was NOT created (validation stopped the pipeline).
    assert not create_spy.called

    # VMRunResult reflects failure.
    assert len(result.results) == 1
    assert result.results[0].success is False
    assert result.results[0].error is not None
    assert "snapshot_dir not found" in result.results[0].error
