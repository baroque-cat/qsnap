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
    """

    state_dir: str = "/var/lib/qsnap/state"
    lockfile: str | None = None
    snapshot_chain_length: int | None = 24
    target_chain_length: int | None = 168
    target_keep_generations: int | None = 2
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
    verification.  When the user explicitly sets ``verify``, the
    explicit value takes precedence.

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
    # Inherited from global → target.
    convert_parallel: int = 4
    # ``qemu-img convert -W`` flag (out-of-order writes).  Inherited
    # from global → target.
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
class VMConfig:
    """Configuration for a single VM.

    Required fields: ``name``, ``base_image``, ``snapshot_dir``.
    ``snapshot_create`` defaults to ``"always"``.
    ``snapshot_chain_length``, ``target_chain_length``, and
    ``target_keep_generations`` are count-based retention overrides
    resolved via option inheritance (global → VM → target).

    ``disks`` is an optional explicit list of disk targets (e.g.
    ``["vda", "vdb"]``).  When ``None``, Core auto-discovers all disks
    via ``virsh domblklist``.

    ``snapshot_quiesce`` enables the ``--quiesce`` flag for
    ``virsh snapshot-create-as`` (requires qemu-guest-agent).

    ``lifecycle_mode`` selects the snapshot-merge strategy:
    ``"virsh"`` (blockcommit, default) or ``"qemu-img"`` (commit).

    ``change_detection_mode`` selects the change-detection strategy
    for ``onchange`` mode: ``"allocation-size"`` (default, compares
    ``qemu-img info`` actual-size) or ``"allocation-map"`` (compares
    ``qemu-img map`` allocated regions).

    ``targets`` uses a defensive copy on construction so that
    external mutation of the original list does not affect this instance.
    """

    name: str
    base_image: Path
    snapshot_dir: Path
    snapshot_create: str = "always"
    snapshot_chain_length: int | None = None
    target_chain_length: int | None = None
    target_keep_generations: int | None = None
    snapshot_quiesce: bool = False
    lifecycle_mode: str = "virsh"
    change_detection_mode: str = "allocation-size"
    disks: list[str] | None = None
    # Deep verification controls (T2 — per-VM because disk sizes differ).
    blockcommit_deep_verify: bool = False
    targets: list[TargetConfig] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]

    def __post_init__(self) -> None:
        # Defensive copy: replace the list with a shallow copy so that
        # external mutation of the original list does not affect this
        # frozen instance's internal state.
        object.__setattr__(self, "targets", list(self.targets))
