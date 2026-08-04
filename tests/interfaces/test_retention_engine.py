"""Contract test: IRetentionEngine is a standalone ABC (no Core inheritance)."""

from __future__ import annotations

from qsnap.core import Core
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.retention.time_based import TimeBasedRetention


def test_iretention_engine_standalone_no_core():
    """IRetentionEngine is a standalone ABC that does NOT inherit from Core.

    TimeBasedRetention is a subclass of IRetentionEngine and also does not
    inherit from Core.  This confirms the retention engine is a pure function
    with no I/O and no orchestrator coupling.
    """
    # IRetentionEngine is an ABC with non-empty abstract methods.
    assert hasattr(IRetentionEngine, "__abstractmethods__")
    assert len(IRetentionEngine.__abstractmethods__) > 0

    # IRetentionEngine does NOT inherit from Core.
    assert not issubclass(IRetentionEngine, Core)

    # TimeBasedRetention is a subclass of IRetentionEngine.
    assert issubclass(TimeBasedRetention, IRetentionEngine)

    # TimeBasedRetention does NOT inherit from Core.
    assert not issubclass(TimeBasedRetention, Core)
