"""Models package — immutable dataclasses for results and configuration."""

from __future__ import annotations

from qsnap.models.config import (
    GlobalConfig,
    RetentionPolicy,
    TargetConfig,
    VMConfig,
)
from qsnap.models.results import (
    BackupInfo,
    BackupResult,
    ChangeResult,
    CheckResult,
    CommitResult,
    NbdExtent,
    NbdResult,
    RetentionItem,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
    SnapshotResult,
    SnapshotSpec,
)

__all__ = [
    "BackupInfo",
    "BackupResult",
    "ChangeResult",
    "CheckResult",
    "CommitResult",
    "GlobalConfig",
    "NbdExtent",
    "NbdResult",
    "RetentionItem",
    "RetentionPolicy",
    "RetentionResult",
    "ShellResult",
    "SnapshotInfo",
    "SnapshotResult",
    "SnapshotSpec",
    "TargetConfig",
    "VMConfig",
]
