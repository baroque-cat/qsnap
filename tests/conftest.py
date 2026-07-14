"""Shared pytest fixtures for qsnap tests."""

from __future__ import annotations

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


def _setup_validation_expectations(shell: MockShell) -> None:
    """Pre-configure MockShell with expectations for Core._validate_environment().

    Core's pre-flight validation runs ``test -d``, ``test -w``,
    ``test -f``, ``which virsh``, ``which qemu-img``, and
    ``virsh dominfo``.  These expectations ensure validation passes
    for any VM name.
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
    shell.expect("which rsync").returns(
        ShellResult(
            success=True,
            stdout="/usr/bin/rsync\n",
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
        rate_limit: str = "no",
        **kwargs: object,
    ) -> TargetConfig:
        defaults: dict[str, object] = {
            "path": Path(path),
            "incremental": incremental,
            "rate_limit": rate_limit,
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
        rate_limit: str = "no",
        deferred_warn_count: str = "5",
        deferred_crit_count: str = "10",
        deferred_warn_age: str = "7d",
        deferred_crit_age: str = "14d",
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
            rate_limit=rate_limit,
            deferred_warn_count=deferred_warn_count,
            deferred_crit_count=deferred_crit_count,
            deferred_warn_age=deferred_warn_age,
            deferred_crit_age=deferred_crit_age,
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
