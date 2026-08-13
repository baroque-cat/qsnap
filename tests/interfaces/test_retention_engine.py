"""Contract test: IRetentionEngine is a standalone ABC (no Core inheritance).

Design D3 purity contract: the retention engine is a pure function — no
I/O, no Core import, no side effects, no hysteresis knowledge.  All
mode/phase/cap orchestration lives in Core, never in the engine.
"""

from __future__ import annotations

import inspect
import os
from datetime import datetime, timedelta
from pathlib import Path

from qsnap.core import Core
from qsnap.interfaces.retention import IRetentionEngine
from qsnap.models.config import RetentionPolicy
from qsnap.models.results import RetentionItem, RetentionResult
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


def _fixed_items(count: int = 48) -> list[RetentionItem]:
    """A deterministic ascending-timestamp item set for purity checks."""
    base = datetime(2025, 7, 13, 10, 0)
    return [
        RetentionItem(name=f"s{i:02d}", timestamp=base + timedelta(hours=i))
        for i in range(count)
    ]


def test_retention_engine_evaluate_is_deterministic_and_side_effect_free():
    """evaluate() is a pure function: same inputs → same output, no mutation.

    Determinism is asserted by evaluating twice with identical inputs;
    side-effect-freedom by verifying the input list is not mutated and the
    engine instance holds no cross-call state that alters results.
    """
    engine = TimeBasedRetention(RetentionPolicy())
    policy = RetentionPolicy(chain_length=24, keep_generations=1, preserve_min=48)
    items = _fixed_items()
    original = list(items)

    first = engine.evaluate(items, policy, datetime(2025, 7, 14, 0, 0))
    second = engine.evaluate(items, policy, datetime(2025, 7, 14, 0, 0))

    # Deterministic: identical inputs produce identical results.
    assert first == second
    assert isinstance(first, RetentionResult)
    assert len(first.keep) == 24
    assert len(first.remove) == 24
    # No side effects: the caller's input list is untouched.
    assert items == original
    # No hidden state: the second call (same instance) yields the same result.
    assert second.keep == first.keep
    assert second.remove == first.remove


def test_retention_engine_performs_no_io(monkeypatch):
    """evaluate() never touches the filesystem (design D3: zero I/O)."""

    def _forbid(*args, **kwargs):
        raise AssertionError("retention engine must not perform any I/O")

    monkeypatch.setattr("builtins.open", _forbid)
    monkeypatch.setattr(Path, "exists", _forbid)
    monkeypatch.setattr(Path, "read_text", _forbid)
    monkeypatch.setattr(os, "stat", _forbid)

    engine = TimeBasedRetention(RetentionPolicy())
    policy = RetentionPolicy(chain_length=24, keep_generations=1, preserve_min=48)
    result = engine.evaluate(_fixed_items(), policy, datetime(2025, 7, 14, 0, 0))

    assert isinstance(result, RetentionResult)
    assert len(result.keep) == 24
    assert len(result.remove) == 24


def test_retention_engine_has_no_hysteresis_knowledge():
    """Hysteresis orchestration must NOT live in the engine (design D3).

    The engine stays a pure count-based function: no retention-mode
    branching, no collapse phase, no thresholds/floors/caps.  Those live in
    Core (``qsnap.core``), which is deliberately absent from the engine's
    source.
    """
    for module in (TimeBasedRetention, IRetentionEngine):
        source = inspect.getsource(module).lower()
        assert "qsnap.core" not in source, f"{module.__name__} must not import Core"
        for forbidden in (
            "hysteresis",
            "collapse",
            "threshold",
            "floor",
            "max_commits",
            "retention_mode",
            "cap",
        ):
            assert forbidden not in source, (
                f"{module.__name__} must stay mode/phase/cap-agnostic, "
                f"but its source mentions {forbidden!r}"
            )

    # Behavioral pin: with chain_length=24 the engine keeps exactly the newest
    # 24 — pure count-based, no phase/mode inputs in its API.
    engine = TimeBasedRetention(RetentionPolicy())
    policy = RetentionPolicy(chain_length=24, keep_generations=1, preserve_min=48)
    result = engine.evaluate(_fixed_items(), policy, datetime(2025, 7, 14, 0, 0))
    assert result.keep == [f"s{i:02d}" for i in range(24, 48)]
    assert result.remove == [f"s{i:02d}" for i in range(0, 24)]
