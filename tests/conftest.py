"""Shared pytest fixtures for qsnap tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from qsnap.models.config import TargetConfig, VMConfig
from tests.mocks import (
    InMemoryStateManager,
    MockConfigFacade,
    MockShell,
    MockVMModuleFactory,
)


@pytest.fixture
def mock_shell() -> MockShell:
    """A fresh MockShell instance."""
    return MockShell()


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
        **kwargs: object,
    ) -> TargetConfig:
        defaults: dict[str, object] = {
            "path": Path(path),
            "incremental": incremental,
        }
        defaults.update(kwargs)
        return TargetConfig(**defaults)  # type: ignore[arg-type]

    return _make
