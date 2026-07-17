"""Immutable result dataclasses for all fallible operations.

Every fallible operation returns a result type, never raising exceptions for
expected failures.  Each result carries a ``success`` boolean and an ``error``
string that is non-None iff ``success`` is False.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
    content_hash: str | None = None


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

    ``changed`` is True when the VM disk allocation has grown since the
    last recorded value.
    """

    changed: bool
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
    content_hash: str | None = None


# ── Full backup info (state record) ───────────────────────────────────────


@dataclass(frozen=True)
class FullBackupInfo:
    """A recorded full (anchor) backup in persistent state.

    Used by ``IStateManager`` to track when the last full backup was
    created for a given target, so that incremental backups can rebase
    to the correct anchor.

    ``bucket_level`` records which retention bucket triggered the FULL
    creation (e.g. ``"yearly"``, ``"monthly"``, ``"weekly"``).
    """

    name: str
    path: Path
    timestamp: datetime
    bucket_level: str = "monthly"


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


# ── Deferred operations ──────────────────────────────────────────────────


@dataclass(frozen=True)
class DeferredBlockcommit:
    """A deferred blockcommit operation blocked by MAC (AppArmor/SELinux).

    Stored in ``IStateManager`` and retried when the VM is shut off.
    ``last_warned_at`` tracks the last time a warning was logged for this
    deferred operation (backward-compatible: ``None`` for old state files).
    """

    snapshots: list[str]
    reason: str
    since: datetime
    last_warned_at: datetime | None = None


@dataclass(frozen=True)
class DeferredSummary:
    """Per-VM summary of deferred blockcommit operations.

    ``snapshot_count`` is the total number of snapshots across all deferred
    operations for this VM.  ``reason`` is the MAC reason (apparmor/selinux)
    from the oldest deferred entry.  ``age`` is the age of the oldest
    deferred operation.  ``since`` is the timestamp of the oldest entry.
    """

    vm_name: str
    snapshot_count: int
    reason: str
    age: timedelta
    since: datetime


# ── Check (backing-chain integrity) ───────────────────────────────────────


@dataclass(frozen=True)
class ChainVerifyResult:
    """Outcome of a backing-chain integrity verification.

    ``success`` is True when the chain is intact (all files exist, all
    are qcow2, references are consistent, no cycles).  ``error`` is
    non-None when ``success`` is False, describing the problem.
    ``broken_file`` is the path of the first problematic file, or None
    when the verification passes or the error is not file-specific.
    """

    success: bool
    error: str | None
    broken_file: Path | None = None


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a backing-chain integrity check for a single VM.

    ``status`` is ``"ok"`` when the chain is intact, ``"broken"`` when one
    or more snapshots in the chain are missing or corrupt.
    ``broken_snapshots`` lists the names of problematic snapshots (empty
    when ``status`` is ``"ok"``).

    Deferred fields: ``deferred_count`` is the number of pending deferred
    blockcommits, ``deferred_reason`` is the MAC reason (apparmor/selinux),
    ``deferred_age`` is the age of the oldest deferred operation (human-
    readable string), ``deferred_severity`` is ``"ok"``, ``"warning"``, or
    ``"critical"``, and ``remediation`` contains suggested fix commands.
    """

    vm_name: str
    status: str
    broken_snapshots: list[str] = field(default_factory=list)
    deferred_count: int = 0
    deferred_reason: str | None = None
    deferred_age: str | None = None
    deferred_severity: str = "ok"
    remediation: str | None = None


# ── Restore ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RestoreResult:
    """Outcome of a restore operation.

    ``chain_files`` lists all copied file paths (base-to-top order).
    ``restored_path`` is the target directory.
    """

    success: bool
    snapshot_name: str
    restored_path: Path
    chain_files: list[Path]
    error: str | None


# ── Schedule (print_schedule) ─────────────────────────────────────────────


@dataclass(frozen=True)
class ScheduleResult:
    """Retention schedule for a single VM.

    ``snapshots`` is the snapshot retention result.
    ``backups`` maps target path strings to per-target backup retention.
    """

    snapshots: RetentionResult
    backups: dict[str, RetentionResult] = field(default_factory=dict)  # type: ignore[unknown-variable-type]


# ── State consistency check ─────────────────────────────────────────────


@dataclass(frozen=True)
class StateCheckResult:
    """Outcome of a state consistency check for a single VM.

    Reports phantom entries (state records pointing to non-existent files)
    and orphan files (files on disk not recorded in state).  The check is
    read-only — it never deletes files or state entries.

    ``status`` is ``"ok"`` when no issues found, or a combination of
    flags: ``"stale_snapshots"``, ``"stale_fulls"``, ``"stale_deps"``,
    ``"corrupt_state"``.
    """

    vm_name: str
    status: str  # "ok" or combination of flags joined by ":"
    phantom_snapshots: list[str] = field(default_factory=list)
    phantom_fulls: list[str] = field(default_factory=list)
    stale_deps: list[str] = field(default_factory=list)
    corrupt_files: list[str] = field(default_factory=list)
