"""Immutable result dataclasses for all fallible operations.

Every fallible operation returns a result type, never raising exceptions for
expected failures.  Each result carries a ``success`` boolean and an ``error``
string that is non-None iff ``success`` is False.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Shell ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShellResult:
    """Outcome of a subprocess command execution."""

    success: bool
    stdout: str
    stderr: str
    returncode: int
    error: str | None


# ── Snapshot ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SnapshotResult:
    """Outcome of a snapshot creation operation."""

    success: bool
    name: str
    path: Path
    new_allocation: int
    error: str | None


# ── Backup ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BackupResult:
    """Outcome of a backup transfer operation."""

    success: bool
    snapshot_name: str
    source_path: Path
    target_path: Path
    bytes_transferred: int
    error: str | None


# ── Commit (blockcommit) ─────────────────────────────────────────────────


@dataclass(frozen=True)
class CommitResult:
    """Outcome of a ``virsh blockcommit`` operation."""

    success: bool
    committed_snapshot: str
    error: str | None


# ── Change detection ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChangeResult:
    """Outcome of a change-detection check.

    ``has_changed`` is True when the VM disk allocation has grown since the
    last recorded value.
    """

    has_changed: bool
    last_allocation: int
    current_allocation: int


# ── Snapshot info (state record) ─────────────────────────────────────────


@dataclass(frozen=True)
class SnapshotInfo:
    """A recorded snapshot in persistent state.

    Used by ``IStateManager`` to record and retrieve snapshot metadata
    across pipeline runs.
    """

    name: str
    path: Path
    timestamp: datetime
    allocation: int


# ── Retention ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetentionItem:
    """A single item (snapshot or backup) evaluated by the retention engine.

    The retention engine is a pure function: it consumes a list of
    ``RetentionItem`` objects and produces a ``RetentionResult`` containing
    the *names* of items to keep and remove.
    """

    name: str
    timestamp: datetime


@dataclass(frozen=True)
class RetentionResult:
    """Output of retention policy evaluation.

    ``keep`` and ``remove`` contain the *names* (identifiers) of items that
    should be preserved or removed, respectively.
    """

    keep: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
