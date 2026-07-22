"""Integration tests for environment validation (libnbd availability check).

Covers the ``_validate_environment`` unconditional libnbd hard check:
- Libnbd installed → validation passes.
- Libnbd missing → hard failure (RuntimeError naming python3-libnbd).

All tests are marked ``@pytest.mark.integration``.  They use the
``test_vm`` fixture from ``conftest.py`` where available.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

import pytest

from qsnap.core import Core
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_vm_running
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_factory import MockVMModuleFactory
from tests.mocks.mock_state import InMemoryStateManager

# ── Test 1: Validation passes with libnbd installed ────────────────────


@pytest.mark.integration
def test_validate_environment_passes_with_libnbd(test_vm) -> None:
    """Verify Core._validate_environment passes the unconditional libnbd
    check when the ``nbd`` package is importable.

    Skip-guarded: the test requires the real ``nbd`` package, but
    since it's an integration test that also requires libvirt, we
    guard both at function level.
    """
    # Skip if nbd not importable.
    try:
        import nbd  # noqa: F401
    except ImportError:
        pytest.skip("python3-libnbd not installed in this interpreter")

    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start VM — needed for _validate_environment's dominfo check.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Create a VMConfig with a target (no incremental_mode kwarg — removed).
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[
            TargetConfig(
                path=target_dir,
                incremental=True,
                verify="metadata",
            ),
        ],
    )

    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "qsnap-test.toml")
    factory = MockVMModuleFactory()
    state = InMemoryStateManager()

    core = Core(
        config=config,
        factory=factory,
        state=state,
        shell=shell,
    )

    # Run validation — should pass because nbd is importable.
    result = core._validate_environment(vm_config)
    assert result.status == "ok", (
        f"Validation should pass with libnbd installed, got status={result.status!r}"
        f" broken={result.broken_snapshots!r}"
    )


# ── Test 2: Libnbd missing → hard failure (NEW — test-plan R9) ────────


@pytest.mark.integration
def test_libnbd_missing_hard_failure(test_vm) -> None:
    """Verify that when libnbd is not available, _validate_environment
    returns a validation_failed status naming python3-libnbd, and the
    pipeline (via _execute_pipeline) raises RuntimeError in normal mode.

    This test patches ``is_libnbd_available`` to return False within
    the ``qsnap.core`` module scope, simulates the missing package
    scenario regardless of whether the real nbd package is installed.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Start VM — needed for dominfo check.
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)

    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    # Minimal config — no incremental_mode field (removed from TargetConfig).
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[
            TargetConfig(
                path=target_dir,
                incremental=True,
            ),
        ],
    )

    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "qsnap-libnbd-fail.toml")
    factory = MockVMModuleFactory()
    state = InMemoryStateManager()

    core = Core(
        config=config,
        factory=factory,
        state=state,
        shell=shell,
    )

    # Patch is_libnbd_available within the qsnap.core module to simulate
    # the missing package.  The env validation is unconditional now, so
    # the check should fail even for minimal configs.
    with mock.patch("qsnap.core.is_libnbd_available", return_value=False):
        result = core._validate_environment(vm_config)

    # Validation should report failure naming python3-libnbd.
    assert result.status == "validation_failed", (
        f"Expected validation_failed when libnbd missing, got {result.status!r}"
    )
    libnbd_errors = [b for b in result.broken_snapshots if "python3-libnbd" in b]
    assert len(libnbd_errors) >= 1, (
        f"Expected a broken entry naming python3-libnbd, got: {result.broken_snapshots}"
    )

    # In normal (non-dry-run) mode, _execute_pipeline should raise RuntimeError.
    with (
        mock.patch("qsnap.core.is_libnbd_available", return_value=False),
        pytest.raises(RuntimeError, match="python3-libnbd"),
    ):
        core._execute_pipeline(vm_config)
