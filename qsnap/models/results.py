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


# ── NBD ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NbdExtent:
    """A contiguous disk region reported by an NBD block-status query.

    ``offset`` and ``length`` are bytes.  ``data`` is True when the
    region carries data relevant to the queried meta-context (allocated
    for ``base:allocation``, dirty for ``qemu:dirty-bitmap:<name>``) and
    False for hole/zero/clean regions.
    """

    offset: int
    length: int
    data: bool


@dataclass(frozen=True)
class NbdResult:
    """Outcome of an NBD client operation.

    ``payload`` carries the operation-specific value on success
    (``dict[str, list[NbdExtent]]`` mapping meta-context name to
    extents for ``block_status``, ``bytes`` for ``pread``, ``None``
    otherwise).  ``error`` is non-None iff ``success`` is False; error
    strings are normalized so transient conditions map to the existing
    retryable patterns ("eof", "timed out", "broken pipe",
    "connection refused").
    """

    success: bool
    payload: object | None
    error: str | None


# ── Snapshot ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SnapshotResult:
    """Outcome of a snapshot creation operation.

    ``disk`` identifies the disk target (e.g. ``"vda"``) the snapshot
    belongs to.  Core tags simulated dry-run snapshot results per disk
    (design D1 of fix-dry-run-predictions) so downstream prediction
    channels can attribute each snapshot to its disk.  ``None`` when the
    caller has no disk context.
    """

    success: bool
    name: str
    path: Path
    new_allocation: int
    error: str | None
    disk: str | None = None


@dataclass(frozen=True)
class SnapshotSpec:
    """Specification for a single disk in a multi-disk snapshot batch.

    ``disk`` is the disk target (e.g. ``"vda"``), ``name`` is the
    per-disk snapshot name (e.g. ``"vm.20260101T000000Z_vda_abc123"``),
    and ``path`` is the absolute path to the qcow2 overlay file to
    create (design D8).
    """

    disk: str
    name: str
    path: Path


# ── Backup ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BackupResult:
    """Outcome of a backup transfer operation.

    ``disk`` identifies the disk target (e.g. ``"vda"``) the transferred
    snapshot belongs to — it mirrors ``SnapshotInfo.disk`` of the source
    snapshot so audit trails and summaries can attribute the transfer to
    a specific disk.  ``None`` only when the source snapshot carries no
    disk information.

    ``checkpoint`` carries the exact libvirt checkpoint name created
    during the operation — populated by ``create_full_backup`` on the
    running-VM path and ``None`` when no checkpoint was created
    (stopped-VM path or plain transfers).

    ``kind`` records the backup kind — ``"full"``, ``"delta"``, or
    ``"recovered_delta"`` — for audit trails and summary rendering.
    Defaults to ``"delta"`` for backward compatibility with callers
    that do not yet set it.

    ``recovery`` is ``True`` when this result was produced by the
    bitmap-loss recovery path (FULL fallback after a dead-bitmap
    checkpoint).  Core uses it to retire the superseded generation
    immediately regardless of ``keep_generations`` (per-chain-retention
    spec, recover-lost-checkpoint-bitmaps design D8).
    """

    success: bool
    snapshot_name: str
    source_path: Path
    target_path: Path
    bytes_transferred: int
    error: str | None
    duration: float = 0.0
    disk: str | None = None
    checkpoint: str | None = None
    deferred: bool = False
    kind: str = "delta"
    recovery: bool = False


@dataclass(frozen=True)
class BaselineAssessment:
    """Read-only baseline assessment for dry-run parity and recovery gating.

    Returned by :meth:`IBackupProvider.assess_baseline`.  Provides the
    health status of the newest checkpoint's dirty bitmap, the gate
    outcome (when the bitmap is dead), and a size estimate for the
    backup that will be produced.

    ``status`` is one of ``"no_checkpoint"``, ``"healthy"``, ``"dead"``,
    or ``"unknown"``.  ``newest_checkpoint`` is the checkpoint name used
    for the assessment, or ``None`` when no checkpoint exists.
    ``gates_passed`` is ``True`` when all recovery gates (G1–G3) pass
    (only meaningful when ``status == "dead"``; ``False`` otherwise).
    ``failed_gate_reason`` names the first failed gate (e.g. ``"G1"``),
    or is ``None`` when all gates pass or the status is not ``"dead"``.
    ``size_estimate`` is the estimated transfer size in bytes, or
    ``None`` when undecidable.
    """

    status: str
    newest_checkpoint: str | None = None
    gates_passed: bool = False
    failed_gate_reason: str | None = None
    size_estimate: int | None = None


# ── Commit (blockcommit) ─────────────────────────────────────────────────


