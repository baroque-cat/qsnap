"""Unit tests for DefaultFactory."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.factory.default import DefaultFactory
from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.bucket_strategy import IBucketFullStrategy
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import CommitResult, ShellResult, SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.modules.backup.bucket_strategy import BucketFullStrategy
from qsnap.modules.backup.file_copy import FileCopyBackupProvider
from qsnap.modules.change.allocation_detector import AllocationSizeDetector
from qsnap.modules.change.map_detector import MapChangeDetector
from qsnap.modules.lifecycle.blockcommit_manager import BlockCommitManager
from qsnap.modules.lifecycle.qemu_img_commit import QemuImgCommitManager
from qsnap.utils.nbd import is_libvirt_new_enough


def test_default_factory_stores_shell_and_state(mock_shell, mock_state):
    """DefaultFactory stores the shell and state references passed at construction."""
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    assert factory._shell is mock_shell
    assert factory._state is mock_state


@pytest.mark.parametrize(
    ("method_name", "expected_interface"),
    [
        ("create_snapshot_provider", ISnapshotProvider),
        ("create_backup_provider", IBackupProvider),
        ("create_change_detector", IChangeDetector),
        ("create_lifecycle_manager", ILifecycleManager),
    ],
)
def test_default_factory_returns_correct_interface_types(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
    method_name,
    expected_interface,
):
    """Each create_* method (except create_retention_engine) returns an instance
    that implements the correct ABC interface.

    ``create_retention_engine`` was already implemented earlier and is
    verified separately in ``test_default_factory_all_five_methods_return_instances``.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    method = getattr(factory, method_name)

    # Build appropriate arguments per method signature.
    if method_name == "create_snapshot_provider":
        args = (make_vm_config(),)
    elif method_name == "create_backup_provider":
        args = (make_vm_config(), make_target())
    elif method_name == "create_change_detector":
        args = ("always",)
    else:  # create_lifecycle_manager
        args = ()

    result = method(*args)
    assert isinstance(result, expected_interface)


