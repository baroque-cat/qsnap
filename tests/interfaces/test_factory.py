"""Contract test: IVMModuleFactory ABC defines all creation methods."""

from __future__ import annotations

import inspect

import pytest

from qsnap.factory.default import DefaultFactory
from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.factory import IVMModuleFactory


def test_ivm_module_factory_methods():
    """IVMModuleFactory defines exactly five creation methods.

    Verifies that the abstract method set matches the spec exactly (no more,
    no fewer), that ``create_bucket_full_strategy`` is NOT in the interface,
    that the ABC cannot be instantiated directly, and that ``DefaultFactory``
    is a subclass.
    """
    # IVMModuleFactory is an ABC.
    assert hasattr(IVMModuleFactory, "__abstractmethods__")

    expected_methods = {
        "create_snapshot_provider",
        "create_backup_provider",
        "create_retention_engine",
        "create_change_detector",
        "create_lifecycle_manager",
    }
    # Exact match: no speculative future methods, no missing methods.
    assert set(IVMModuleFactory.__abstractmethods__) == expected_methods

    # Cannot instantiate the ABC directly.
    with pytest.raises(TypeError):
        IVMModuleFactory()  # type: ignore[abstract]

    # DefaultFactory is a subclass of IVMModuleFactory.
    assert issubclass(DefaultFactory, IVMModuleFactory)


def test_factory_create_backup_provider_returns_ibackup_provider():
    """``create_backup_provider`` exists and returns ``IBackupProvider``.

    The abstract factory declares the creation method and its return
    annotation is the backup-provider ABC (the concrete factory wires a
    ``BitmapBackupProvider`` — see ``tests/factory/test_default.py``).
    """
    assert "create_backup_provider" in IVMModuleFactory.__abstractmethods__

    sig = inspect.signature(IVMModuleFactory.create_backup_provider)
    ret = sig.return_annotation
    assert ret in (IBackupProvider, "IBackupProvider"), (
        f"create_backup_provider must return IBackupProvider, got {ret!r}"
    )

    # Concrete factory honors the same return contract.
    assert issubclass(DefaultFactory, IVMModuleFactory)
    impl_ret = inspect.signature(DefaultFactory.create_backup_provider).return_annotation
    assert impl_ret in (IBackupProvider, "IBackupProvider"), (
        f"DefaultFactory.create_backup_provider must return IBackupProvider, got {impl_ret!r}"
    )
