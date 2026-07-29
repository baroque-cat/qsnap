"""Shared test helper functions used across multiple test modules."""

from __future__ import annotations

from datetime import datetime

from qsnap.models.results import DeferredBlockcommit


def add_deferred_with_since(
    state,
    vm_name: str,
    snapshots: list[str],
    reason: str,
    since: datetime,
) -> None:
    """Add a deferred blockcommit with a specific ``since`` timestamp.

    Unlike ``InMemoryStateManager.add_deferred_blockcommit`` which always
    uses ``datetime.now()``, this helper lets tests control the ``since``
    timestamp for age-based assertions.
    """
    if vm_name not in state._state:
        state._state[vm_name] = {}
    deferred = state._state[vm_name].setdefault("deferred_operations", [])
    deferred.append(
        DeferredBlockcommit(
            snapshots=list(snapshots),
            reason=reason,
            since=since,
        )
    )
