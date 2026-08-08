"""Tests verifying that mocks correctly implement their ABC interfaces."""

from __future__ import annotations

import inspect
from pathlib import Path

from qsnap.interfaces.backup import IBackupProvider
from qsnap.models.config import DiskConfig, VMConfig
from qsnap.models.results import (
    BaselineAssessment,
    SnapshotResult,
    SnapshotSpec,
)
from tests.mocks.mock_modules import MockBitmapBackupProvider, MockSnapshotProvider


def test_mock_shell_implements_full_interface():
    """MockShell implements the full IShell ABC, including both ``run``
    and ``run_with_stall_detection``."""
    from qsnap.interfaces.shell import IShell
    from tests.mocks.mock_shell import MockShell

    mock = MockShell()
    assert isinstance(mock, IShell), "MockShell must be an IShell instance"

    # Both abstract methods must exist
    assert hasattr(mock, "run"), "MockShell must define run()"
    assert hasattr(mock, "run_with_stall_detection"), (
        "MockShell must define run_with_stall_detection()"
    )

    # run_with_stall_detection must be callable and accept the full
    # IShell ABC signature.
    assert callable(mock.run_with_stall_detection), "run_with_stall_detection must be callable"


def _make_vm_config() -> VMConfig:
    """A minimal two-disk VMConfig for mock validity checks."""
    return VMConfig(
        name="testvm",
        disks=[
            DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2")),
            DiskConfig(target="vdb", base_image=Path("/var/lib/libvirt/images/testvm-vdb.qcow2")),
        ],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )


def test_mock_create_multi_validity():
    """MockSnapshotProvider.create_multi never returns None and returns
    exactly one SnapshotResult per spec, in spec order (TESTING.md §2)."""
    provider = MockSnapshotProvider()
    specs = [
        SnapshotSpec(disk="vda", name="test-snap-vda", path=Path("/tmp/testvm_vda.qcow2")),
        SnapshotSpec(disk="vdb", name="test-snap-vdb", path=Path("/tmp/testvm_vdb.qcow2")),
    ]
    results = provider.create_multi(_make_vm_config(), specs, quiesce=True)

    # Never returns None.
    assert results is not None
    # One result per spec.
    assert isinstance(results, list)
    assert len(results) == len(specs)
    for result, spec in zip(results, specs, strict=True):
        assert isinstance(result, SnapshotResult)
        assert result.success is True
        assert result.disk == spec.disk
        assert result.name == spec.name
        assert result.path == spec.path


def test_mock_create_multi_validity_empty_specs():
    """MockSnapshotProvider.create_multi([]) returns [] — never None."""
    provider = MockSnapshotProvider()
    results = provider.create_multi(_make_vm_config(), [], quiesce=False)
    assert results is not None
    assert results == []


def test_mock_backup_provider_api_carries_no_snapshotinfo():
    """MockBitmapBackupProvider's public API never references SnapshotInfo.

    The backup world is target-world only (design D2 of
    orthogonalize-snapshots-and-backups): no public method signature or
    return annotation may mention ``SnapshotInfo``.  All backup data is
    modeled via ``BackupResult``/``BackupInfo``.
    """
    provider = MockBitmapBackupProvider()
    assert isinstance(provider, IBackupProvider)

    public_methods = [
        name
        for name in dir(provider)
        if not name.startswith("_") and callable(getattr(provider, name))
    ]
    assert public_methods, "expected at least one public method on the mock"

    for name in public_methods:
        signature = inspect.signature(getattr(provider, name))
        assert "SnapshotInfo" not in str(signature), (
            f"MockBitmapBackupProvider.{name}{signature} references SnapshotInfo"
        )
        assert "SnapshotInfo" not in (
            getattr(signature.return_annotation, "__name__", repr(signature.return_annotation))
        ), f"MockBitmapBackupProvider.{name} return annotation references SnapshotInfo"


# ── assess_baseline mock contract (backup-provider spec) ──────────────────
# TESTING.md §2: every mock method must return a valid result type (never
# None).  ``MockBitmapBackupProvider.assess_baseline`` must return a valid
# frozen ``BaselineAssessment`` — never ``None``.


def test_mock_backup_provider_assess_baseline_returns_valid_assessment(make_vm_config, make_target):
    """MockBitmapBackupProvider.assess_baseline returns a BaselineAssessment.

    The mock implements the read-only assessment contract (backup-provider
    spec scenario "Mock implements the assessment contract"): a valid
    frozen result object, never ``None``, with a defined status.
    """
    provider = MockBitmapBackupProvider()
    assert isinstance(provider, IBackupProvider)

    vm_config = make_vm_config()
    target = make_target()
    disk = vm_config.disks[0]

    result = provider.assess_baseline(vm_config, target, disk)

    assert result is not None, "assess_baseline must never return None"
    assert isinstance(result, BaselineAssessment)
    assert result.__dataclass_params__.frozen is True
    assert result.status in ("no_checkpoint", "healthy", "dead", "unknown")


def test_mock_backup_provider_assess_baseline_configurable_assessment(
    make_vm_config,
    make_target,
):
    """A configured assessment is returned as-is (read-only).

    Core dry-run tests inject a specific assessment (e.g. dead checkpoint
    with a failed gate); the mock must return exactly that object, never
    replacing it or returning None.
    """
    configured = BaselineAssessment(
        status="dead",
        newest_checkpoint="qsnap-ab12cd34-vda-20260808T160755-e1eb7a",
        gates_passed=False,
        failed_gate_reason="G1",
        size_estimate=1048576,
    )
    provider = MockBitmapBackupProvider(assessment=configured)

    vm_config = make_vm_config()
    target = make_target()
    disk = vm_config.disks[0]

    result = provider.assess_baseline(vm_config, target, disk)
    assert result is configured
    assert result.status == "dead"
    assert result.failed_gate_reason == "G1"
    assert result.size_estimate == 1048576


def test_mock_backup_provider_assess_baseline_signature(
    make_vm_config,
    make_target,
):
    """assess_baseline matches the IBackupProvider declaration.

    The signature takes (vm_config, target, disk) and is annotated to
    return BaselineAssessment — the interface contract is preserved by
    the mock (backup-provider spec: BREAKING interface addition).
    """
    provider = MockBitmapBackupProvider()
    sig = inspect.signature(provider.assess_baseline)
    for required in ("vm_config", "target", "disk"):
        assert required in sig.parameters

    ret = sig.return_annotation
    ret_name = getattr(ret, "__name__", repr(ret))
    assert "BaselineAssessment" in ret_name

    # Default constructor builds a valid no-checkpoint assessment.
    result = provider.assess_baseline(make_vm_config(), make_target(), make_vm_config().disks[0])
    assert isinstance(result, BaselineAssessment)
    assert result.status == "no_checkpoint"


def test_mock_backup_provider_run_backup_kind_is_valid():
    """MockBitmapBackupProvider.run_backup sets a valid kind on the result.

    ``BackupResult.kind`` must be one of ``full``/``delta``/
    ``recovered_delta`` (backup-provider spec: "Backup results carry the
    backup kind").
    """
    from qsnap.models.config import TargetConfig
    from qsnap.models.results import BackupResult

    provider = MockBitmapBackupProvider(backup_kind="recovered_delta")
    vm_config = _make_vm_config()
    target = TargetConfig(path=Path("/mnt/backup/testvm"))
    disk = vm_config.disks[0]
    result = provider.run_backup(vm_config, target, disk)
    assert isinstance(result, BackupResult)
    assert result.kind in ("full", "delta", "recovered_delta")
    assert result.kind == "recovered_delta"
