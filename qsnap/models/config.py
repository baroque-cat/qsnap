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


@dataclass(frozen=True)
class TargetConfig:
    """A single backup target for a VM.

    ``target_preserve`` is a raw retention-policy string resolved via
    option inheritance (global → VM → target).
    """

    path: Path
    incremental: bool = True
    target_preserve: str | None = None


@dataclass(frozen=True)
class VMConfig:
    """Configuration for a single VM.

    Required fields: ``name``, ``base_image``, ``snapshot_dir``.
    ``snapshot_create`` defaults to ``"always"``.
    ``snapshot_preserve`` and ``target_preserve`` are raw retention-policy
    strings resolved via option inheritance.

    ``targets`` uses a defensive copy on construction so that external
    mutation of the original list does not affect this instance.
    """

    name: str
    base_image: Path
    snapshot_dir: Path
    snapshot_create: str = "always"
    snapshot_preserve: str | None = None
    target_preserve: str | None = None
    targets: list[TargetConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Defensive copy: replace the list with a shallow copy so that
        # external mutation of the original list does not affect this
        # frozen instance's internal state.
        object.__setattr__(self, "targets", list(self.targets))
