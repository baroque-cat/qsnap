"""Contract test: IVMModuleFactory ABC defines all creation methods."""

from __future__ import annotations

import pytest

from qsnap.factory.default import DefaultFactory
from qsnap.interfaces.factory import IVMModuleFactory


def test_ivmmodulefactory_defines_all_creation_methods():
    """IVMModuleFactory is an ABC defining exactly the six creation methods.

    Verifies that the abstract method set matches the spec exactly (no more,
    no fewer), that the ABC cannot be instantiated directly, and that
    DefaultFactory is a subclass.
    """
    # IVMModuleFactory is an ABC.
    assert hasattr(IVMModuleFactory, "__abstractmethods__")

    expected_methods = {
        "create_snapshot_provider",
        "create_backup_provider",
        "create_retention_engine",
        "create_change_detector",
        "create_lifecycle_manager",
        "create_bucket_full_strategy",
    }
    # Exact match: no speculative future methods, no missing methods.
    assert set(IVMModuleFactory.__abstractmethods__) == expected_methods

    # Cannot instantiate the ABC directly.
    with pytest.raises(TypeError):
        IVMModuleFactory()  # type: ignore[abstract]

    # DefaultFactory is a subclass of IVMModuleFactory.
    assert issubclass(DefaultFactory, IVMModuleFactory)
