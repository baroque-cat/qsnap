"""Immutable configuration dataclasses.

All config dataclasses are ``@dataclass(frozen=True)`` — they are constructed
once by ``ConfigFacade`` and passed down.  Modules cannot mutate config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RetentionPolicy:
    """Count-based retention policy for snapshots or backups.

    The retention engine is the same for both snapshots and targets —
    "keep newest N, remove oldest".  For snapshots, N = ``chain_length``.
    For targets (per-chain), N = ``keep_generations``.  The engine does
    not care which field it uses; Core passes the appropriate value.

    ``chain_length`` is how many items to keep in a chain before
    triggering blockcommit (snapshots) or a new FULL (targets).  When
    ``0``, the engine falls back to ``keep_generations`` as the keep
    count; when both are ``0``, all items are marked for removal.

    ``keep_generations`` is how many FULL chains to keep on targets
    (minimum 1).  Ignored for snapshot retention when ``chain_length``
    is positive.
    """

    chain_length: int = 0
    keep_generations: int = 1
    preserve_min: int = 0


@dataclass(frozen=True)
class GlobalConfig:
    """Global configuration options that serve as defaults for all VMs.

    All fields are optional with documented defaults.  Count-based
    retention defaults (``snapshot_chain_length``, ``target_chain_length``,
    ``target_keep_generations``) are resolved by ``ConfigFacade`` via
    option inheritance (global → VM → target).

    Default retention values: ``snapshot_chain_length=24`` (hourly
    snapshots, ~1 day of history), ``target_chain_length=168`` (hourly
    incrementals, ~1 week of history), ``target_keep_generations=2``
    (keep 2 FULL chains on targets).

    ``snapshot_preserve_min`` defaults to 48 — a safe floor keeping
    ~2 days of hourly snapshots uncommitted.  When preserve_min exceeds
    chain_length, the floor dominates effective retention.  Explicit
    ``snapshot_preserve_min = 0`` still disables the floor (design D13).
    """

    state_dir: str = "/var/lib/qsnap/state"
    lockfile: str | None = None
    snapshot_chain_length: int | None = 24
    target_chain_length: int | None = 168
    target_keep_generations: int | None = 2
    # Snapshot preservation floor — active by default (48 = ~2 days of
    # hourly snapshots).  Explicit 0 disables; when > chain_length, the
    # floor dominates effective retention (design D13).
    snapshot_preserve_min: int = 48
    deferred_warn_count: str = "5"
    deferred_crit_count: str = "10"
    deferred_warn_age: str = "7d"
    deferred_crit_age: str = "14d"
    # Fault-tolerance safety controls (T0/T1 — fast, ON by default;
    # T3 — heavy, OFF by default).
    auto_cleanup: bool = True
    state_backup_count: int = 2
    chain_verify_before_commit: bool = True
    chain_verify_after_commit: bool = True
    deep_check_schedule: str = "off"
    # Compress full backups (global default, overridden per-VM/target).
    compress: bool = True
    # Compression algorithm for FULL backups (compress driver on the
    # compression_type=<type>``).  ``"zstd"`` (default) is 11x faster
    # than ``"zlib"``.  Only effective when ``compress=True``; when
    # ``compress=False``, no compression is applied regardless.
    compression_type: str = "zstd"
    # ``qemu-img convert -m`` flag (parallel coroutines, range 1-8).
    convert_parallel: int = 4
    # ``qemu-img convert -W`` flag (out-of-order writes).  ``True``
    # optimizes for HDDs; ``False`` for in-order writes (some SSDs).
    convert_out_of_order: bool = True
    # Stall detection timeout for data-transfer commands (``qemu-img
    # convert``).  Duration string (e.g. ``"30m"``, ``"1h"``)
    # parsed to seconds via ``parse_duration()``.  When the output file
    # shows no growth for this duration, the process is killed.  ``"0s"``
    # disables stall detection (falls back to fixed-timeout ``run()``).
    backup_stall_timeout: str = "30m"
    # Backup retry controls (global defaults).  ``backup_retry_max`` is
    # the maximum number of attempts for a retryable operation;
    # ``backup_retry_base`` is the exponential-backoff base duration
    # (e.g. ``"2s"``).  Reused by the standalone-image conversion helpers
    # (``convert_with_retry``) in ``fork``/``restore`` — no separate
    # conversion-retry options are introduced (design D5).
    backup_retry_max: int = 3
    backup_retry_base: str = "2s"
    # FULL backup integrity verification tiers (M1/M2/M3).
    # ``full_verify_after_create``: verification after ``create_full_backup()``
    #   completes, before state recording.  ``"metadata"`` (M1 — qemu-img info
    #   + corrupt-bit check), ``"check"`` (M1 + M2 — qemu-img check),
    #   ``"compare"`` (M1 + M2 + M3 — qemu-img compare), ``"off"`` (none).
    full_verify_after_create: str = "check"
    # ``full_verify_before_delete``: verification before cascade-deletion of
    #   a FULL and its dependent incrementals.  ``"metadata"`` (M1 only),
    #   ``"check"`` (M1 + M2), ``"off"`` (M1 only — M1 is ALWAYS enforced
    #   regardless of this setting and is non-configurable).
    full_verify_before_delete: str = "check"
    # ``transaction_log``: optional absolute path to a btrbk-compatible
    #   transaction log file.  When ``None`` (default), no transaction
    #   log is written.  When set, one line per ``ActionRecord`` is
    #   appended in ``localtime type status target_url source_url parent_url``
    #   format.  Skipped in dry-run mode.
    transaction_log: str | None = None
    # Proactive free-space gate before backup transfers.
    # ``free_space_check``: ``"strict"`` (suspend target when free space is
    #   insufficient — default), ``"warn"`` (log WARNING and proceed), or
    #   ``"off"`` (no check).  ``free_space_reserve`` (bytes) is an extra
    #   safety margin added to the estimated transfer size.  ``free_space_factor``
    #   multiplies the estimate (>= 1.0) to account for compression/sparse
    #   inaccuracy (design D5/D16).
    free_space_check: str = "strict"
    free_space_reserve: int = 0
    free_space_factor: float = 1.0
    # When to create backups (global default): ``"always"`` (default —
    # always transfer backups) or ``"onchange"`` (skip backup transfer
    # when the VM disk has not changed since the last backup to that
    # target).  Inherited by VM and target levels.
    backup_create: str = "always"


@dataclass(frozen=True)
class TargetConfig:
    """A single backup target for a VM.

    All backups use the NBD bitmap strategy (dirty-block extraction via
    NBD/checkpoint, crash-consistent, backing-chained incrementals).
    ``libvirt >= 7.2`` and ``python3-libnbd`` are hard requirements.

    ``target_chain_length`` and ``target_keep_generations`` are
    count-based retention overrides resolved via option inheritance
    (global → VM → target).

    ``verify`` controls post-transfer verification.  The default is
    ``"metadata"`` (checks qcow2 format, corrupt-bit, and — for bitmap
    incrementals — backing-filename plus the dirty-size regression
    barrier).  ``"compare"`` additionally runs chain-traversing
    ``qemu-img compare`` content verification, ``"off"`` skips
    verification.  Resolution follows VM → target inheritance (the
    VM-level ``verify``, defaulting to ``"metadata"`` when absent,
    serves as the fallback for every target — no global-level key exists).

    ``compress`` controls whether backups are compressed (default
    ``True``).  Applies to FULL backups (compress driver on the
    write-side qemu-nbd) only — bitmap incrementals are always
    uncompressed.  Inherited from global → VM → target.

    ``compression_type`` selects the compression algorithm (default
    ``"zstd"`` — 11x faster than ``"zlib"``).  Only effective when
    ``compress=True``.  Inherited from global → VM → target.

    ``backup_stall_timeout`` is a duration string (e.g. ``"30m"``)
    controlling stall detection on data-transfer commands.  ``"0s"``
    disables stall detection (falls back to fixed-timeout ``run()``).
    Inherited from global → VM → target.
    """

    path: Path
    target_chain_length: int | None = None
    target_keep_generations: int | None = None
    verify: str = "metadata"
    compress: bool = True
    compression_type: str = "zstd"
    # ``qemu-img convert -m`` flag (parallel coroutines, range 1-8).
    # Inherited from global → VM → target.
    convert_parallel: int = 4
    # ``qemu-img convert -W`` flag (out-of-order writes).  Inherited
    # from global → VM → target.
    convert_out_of_order: bool = True
    backup_stall_timeout: str = "30m"
    # Backup retry controls (target-level — network reliability varies
    # per target).
    backup_retry_max: int = 3
    backup_retry_base: str = "2s"
    # When to create backups for this target: ``"always"`` (default —
    # always transfer backups) or ``"onchange"`` (skip backup transfer
    # when the VM disk has not changed since the last backup to this
    # target).  Inherited from global → VM → target.
    backup_create: str = "always"


@dataclass(frozen=True)
class DiskConfig:
    """Configuration for a single disk within a VM.

    ``target`` is the libvirt device target name (e.g. ``"vda"``,
    ``"vdb"``).  ``base_image`` is the path to the base qcow2 image for
    this disk — each disk owns its own base image and its own backing
    chain.  ``snapshot_dir`` is an optional per-disk snapshot directory
    override; when ``None``, the VM-level ``VMConfig.snapshot_dir`` is
    used as the default.
    """

    target: str
    base_image: Path
    snapshot_dir: Path | None = None


@dataclass(frozen=True)
class VMConfig:
    """Configuration for a single VM.

    Required fields: ``name`` and ``disks`` (one or more
    :class:`DiskConfig`).  Each disk carries its own ``base_image`` —
    the former VM-level ``base_image`` no longer exists (multi-disk
    refactor).  ``snapshot_dir`` is the VM-level default snapshot
    directory; individual disks may override it via
    ``DiskConfig.snapshot_dir``.

    ``snapshot_create`` defaults to ``"always"``.
    ``snapshot_chain_length``, ``target_chain_length``, and
    ``target_keep_generations`` are count-based retention overrides
    resolved via option inheritance (global → VM → target).

    ``snapshot_quiesce`` enables the ``--quiesce`` flag for
    ``virsh snapshot-create-as`` (requires qemu-guest-agent).

    ``lifecycle_mode`` selects the snapshot-merge strategy:
    ``"virsh"`` (blockcommit, default) or ``"qemu-img"`` (commit).

    ``change_detection_mode`` selects the change-detection strategy
    for ``onchange`` mode: ``"allocation-map"`` (default, compares
    ``qemu-img map`` allocated regions) or ``"allocation-size"``
    (compares ``qemu-img info`` actual-size).

    ``compress``, ``compression_type``, ``convert_parallel``,
    ``convert_out_of_order``, ``backup_stall_timeout`` are backup
    engine options inherited from ``GlobalConfig`` when absent from the
    VM-level ``[[vm]]`` section; each target inherits the VM-resolved
    value as its fallback.  ``verify`` is similar but defaults to
    ``"metadata"`` at the VM level (no global key).

    ``disks`` and ``targets`` use defensive copies on construction so
    that external mutation of the original lists does not affect this
    instance.
    """

    name: str
    disks: list[DiskConfig]
    snapshot_dir: Path | None = None
    snapshot_create: str = "always"
    snapshot_chain_length: int | None = None
    target_chain_length: int | None = None
    target_keep_generations: int | None = None
    snapshot_preserve_min: int | None = None
    snapshot_quiesce: bool = False
    lifecycle_mode: str = "virsh"
    change_detection_mode: str = "allocation-map"
    # Proactive free-space gate (inherited from global).
    free_space_check: str | None = None
    free_space_reserve: int | None = None
    free_space_factor: float | None = None
    # Deep verification controls (T2 — per-VM because disk sizes differ).
    blockcommit_deep_verify: bool = False
    # Compress full backups (VM-level override of global default).
    compress: bool = True
    # Compression algorithm for FULL backups (``"zstd"`` default,
    # ``"zlib"`` alternative).  Only effective when ``compress=True``.
    compression_type: str = "zstd"
    # ``qemu-img convert -m`` flag (parallel coroutines, range 1-8).
    # VM-level override of global default; inherited by targets.
    convert_parallel: int = 4
    # ``qemu-img convert -W`` flag (out-of-order writes).  ``True``
    # optimizes for HDDs; ``False`` for in-order writes (some SSDs).
    # VM-level override of global default; inherited by targets.
    convert_out_of_order: bool = True
    # Stall detection timeout for data-transfer commands (``qemu-img
    # convert``).  Duration string (e.g. ``"30m"``, ``"1h"``).
    # VM-level override of global default; inherited by targets.
    backup_stall_timeout: str = "30m"
    # Post-transfer verification mode: ``"metadata"`` (default, M1),
    # ``"check"`` (M1 + M2), ``"compare"`` (M1 + M2 + M3), ``"off"``.
    # Resolution follows VM → target inheritance (no global-level key).
    verify: str = "metadata"
    targets: list[TargetConfig] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]

    def __post_init__(self) -> None:
        # Defensive copies: replace the lists with shallow copies so that
        # external mutation of the original lists does not affect this
        # frozen instance's internal state.
        object.__setattr__(self, "disks", list(self.disks))
        object.__setattr__(self, "targets", list(self.targets))

    def get_disk(self, target: str) -> DiskConfig | None:
        """Return the :class:`DiskConfig` for *target*, or None if absent."""
        for disk in self.disks:
            if disk.target == target:
                return disk
        return None

    def snapshot_dir_for(self, disk: DiskConfig) -> Path | None:
        """Resolve the effective snapshot directory for *disk*.

        Returns the per-disk override when set, otherwise the VM-level
        ``snapshot_dir``.  Returns ``None`` only when neither is
        configured (a configuration error caught during validation).
        """
        if disk.snapshot_dir is not None:
            return disk.snapshot_dir
        return self.snapshot_dir
