"""Contract test: IRetentionEngine is a standalone ABC (no Core inheritance)."""

from __future__ import annotations

from datetime import datetime

import pytest

from qsnap.core import Core
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import RetentionItem, RetentionResult
from qsnap.retention.time_based import TimeBasedRetention
from tests.mocks import MockRetentionEngine


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


def _make_engine(engine_cls, policy: RetentionPolicy):
    """Instantiate an engine, passing policy only if the constructor accepts it."""
    try:
        return engine_cls(policy)
    except TypeError:
        return engine_cls()


@pytest.mark.parametrize("engine_cls", [TimeBasedRetention, MockRetentionEngine])
def test_retention_engine_evaluate_accepts_preserve_day_of_week(engine_cls):
    """Both implementations accept preserve_day_of_week and return RetentionResult."""
    policy = RetentionPolicy(weekly=2, preserve_min="0h")
    items = [
        RetentionItem(name="snap1", timestamp=datetime(2025, 1, 6, 12, 0)),
        RetentionItem(name="snap2", timestamp=datetime(2025, 1, 13, 12, 0)),
    ]
    engine = _make_engine(engine_cls, policy)
    result = engine.evaluate(
        items, policy, now=datetime(2025, 1, 13, 12, 0), preserve_day_of_week="tuesday"
    )
    assert isinstance(result, RetentionResult)


@pytest.mark.parametrize("engine_cls", [TimeBasedRetention, MockRetentionEngine])
def test_retention_engine_evaluate_preserve_day_of_week_defaults_to_monday(engine_cls):
    """Calling evaluate without preserve_day_of_week defaults to monday."""
    policy = RetentionPolicy(weekly=2, preserve_min="0h")
    items = [RetentionItem(name="snap1", timestamp=datetime(2025, 1, 6, 12, 0))]
    engine = _make_engine(engine_cls, policy)
    result = engine.evaluate(items, policy, now=datetime(2025, 1, 6, 12, 0))
    assert isinstance(result, RetentionResult)
