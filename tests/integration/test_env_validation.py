"""Integration tests for environment validation (libnbd availability check).

Covers the ``_validate_environment`` libnbd check:
- Bitmap mode with libnbd installed → validation passes.
- No bitmap targets → check skipped (runs even when nbd absent).

All tests are marked ``@pytest.mark.integration``.  They use the
``test_vm`` fixture from ``conftest.py`` where available, and fall
back to mock-based setup for the no-bitmap-targets test.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.models.config import TargetConfig, VMConfig
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_vm_running
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_factory import MockVMModuleFactory
from tests.mocks.mock_state import InMemoryStateManager

# ── Test 1: Bitmap mode with libnbd installed passes validation ────────


@pytest.mark.integration
def test_bitmap_mode_with_libnbd_installed_passes(test_vm) -> None:
    """Verify Core._validate_environment with a bitmap target passes
    the libnbd check when the ``nbd`` package is importable.

    Skip-guarded: the test requires the real ``nbd`` package, but
    since it's an integration test that also requires libvirt, we
    guard both at function level.
    """
    # Skip if nbd not importable (separate from the module-level skip
    # in the other files — this file is designed to have tests that
    # run WITHOUT nbd as well, so no module-level importorskip).
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

    # libvirt version check is NOT needed for env validation —
    # _validate_environment only calls dominfo, not backup-begin.

    # Create a VMConfig with a bitmap target.
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[
            TargetConfig(
                path=target_dir,
                incremental=True,
                incremental_mode="bitmap",
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


# ── Test 2: No bitmap targets — libnbd check skipped ───────────────────


@pytest.mark.integration
def test_no_bitmap_targets_skips_libnbd_check(test_vm) -> None:
    """Verify that when no target uses ``incremental_mode='bitmap'``,
    ``Core._validate_environment`` passes even when the ``nbd``
    package is absent — the libnbd check is not consulted.

    This test is designed to RUN in the venv where ``nbd`` is not
    importable, validating that file-copy-only configs work without
    the python3-libnbd package.
    """
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

    # Create a VMConfig with ONLY file-copy targets (no bitmap).
    vm_config = VMConfig(
        name=vm_name,
        base_image=base_image,
        snapshot_dir=snapshot_dir,
        targets=[
            TargetConfig(
                path=target_dir,
                incremental=True,
                incremental_mode="file-copy",
                verify="metadata",
            ),
        ],
    )

    config = MockConfigFacade(vms=[vm_config], config_path=tmpdir / "qsnap-fc-test.toml")
    factory = MockVMModuleFactory()
    state = InMemoryStateManager()

    core = Core(
        config=config,
        factory=factory,
        state=state,
        shell=shell,
    )

    # Run validation — should pass regardless of nbd availability.
    result = core._validate_environment(vm_config)
    assert result.status == "ok", (
        f"Validation should pass for file-copy-only config (libnbd check skipped), "
        f"got status={result.status!r} broken={result.broken_snapshots!r}"
    )

    # Also assert: no "python3-libnbd" mention in any broken entry.
    # The libnbd check should not have been consulted at all.
    for broken_item in result.broken_snapshots:
        assert "libnbd" not in broken_item.lower(), (
            f"Libnbd-related failure found in file-copy-only config: {broken_item!r}"
        )