@dataclass(frozen=True)
class CommitResult:
    """Outcome of a ``virsh blockcommit`` operation.

    ``outcome`` is ``"success"``, ``"failure"``, or ``"unknown"``.
    ``"unknown"`` denotes an indeterminate outcome (command timed out or
    was killed); the real state of the chain is unknown and MUST be
    reconciled.  ``success=True`` implies ``outcome="success"`` — the
    invariant is enforced in ``__post_init__`` (result-types spec).
    Defaults to ``"failure"`` so every existing constructor call keeps
    working unchanged.
    """

    success: bool
    committed_snapshot: str
    error: str | None
    outcome: str = "failure"

    def __post_init__(self) -> None:
        # result-types spec: "success=True SHALL imply outcome='success'".
        if self.success and self.outcome != "success":
            raise ValueError(
                f"CommitResult invariant violated: success=True requires "
                f"outcome='success', got outcome={self.outcome!r}"
            )


# ── Change detection ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChangeResult:
    """Outcome of a change-detection check.

    ``changed`` is True when the VM disk allocation has grown since the
    last recorded value.  ``disk`` identifies the disk target (e.g.
    ``"vda"``) this result applies to — change detection is per-disk.
    """

    changed: bool
    last_allocation: int
    current_allocation: int
    disk: str


# ── Snapshot info (state record) ─────────────────────────────────────────


@dataclass(frozen=True)
class SnapshotInfo:
    """A recorded snapshot in persistent state.

    Used by ``IStateManager`` to record and retrieve snapshot metadata
    across pipeline runs.  ``disk`` identifies the disk target (e.g.
    ``"vda"``) the snapshot belongs to — snapshots of different disks
    within the same VM are differentiated by this field.
    """

    name: str
    path: Path
    timestamp: datetime
    allocation: int
    disk: str


# ── Backup info (target-world model) ──────────────────────────────────────


@dataclass(frozen=True)
class BackupInfo:
    """A backup file on a target, discovered via ``IBackupProvider.list()``."""

    name: str
    path: Path
    timestamp: datetime
    disk: str
    is_full: bool = False


# ── Full backup info (state record) ───────────────────────────────────────


@dataclass(frozen=True)
class FullBackupInfo:
    """A recorded full (anchor) backup in persistent state.

    Used by ``IStateManager`` to track when the last full backup was
    created for a given target, so that incremental backups can rebase
    to the correct anchor.  ``disk`` identifies the disk target (e.g.
    ``"vda"``) this FULL anchors — each disk owns its own FULL chain.
    """

    name: str
    path: Path
    timestamp: datetime
    disk: str


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

    keep: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
    remove: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]


# ── Commit intent journal ────────────────────────────────────────────────


@dataclass(frozen=True)
class CommitIntent:
    """A record of an in-progress commit operation.

    Written before every irreversible commit and cleared only after the
    outcome is finalized.  Provides crash-window observability and zombie-
    job attribution.

    ``disk`` is the libvirt target device name (e.g. ``"vda"``).
    ``snapshots`` is the merge set, oldest first.
    ``base`` is the absolute path to the backing file receiving the merge.
    ``started_ts`` is an opaque timestamp string (``YYYYMMDDTHHMMSS``) set
    by the caller.
    """

    disk: str
    snapshots: list[str]
    base: str
    started_ts: str


# ── Deferred operations ──────────────────────────────────────────────────


@dataclass(frozen=True)
class DeferredBlockcommit:
    """A deferred blockcommit operation blocked by MAC (AppArmor/SELinux).

    Stored in ``IStateManager`` and retried when the VM is shut off.
    ``disk`` identifies the disk target (e.g. ``"vda"``) whose blockcommit
    was deferred — the deferred queue is per-disk.  ``last_warned_at``
    tracks the last time a warning was logged for this deferred operation
    (backward-compatible: ``None`` for old state files).
    """

    snapshots: list[str]
    reason: str
    since: datetime
    disk: str
    last_warned_at: datetime | None = None


@dataclass(frozen=True)
class DeferredSummary:
    """Per-VM per-disk summary of deferred blockcommit operations.

    ``disk`` identifies the disk target the deferred entries belong to
    (multi-disk refactor).  ``snapshot_count`` is the total number of
    snapshots across all deferred operations for this VM+disk.
    ``reason`` is the MAC reason (apparmor/selinux) from the oldest
    deferred entry.  ``age`` is the age of the oldest deferred
    operation.  ``since`` is the timestamp of the oldest entry.
    """

    vm_name: str
    disk: str
    snapshot_count: int
    reason: str
    age: timedelta
    since: datetime


# ── Check (backing-chain integrity) ───────────────────────────────────────


