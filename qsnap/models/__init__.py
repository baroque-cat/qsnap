"""Models package — immutable dataclasses for results and configuration."""

from __future__ import annotations

from qsnap.models.config import (
    GlobalConfig,
    RetentionPolicy,
    TargetConfig,
    VMConfig,
)
from qsnap.models.results import (
    BackupResult,
    ChangeResult,
    CommitResult,
    RetentionItem,
    RetentionResult,
    ShellResult,
    SnapshotInfo,
    SnapshotResult,
)

__all__ = [
    "BackupResult",
    "ChangeResult",
    "CommitResult",
    "GlobalConfig",
    "RetentionItem",
    "RetentionPolicy",
    "RetentionResult",
    "ShellResult",
    "SnapshotInfo",
    "SnapshotResult",
    "TargetConfig",
    "VMConfig",
]