def test_default_factory_all_five_methods_return_instances(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """All five create_* methods return concrete instances of their ABC.

    This includes ``create_retention_engine``, which was implemented before
    the other four.  Together with the parametrized test above, this
    guarantees that no factory method returns ``None`` or raises.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    assert isinstance(factory.create_snapshot_provider(make_vm_config()), ISnapshotProvider)
    assert isinstance(
        factory.create_backup_provider(make_vm_config(), make_target()), IBackupProvider
    )
    assert isinstance(factory.create_retention_engine(RetentionPolicy()), IRetentionEngine)
    assert isinstance(factory.create_change_detector("always"), IChangeDetector)
    assert isinstance(factory.create_lifecycle_manager(), ILifecycleManager)


def test_factory_selects_bitmap_provider_for_bitmap_mode(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """DefaultFactory.create_backup_provider() with bitmap mode returns
    BitmapBackupProvider when libvirt version >= 7.2.

    Verifies that the factory gates construction on ``is_libvirt_new_enough``
    before returning BitmapBackupProvider, and that the factory injects its
    ``_state`` reference into the provider so the provider can persist
    cross-run data.
    """
    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 8.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target(incremental_mode="bitmap")

    with patch(
        "qsnap.factory.default.is_libvirt_new_enough",
        wraps=is_libvirt_new_enough,
    ) as mock_check:
        provider = factory.create_backup_provider(make_vm_config(), target)

    assert isinstance(provider, BitmapBackupProvider)
    assert provider._state is mock_state
    mock_check.assert_called_once_with(mock_shell)


def test_factory_selects_file_copy_provider_for_default_mode(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """DefaultFactory.create_backup_provider() with file-copy mode returns
    FileCopyBackupProvider (no qemu-img version check needed)."""
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target(incremental_mode="file-copy")
    provider = factory.create_backup_provider(make_vm_config(), target)
    assert isinstance(provider, FileCopyBackupProvider)


# ── new factory branch tests (Group G) ───────────────────────────────


def test_factory_map_mode_returns_map_detector(mock_shell, mock_state):
    """create_change_detector("allocation-map") returns a MapChangeDetector.

    The factory routes the ``"allocation-map"`` mode to the map-based
    detector (qemu-img map comparison) rather than the default
    allocation-size detector.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    detector = factory.create_change_detector("allocation-map")
    assert isinstance(detector, MapChangeDetector)


def test_factory_unrecognized_mode_falls_back(mock_shell, mock_state):
    """create_change_detector("unknown") falls back to AllocationSizeDetector.

    Any mode that is not explicitly handled defaults to the
    allocation-size detector so that an unrecognized config value never
    crashes the pipeline.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    detector = factory.create_change_detector("unknown")
    assert isinstance(detector, AllocationSizeDetector)


def test_factory_qemu_img_mode_returns_qemu_img_commit(mock_shell, mock_state):
    """create_lifecycle_manager(mode="qemu-img") returns QemuImgCommitManager.

    The ``"qemu-img"`` lifecycle mode selects the offline
    ``qemu-img commit`` merge strategy instead of the default
    ``virsh blockcommit``.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    manager = factory.create_lifecycle_manager(mode="qemu-img")
    assert isinstance(manager, QemuImgCommitManager)


def test_factory_default_lifecycle_returns_blockcommit(mock_shell, mock_state):
    """create_lifecycle_manager() with default mode returns BlockCommitManager.

    Calling without an explicit ``mode`` argument defaults to
    ``"virsh"`` and returns the ``virsh blockcommit``-based manager.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    manager = factory.create_lifecycle_manager()
    assert isinstance(manager, BlockCommitManager)


# ── bitmap libvirt version gating tests ──────────────────────────────


def test_factory_bitmap_mode_old_libvirt_falls_back(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """bitmap mode + libvirt < 7.2 → FileCopyBackupProvider fallback.

    The factory calls ``is_libvirt_new_enough``, which returns False for
    old libvirt versions (below 7.2), causing the factory to log a WARNING
    and return a ``FileCopyBackupProvider`` instead of
    ``BitmapBackupProvider``.
    This absorbs the former ``test_factory_falls_back_to_file_copy_on_old_qemu``,
    ``test_factory_falls_back_on_old_libvirt``, and
    ``test_risk_factory_falls_back_to_file_copy_on_old_libvirt``.
    """
    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 4.5.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target(incremental_mode="bitmap")
    provider = factory.create_backup_provider(make_vm_config(), target)
    assert isinstance(provider, FileCopyBackupProvider)


def test_factory_bitmap_mode_new_libvirt_returns_bitmap(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """bitmap mode + libvirt >= 7.2 → BitmapBackupProvider.

    When ``is_libvirt_new_enough`` returns True, the factory constructs
    a ``BitmapBackupProvider`` for the bitmap incremental mode and injects
    the factory's ``_state`` reference into the provider.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target(incremental_mode="bitmap")

    with patch(
        "qsnap.factory.default.is_libvirt_new_enough",
        return_value=True,
    ) as mock_check:
        provider = factory.create_backup_provider(make_vm_config(), target)

    assert isinstance(provider, BitmapBackupProvider)
    assert provider._state is mock_state
    mock_check.assert_called_once_with(mock_shell)


def test_factory_passes_state_to_bitmap_provider(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """DefaultFactory.create_backup_provider() injects its ``_state`` into
    BitmapBackupProvider so the provider can persist cross-run data.

    This test isolates the state-injection concern: it patches
    ``is_libvirt_new_enough`` to return True and then verifies the
    returned provider holds a reference to the factory's state manager.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target(incremental_mode="bitmap")

    with patch(
        "qsnap.factory.default.is_libvirt_new_enough",
        return_value=True,
    ):
        provider = factory.create_backup_provider(make_vm_config(), target)

    assert isinstance(provider, BitmapBackupProvider)
    assert provider._state is mock_state, (
        "Factory must inject its IStateManager into BitmapBackupProvider"
    )


def test_factory_non_bitmap_mode_no_version_check(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """Non-bitmap mode skips libvirt version check entirely.

    When the target's ``incremental_mode`` is not ``"bitmap"``, the
    factory must not call ``is_libvirt_new_enough`` at all — there is
    no reason to check libvirt version for file-copy providers.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target(incremental_mode="file-copy")

    with patch("qsnap.factory.default.is_libvirt_new_enough") as mock_check:
        provider = factory.create_backup_provider(make_vm_config(), target)

    assert isinstance(provider, FileCopyBackupProvider)
    mock_check.assert_not_called()


def test_factory_bitmap_fallback_logs_warning(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
    caplog,
):
    """When the factory falls back from BitmapBackupProvider to
    FileCopyBackupProvider, a WARNING is logged containing "falling back"
    so operators are aware of the silent degradation.

    Uses libvirt 5.9.0 (below the 7.2 checkpoint threshold) to confirm the
    WARNING fires on any version older than the minimum.

    (Renamed from ``test_risk_factory_fallback_logs_warning``.)
    """
    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 5.9.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target(incremental_mode="bitmap")

    with caplog.at_level(
        logging.WARNING,
        logger="qsnap.factory.default",
    ):
        provider = factory.create_backup_provider(make_vm_config(), target)

    assert isinstance(provider, FileCopyBackupProvider)
    assert "falling back" in caplog.text or "FileCopyBackupProvider" in caplog.text


# ── 7.2 boundary tests ────────────────────────────────────────────────


def test_factory_libvirt_7_1_falls_back(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
    caplog,
):
    """libvirt 7.1.0 is not sufficient (< 7.2) → FileCopyBackupProvider fallback.

    With the atomic checkpoint threshold raised to 7.2, versions 6.x,
    7.0, and 7.1 are no longer sufficient for BitmapBackupProvider.
    The factory must fall back to FileCopyBackupProvider and log a WARNING
    so operators are aware of the silent degradation.
    """
    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 7.1.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target(incremental_mode="bitmap")

    with caplog.at_level(
        logging.WARNING,
        logger="qsnap.factory.default",
    ):
        provider = factory.create_backup_provider(make_vm_config(), target)

    assert isinstance(provider, FileCopyBackupProvider)
    assert "falling back" in caplog.text or "FileCopyBackupProvider" in caplog.text


def test_factory_libvirt_7_2_returns_bitmap(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """libvirt 7.2.0 meets the checkpoint threshold → BitmapBackupProvider.

    7.2 is the exact minimum for the atomic checkpoint API.  The factory
    must construct a BitmapBackupProvider directly (no fallback WARNING)
    and inject the factory's ``_state`` reference into the provider.
    """
    mock_shell.expect("virsh --version").returns(
        ShellResult(
            success=True,
            stdout="virsh 7.2.0\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target(incremental_mode="bitmap")

    provider = factory.create_backup_provider(make_vm_config(), target)

    assert isinstance(provider, BitmapBackupProvider)
    assert provider._state is mock_state


def test_create_bucket_full_strategy_returns_bucketfullstrategy(
    mock_shell,
    mock_state,
):
    """DefaultFactory.create_bucket_full_strategy() returns BucketFullStrategy.

    Verifies the factory method returns an instance that implements both
    ``IBucketFullStrategy`` and the concrete ``BucketFullStrategy``.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    strategy = factory.create_bucket_full_strategy()
    assert isinstance(strategy, IBucketFullStrategy)
    assert isinstance(strategy, BucketFullStrategy)


# ── deep_verify factory test ──────────────────────────────────────────

# Standard domblklist output used by lifecycle manager tests.
_DOMBLKLIST_OUTPUT = (
    " Target   Source\n"
    "------------------------------------\n"
    " vda      /var/lib/libvirt/images/testvm.qcow2\n"
)


def test_factory_create_lifecycle_manager_accepts_deep_verify(
    mock_shell,
    mock_state,
    make_vm_config,
):
    """Factory's create_lifecycle_manager() returns an ILifecycleManager
    whose blockcommit() accepts the deep_verify kwarg.

    deep_verify is a method parameter on ILifecycleManager.blockcommit(),
    not a factory concern — the factory does not need modification.
    This test verifies that the returned manager from
    ``create_lifecycle_manager(mode="virsh")`` can be called with
    ``deep_verify=True`` and returns a ``CommitResult``.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    manager = factory.create_lifecycle_manager(mode="virsh")
    assert isinstance(manager, BlockCommitManager)

    vm_config = make_vm_config()
    snap = SnapshotInfo(
        name="test-snap",
        path=Path("/tmp/snap.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=65536,
    )

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions": 0}',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    result = manager.blockcommit(vm_config, [snap], deep_verify=True)
    assert isinstance(result, CommitResult)
    assert result.success is True
    assert result.error is None
