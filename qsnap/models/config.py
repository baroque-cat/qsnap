"""Immutable configuration dataclasses.

All config dataclasses are ``@dataclass(frozen=True)`` — they are constructed
once by ``ConfigFacade`` and passed down.  Modules cannot mutate config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RetentionPolicy:
    """Retention policy for snapshots or backups.

    Each field is a count of items to preserve for that time bucket.
    ``preserve_min`` is a duration string (e.g. ``"6h"``, ``"2d"``) or
    ``"all"`` meaning keep everything.
    """

    hourly: int = 0
    daily: int = 0
    weekly: int = 0
    monthly: int = 0
    yearly: int = 0
    preserve_min: str = "all"


@dataclass(frozen=True)
class GlobalConfig:
    """Global configuration options that serve as defaults for all VMs.

    All fields are optional with documented defaults.  ``snapshot_preserve``
    and ``target_preserve`` are raw retention-policy strings (e.g.
    ``"24h 2d"``) resolved by ``ConfigFacade`` via option inheritance.
    """

    timestamp_format: str = "long"
    preserve_day_of_week: str = "monday"
    state_dir: str = "/var/lib/qsnap/state"
    lockfile: str | None = None
    snapshot_preserve: str | None = None
    target_preserve: str | None = None
    snapshot_preserve_min: str | None = None
    target_preserve_min: str | None = None
    rate_limit: str = "no"
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


@dataclass(frozen=True)
class TargetConfig:
    """A single backup target for a VM.

    ``incremental_mode`` selects the backup strategy: ``"file-copy"``
    (whole-file copy, current behaviour) or ``"bitmap"`` (dirty-block
    extraction via checkpoint).

    ``target_preserve`` is a raw retention-policy string resolved via
    option inheritance (global → VM → target).

    ``verify`` controls post-transfer verification: ``"metadata"``
    (default) checks qcow2 format and virtual-size match, ``"full"``
    additionally runs ``qemu-img compare``, ``"off"`` skips verification.

    ``compress`` controls whether full backups are compressed (default
    ``True``).  Inherited from global → VM → target.

    ``copy_base`` controls whether the base image is copied to the
    target on first backup (default ``False`` — first backup is always
    a FULL via ``qemu-img convert``).
    """

    path: Path
    incremental: bool = True
    incremental_mode: str = "file-copy"
    target_preserve: str | None = None
    verify: str = "metadata"
    target_preserve_min: str | None = None
    compress: bool = True
    copy_base: bool = False
    rate_limit: str = "no"
    # Backup retry controls (target-level — network reliability varies
    # per target).
    backup_retry_max: int = 3
    backup_retry_base: str = "2s"


@dataclass(frozen=True)
class VMConfig:
    """Configuration for a single VM.

    Required fields: ``name``, ``base_image``, ``snapshot_dir``.
    ``snapshot_create`` defaults to ``"always"``.
    ``snapshot_preserve`` and ``target_preserve`` are raw retention-policy
    strings resolved via option inheritance.

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
    snapshot_preserve: str | None = None
    target_preserve: str | None = None
    snapshot_preserve_min: str | None = None
    target_preserve_min: str | None = None
    snapshot_quiesce: bool = False
    lifecycle_mode: str = "virsh"
    change_detection_mode: str = "allocation-size"
    disks: list[str] | None = None
    # Deep verification controls (T2 — per-VM because disk sizes differ).
    blockcommit_deep_verify: bool = False
    snapshot_deep_verify: bool = False
    targets: list[TargetConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Defensive copy: replace the list with a shallow copy so that
        # external mutation of the original list does not affect this
        # frozen instance's internal state.
        object.__setattr__(self, "targets", list(self.targets))
