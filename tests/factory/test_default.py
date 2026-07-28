"""Unit tests for DefaultFactory."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.factory.default import DefaultFactory
from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import CommitResult, ShellResult, SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.modules.change.allocation_detector import AllocationSizeDetector
from qsnap.modules.change.map_detector import MapChangeDetector
from qsnap.modules.lifecycle.blockcommit_manager import BlockCommitManager
from qsnap.modules.lifecycle.qemu_img_commit import QemuImgCommitManager
from qsnap.utils.nbd import is_libvirt_new_enough
from qsnap.utils.nbd_client import LibnbdClient


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
        # create_backup_provider has hard version/libnbd gates — patch them.
        with (
            patch(
                "qsnap.factory.default.is_libvirt_new_enough",
                return_value=True,
            ),
            patch(
                "qsnap.factory.default.is_libnbd_available",
                return_value=True,
            ),
        ):
            result = method(*args)
            assert isinstance(result, expected_interface)
        return
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

    ``create_backup_provider`` has hard version/libnbd gates — they are
    patched here to let the interface-contract check pass.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    assert isinstance(factory.create_snapshot_provider(make_vm_config()), ISnapshotProvider)
    with (
        patch(
            "qsnap.factory.default.is_libvirt_new_enough",
            return_value=True,
        ),
        patch(
            "qsnap.factory.default.is_libnbd_available",
            return_value=True,
        ),
    ):
        assert isinstance(
            factory.create_backup_provider(make_vm_config(), make_target()), IBackupProvider
        )
    assert isinstance(factory.create_retention_engine(RetentionPolicy()), IRetentionEngine)
    assert isinstance(factory.create_change_detector("always"), IChangeDetector)
    assert isinstance(factory.create_lifecycle_manager(), ILifecycleManager)


def test_factory_always_returns_bitmap_backup_provider(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """DefaultFactory.create_backup_provider() always returns
    BitmapBackupProvider when libvirt version >= 7.2 and libnbd is available.

    There is no mode-select logic — BitmapBackupProvider is the sole
    backup provider.  Verifies that the factory gates construction on
    ``is_libvirt_new_enough`` and ``is_libnbd_available`` before
    returning BitmapBackupProvider, and that the factory injects its
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
    target = make_target()

    with (
        patch(
            "qsnap.factory.default.is_libvirt_new_enough",
            wraps=is_libvirt_new_enough,
        ) as mock_check,
        patch(
            "qsnap.factory.default.is_libnbd_available",
            return_value=True,
        ),
    ):
        provider = factory.create_backup_provider(make_vm_config(), target)

    assert isinstance(provider, BitmapBackupProvider)
    assert provider._state is mock_state
    mock_check.assert_called_once_with(mock_shell)


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


def test_factory_bitmap_mode_new_libvirt_returns_bitmap(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """libvirt >= 7.2 + libnbd available → BitmapBackupProvider.

    When both ``is_libvirt_new_enough`` and ``is_libnbd_available``
    return True, the factory constructs a ``BitmapBackupProvider`` and
    injects the factory's ``_state`` reference into the provider.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target()

    with (
        patch(
            "qsnap.factory.default.is_libvirt_new_enough",
            return_value=True,
        ) as mock_check,
        patch(
            "qsnap.factory.default.is_libnbd_available",
            return_value=True,
        ),
    ):
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
    returned provider holds a reference to the factory's state manager
    and has a LibnbdClient wired in.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target()

    with (
        patch(
            "qsnap.factory.default.is_libvirt_new_enough",
            return_value=True,
        ),
        patch(
            "qsnap.factory.default.is_libnbd_available",
            return_value=True,
        ),
    ):
        provider = factory.create_backup_provider(make_vm_config(), target)

    assert isinstance(provider, BitmapBackupProvider)
    assert provider._state is mock_state, (
        "Factory must inject its IStateManager into BitmapBackupProvider"
    )
    assert isinstance(provider._nbd, LibnbdClient), (
        "Factory must wire a LibnbdClient into BitmapBackupProvider"
    )


# ── 7.2 boundary tests ────────────────────────────────────────────────


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
    target = make_target()

    with patch(
        "qsnap.factory.default.is_libnbd_available",
        return_value=True,
    ):
        provider = factory.create_backup_provider(make_vm_config(), target)

    assert isinstance(provider, BitmapBackupProvider)
    assert provider._state is mock_state


def test_factory_old_libvirt_raises_runtime_error(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """libvirt < 7.2 → RuntimeError with actionable message.

    When ``is_libvirt_new_enough`` returns False, the factory raises
    ``RuntimeError`` naming "libvirt" and "7.2" in the message.  No
    provider is returned — no bitmap provider when prerequisites are unmet.
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target()

    with (
        patch(
            "qsnap.factory.default.is_libvirt_new_enough",
            return_value=False,
        ),
        pytest.raises(RuntimeError, match=r"libvirt.*7\.2"),
    ):
        factory.create_backup_provider(make_vm_config(), target)


# ── bitmap libnbd availability gating tests ─────────────────────────────


def test_factory_bitmap_mode_without_libnbd_raises_actionable_error(
    mock_shell,
    mock_state,
    make_vm_config,
    make_target,
):
    """libvirt >= 7.2 + missing libnbd → RuntimeError.

    When libvirt meets the version threshold but ``python3-libnbd`` is not
    installed, the factory raises ``RuntimeError(MISSING_LIBNBD_ERROR)``
    naming the distro package — no silent fallback (design R4).
    """
    factory = DefaultFactory(shell=mock_shell, state=mock_state)
    target = make_target()

    with (
        patch(
            "qsnap.factory.default.is_libvirt_new_enough",
            return_value=True,
        ),
        patch(
            "qsnap.factory.default.is_libnbd_available",
            return_value=False,
        ),
        pytest.raises(RuntimeError, match="python3-libnbd"),
    ):
        factory.create_backup_provider(make_vm_config(), target)


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
