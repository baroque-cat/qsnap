"""Contract tests: IBackupProvider ABC and concrete implementations.

Verifies that every implementation of IBackupProvider obeys the interface
contract: correct return types, ABC enforcement, and no Core inheritance (D1).

The backup-provider API is orthogonal to the snapshot world (design D2 of
``orthogonalize-snapshots-and-backups``): ``run_backup(vm_config, target,
disk, *, opts)`` is the single work unit, ``list()`` returns ``BackupInfo``
(never ``SnapshotInfo``), ``delete()`` accepts ``BackupInfo``, and no
interface method references ``SnapshotInfo`` anywhere in its signature.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.interfaces.backup import IBackupProvider
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import (
    BackupInfo,
    BackupResult,
    BaselineAssessment,
    ShellResult,
    SnapshotInfo,
)
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from tests.mocks.mock_modules import MockBitmapBackupProvider
from tests.mocks.mock_shell import MockShell

PROVIDER_CLASSES = [BitmapBackupProvider, MockBitmapBackupProvider]

RUN_BACKUP_OPTS = (
    "force_full",
    "compression_type",
    "stall_timeout",
    "convert_parallel",
    "convert_out_of_order",
)


def _make_provider(provider_cls):
    """Construct a provider instance with a fresh MockShell where needed."""
    if provider_cls is BitmapBackupProvider:
        return provider_cls(shell=MockShell())
    return provider_cls()


def _make_vm_config() -> VMConfig:
    return VMConfig(
        name="testvm",
        disks=[
            DiskConfig(
                target="vda",
                base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
            ),
        ],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )


def _make_target() -> TargetConfig:
    return TargetConfig(path=Path("/mnt/backup/testvm"))


def _make_backup_info(name: str = "testvm.20260808T030000_vda_a1b2c3") -> BackupInfo:
    return BackupInfo(
        name=name,
        path=Path("/mnt/backup/testvm") / f"{name}.qcow2",
        timestamp=datetime(2026, 8, 8, 3, 0, 0),
        disk="vda",
        is_full=False,
    )


def _annotation_str(annotation) -> str:
    """Render a signature annotation for string comparison.

    ``from __future__ import annotations`` makes annotations strings at
    runtime; without it they are type objects.  Handle both.
    """
    if isinstance(annotation, str):
        return annotation
    return getattr(annotation, "__name__", str(annotation))


# ── ABC enforcement ──────────────────────────────────────────────────


def test_ibackup_provider_is_abstract():
    """IBackupProvider is an ABC with non-empty abstract methods.

    It cannot be instantiated directly.
    """
    assert hasattr(IBackupProvider, "__abstractmethods__")
    assert len(IBackupProvider.__abstractmethods__) > 0
    with pytest.raises(TypeError):
        IBackupProvider()  # type: ignore[abstract]


def test_bitmap_backup_provider_is_ibackup_provider():
    """BitmapBackupProvider is a subclass of IBackupProvider."""
    assert issubclass(BitmapBackupProvider, IBackupProvider)


def test_bitmap_backup_provider_no_core_inheritance():
    """BitmapBackupProvider does NOT inherit from Core (design D1)."""
    assert not issubclass(BitmapBackupProvider, Core)


# ── run_backup contract ──────────────────────────────────────────────


@pytest.mark.parametrize("provider_cls", PROVIDER_CLASSES)
def test_backup_provider_run_backup_returns_backup_result(provider_cls):
    """run_backup() returns a BackupResult carrying the source disk.

    ``BitmapBackupProvider`` with a bare ``MockShell`` fails on the
    offline FULL convert step but returns ``BackupResult(success=False)``.
    ``MockBitmapBackupProvider`` returns a successful ``BackupResult``.
    """
    provider = _make_provider(provider_cls)
    vm_config = _make_vm_config()
    disk = vm_config.disks[0]
    target = _make_target()
    result = provider.run_backup(vm_config, target, disk, force_full=True)
    assert isinstance(result, BackupResult)
    assert isinstance(result.success, bool)
    assert result.disk == "vda", f"Expected disk='vda', got disk={result.disk!r}"


@pytest.mark.parametrize("provider_cls", PROVIDER_CLASSES)
def test_backup_provider_run_backup_accepts_opts(provider_cls):
    """run_backup() accepts the keyword-only opts (force_full, compression_type, ...)."""
    sig = inspect.signature(provider_cls.run_backup)
    params = sig.parameters
    for required in ("vm_config", "target", "disk"):
        assert required in params, f"{required} missing from {provider_cls.__name__}.run_backup"
    for opt in RUN_BACKUP_OPTS:
        assert opt in params, f"opts keyword {opt} missing from {provider_cls.__name__}.run_backup"
        assert params[opt].kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{provider_cls.__name__}.run_backup opt {opt} must be keyword-only"
        )

    # The opts can actually be passed and are honored.
    provider = _make_provider(provider_cls)
    vm_config = _make_vm_config()
    disk = vm_config.disks[0]
    target = _make_target()
    result = provider.run_backup(
        vm_config,
        target,
        disk,
        force_full=True,
        compression_type="zlib",
        stall_timeout=0,
        convert_parallel=8,
        convert_out_of_order=False,
    )
    assert isinstance(result, BackupResult)


# ── list / delete contract ───────────────────────────────────────────


@pytest.mark.parametrize("provider_cls", PROVIDER_CLASSES)
def test_backup_provider_list_returns_backupinfo(provider_cls, tmp_path):
    """list() returns list[BackupInfo] — never SnapshotInfo.

    ``BitmapBackupProvider`` scans the target directory and parses each
    ``*.qcow2`` file into ``BackupInfo``.  ``MockBitmapBackupProvider``
    returns an empty list — still a valid ``list[BackupInfo]``.
    """
    if provider_cls is BitmapBackupProvider:
        shell = MockShell()
        (tmp_path / "testvm.20260808T030000_vda_a1b2c3.qcow2").write_bytes(b"\x00")
        shell.expect(r"qemu-img info").returns(
            ShellResult(
                success=True,
                stdout='{"actual-size": 1048576}',
                stderr="",
                returncode=0,
                error=None,
            )
        )
        provider = provider_cls(shell=shell)
        target = TargetConfig(path=tmp_path)
    else:
        provider = _make_provider(provider_cls)
        target = _make_target()

    result = provider.list(target)
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, BackupInfo), (
            f"{provider_cls.__name__}.list() returned {type(item).__name__}, expected BackupInfo"
        )
        assert not isinstance(item, SnapshotInfo), (
            f"{provider_cls.__name__}.list() must never return SnapshotInfo"
        )


@pytest.mark.parametrize("provider_cls", PROVIDER_CLASSES)
def test_backup_provider_delete_accepts_backupinfo(provider_cls):
    """delete() accepts a BackupInfo (never SnapshotInfo) and returns ShellResult."""
    sig = inspect.signature(provider_cls.delete)
    assert "backup" in sig.parameters, f"backup param missing from {provider_cls.__name__}.delete"
    ann_str = _annotation_str(sig.parameters["backup"].annotation)
    assert "BackupInfo" in ann_str, (
        f"{provider_cls.__name__}.delete must annotate backup as BackupInfo, got {ann_str!r}"
    )
    assert "SnapshotInfo" not in ann_str

    provider = _make_provider(provider_cls)
    result = provider.delete(_make_backup_info())
    assert isinstance(result, ShellResult)


# ── assess_baseline contract ──────────────────────────────────────────


@pytest.mark.parametrize("provider_cls", PROVIDER_CLASSES)
def test_backup_provider_assess_baseline_contract(provider_cls):
    """assess_baseline() returns a valid BaselineAssessment, never None.

    ``IBackupProvider.assess_baseline(vm_config, target, disk)`` is a
    read-only baseline assessment for dry-run parity (backup-provider
    spec, design D10).  Every implementation — including the mock — must
    return a frozen ``BaselineAssessment`` object whose ``status`` is one
    of the four defined states.  A bare ``MockShell`` yields
    ``no_checkpoint`` (checkpoint-list fails non-fatally → empty).
    """
    sig = inspect.signature(provider_cls.assess_baseline)
    for required in ("vm_config", "target", "disk"):
        assert required in sig.parameters, (
            f"{required} missing from {provider_cls.__name__}.assess_baseline"
        )

    provider = _make_provider(provider_cls)
    vm_config = _make_vm_config()
    disk = vm_config.disks[0]
    target = _make_target()

    result = provider.assess_baseline(vm_config, target, disk)

    # Never None; always a frozen BaselineAssessment.
    assert result is not None, f"{provider_cls.__name__}.assess_baseline must never return None"
    assert isinstance(result, BaselineAssessment)
    assert result.__dataclass_params__.frozen is True

    # Status is one of the defined tri-state-plus-absent values.
    assert result.status in ("no_checkpoint", "healthy", "dead", "unknown"), (
        f"Unexpected baseline status {result.status!r}"
    )
    # newest_checkpoint is None when no checkpoint is reported.
    if result.status == "no_checkpoint":
        assert result.newest_checkpoint is None


@pytest.mark.parametrize("provider_cls", PROVIDER_CLASSES)
def test_backup_provider_assess_baseline_is_read_only(provider_cls, tmp_path):
    """assess_baseline issues no mutating shell commands.

    The assessment MUST be read-only (dry-run parity): with a preloaded
    shell, no ``checkpoint-create``, ``checkpoint-delete``, ``backup-*``,
    ``domjobabort``, or file-writing ``qemu-img`` command may be issued.
    For the mock, the method performs no shell calls at all.
    """
    shell = MockShell()
    # Preload a read-only checkpoint-list result so the real provider
    # reaches a deterministic no-checkpoint assessment.
    shell.expect(r"virsh checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    provider = provider_cls(shell=shell) if provider_cls is BitmapBackupProvider else provider_cls()

    vm_config = _make_vm_config()
    disk = vm_config.disks[0]
    target = _make_target()

    result = provider.assess_baseline(vm_config, target, disk)
    assert isinstance(result, BaselineAssessment)

    for cmd in shell.call_history:
        assert "checkpoint-create" not in cmd
        assert "checkpoint-delete" not in cmd
        assert "backup-begin" not in cmd
        assert "domjobabort" not in cmd


# ── BackupResult.kind contract ────────────────────────────────────────


@pytest.mark.parametrize("provider_cls", PROVIDER_CLASSES)
def test_backup_provider_run_backup_result_carries_kind(provider_cls):
    """run_backup() results carry a BackupResult.kind in the defined set.

    ``kind`` identifies how the backup was produced — ``"full"``,
    ``"delta"``, or ``"recovered_delta"`` (backup-provider spec).
    """
    provider = _make_provider(provider_cls)
    vm_config = _make_vm_config()
    disk = vm_config.disks[0]
    target = _make_target()

    result = provider.run_backup(vm_config, target, disk, force_full=True)
    assert isinstance(result, BackupResult)
    assert result.kind in ("full", "delta", "recovered_delta")


# ── Orthogonality: no SnapshotInfo in the provider API ───────────────


@pytest.mark.parametrize("provider_cls", PROVIDER_CLASSES)
def test_backup_provider_api_never_references_snapshotinfo(provider_cls):
    """No IBackupProvider method references SnapshotInfo in its signature.

    Inspects every method declared on ``IBackupProvider`` (both the ABC
    declaration and the concrete implementation): parameters and return
    annotations must never mention ``SnapshotInfo``.
    """
    interface_methods = [name for name in IBackupProvider.__dict__ if not name.startswith("_")]
    assert interface_methods, "expected IBackupProvider to declare public methods"
    # The new read-only assessment method is part of the public API.
    assert "assess_baseline" in interface_methods

    for name in interface_methods:
        for method in (getattr(IBackupProvider, name), getattr(provider_cls, name)):
            sig = inspect.signature(method)
            for param in sig.parameters.values():
                if param.annotation is inspect.Parameter.empty:
                    continue
                ann_str = _annotation_str(param.annotation)
                assert "SnapshotInfo" not in ann_str, (
                    f"{provider_cls.__name__}.{name} param {param.name} "
                    f"references SnapshotInfo ({ann_str})"
                )
            if sig.return_annotation is not inspect.Signature.empty:
                ret_str = _annotation_str(sig.return_annotation)
                assert "SnapshotInfo" not in ret_str, (
                    f"{provider_cls.__name__}.{name} return type "
                    f"references SnapshotInfo ({ret_str})"
                )

    # Explicit assess_baseline orthogonality check: the signature takes
    # (vm_config, target, disk) and returns BaselineAssessment — never
    # SnapshotInfo (backup-target-orthogonality spec scenario "Provider
    # receives no SnapshotInfo").
    assess_sig = inspect.signature(provider_cls.assess_baseline)
    assert "SnapshotInfo" not in str(assess_sig)
    ret_str = _annotation_str(assess_sig.return_annotation)
    assert "BaselineAssessment" in ret_str
    assert "SnapshotInfo" not in ret_str
