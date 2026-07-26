"""Shared pytest fixtures for qsnap tests."""

from __future__ import annotations

import getpass
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from qsnap.cli.app import build_argparser
from qsnap.models.config import GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import ShellResult
from tests.mocks import (
    InMemoryStateManager,
    MockConfigFacade,
    MockShell,
    MockVMModuleFactory,
)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Remove stale pytest temp dirs left by previous runs.

    Integration tests write qcow2 artifacts into ``tmp_path`` (~1.4 GB per
    full run).  The default basetemp root lives on the small ``/tmp`` tmpfs,
    so a few stale runs fill it up and the whole suite fails with
    ``OSError: [Errno 122] Disk quota exceeded``.  Purge every ``pytest-*``
    dir except the current session's at session start.  Best-effort: cleanup
    failures must never break the run.
    """
    try:
        root = Path(tempfile.gettempdir()) / f"pytest-of-{getpass.getuser()}"
        if not root.is_dir():
            return
        current_link = root / "pytest-current"
        current = current_link.resolve() if current_link.is_symlink() else None
        for child in root.iterdir():
            if child.name == "pytest-current" or child == current:
                continue
            if child.name.startswith("pytest-") and child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
    except Exception:  # noqa: BLE001 — cleanup must never break the suite
        pass


def _setup_validation_expectations(shell: MockShell) -> None:
    """Pre-configure MockShell with expectations for Core._validate_environment().

    Core's pre-flight validation runs ``test -d``, ``test -w``,
    ``test -f``, ``which virsh``, ``which qemu-img``, and
    ``virsh dominfo``.  These expectations ensure validation passes
    for any VM name.

    Also pre-configures expectations for pre-flight cleanup (``find``
    commands) and chain verification (``qemu-img info --backing-chain``)
    so that existing tests are not broken by the new safety steps.
    """
    shell.expect("test -d").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    shell.expect("test -w").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    shell.expect("test -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    shell.expect("which virsh").returns(
        ShellResult(
            success=True,
            stdout="/usr/bin/virsh\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    shell.expect("which qemu-img").returns(
        ShellResult(
            success=True,
            stdout="/usr/bin/qemu-img\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    shell.expect("virsh dominfo").returns(
        ShellResult(
            success=True,
            stdout="Id: 1\nName: testvm\nState: running\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Default VM-state for the blockcommit VM-state check in _blockcommit_snapshots().
    # Tests needing a different state must use mock_shell.expect_first("domstate").
    shell.expect("virsh domstate").returns(
        ShellResult(success=True, stdout="shut off\n", stderr="", returncode=0, error=None)
    )
    # Default domblklist output for the active-layer detection in _plan_blockcommit().
    # Returns the latest snapshot from the common test fixture chain (snap4 in
    # _add_snapshots_for_chain, timestamp 14:00).  This path is never in the
    # remove set of stale_guard / post_commit tests, so the full remove set is
    # committable under the "shut off" default.  Tests needing a different
    # active layer (e.g. one that IS in their remove set) must use
    # ``mock_shell.expect_first("virsh domblklist")`` to override.
    shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=(
                "Target   Source\n"
                "--------------------------------\n"
                "vda   /var/lib/libvirt/snapshots/testvm/snap4.qcow2\n"
            ),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Pre-flight cleanup: find commands return empty (no stale files)
    shell.expect("find").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # Compress driver availability check (design D10) — succeeds by
    # default so that validation passes.  Tests needing the missing-driver
    # scenario override with ``mock_shell.expect_first("qemu-nbd --image-opts")``.
    shell.expect("qemu-nbd --image-opts").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )


@pytest.fixture
def mock_shell() -> MockShell:
    """A fresh MockShell instance with validation expectations pre-configured."""
    shell = MockShell()
    _setup_validation_expectations(shell)
    return shell


@pytest.fixture
def mock_state() -> InMemoryStateManager:
    """A fresh InMemoryStateManager instance."""
    return InMemoryStateManager()


@pytest.fixture
def mock_config() -> MockConfigFacade:
    """A fresh MockConfigFacade with default empty config."""
    return MockConfigFacade()


@pytest.fixture
def mock_factory() -> MockVMModuleFactory:
    """A fresh MockVMModuleFactory instance."""
    return MockVMModuleFactory()


@pytest.fixture
def make_vm_config():
    """Factory function to create VMConfig instances for tests."""

    def _make(
        name: str = "testvm",
        base_image: str = "/var/lib/libvirt/images/testvm.qcow2",
        snapshot_dir: str = "/var/lib/libvirt/snapshots/testvm",
        **kwargs: object,
    ) -> VMConfig:
        defaults: dict[str, object] = {
            "name": name,
            "base_image": Path(base_image),
            "snapshot_dir": Path(snapshot_dir),
        }
        defaults.update(kwargs)
        return VMConfig(**defaults)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def make_target():
    """Factory function to create TargetConfig instances for tests."""

    def _make(
        path: str = "/mnt/backup/testvm",
        incremental: bool = True,
        compress: bool = True,
        compression_type: str = "zstd",
        full_transfer_engine: str = "qemu-img-convert",
        convert_parallel: int = 4,
        convert_out_of_order: bool = True,
        backup_stall_timeout: str = "30m",
        backup_create: str = "always",
        **kwargs: object,
    ) -> TargetConfig:
        defaults: dict[str, object] = {
            "path": Path(path),
            "incremental": incremental,
            "compress": compress,
            "compression_type": compression_type,
            "full_transfer_engine": full_transfer_engine,
            "convert_parallel": convert_parallel,
            "convert_out_of_order": convert_out_of_order,
            "backup_stall_timeout": backup_stall_timeout,
            "backup_create": backup_create,
        }
        defaults.update(kwargs)
        return TargetConfig(**defaults)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def make_global_config():
    """Factory function to create GlobalConfig instances for tests."""

    def _make(
        timestamp_format: str = "long",
        preserve_day_of_week: str = "monday",
        state_dir: str = "/var/lib/qsnap/state",
        lockfile: str | None = None,
        snapshot_preserve: str | None = None,
        target_preserve: str | None = None,
        snapshot_preserve_min: str | None = None,
        target_preserve_min: str | None = None,
        deferred_warn_count: str = "5",
        deferred_crit_count: str = "10",
        deferred_warn_age: str = "7d",
        deferred_crit_age: str = "14d",
        auto_cleanup: bool = True,
        state_backup_count: int = 2,
        chain_verify_before_commit: bool = True,
        chain_verify_after_commit: bool = True,
        deep_check_schedule: str = "off",
        compress: bool = True,
        compression_type: str = "zstd",
        full_transfer_engine: str = "qemu-img-convert",
        convert_parallel: int = 4,
        convert_out_of_order: bool = True,
        backup_stall_timeout: str = "30m",
        full_verify_after_create: str = "check",
        full_verify_before_delete: str = "check",
        deep_check_targets: bool = False,
        transaction_log: str | None = None,
        backup_create: str = "always",
    ) -> GlobalConfig:
        return GlobalConfig(
            timestamp_format=timestamp_format,
            preserve_day_of_week=preserve_day_of_week,
            state_dir=state_dir,
            lockfile=lockfile,
            snapshot_preserve=snapshot_preserve,
            target_preserve=target_preserve,
            snapshot_preserve_min=snapshot_preserve_min,
            target_preserve_min=target_preserve_min,
            deferred_warn_count=deferred_warn_count,
            deferred_crit_count=deferred_crit_count,
            deferred_warn_age=deferred_warn_age,
            deferred_crit_age=deferred_crit_age,
            auto_cleanup=auto_cleanup,
            state_backup_count=state_backup_count,
            chain_verify_before_commit=chain_verify_before_commit,
            chain_verify_after_commit=chain_verify_after_commit,
            deep_check_schedule=deep_check_schedule,
            compress=compress,
            compression_type=compression_type,
            full_transfer_engine=full_transfer_engine,
            convert_parallel=convert_parallel,
            convert_out_of_order=convert_out_of_order,
            backup_stall_timeout=backup_stall_timeout,
            full_verify_after_create=full_verify_after_create,
            full_verify_before_delete=full_verify_before_delete,
            deep_check_targets=deep_check_targets,
            transaction_log=transaction_log,
            backup_create=backup_create,
        )

    return _make


@pytest.fixture
def mock_lock_manager():
    """A mock LockManager whose acquire() always returns True."""
    mgr = Mock()
    mgr.acquire.return_value = True
    mgr.release.return_value = None
    return mgr


@pytest.fixture
def cli_app():
    """The built ArgumentParser for CLI tests that parse argv lists."""
    return build_argparser()


@pytest.fixture
def frozen_clock():
    """Context manager / fixture that freezes datetime.now() to a fixed value.

    Returns a function that takes a datetime and returns a context manager.
    Usage::

        with frozen_clock(datetime(2025, 7, 13, 15, 31)):
            assert datetime.now() == datetime(2025, 7, 13, 15, 31)
    """
    from contextlib import contextmanager
    from unittest.mock import patch

    def _freeze(frozen_dt: datetime):
        @contextmanager
        def _ctx():
            with patch("qsnap.core.datetime") as mock_dt:
                mock_dt.now.return_value = frozen_dt
                mock_dt.side_effect = lambda *a, **kw: frozen_dt
                yield frozen_dt

        return _ctx()

    return _freeze