@dataclass(frozen=True)
class ChainScanResult:
    """Detailed outcome of a backing-chain scan via ``qemu-img info
    --backing-chain``.

    Consolidates 4 independent chain-verification implementations into a
    single reusable result.  ``paths`` collects every file path found in
    the chain.  ``broken_files`` lists files with issues (missing,
    non-qcow2, cycle, backing-filename mismatch).  ``success`` is False
    only when the ``qemu-img info`` command itself fails or JSON parsing
    fails — individual file issues are reported in ``broken_files`` with
    ``success`` remaining True (detection succeeded, chain has issues).
    """

    paths: set[str]
    broken_files: list[str]
    success: bool
    error: str | None


@dataclass(frozen=True)
class ChainVerifyResult:
    """Outcome of a backing-chain integrity verification.

    ``success`` is True when the chain is intact (all files exist, all
    are qcow2, references are consistent, no cycles).  ``error`` is
    non-None when ``success`` is False, describing the problem.
    ``broken_file`` is the path of the first problematic file, or None
    when the verification passes or the error is not file-specific.
    ``disk`` identifies the disk target whose chain was verified, when
    known (None in contexts without a specific disk).
    ``chain_length`` is the measured number of files in the backing chain
    (additive, populated from the ``qemu-img info --backing-chain`` scan),
    or None when the scan failed or no length was measured — Core reuses
    it as the pre-commit baseline instead of a second full chain walk.
    """

    success: bool
    error: str | None
    broken_file: Path | None = None
    disk: str | None = None
    chain_length: int | None = None


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
    broken_snapshots: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
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
    ``restored_path`` is the target directory.  ``disk`` identifies the
    disk target (e.g. ``"vda"``) that was restored, when known (None when
    the restore source does not map to a specific disk).
    """

    success: bool
    snapshot_name: str
    restored_path: Path
    chain_files: list[Path]
    error: str | None
    disk: str | None = None


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
    ``"corrupt_state"``, ``"orphan_checkpoints"``.

    ``orphan_checkpoints`` lists libvirt checkpoints (named
    ``qsnap-{target_hash}-{snapshot}``) whose ``target_hash`` does not
    match any configured target for this VM.  Detection is read-only —
    no checkpoints are deleted automatically.
    """

    vm_name: str
    status: str  # "ok" or combination of flags joined by ":"
    phantom_snapshots: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
    phantom_fulls: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
    stale_deps: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
    corrupt_files: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
    orphan_checkpoints: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
    broken_chains: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]


# ── State reconciliation ────────────────────────────────────────────────


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of state reconciliation for a single VM.

    Reports counts of items actively fixed by ``Core.reconcile()``.
    Unlike :class:`StateCheckResult` (read-only), reconcile supplements
    state from disk+XML reality (recording untracked snapshots and
    backups), refreshes stale domain XML, deletes truly orphan files
    (on disk but NOT in domain XML), and deletes orphaned checkpoints.

    New fields (D8):
    - ``state_supplemented``: count of snapshots/backups recorded into
      state that were present on disk but missing from state JSON.
    - ``xml_refreshed``: True if stale domain XML was refreshed.
    - ``allocation_fixed``: True if ``last_allocation`` was corrected.
    """

    vm_name: str
    phantom_snapshots_removed: int = 0
    phantom_fulls_removed: int = 0
    stale_deps_removed: int = 0
    baselines_cleared: int = 0
    orphan_checkpoints_deleted: int = 0
    orphan_files_removed: int = 0
    state_supplemented: int = 0
    xml_refreshed: bool = False
    allocation_fixed: bool = False
    errors: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
    broken_chains: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]


# ── Action audit trail ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ActionRecord:
    """A single pipeline action for the audit trail / summary table.

    Accumulated by Core during ``_run_pipeline()`` in ``self._actions``
    and attached to :class:`PipelineResult` at the end of the run.
    Consumed by the CLI summary formatter
    (:func:`qsnap.cli.summary.format_summary`) and the optional
    transaction log writer (:class:`qsnap.utils.transaction.TransactionWriter`).

    ``action`` is one of ``"snapshot_create"``, ``"snapshot_delete"``,
    ``"backup_transfer"``, ``"backup_full"``, ``"backup_delete"``,
    ``"blockcommit"``, ``"error"``.  ``"blockcommit"`` is
    prediction-only: it appears in the dry-run ``predictions`` channel
    of ``PipelineResult`` (one entry per disk whose overlays would be
    merged) and is never recorded for real runs.  ``size`` is bytes
    transferred/created (0 for deletions).  ``duration`` is seconds
    elapsed (0.0 when not measured).  ``error`` is non-None iff
    ``action == "error"``.

    ``disk`` identifies the disk target (e.g. ``"vda"``) the action
    applies to, so multi-disk VMs produce per-disk audit rows.  It is
    ``None`` for VM-level actions (e.g. pipeline ``"error"`` records
    that are not attributable to a single disk).
    """

    action: str
    vm_name: str
    name: str
    path: Path
    size: int = 0
    duration: float = 0.0
    error: str | None = None
    disk: str | None = None
    target: str | None = None
    kind: str = "delta"
