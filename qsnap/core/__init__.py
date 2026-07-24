"""Core orchestrator — pipeline runner, dependency injection host.

Core is the only coordinator.  Modules do not call each other.  Core
invokes them in sequence via their ABC interfaces.

Constructor receives ``IConfigFacade``, ``IVMModuleFactory``,
``IStateManager``, and ``IShell`` via DI.  No global state, no hidden
imports.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from xml.etree import ElementTree as ET

from qsnap.interfaces.backup import IBackupProvider
from qsnap.interfaces.config import IConfigFacade
from qsnap.interfaces.factory import IVMModuleFactory
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import RetentionPolicy, TargetConfig, VMConfig
from qsnap.models.results import (
    ActionRecord,
    BackupResult,
    ChainVerifyResult,
    CheckResult,
    DeferredBlockcommit,
    DeferredSummary,
    FullBackupInfo,
    RestoreResult,
    RetentionItem,
    RetentionResult,
    ScheduleResult,
    SnapshotInfo,
    SnapshotResult,
    StateCheckResult,
)
from qsnap.retention.time_based import parse_duration, parse_stall_timeout
from qsnap.utils.nbd import get_first_disk_target, is_vm_running, write_backup_xml
from qsnap.utils.nbd_client import MISSING_LIBNBD_ERROR, is_libnbd_available
from qsnap.utils.parsing import parse_domblklist_disks, parse_domblklist_path
from qsnap.utils.retry import compute_backoff, is_retryable, parse_retry_duration
from qsnap.utils.time import format_snapshot_timestamp
from qsnap.utils.transaction import TransactionWriter
from qsnap.utils.verification import verify_full_backup

logger = logging.getLogger(__name__)


# ── Pipeline result types ────────────────────────────────────────────────


@dataclass(frozen=True)
class VMRunResult:
    """Per-VM pipeline execution result."""

    vm_name: str
    success: bool
    error: str | None = None
    backup_failed: bool = False


@dataclass(frozen=True)
class PipelineResult:
    """Aggregate pipeline result for all processed VMs."""

    results: list[VMRunResult] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
    actions: list[ActionRecord] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
    dry_run: bool = False
    config_path: str | None = None

    @property
    def success(self) -> bool:
        """True iff every VM succeeded."""
        return all(r.success for r in self.results)


@dataclass(frozen=True)
class _CommitPlan:
    """Adaptive blockcommit plan (design D2) — module-private.

    Produced by :meth:`Core._plan_blockcommit`.  ``committable`` holds
    snapshots (oldest first) that are safe to commit NOW with
    ``effective_mode``; ``deferrable`` holds snapshots that must wait
    (active layer / XML-referenced tip / whole set when unsafe).
    """

    committable: list[SnapshotInfo]
    deferrable: list[SnapshotInfo]
    effective_mode: str | None  # "virsh" | "qemu-img" | None (nothing committable)
    defer_reason: str | None  # "vm_running" | "active_layer" | None


def _same_file(path: Path, other: str | None) -> bool:
    """True when *other* refers to the same file as *path* (symlink-safe)."""
    if other is None:
        return False
    return os.path.realpath(str(path)) == os.path.realpath(other)


# ── Core ─────────────────────────────────────────────────────────────────


class Core:
    """Pipeline runner and dependency injection host.

    Core owns the execution order.  Modules never know which step they
    are; Core owns the sequence.
    """

    def __init__(
        self,
        config: IConfigFacade,
        factory: IVMModuleFactory,
        state: IStateManager,
        shell: IShell,
    ) -> None:
        self._config = config
        self._factory = factory
        self._state = state
        self._shell = shell
        self._dry_run = False
        self._preserve_snapshots = False
        self._preserve_backups = False
        # Action audit trail — accumulated during _run_pipeline(), cleared
        # at the start of each run (design D1: ephemeral, single-run scope).
        self._actions: list[ActionRecord] = []

    # ── properties ─────────────────────────────────────────────────────

    @property
    def config(self) -> IConfigFacade:
        """Public read-only access to the config facade."""
        return self._config

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @dry_run.setter
    def dry_run(self, value: bool) -> None:
        self._dry_run = value

    @property
    def preserve_snapshots(self) -> bool:
        return self._preserve_snapshots

    @preserve_snapshots.setter
    def preserve_snapshots(self, value: bool) -> None:
        self._preserve_snapshots = value

    @property
    def preserve_backups(self) -> bool:
        return self._preserve_backups

    @preserve_backups.setter
    def preserve_backups(self, value: bool) -> None:
        self._preserve_backups = value

    # ── public API ─────────────────────────────────────────────────────

    def run(self, vm_filter: str | None = None) -> PipelineResult:
        """Execute the full pipeline for all (filtered) VMs."""
        return self._run_pipeline(vm_filter, self._execute_pipeline)

    def snapshot(self, vm_filter: str | None = None) -> PipelineResult:
        """Execute only snapshot steps (1-4). No backup steps."""
        return self._run_pipeline(vm_filter, self._execute_snapshot_steps)

    def backup(self, vm_filter: str | None = None) -> PipelineResult:
        """Execute only backup steps (5). No snapshot steps."""
        return self._run_pipeline(vm_filter, self._execute_backup_steps)

    def prune(self, vm_filter: str | None = None) -> PipelineResult:
        """Execute only retention and lifecycle cleanup for snapshots and backups."""
        return self._run_pipeline(vm_filter, self._execute_prune_steps)

    # ── informational commands ─────────────────────────────────────────

    def list_snapshots(self, vm_filter: str | None = None) -> dict[str, list[SnapshotInfo]]:
        """Return all recorded snapshots per VM, sorted ascending by timestamp."""
        vms = self._filter_vms(vm_filter)
        results: dict[str, list[SnapshotInfo]] = {}
        for vm in vms:
            snapshots = self._state.get_snapshots(vm.name)
            results[vm.name] = sorted(snapshots, key=lambda s: s.timestamp)
        return results

    def list_backups(self, vm_filter: str | None = None) -> dict[str, list[SnapshotInfo]]:
        """Return all backups per VM (across all targets), sorted ascending."""
        vms = self._filter_vms(vm_filter)
        results: dict[str, list[SnapshotInfo]] = {}
        for vm in vms:
            all_backups: list[SnapshotInfo] = []
            for target in vm.targets:
                provider = self._factory.create_backup_provider(vm, target)
                all_backups.extend(provider.list(target))
            results[vm.name] = sorted(all_backups, key=lambda b: b.timestamp)
        return results

    def list_config(self) -> list[VMConfig]:
        """Return all VM configurations from the config facade."""
        return self._config.get_vms()

    def list_latest(self, vm_filter: str | None = None) -> dict[str, SnapshotInfo | None]:
        """Return the most recent snapshot per VM (or ``None`` if none)."""
        vms = self._filter_vms(vm_filter)
        results: dict[str, SnapshotInfo | None] = {}
        for vm in vms:
            snapshots = self._state.get_snapshots(vm.name)
            if not snapshots:
                results[vm.name] = None
            else:
                results[vm.name] = max(snapshots, key=lambda s: s.timestamp)
        return results

    def list_deferred(self, vm_filter: str | None = None) -> list[DeferredSummary]:
        """Return per-VM summaries of deferred blockcommit operations.

        Each summary includes the VM name, total snapshot count across all
        deferred operations, the reason from the oldest entry, the age of
        the oldest deferred operation, and its ``since`` timestamp.
        """
        vms = self._filter_vms(vm_filter)
        summaries: list[DeferredSummary] = []
        now = datetime.now()
        for vm in vms:
            deferred = self._state.get_deferred_operations(vm.name)
            if not deferred:
                continue
            oldest = min(deferred, key=lambda d: d.since)
            snapshot_count = sum(len(d.snapshots) for d in deferred)
            summaries.append(
                DeferredSummary(
                    vm_name=vm.name,
                    snapshot_count=snapshot_count,
                    reason=oldest.reason,
                    age=now - oldest.since,
                    since=oldest.since,
                )
            )
        return summaries

    def print_schedule(self, vm_filter: str | None = None) -> dict[str, ScheduleResult]:
        """Evaluate retention for each VM without executing any deletion.

        Returns snapshot retention and per-target backup retention.
        """
        vms = self._filter_vms(vm_filter)
        results: dict[str, ScheduleResult] = {}
        dow = self._config.get_global().preserve_day_of_week
        for vm in vms:
            # Snapshot retention
            snapshots = self._state.get_snapshots(vm.name)
            if not snapshots:
                snap_retention = RetentionResult(keep=[], remove=[])
            else:
                policy = self._parse_preserve(vm.snapshot_preserve, vm.snapshot_preserve_min)
                engine = self._factory.create_retention_engine(policy)
                items = [RetentionItem(name=s.name, timestamp=s.timestamp) for s in snapshots]
                snap_retention = engine.evaluate(
                    items, policy, datetime.now(), preserve_day_of_week=dow
                )

            # Per-target backup retention
            backup_retentions: dict[str, RetentionResult] = {}
            for target in vm.targets:
                provider = self._factory.create_backup_provider(vm, target)
                backups = provider.list(target)
                if backups:
                    policy = self._parse_preserve(
                        target.target_preserve, target.target_preserve_min
                    )
                    engine = self._factory.create_retention_engine(policy)
                    items = [RetentionItem(name=b.name, timestamp=b.timestamp) for b in backups]
                    backup_retentions[str(target.path)] = engine.evaluate(
                        items, policy, datetime.now(), preserve_day_of_week=dow
                    )
                else:
                    backup_retentions[str(target.path)] = RetentionResult(keep=[], remove=[])

            results[vm.name] = ScheduleResult(
                snapshots=snap_retention,
                backups=backup_retentions,
            )
        return results

    def schedule_summary(self, vm_filter: str | None = None) -> str:
        """Produce a human-readable retention preview.

        Simulates the retention engine against a synthetic timestamp
        distribution (one per hour for the configured retention window
        + 50% margin) and returns a formatted string showing expected
        chain length and bucket breakdown for each VM and each target.

        Logs only factual data: base image actual-size (from
        ``qemu-img info``) and compression_type (from config).  No
        size projections — the ``base_size × 0.3`` formula was removed
        because it cannot predict data compressibility.
        """
        vms = self._filter_vms(vm_filter)
        dow = self._config.get_global().preserve_day_of_week
        now = datetime.now()

        lines: list[str] = []

        for vm in vms:
            lines.append(f"=== {vm.name} ===")

            # Get base image actual-size (factual — no projections).
            base_size = 0
            info_cmd = [
                "qemu-img",
                "info",
                "--force-share",
                "--output=json",
                str(vm.base_image),
            ]
            info_result = self._shell.run(info_cmd, timeout=60, check=True)
            if info_result.success:
                try:
                    info = json.loads(info_result.stdout)
                    base_size = int(info.get("actual-size", 0))
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            lines.append(f"  Base image actual-size: {base_size} B")

            # Snapshot retention
            snap_policy = self._parse_preserve(vm.snapshot_preserve, vm.snapshot_preserve_min)
            snap_window = self._retention_window(snap_policy)
            snap_items = self._generate_synthetic_items(now, snap_window, "snap")
            snap_engine = self._factory.create_retention_engine(snap_policy)
            snap_result = snap_engine.evaluate(
                snap_items, snap_policy, now, preserve_day_of_week=dow
            )
            snap_explain = snap_engine.explain(
                snap_items, snap_policy, now, preserve_day_of_week=dow
            )

            lines.append("  Snapshots:")
            lines.append(
                f"    Policy: hourly={snap_policy.hourly} daily={snap_policy.daily} "
                f"weekly={snap_policy.weekly} monthly={snap_policy.monthly} "
                f"yearly={snap_policy.yearly} preserve_min={snap_policy.preserve_min}"
            )
            lines.append(f"    Simulated items: {len(snap_items)}")
            lines.append(f"    Expected kept:   {len(snap_result.keep)}")
            lines.append(f"    Expected remove: {len(snap_result.remove)}")
            for bucket in ("preserve_min", "hourly", "daily", "weekly", "monthly", "yearly"):
                info = snap_explain.get(bucket, {})
                count = info.get("count", 0)
                if count > 0:
                    lines.append(f"    {bucket}: {count}")

            # Per-target backup retention
            for target in vm.targets:
                tgt_policy = self._parse_preserve(
                    target.target_preserve, target.target_preserve_min
                )
                tgt_window = self._retention_window(tgt_policy)
                tgt_items = self._generate_synthetic_items(now, tgt_window, "backup")
                tgt_engine = self._factory.create_retention_engine(tgt_policy)
                tgt_result = tgt_engine.evaluate(
                    tgt_items, tgt_policy, now, preserve_day_of_week=dow
                )
                tgt_explain = tgt_engine.explain(
                    tgt_items, tgt_policy, now, preserve_day_of_week=dow
                )

                lines.append(f"  Backups [{target.path}]:")
                lines.append(
                    f"    Policy: hourly={tgt_policy.hourly} daily={tgt_policy.daily} "
                    f"weekly={tgt_policy.weekly} monthly={tgt_policy.monthly} "
                    f"yearly={tgt_policy.yearly} preserve_min={tgt_policy.preserve_min}"
                )
                lines.append(f"    Simulated items: {len(tgt_items)}")
                lines.append(f"    Expected kept:   {len(tgt_result.keep)}")
                lines.append(f"    Expected remove: {len(tgt_result.remove)}")
                for bucket in ("preserve_min", "hourly", "daily", "weekly", "monthly", "yearly"):
                    info = tgt_explain.get(bucket, {})
                    count = info.get("count", 0)
                    if count > 0:
                        lines.append(f"    {bucket}: {count}")
                lines.append(
                    f"    Compression: {target.compression_type} (compress={target.compress})"
                )

            lines.append("")

        return "\n".join(lines)

    def estimate(self, vm_filter: str | None = None) -> str:
        """Produce a human-readable size estimation report.

        Prints only factual data: base image actual-size (from
        ``qemu-img info``), compression_type, and compress enabled/
        disabled.  No projections — the ``base_size × 0.3`` formula
        was removed because it cannot predict data compressibility.
        """
        vms = self._filter_vms(vm_filter)
        now = datetime.now()

        lines: list[str] = []

        for vm in vms:
            lines.append(f"=== {vm.name} ===")

            # Get base image actual-size (factual — no projections).
            base_size = 0
            info_cmd = [
                "qemu-img",
                "info",
                "--force-share",
                "--output=json",
                str(vm.base_image),
            ]
            info_result = self._shell.run(info_cmd, timeout=60, check=True)
            if info_result.success:
                try:
                    info = json.loads(info_result.stdout)
                    base_size = int(info.get("actual-size", 0))
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            lines.append(f"  Base image actual-size: {base_size} B")

            # Per-target factual data.
            for target in vm.targets:
                tgt_policy = self._parse_preserve(
                    target.target_preserve, target.target_preserve_min
                )
                tgt_window = self._retention_window(tgt_policy)
                tgt_items = self._generate_synthetic_items(now, tgt_window, "backup")
                tgt_engine = self._factory.create_retention_engine(tgt_policy)
                tgt_result = tgt_engine.evaluate(
                    tgt_items,
                    tgt_policy,
                    now,
                    preserve_day_of_week=self._config.get_global().preserve_day_of_week,
                )

                lines.append(f"  Backups [{target.path}]:")
                lines.append(
                    f"    Policy: hourly={tgt_policy.hourly} daily={tgt_policy.daily} "
                    f"weekly={tgt_policy.weekly} monthly={tgt_policy.monthly} "
                    f"yearly={tgt_policy.yearly} preserve_min={tgt_policy.preserve_min}"
                )
                lines.append(f"    Expected kept:   {len(tgt_result.keep)}")
                lines.append(f"    Expected remove: {len(tgt_result.remove)}")
                lines.append(
                    f"    Compression: {target.compression_type} (compress={target.compress})"
                )

            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _retention_window(policy: RetentionPolicy) -> timedelta:
        """Calculate the total retention window from a policy.

        Returns the maximum duration across all configured buckets.
        """
        windows: list[timedelta] = []

        if policy.preserve_min not in ("all", "latest"):
            match = re.match(r"(\d+)([hdwmy])", policy.preserve_min)
            if match:
                count = int(match.group(1))
                unit = match.group(2)
                unit_hours = {"h": 1, "d": 24, "w": 168, "m": 720, "y": 8760}
                windows.append(timedelta(hours=count * unit_hours[unit]))
        elif policy.preserve_min == "all":
            windows.append(timedelta(days=365))

        if policy.hourly > 0:
            windows.append(timedelta(hours=policy.hourly))
        if policy.daily > 0:
            windows.append(timedelta(days=policy.daily))
        if policy.weekly > 0:
            windows.append(timedelta(weeks=policy.weekly))
        if policy.monthly > 0:
            windows.append(timedelta(days=policy.monthly * 30))
        if policy.yearly > 0:
            windows.append(timedelta(days=policy.yearly * 365))

        if not windows:
            return timedelta(days=7)

        return max(windows)

    @staticmethod
    def _generate_synthetic_items(
        now: datetime, window: timedelta, prefix: str = "item"
    ) -> list[RetentionItem]:
        """Generate synthetic RetentionItems, one per hour for window + 50% margin."""
        total_hours = int(window.total_seconds() / 3600)
        total_hours = int(total_hours * 1.5)
        total_hours = max(total_hours, 1)

        items: list[RetentionItem] = []
        for i in range(total_hours):
            ts = now - timedelta(hours=total_hours - i)
            items.append(RetentionItem(name=f"{prefix}_{i:04d}", timestamp=ts))

        return items

    def check(
        self,
        vm_filter: str | None = None,
        deep: bool = False,
    ) -> dict[str, CheckResult]:
        """Verify backing-chain integrity for each VM.

        When *deep* is False (default), checks backing-file existence via
        ``qemu-img info --backing-chain``.  When *deep* is True, also runs
        ``qemu-img check --output=json`` on each snapshot and backup file;
        files with ``corruptions > 0`` are reported as broken with status
        ``"warning"`` (or ``"critical"`` if unreadable).

        Per-VM status aggregation for deep check:
        - ``"ok"`` — 0 corruptions
        - ``"warning"`` — >0 corruptions but readable
        - ``"critical"`` — images missing or unreadable
        """
        vms = self._filter_vms(vm_filter)
        results: dict[str, CheckResult] = {}
        for vm in vms:
            broken: list[str] = []
            corrupted = False
            unreadable = False
            snapshots = self._state.get_snapshots(vm.name)
            for snap in snapshots:
                if deep:
                    status = self._deep_check_file(snap.path, snap.name, broken)
                    if status == "warning":
                        corrupted = True
                    elif status == "critical":
                        unreadable = True
                else:
                    result = self._shell.run(
                        ["qemu-img", "info", "--force-share", "--backing-chain", str(snap.path)],
                        timeout=30,
                        check=True,
                    )
                    if not result.success:
                        broken.append(snap.name)

            # Deep check also inspects backup files on targets
            if deep:
                for target in vm.targets:
                    provider = self._factory.create_backup_provider(vm, target)
                    backups = provider.list(target)
                    for backup in backups:
                        status = self._deep_check_file(backup.path, backup.name, broken)
                        if status == "warning":
                            corrupted = True
                        elif status == "critical":
                            unreadable = True

            if deep:
                if unreadable:
                    status = "broken"
                elif corrupted:
                    status = "corrupted"
                else:
                    status = "ok"
            elif corrupted:
                status = "corrupted"
            elif broken:
                status = "broken"
            else:
                status = "ok"

            # Deferred blockcommit status
            deferred = self._state.get_deferred_operations(vm.name)
            deferred_count = len(deferred)
            deferred_reason: str | None = None
            deferred_age_str: str | None = None
            deferred_severity = "ok"
            remediation: str | None = None

            if deferred:
                oldest = min(deferred, key=lambda d: d.since)
                deferred_reason = oldest.reason
                age = datetime.now() - oldest.since
                deferred_age_str = self._format_age(age)

                global_cfg = self._config.get_global()
                try:
                    warn_count = int(global_cfg.deferred_warn_count)
                    crit_count = int(global_cfg.deferred_crit_count)
                    warn_age = parse_duration(global_cfg.deferred_warn_age)
                    crit_age = parse_duration(global_cfg.deferred_crit_age)

                    if deferred_count >= crit_count or age >= crit_age:
                        deferred_severity = "critical"
                    elif deferred_count >= warn_count or age >= warn_age:
                        deferred_severity = "warning"
                except (ValueError, TypeError):
                    pass

                remediation = self._build_remediation(oldest.reason)

            results[vm.name] = CheckResult(
                vm_name=vm.name,
                status=status,
                broken_snapshots=broken,
                deferred_count=deferred_count,
                deferred_reason=deferred_reason,
                deferred_age=deferred_age_str,
                deferred_severity=deferred_severity,
                remediation=remediation,
            )

        # Record last deep check time
        if deep:
            self._set_last_deep_check_time()

        return results

    def _deep_check_file(self, path: Path, name: str, broken: list[str]) -> str:
        """Run ``qemu-img check`` on a single file.

        Appends *name* to *broken* if the file is corrupt or unreadable.
        Returns ``"ok"`` when clean, ``"warning"`` when ``corruptions > 0``,
        ``"critical"`` when unreadable.
        """
        chk = self._shell.run(
            ["qemu-img", "check", "--force-share", "--output=json", str(path)],
            timeout=60,
            check=True,
        )
        if not chk.success:
            broken.append(name)
            return "critical"
        try:
            data = json.loads(chk.stdout)
            if data.get("corruptions", 0) > 0:
                broken.append(name)
                return "warning"
        except json.JSONDecodeError:
            broken.append(name)
            return "critical"
        return "ok"

    def _get_last_deep_check_time(self) -> datetime | None:
        """Read the last deep check timestamp from the state directory."""
        state_dir = Path(self._config.get_global().state_dir)
        path = state_dir / "_last_deep_check.json"
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            ts = data.get("last_deep_check")
            if ts:
                return datetime.fromisoformat(ts)
        except (json.JSONDecodeError, OSError, ValueError):
            pass
        return None

    def _set_last_deep_check_time(self) -> None:
        """Write the current timestamp as the last deep check time."""
        state_dir = Path(self._config.get_global().state_dir)
        path = state_dir / "_last_deep_check.json"
        data = {"last_deep_check": datetime.now().isoformat()}
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except OSError:
            pass

    def get_deep_check_schedule_info(self) -> str:
        """Return a human-readable deep check schedule status string.

        When ``deep_check_schedule`` is ``"off"``, returns ``"OFF"``.
        Otherwise, computes days since the last deep check and reports
        ``"OVERDUE"`` if the schedule interval has been exceeded.
        """
        schedule = self._config.get_global().deep_check_schedule
        if schedule == "off":
            return "OFF"

        last_check = self._get_last_deep_check_time()
        if last_check is None:
            return f"{schedule.upper()} — OVERDUE (never checked)"

        days_since = (datetime.now() - last_check).days
        if schedule == "weekly" and days_since > 7:
            return f"Last deep check: {days_since} days ago (expected: weekly) — OVERDUE"
        elif schedule == "monthly" and days_since > 30:
            return f"Last deep check: {days_since} days ago (expected: monthly) — OVERDUE"
        else:
            return f"Last deep check: {days_since} days ago (expected: {schedule})"

    def _resolve_snapshot(
        self,
        snapshot_name: str,
        vm_filter: str | None = None,
    ) -> tuple[SnapshotInfo, VMConfig]:
        """Locate a snapshot/backup by name across all sources.

        Searches ``IStateManager`` across all configured VMs (filtered by
        *vm_filter*), matching by snapshot name or path basename.  If not
        found in state, searches all backup providers via
        ``provider.list(target)`` for each VM's targets.

        Raises ``FileNotFoundError`` with message
        ``"Snapshot not found: {name}"`` if not found in either source.
        """
        vms = self._filter_vms(vm_filter)

        for vm in vms:
            # Search in IStateManager
            snapshots = self._state.get_snapshots(vm.name)
            for snap in snapshots:
                if (
                    snap.name == snapshot_name
                    or snap.path.name == snapshot_name
                    or snap.path.stem == snapshot_name
                ):
                    return snap, vm

            # Search in backup targets
            for target in vm.targets:
                provider = self._factory.create_backup_provider(vm, target)
                backups = provider.list(target)
                for backup in backups:
                    if (
                        backup.name == snapshot_name
                        or backup.path.name == snapshot_name
                        or backup.path.stem == snapshot_name
                    ):
                        return backup, vm

        raise FileNotFoundError(f"Snapshot not found: {snapshot_name}")

    def restore(
        self,
        snapshot_name: str,
        target_dir: Path,
        vm_filter: str | None = None,
    ) -> RestoreResult:
        """Restore a snapshot/backup chain to *target_dir*.

        Searches snapshots in ``IStateManager`` and backups on targets for
        *snapshot_name*.  Copies the entire backing chain to *target_dir*
        and rebases each file with relative ``./`` backing paths.

        Returns a ``RestoreResult``; never raises for expected failures.
        """
        try:
            snapshot_info, _ = self._resolve_snapshot(snapshot_name, vm_filter)
        except FileNotFoundError:
            return RestoreResult(
                success=False,
                snapshot_name=snapshot_name,
                restored_path=target_dir,
                chain_files=[],
                error=f"Snapshot '{snapshot_name}' not found",
            )

        source_path = snapshot_info.path

        # Get backing chain via qemu-img info --backing-chain --output=json
        result = self._shell.run(
            ["qemu-img", "info", "--backing-chain", "--output=json", str(source_path)],
            timeout=30,
            check=True,
        )
        if not result.success:
            return RestoreResult(
                success=False,
                snapshot_name=snapshot_name,
                restored_path=target_dir,
                chain_files=[],
                error=f"qemu-img info failed: {result.error}",
            )

        # Parse chain (JSON array, top-to-base order)
        try:
            chain_data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return RestoreResult(
                success=False,
                snapshot_name=snapshot_name,
                restored_path=target_dir,
                chain_files=[],
                error=f"Failed to parse qemu-img output: {exc}",
            )

        # Extract chain file paths and reverse to base-to-top order
        chain_paths: list[Path] = []
        for item in chain_data:
            # Accept both legacy "image" (QEMU < 11.0) and "filename" (QEMU 11.0+) keys.
            image = item.get("image") or item.get("filename", "")
            if image:
                chain_paths.append(Path(image))
        chain_paths.reverse()

        if not chain_paths:
            return RestoreResult(
                success=False,
                snapshot_name=snapshot_name,
                restored_path=target_dir,
                chain_files=[],
                error="No chain files found in qemu-img output",
            )

        # Copy all chain files to target_dir
        chain_files: list[Path] = []
        for src in chain_paths:
            dst = target_dir / src.name
            cp_result = self._shell.run(
                ["cp", str(src), str(dst)],
                timeout=60,
                check=True,
            )
            if not cp_result.success:
                return RestoreResult(
                    success=False,
                    snapshot_name=snapshot_name,
                    restored_path=target_dir,
                    chain_files=chain_files,
                    error=f"Failed to copy {src}: {cp_result.error}",
                )
            chain_files.append(dst)

        # Rebase with relative paths (base-to-top, skip base)
        for i in range(1, len(chain_files)):
            backing_name = chain_files[i - 1].name
            rebase_result = self._shell.run(
                ["qemu-img", "rebase", "-u", "-b", f"./{backing_name}", str(chain_files[i])],
                timeout=30,
                check=True,
            )
            if not rebase_result.success:
                return RestoreResult(
                    success=False,
                    snapshot_name=snapshot_name,
                    restored_path=target_dir,
                    chain_files=chain_files,
                    error=f"Failed to rebase {chain_files[i]}: {rebase_result.error}",
                )

        return RestoreResult(
            success=True,
            snapshot_name=snapshot_name,
            restored_path=target_dir,
            chain_files=chain_files,
            error=None,
        )

    def fork(
        self,
        snapshot_name: str,
        new_vm_name: str,
        storage_dir: Path,
        add_to_config: bool = False,
        vm_filter: str | None = None,
    ) -> RestoreResult:
        """Create a standalone VM from a snapshot or backup.

        Resolves *snapshot_name* via ``_resolve_snapshot()``, then runs
        image conversion to produce a single standalone qcow2
        with no backing dependencies.  Defines a new libvirt VM using a
        modified copy of the source VM's XML (new name, new UUID, new disk
        path, MAC removed).

        When *add_to_config* is True, appends a minimal ``[[vm]]`` block
        to the qsnap config file.

        Returns a ``RestoreResult``; never raises for expected failures.
        """
        # Step 1: Resolve the snapshot and source VM
        try:
            snapshot_info, source_vm = self._resolve_snapshot(snapshot_name, vm_filter)
        except FileNotFoundError:
            return RestoreResult(
                success=False,
                snapshot_name=snapshot_name,
                restored_path=storage_dir,
                chain_files=[],
                error=f"Snapshot not found: {snapshot_name}",
            )

        source_path = snapshot_info.path
        vm_dir = storage_dir / new_vm_name
        target_qcow2 = vm_dir / f"{new_vm_name}.qcow2"
        xml_path = vm_dir / f"{new_vm_name}.xml"

        # Step 2: Resolve backing chain to estimate total chain size
        # --force-share: the source may be the active layer of a running
        # VM, which has an exclusive write lock (design D5, bug U).
        chain_size = 0
        info_result = self._shell.run(
            [
                "qemu-img",
                "info",
                "--force-share",
                "--backing-chain",
                "--output=json",
                str(source_path),
            ],
            timeout=30,
            check=True,
        )
        if info_result.success:
            try:
                chain_data = cast(list[dict[str, object]], json.loads(info_result.stdout))
                if isinstance(chain_data, list):  # type: ignore[reportUnnecessaryIsInstance]
                    for item in chain_data:
                        chain_size += int(cast(int, item.get("actual-size", 0)))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        # Step 3: Log estimated chain size
        size_str = self._format_bytes(chain_size)
        logger.info(
            "Converting snapshot %s (chain size: ~%s) to standalone qcow2...",
            snapshot_name,
            size_str,
        )

        # Step 4: Create target directory
        mkdir_result = self._shell.run(
            ["mkdir", "-p", str(vm_dir)],
            timeout=30,
            check=True,
        )
        if not mkdir_result.success:
            return RestoreResult(
                success=False,
                snapshot_name=snapshot_name,
                restored_path=vm_dir,
                chain_files=[],
                error=f"Failed to create directory {vm_dir}: {mkdir_result.error}",
            )

        # Step 5: Execute image conversion (hybrid NBD/direct — design D9)
        # If the source VM is running, the active layer has an exclusive
        # write lock.  Use NBD pull-model to avoid the lock conflict.
        # If stopped, direct convert is safe.
        if is_vm_running(self._shell, source_vm.name):
            logger.info(
                "VM %s is running — using NBD export for fork",
                source_vm.name,
            )
            # Inline NBD export + convert (the former full-export helper
            # was removed in the unify-nbd-transfer change — the restore
            # simple full pull without checkpoints or the unified engine).
            import os as _os

            _fork_socket = f"/tmp/qsnap-restore-{_os.getpid()}.sock"
            self._shell.run(["rm", "-f", _fork_socket], timeout=10)
            _fork_xml = write_backup_xml(_fork_socket)
            # libvirt's NBD server exports each disk under its target
            # device name (e.g., "vda") — needed for the export name.
            _fork_disk_target = get_first_disk_target(self._shell, source_vm.name)
            try:
                _begin_result = self._shell.run(
                    [
                        "virsh",
                        "backup-begin",
                        "--domain",
                        source_vm.name,
                        str(_fork_xml),
                    ],
                    timeout=120,
                    check=True,
                )
                if not _begin_result.success:
                    convert_result = _begin_result
                else:
                    _nbd_uri = f"nbd:unix:{_fork_socket}"
                    if _fork_disk_target:
                        _nbd_uri = f"nbd:unix:{_fork_socket}:exportname={_fork_disk_target}"
                    convert_result = self._shell.run(
                        [
                            "qemu-img",
                            "convert",
                            "-O",
                            "qcow2",
                            _nbd_uri,
                            str(target_qcow2),
                        ],
                        timeout=7200,
                        check=True,
                    )
            finally:
                _abort_result = self._shell.run(
                    ["virsh", "domjobabort", "--domain", source_vm.name],
                    timeout=30,
                    check=True,
                )
                if not _abort_result.success:
                    logger.warning(
                        "virsh domjobabort failed for VM %s (job may have already terminated): %s",
                        source_vm.name,
                        _abort_result.error,
                    )
                self._shell.run(["rm", "-f", _fork_socket], timeout=10)
                try:
                    _fork_xml.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Failed to remove temp XML file %s: %s", _fork_xml, exc)
        else:
            convert_result = self._shell.run(
                [
                    "qemu-img",
                    "convert",
                    "-O",
                    "qcow2",
                    str(source_path),
                    str(target_qcow2),
                ],
                timeout=7200,
                check=True,
            )
        if not convert_result.success:
            return RestoreResult(
                success=False,
                snapshot_name=snapshot_name,
                restored_path=vm_dir,
                chain_files=[],
                error=f"image conversion failed: {convert_result.error}",
            )

        # Step 6: Obtain source VM XML
        dumpxml_result = self._shell.run(
            ["virsh", "dumpxml", "--domain", source_vm.name],
            timeout=30,
            check=True,
        )
        if not dumpxml_result.success:
            return RestoreResult(
                success=False,
                snapshot_name=snapshot_name,
                restored_path=vm_dir,
                chain_files=[target_qcow2],
                error=f"virsh dumpxml failed: {dumpxml_result.error}",
            )

        # Step 7: Modify XML
        try:
            root = ET.fromstring(dumpxml_result.stdout)
        except ET.ParseError as exc:
            return RestoreResult(
                success=False,
                snapshot_name=snapshot_name,
                restored_path=vm_dir,
                chain_files=[target_qcow2],
                error=f"Failed to parse VM XML: {exc}",
            )

        # Replace <name>
        name_elem = root.find("name")
        if name_elem is not None:
            name_elem.text = new_vm_name

        # Replace <uuid> with a newly generated one
        uuid_elem = root.find("uuid")
        new_uuid = str(uuid.uuid4())
        if uuid_elem is not None:
            uuid_elem.text = new_uuid
        else:
            uuid_elem = ET.SubElement(root, "uuid")
            uuid_elem.text = new_uuid

        # Replace <source file="..."> paths to point to the new qcow2
        for disk in root.iter("disk"):
            source = disk.find("source")
            if source is not None:
                source.set("file", str(target_qcow2))

        # Remove <mac address="..."> to avoid MAC conflicts
        for interface in root.iter("interface"):
            mac = interface.find("mac")
            if mac is not None:
                interface.remove(mac)

        # Step 8: Write modified XML
        try:
            ET.ElementTree(root).write(str(xml_path), encoding="unicode")
        except OSError as exc:
            return RestoreResult(
                success=False,
                snapshot_name=snapshot_name,
                restored_path=vm_dir,
                chain_files=[target_qcow2],
                error=f"Failed to write XML: {exc}",
            )

        # Step 9: Execute virsh define
        define_result = self._shell.run(
            ["virsh", "define", str(xml_path)],
            timeout=30,
            check=True,
        )
        if not define_result.success:
            return RestoreResult(
                success=False,
                snapshot_name=snapshot_name,
                restored_path=vm_dir,
                chain_files=[target_qcow2],
                error=f"virsh define failed: {define_result.error}",
            )

        # Step 10: Optionally append [[vm]] block to config file
        if add_to_config:
            self._append_vm_to_config(new_vm_name, target_qcow2, vm_dir)

        return RestoreResult(
            success=True,
            snapshot_name=snapshot_name,
            restored_path=target_qcow2,
            chain_files=[target_qcow2],
            error=None,
        )

    def deploy(
        self,
        backup_name: str,
        new_vm_name: str,
        storage_dir: Path,
        add_to_config: bool = False,
        vm_filter: str | None = None,
    ) -> RestoreResult:
        """Deploy a backup as a new VM.

        Thin wrapper around ``fork()`` — fork already handles resolution
        from both snapshots and backups.
        """
        return self.fork(
            backup_name,
            new_vm_name,
            storage_dir,
            add_to_config=add_to_config,
            vm_filter=vm_filter,
        )

    def _append_vm_to_config(
        self,
        vm_name: str,
        base_image: Path,
        vm_dir: Path,
    ) -> None:
        """Append a minimal ``[[vm]]`` block to the qsnap config file."""
        snapshot_dir = vm_dir / "snapshots"
        config_path = self._config.config_path
        block = (
            f"\n[[vm]]\n"
            f'name = "{vm_name}"\n'
            f'base_image = "{base_image}"\n'
            f'snapshot_dir = "{snapshot_dir}"\n'
            f'snapshot_create = "always"\n'
        )
        try:
            with open(config_path, "a", encoding="utf-8") as fh:
                fh.write(block)
            # Create snapshot_dir if it does not exist
            mkdir_result = self._shell.run(
                ["mkdir", "-p", str(snapshot_dir)],
                timeout=10,
                check=True,
            )
            if not mkdir_result.success:
                logger.warning(
                    "Failed to create snapshot directory %s: %s",
                    snapshot_dir,
                    mkdir_result.error,
                )
        except OSError as exc:
            logger.warning("Failed to append VM to config: %s", exc)

    @staticmethod
    def _format_bytes(size: int) -> str:
        """Format a byte count as a human-readable string."""
        if size >= 1024**3:
            return f"{size / (1024**3):.1f} GiB"
        if size >= 1024**2:
            return f"{size / (1024**2):.1f} MiB"
        if size >= 1024:
            return f"{size / 1024:.1f} KiB"
        return f"{size} B"

    # ── state consistency check ─────────────────────────────────────────

    def check_state(
        self,
        vm_filter: str | None = None,
    ) -> dict[str, StateCheckResult]:
        """Verify consistency between persisted state and filesystem.

        Cross-references recorded snapshots, FULL backups, and incremental
        dependencies against actual files on disk.  Reports phantom
        entries (state records pointing to non-existent files).  The
        check is read-only — it never deletes files or state entries.
        """
        vms = self._filter_vms(vm_filter)
        results: dict[str, StateCheckResult] = {}
        for vm in vms:
            phantom_snapshots: list[str] = []
            phantom_fulls: list[str] = []
            stale_deps: list[str] = []
            corrupt_files: list[str] = []
            status_parts: list[str] = []

            # ── Phantom snapshots ────────────────────────────────────
            snapshots = self._state.get_snapshots(vm.name)
            for sn in snapshots:
                if not os.path.exists(str(sn.path)):
                    phantom_snapshots.append(f"{sn.name} (expected: {sn.path})")
            if phantom_snapshots:
                status_parts.append("stale_snapshots")

            # ── Phantom FULLs ────────────────────────────────────────
            for target in vm.targets:
                fulls = self._state.get_full_backups(str(target.path))
                for full in fulls:
                    if not os.path.exists(str(full.path)):
                        phantom_fulls.append(f"{full.name} (target: {target.path})")
                # ── Stale dependencies ───────────────────────────────
                for full in fulls:
                    deps = self._state.get_incremental_dependencies(str(target.path), full.name)
                    for dep_name in deps:
                        dep_path = target.path / f"{dep_name}.qcow2"
                        if not os.path.exists(str(dep_path)):
                            stale_deps.append(f"{dep_name} → {full.name} (target: {target.path})")
            if phantom_fulls:
                status_parts.append("stale_fulls")
            if stale_deps:
                status_parts.append("stale_deps")

            # ── Corrupt state files ──────────────────────────────────
            state_dir = Path(self._config.get_global().state_dir)
            vm_state_path = state_dir / f"{vm.name}.json"
            if vm_state_path.exists():
                try:
                    with open(vm_state_path, encoding="utf-8") as fh:
                        json.load(fh)
                except (json.JSONDecodeError, OSError) as exc:
                    corrupt_files.append(f"{vm_state_path}: {exc}")
                    status_parts.append("corrupt_state")

            # ── Orphaned checkpoints (design D6) ─────────────────────
            # Detect libvirt checkpoints (qsnap-{hash}-{snapshot}) whose
            # target_hash does not match any configured target for this VM.
            # Checkpoints live only in libvirt (not in state files), so
            # when a target is removed or its path changes, checkpoints
            # become permanently orphaned.  Detection is read-only and
            # non-fatal — a failed checkpoint-list logs a WARNING and
            # continues to the next VM.
            orphan_checkpoints = self._detect_orphan_checkpoints(vm)
            if orphan_checkpoints:
                status_parts.append("orphan_checkpoints")

            status = ":".join(status_parts) if status_parts else "ok"
            results[vm.name] = StateCheckResult(
                vm_name=vm.name,
                status=status,
                phantom_snapshots=phantom_snapshots,
                phantom_fulls=phantom_fulls,
                stale_deps=stale_deps,
                corrupt_files=corrupt_files,
                orphan_checkpoints=orphan_checkpoints,
            )
        return results

    def _detect_orphan_checkpoints(self, vm: VMConfig) -> list[str]:
        """Detect libvirt checkpoints that no longer match any target.

        Checkpoints are named ``qsnap-{target_hash}-{snapshot}`` where
        ``target_hash`` is an 8-char MD5 hash of the target path.  A
        checkpoint is orphaned when its hash does not match
        ``target_hash(str(target.path))`` for any target configured for
        this VM.

        Uses the backup provider obtained via
        :meth:`IVMModuleFactory.create_backup_provider` (which only
        needs ``IShell``, not ``IStateManager``) — Core SHALL NOT
        directly instantiate ``BitmapBackupProvider`` (design D5).
        Detection is non-fatal: if ``virsh checkpoint-list``
        fails, a WARNING is logged (inside ``list_checkpoints``) and an
        empty list is returned.
        """
        if not vm.targets:
            return []

        # Obtain the backup provider via the factory (design D5 — no
        # direct BitmapBackupProvider instantiation in Core).
        provider = self._factory.create_backup_provider(vm, vm.targets[0])
        checkpoints = provider.list_checkpoints(vm.name)
        if not checkpoints:
            return []

        # Compute the set of configured target hashes for this VM.
        configured_hashes = {provider.target_hash(str(t.path)) for t in vm.targets}

        orphans: list[str] = []
        for cp in checkpoints:
            # Parse target_hash from checkpoint name:
            # qsnap-{8-char-hash}-{snapshot_name}
            parts = cp.split("-", 2)
            if len(parts) < 3:
                continue  # malformed — skip
            cp_hash = parts[1]
            if cp_hash not in configured_hashes:
                logger.warning(
                    "Orphaned checkpoint %s for VM %s — target hash %s "
                    "matches no configured target",
                    cp,
                    vm.name,
                    cp_hash,
                )
                orphans.append(cp)
        return orphans

    # ── pipeline runner ────────────────────────────────────────────────

    def _run_pipeline(
        self,
        vm_filter: str | None,
        step_fn: Callable[[VMConfig], bool],
    ) -> PipelineResult:
        """Iterate VMs, call *step_fn* for each, isolate errors."""
        # Clear the action audit trail at the start of each run
        # (design D1: ephemeral, single-run scope).
        self._actions = []
        vms = self._filter_vms(vm_filter)
        results: list[VMRunResult] = []
        for vm in vms:
            try:
                backup_failed = step_fn(vm)
                results.append(
                    VMRunResult(
                        vm_name=vm.name,
                        success=True,
                        backup_failed=backup_failed,
                    )
                )
            except Exception as exc:
                logger.error("Pipeline failed for VM %s: %s", vm.name, exc)
                self._actions.append(
                    ActionRecord(
                        action="error",
                        vm_name=vm.name,
                        name=vm.name,
                        path=Path(),
                        error=str(exc),
                    )
                )
                results.append(
                    VMRunResult(
                        vm_name=vm.name,
                        success=False,
                        error=str(exc),
                    )
                )

        # Post-pipeline: check deferred operation thresholds (non-fatal)
        self._check_deferred_thresholds()

        # Transaction log (spec: transaction-log/spec.md).
        # Write one line per ActionRecord if transaction_log is configured.
        # Skipped in dry-run mode.
        global_cfg = self._config.get_global()
        if global_cfg.transaction_log and not self._dry_run:
            tx_path = Path(global_cfg.transaction_log)
            for action in self._actions:
                try:
                    TransactionWriter.write(tx_path, action)
                except OSError as exc:
                    logger.warning(
                        "Failed to write transaction log entry: %s",
                        exc,
                    )
            try:
                TransactionWriter.write_finished(tx_path)
            except OSError as exc:
                logger.warning(
                    "Failed to write transaction log finished line: %s",
                    exc,
                )

        return PipelineResult(
            results=results,
            actions=list(self._actions),
            dry_run=self._dry_run,
            config_path=str(self._config.config_path),
        )

    def _check_deferred_thresholds(self) -> None:
        """Check deferred blockcommit thresholds across all VMs.

        Compares per-VM deferred operation count and oldest age against
        ``GlobalConfig`` thresholds.  Logs WARNING or CRITICAL but does
        NOT change the pipeline exit code (non-fatal monitoring).

        Severity:
        - OK: below all thresholds
        - WARNING: count >= warn_count OR age >= warn_age
        - CRITICAL: count >= crit_count OR age >= crit_age
        """
        global_cfg = self._config.get_global()
        try:
            warn_count = int(global_cfg.deferred_warn_count)
            crit_count = int(global_cfg.deferred_crit_count)
            warn_age = parse_duration(global_cfg.deferred_warn_age)
            crit_age = parse_duration(global_cfg.deferred_crit_age)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Invalid deferred threshold config: %s — skipping check",
                exc,
            )
            return

        now = datetime.now()
        for vm in self._config.get_vms():
            deferred = self._state.get_deferred_operations(vm.name)
            if not deferred:
                continue

            count = len(deferred)
            oldest_idx = min(range(len(deferred)), key=lambda i: deferred[i].since)
            oldest_entry = deferred[oldest_idx]
            age = now - oldest_entry.since

            severity = "ok"
            if count >= crit_count or age >= crit_age:
                severity = "critical"
            elif count >= warn_count or age >= warn_age:
                severity = "warning"

            if severity == "critical":
                logger.critical(
                    "VM %s: %d deferred blockcommit operations pending "
                    "(oldest age: %s, reason: %s)",
                    vm.name,
                    count,
                    age,
                    oldest_entry.reason,
                )
            elif severity == "warning":
                logger.warning(
                    "VM %s: %d deferred blockcommit operations pending "
                    "(oldest age: %s, reason: %s)",
                    vm.name,
                    count,
                    age,
                    oldest_entry.reason,
                )

                # Update last_warned_at on the oldest deferred entry
                self._state.update_deferred_warning(vm.name, oldest_idx, now)

    def _filter_vms(self, vm_filter: str | None) -> list[VMConfig]:
        vms = self._config.get_vms()
        if vm_filter is None:
            return vms
        return [vm for vm in vms if vm.name == vm_filter]

    # ── full pipeline ──────────────────────────────────────────────────

    def _preflight_cleanup(self, vm_config: VMConfig) -> None:
        """Remove stale temporary files and detect orphan snapshots.

        Defensive step — failures do NOT block pipeline execution.

        Actions (when ``auto_cleanup`` is True):
        (a) Remove ``*.tmp`` and ``*.partial`` files in ``snapshot_dir``
            and each ``target.path`` directory.
        (b) Remove ``/tmp/qsnap-backup-*.sock`` stale NBD sockets.
        (c) Detect orphan ``.qcow2`` files in ``snapshot_dir`` not
            recorded in ``IStateManager`` — log WARNING, do NOT delete.
        """
        global_cfg = self._config.get_global()
        if not global_cfg.auto_cleanup:
            logger.info("auto_cleanup is disabled — skipping stale file cleanup")
            return

        try:
            # (a) Remove stale .tmp and .partial files
            dirs_to_clean: list[Path] = [vm_config.snapshot_dir]
            dirs_to_clean.extend(t.path for t in vm_config.targets)

            removed_count = 0
            for directory in dirs_to_clean:
                for pattern in ("*.tmp", "*.partial"):
                    result = self._shell.run(
                        [
                            "find",
                            str(directory),
                            "-maxdepth",
                            "1",
                            "-name",
                            pattern,
                            "-print",
                        ],
                        timeout=10,
                        check=True,
                    )
                    if result.success and result.stdout.strip():
                        for line in result.stdout.strip().splitlines():
                            filepath = line.strip()
                            if filepath:
                                self._shell.run(
                                    ["rm", "-f", filepath],
                                    timeout=10,
                                    check=True,
                                )
                                removed_count += 1

            # (b) Remove stale NBD sockets
            sock_result = self._shell.run(
                [
                    "find",
                    "/tmp",
                    "-maxdepth",
                    "1",
                    "-name",
                    "qsnap-backup-*.sock",
                    "-print",
                ],
                timeout=10,
                check=True,
            )
            if sock_result.success and sock_result.stdout.strip():
                for line in sock_result.stdout.strip().splitlines():
                    sockpath = line.strip()
                    if sockpath:
                        self._shell.run(
                            ["rm", "-f", sockpath],
                            timeout=10,
                            check=True,
                        )
                        removed_count += 1

            if removed_count > 0:
                logger.info(
                    "Pre-flight cleanup: removed %d stale file(s)",
                    removed_count,
                )

            # (d) Detect truncated .qcow2 files on backup targets
            #     (partial transfer artifacts).  Only scan non-FULL
            #     .qcow2 files — FULL files are verified at lifecycle
            #     points.
            for target_dir in (t.path for t in vm_config.targets):
                for qcow2_file in target_dir.glob("*.qcow2"):
                    # Skip FULL anchor files (verified elsewhere)
                    if ".FULL." in qcow2_file.name:
                        continue
                    info_result = self._shell.run(
                        ["qemu-img", "info", "--output=json", str(qcow2_file)],
                        timeout=10,
                        check=True,
                    )
                    if not info_result.success:
                        self._shell.run(
                            ["rm", "-f", str(qcow2_file)],
                            timeout=10,
                            check=True,
                        )
                        logger.warning(
                            "Stale partial transfer detected and deleted: %s",
                            qcow2_file,
                        )
                        removed_count += 1

            # (c) Detect orphan .qcow2 files (warning only, do NOT delete)
            # Only consider files matching the qsnap naming pattern:
            # {vm_name}.{timestamp}.qcow2
            recorded_names = {s.path.name for s in self._state.get_snapshots(vm_config.name)}
            orphan_pattern = f"{vm_config.name}.*.qcow2"
            orphan_result = self._shell.run(
                [
                    "find",
                    str(vm_config.snapshot_dir),
                    "-maxdepth",
                    "1",
                    "-name",
                    orphan_pattern,
                    "-print",
                ],
                timeout=10,
                check=True,
            )
            if orphan_result.success and orphan_result.stdout.strip():
                qsnap_re = re.compile(rf"^{re.escape(vm_config.name)}\.\d{{8}}T\d{{6}}\.qcow2$")
                for line in orphan_result.stdout.strip().splitlines():
                    filepath = line.strip()
                    if filepath:
                        filename = Path(filepath).name
                        if not qsnap_re.match(filename):
                            continue
                        if filename not in recorded_names:
                            logger.warning(
                                "Orphan snapshot file detected: %s",
                                filepath,
                            )

        except Exception as exc:
            # Cleanup failures must NOT block pipeline execution
            logger.warning(
                "Pre-flight cleanup encountered an error (non-blocking): %s",
                exc,
            )

    def _validate_environment(self, vm_config: VMConfig) -> CheckResult:
        """Pre-flight environment validation before pipeline execution.

        Verifies:
        (a) snapshot_dir exists and is writable (``test -d`` + ``test -w``)
        (b) base_image file exists (``test -f``)
        (c) virsh and qemu-img binaries are in PATH (``which``)
        (d) VM is defined in libvirt (``virsh dominfo`` returns 0)

        All checks go through ``IShell`` so they are fully mockable in tests.
        Returns ``CheckResult`` with ``status="ok"`` or
        ``status="validation_failed"``.
        """
        broken: list[str] = []

        # Step 0: Pre-flight cleanup (stale files, orphan detection)
        self._preflight_cleanup(vm_config)

        # (a) snapshot_dir exists and is writable
        dir_check = self._shell.run(
            ["test", "-d", str(vm_config.snapshot_dir)],
            timeout=10,
            check=True,
        )
        if not dir_check.success:
            broken.append(f"snapshot_dir not found: {vm_config.snapshot_dir}")
        else:
            write_check = self._shell.run(
                ["test", "-w", str(vm_config.snapshot_dir)],
                timeout=10,
                check=True,
            )
            if not write_check.success:
                broken.append(f"snapshot_dir not writable: {vm_config.snapshot_dir}")

        # (b) base_image file exists
        img_check = self._shell.run(
            ["test", "-f", str(vm_config.base_image)],
            timeout=10,
            check=True,
        )
        if not img_check.success:
            broken.append(f"base_image not found: {vm_config.base_image}")

        # (c) virsh and qemu-img in PATH
        for binary in ("virsh", "qemu-img"):
            result = self._shell.run(
                ["which", binary],
                timeout=10,
                check=True,
            )
            if not result.success:
                broken.append(f"{binary} not in PATH")

        # (d) VM defined in libvirt
        dominfo = self._shell.run(
            ["virsh", "dominfo", "--domain", vm_config.name],
            timeout=30,
            check=True,
        )
        if not dominfo.success:
            broken.append(f"VM not defined in libvirt: {vm_config.name}")

        # (e) Target paths exist (mode-dependent)
        for target in vm_config.targets:
            target_check = self._shell.run(
                ["test", "-d", str(target.path)],
                timeout=10,
                check=True,
            )
            if not target_check.success:
                if vm_config.snapshot_create == "ondemand":
                    logger.info(
                        "Target %s unreachable (ondemand mode)",
                        target.path,
                    )
                else:
                    broken.append(f"target directory not found: {target.path}")

        # (f) libnbd availability — unconditional hard requirement
        # (spec: env-validation delta; design D5/R4).  Bitmap is the only
        # backup strategy, so the NBD transport is always needed.  There
        # is no silent fallback: a missing dependency is a hard
        # validation error naming the system package.  In dry-run mode
        # the failure is downgraded to a WARNING by the caller.
        if not is_libnbd_available():
            broken.append(MISSING_LIBNBD_ERROR)

        # (g) qemu-nbd compress driver availability — required for
        # compressed FULL backups (design D10).  Only checked when any
        # target has compress=True (the default).  In dry-run mode,
        # failure is downgraded to a WARNING by the caller.
        #
        # The probe command ``qemu-nbd --image-opts driver=compress``
        # always exits non-zero because ``driver=compress`` requires a
        # ``file`` parameter.  The error message distinguishes the two
        # cases: "Unknown driver 'compress'" means the driver is not
        # installed; any other error (e.g. "A block device must be
        # specified for 'file'") means the driver IS available.
        needs_compress = any(t.compress for t in vm_config.targets)
        if needs_compress:
            compress_result = self._shell.run(
                ["qemu-nbd", "--image-opts", "driver=compress"],
                timeout=10,
                check=True,
            )
            err_text = (compress_result.stderr or compress_result.error or "").lower()
            if "unknown driver" in err_text or "command not found" in err_text:
                broken.append(
                    "qemu-nbd compress driver not available — "
                    "compressed FULL backups require QEMU with compress "
                    "driver support. Install qemu-utils >= 6.0 or set "
                    "compress=false in config."
                )

        if broken:
            return CheckResult(
                vm_name=vm_config.name,
                status="validation_failed",
                broken_snapshots=broken,
            )
        return CheckResult(
            vm_name=vm_config.name,
            status="ok",
        )

    def _execute_pipeline(self, vm_config: VMConfig) -> bool:
        """Execute the full pipeline for a single VM.

        Steps:
        1. Pre-flight environment validation
        2. Deferred blockcommit check (if VM is shut off)
        3. Change detection (if ``snapshot_create`` mode requires it)
        4. Snapshot creation (if detector says we should, or mode is "always")
        5. Snapshot retention evaluation
        6. Snapshot lifecycle — blockcommit removed snapshots
        7. Per-target backup transfer → backup verification → backup retention → cleanup

        Returns:
            True if any backup transfer failed (for EXIT_BACKUP_ABORT).
        """
        # Step 1: Pre-flight validation (always runs — design D6)
        # In dry-run mode, failures are logged as WARNING (non-fatal).
        # In normal mode, failures raise RuntimeError.
        validation = self._validate_environment(vm_config)
        if validation.status != "ok":
            error_msg = "; ".join(validation.broken_snapshots)
            if self._dry_run:
                logger.warning(
                    "Environment validation failed for VM %s (dry-run): %s",
                    vm_config.name,
                    error_msg,
                )
            else:
                logger.error(
                    "Environment validation failed for VM %s: %s",
                    vm_config.name,
                    error_msg,
                )
                raise RuntimeError(error_msg)

        self._execute_snapshot_steps(vm_config)
        return self._execute_backup_steps(vm_config)

    # ── snapshot steps (1-4) ──────────────────────────────────────────

    def _execute_snapshot_steps(self, vm_config: VMConfig) -> bool:
        """Steps 1-4: change detection, snapshot, retention, lifecycle.

        Returns False (no backup steps, so no backup failure).
        """
        # Step 0: Deferred blockcommit check
        self._check_deferred_operations(vm_config)

        # Step 1: Change detection / ondemand check
        should_snapshot = True
        if vm_config.snapshot_create == "onchange":
            detector = self._factory.create_change_detector(vm_config.change_detection_mode)
            disks = self._resolve_disks(vm_config)
            should_snapshot = any(
                detector.has_changed(vm_config, disk=disk).changed for disk in disks
            )
        elif vm_config.snapshot_create == "ondemand":
            has_reachable = any(t.path.is_dir() for t in vm_config.targets)
            if not has_reachable:
                logger.info(
                    "Skipping snapshot for VM %s: no reachable target (ondemand)",
                    vm_config.name,
                )
                should_snapshot = False

        # Step 2: Snapshot creation
        if should_snapshot:
            self._create_snapshot(vm_config)

        # Step 3: Snapshot retention
        retention_result = self._evaluate_snapshot_retention(vm_config)

        # Step 4: Snapshot lifecycle (blockcommit removed snapshots)
        if retention_result and retention_result.remove:
            self._blockcommit_snapshots(vm_config, retention_result)

        return False

    def _check_deferred_operations(self, vm_config: VMConfig) -> None:
        """Check and execute deferred blockcommit operations.

        State-adaptive drain (design D6): the executor is chosen from the
        CURRENT VM power state via the same fork as the main blockcommit
        path (``_plan_blockcommit``), not from ``vm_config.lifecycle_mode``
        alone:

        - shut off (any mode): commit via qemu-img, excluding the
          XML-referenced tip; a tip-only remainder is re-queued with the
          entry's ORIGINAL reason.
        - running + virsh mode: commit non-active snapshots live via
          ``virsh blockcommit``.
        - running + qemu-img mode, paused, or domstate failed: skip.

        Committed snapshots are removed from state unconditionally
        (design D5).  An entry leaves the queue only when ALL of its
        snapshots are committed; stale entries whose snapshots are gone
        from state are dropped.
        """
        deferred = self._state.get_deferred_operations(vm_config.name)
        if not deferred:
            return

        remaining: list[DeferredBlockcommit] = []
        queue_changed = False
        offline_drained = False

        for entry in deferred:
            snapshots = [
                s for s in self._state.get_snapshots(vm_config.name) if s.name in entry.snapshots
            ]
            if not snapshots:
                # Stale entry — snapshots gone from state; drop it.
                logger.warning(
                    "Deferred snapshots not found for VM %s: %s — dropping stale entry",
                    vm_config.name,
                    entry.snapshots,
                )
                queue_changed = True
                continue

            plan = self._plan_blockcommit(vm_config, snapshots)
            if plan is None:
                # domstate failed — conservative: keep everything queued.
                logger.info(
                    "Skipping %d deferred blockcommit(s) for VM %s — "
                    "VM state unknown (domstate failed)",
                    len(entry.snapshots),
                    vm_config.name,
                )
                remaining.append(entry)
                continue
            if not plan.committable or plan.effective_mode is None:
                # VM is running (qemu-img mode), paused, or only the
                # tip/active layer remains — keep for a later run.
                logger.info(
                    "Skipping deferred blockcommit of %d snapshot(s) for "
                    "VM %s — not committable in current VM state "
                    "(reason: %s)",
                    len(entry.snapshots),
                    vm_config.name,
                    plan.defer_reason,
                )
                remaining.append(entry)
                continue

            manager = self._factory.create_lifecycle_manager(
                mode=plan.effective_mode,
            )
            result = manager.blockcommit(
                vm_config,
                plan.committable,
                deep_verify=vm_config.blockcommit_deep_verify,
            )
            if not result.success:
                # Still failing — keep for next run
                logger.warning(
                    "Deferred blockcommit still failing for VM %s: %s",
                    vm_config.name,
                    result.error,
                )
                remaining.append(entry)
                continue

            logger.info(
                "Deferred blockcommit succeeded for VM %s (was blocked by %s)",
                vm_config.name,
                entry.reason,
            )
            queue_changed = True
            if plan.effective_mode == "qemu-img":
                offline_drained = True
            for sn in plan.committable:
                self._state.remove_snapshot(vm_config.name, sn.name)
            if plan.deferrable:
                # Partial drain — re-queue the remainder (XML tip / active
                # layer) with the entry's ORIGINAL reason.
                remaining.append(
                    DeferredBlockcommit(
                        snapshots=[s.name for s in plan.deferrable],
                        reason=entry.reason,
                        since=entry.since,
                        last_warned_at=entry.last_warned_at,
                    )
                )

        # Rewrite the queue only when something changed (dropped stale
        # entries, full or partial drains) — re-adding unchanged entries
        # would reset their `since` timestamps.
        if queue_changed:
            self._state.clear_deferred_operations(vm_config.name)
            for entry in remaining:
                self._state.add_deferred_blockcommit(
                    vm_config.name,
                    entry.snapshots,
                    entry.reason,
                )

        # Offline drains deleted overlay files that the (inactive) domain
        # XML may still reference in <backingStore> chains — refresh the
        # XML so the domain stays bootable (design D8).
        if offline_drained:
            self._refresh_domain_backing_store(vm_config)

    def _create_snapshot(self, vm_config: VMConfig) -> list[SnapshotResult]:
        """Step 2: Create a snapshot for each disk of *vm_config*.

        When ``VMConfig.disks`` is set, uses that explicit list.
        Otherwise auto-discovers all disks via ``virsh domblklist``
        (design D2).  Creates one snapshot per disk with the naming
        convention ``{vm_name}.{timestamp}_{disk}.qcow2``.

        If one disk fails, logs the error and continues with the next
        (design D2 — partial failure tolerance).
        """
        if self._dry_run:
            logger.info(
                "[dry-run] Would create snapshot for VM %s",
                vm_config.name,
            )
            return []

        disks = self._resolve_disks(vm_config)
        results: list[SnapshotResult] = []

        for disk in disks:
            snapshot_name = self._generate_snapshot_name(vm_config, disk)
            snapshot_path = vm_config.snapshot_dir / f"{snapshot_name}.qcow2"

            provider = self._factory.create_snapshot_provider(vm_config)
            result = provider.create(
                vm_config,
                snapshot_name,
                disk,
                snapshot_path,
                quiesce=vm_config.snapshot_quiesce,
            )
            if result.success:
                info = SnapshotInfo(
                    name=result.name,
                    path=result.path,
                    timestamp=datetime.now(),
                    allocation=result.new_allocation,
                )
                self._state.record_snapshot(vm_config.name, info)
                self._state.set_last_allocation(vm_config.name, result.new_allocation)
                # Audit trail + btrbk-style INFO log (design D4, D5).
                self._actions.append(
                    ActionRecord(
                        action="snapshot_create",
                        vm_name=vm_config.name,
                        name=result.name,
                        path=result.path,
                        size=result.new_allocation,
                    )
                )
                logger.info(
                    "[snapshot] %s: created %s (%d B)",
                    vm_config.name,
                    result.name,
                    result.new_allocation,
                )
            else:
                logger.error(
                    "Snapshot creation failed for %s disk %s: %s",
                    vm_config.name,
                    disk,
                    result.error,
                )
            results.append(result)

        return results

    def _resolve_disks(self, vm_config: VMConfig) -> list[str]:
        """Resolve disk target names from config or via ``virsh domblklist``.

        When ``VMConfig.disks`` is set, uses that explicit list.
        Otherwise auto-discovers all disks.  When discovery fails or
        returns no disks, returns an empty list and logs a WARNING.
        """
        if vm_config.disks is not None:
            return vm_config.disks

        domblklist_cmd = ["virsh", "domblklist", "--domain", vm_config.name]
        result = self._shell.run(domblklist_cmd, timeout=30, check=True)
        if not result.success:
            logger.warning(
                "domblklist failed for VM %s: %s",
                vm_config.name,
                result.error,
            )
            return []
        disks = parse_domblklist_disks(result.stdout)
        if not disks:
            logger.warning(
                "domblklist returned no disks for VM %s",
                vm_config.name,
            )
            return []
        return [d[0] for d in disks]

    def _evaluate_snapshot_retention(
        self,
        vm_config: VMConfig,
    ) -> RetentionResult | None:
        """Step 3: Evaluate which snapshots to keep/remove."""
        snapshots = self._state.get_snapshots(vm_config.name)
        if not snapshots:
            return None

        policy = self._parse_preserve(vm_config.snapshot_preserve, vm_config.snapshot_preserve_min)
        engine = self._factory.create_retention_engine(policy)
        items = [RetentionItem(name=s.name, timestamp=s.timestamp) for s in snapshots]
        dow = self._config.get_global().preserve_day_of_week
        return engine.evaluate(items, policy, datetime.now(), preserve_day_of_week=dow)

    def _verify_backing_chain(self, vm_config: VMConfig) -> ChainVerifyResult:
        """Verify backing chain integrity of the active disk image.

        Calls ``qemu-img info --backing-chain --output=json`` on the
        most recent snapshot (or base image if no snapshots).  Verifies:
        (a) every file referenced in the chain exists, (b) every file
        has format ``"qcow2"``, (c) backing-filename references are
        consistent, (d) no file appears twice (no cycles).

        Returns ``ChainVerifyResult`` — never raises.
        """
        snapshots = self._state.get_snapshots(vm_config.name)
        # Use the most recent snapshot as the chain entry point, or
        # fall back to the base image if no snapshots exist.
        if snapshots:
            active_path = max(snapshots, key=lambda s: s.timestamp).path
        else:
            active_path = vm_config.base_image

        result = self._shell.run(
            [
                "qemu-img",
                "info",
                "--force-share",
                "--backing-chain",
                "--output=json",
                str(active_path),
            ],
            timeout=30,
            check=True,
        )
        if not result.success:
            return ChainVerifyResult(
                success=False,
                error=f"qemu-img info failed: {result.error}",
                broken_file=None,
            )

        try:
            chain_data = cast(list[dict[str, object]], json.loads(result.stdout))
        except json.JSONDecodeError as exc:
            return ChainVerifyResult(
                success=False,
                error=f"Failed to parse qemu-img output: {exc}",
                broken_file=None,
            )

        if not isinstance(chain_data, list) or not chain_data:  # type: ignore[reportUnnecessaryIsInstance]
            return ChainVerifyResult(
                success=False,
                error="Empty or invalid backing chain data",
                broken_file=None,
            )

        seen_files: set[str] = set()
        for i, item in enumerate(chain_data):
            # Accept both legacy "image" (QEMU < 11.0) and "filename" (QEMU 11.0+) keys.
            image = cast(str, item.get("image") or item.get("filename", ""))
            if not image:
                return ChainVerifyResult(
                    success=False,
                    error=f"Missing 'image' field in chain entry {i}",
                    broken_file=None,
                )

            image_path = Path(image)

            # (d) Check for cycles
            if str(image_path) in seen_files:
                return ChainVerifyResult(
                    success=False,
                    error=f"Backing chain contains a cycle at {image_path}",
                    broken_file=image_path,
                )
            seen_files.add(str(image_path))

            # (a) Check file exists (via IShell for mockability)
            existence = self._shell.run(
                ["test", "-f", str(image_path)],
                timeout=10,
                check=True,
            )
            if not existence.success:
                return ChainVerifyResult(
                    success=False,
                    error=f"Backing chain broken: missing file {image_path}",
                    broken_file=image_path,
                )

            # (b) Check format is qcow2
            fmt = cast(str, item.get("format", ""))
            if fmt != "qcow2":
                return ChainVerifyResult(
                    success=False,
                    error=(f"Unexpected format '{fmt}' for {image_path} (expected 'qcow2')"),
                    broken_file=image_path,
                )

            # (c) Check backing-filename consistency
            backing = cast(str | None, item.get("backing-filename"))
            if backing is not None:
                backing_path = Path(backing)
                backing_existence = self._shell.run(
                    ["test", "-f", str(backing_path)],
                    timeout=10,
                    check=True,
                )
                if not backing_existence.success:
                    return ChainVerifyResult(
                        success=False,
                        error=(f"Backing chain broken: backing file {backing_path} does not exist"),
                        broken_file=backing_path,
                    )

                # Cross-check: backing-filename must match the next
                # entry's image in the chain array.
                if i + 1 < len(chain_data):
                    # Accept both legacy "image" (QEMU < 11.0) and "filename" (QEMU 11.0+) keys.
                    next_image = cast(
                        str, chain_data[i + 1].get("image") or chain_data[i + 1].get("filename", "")
                    )
                    if next_image and str(backing_path) != next_image:
                        return ChainVerifyResult(
                            success=False,
                            error=(
                                f"Backing-filename mismatch for {image_path}: "
                                f"expected {next_image}, got {backing_path}"
                            ),
                            broken_file=image_path,
                        )

        return ChainVerifyResult(success=True, error=None, broken_file=None)

    def _get_chain_length(
        self,
        vm_config: VMConfig,
    ) -> int | None:
        """Return the number of files in the backing chain.

        Queries the most recent snapshot recorded in ``IStateManager``,
        falling back to ``vm_config.base_image`` when no snapshots
        exist.  Returns ``None`` if the chain could not be queried.
        """
        snapshots = self._state.get_snapshots(vm_config.name)
        if snapshots:
            active_path = max(snapshots, key=lambda s: s.timestamp).path
        else:
            active_path = vm_config.base_image

        result = self._shell.run(
            [
                "qemu-img",
                "info",
                "--force-share",
                "--backing-chain",
                "--output=json",
                str(active_path),
            ],
            timeout=30,
            check=True,
        )
        if not result.success:
            return None

        try:
            chain_data = cast(list[dict[str, object]], json.loads(result.stdout))
            if isinstance(chain_data, list):  # type: ignore[reportUnnecessaryIsInstance]
                return len(chain_data)
        except json.JSONDecodeError:
            pass
        return None

    def _detect_active_layer_path(self, vm_config: VMConfig) -> str | None:
        """Return the current top overlay path for *vm_config*.

        Uses ``virsh domblklist`` — the source of the first disk is the
        active overlay on a running VM and the XML-referenced tip on an
        inactive one.  On failure, falls back to the newest snapshot
        recorded in state (by timestamp) with a WARNING — correct in the
        normal case because qsnap-created snapshots are appended in order.
        Returns ``None`` when the layer cannot be determined at all.
        """
        domblklist_result = self._shell.run(
            ["virsh", "domblklist", "--domain", vm_config.name],
            timeout=30,
            check=True,
        )
        if domblklist_result.success:
            try:
                return parse_domblklist_path(domblklist_result.stdout)
            except ValueError:
                pass  # fall through to the state heuristic
        snapshots = self._state.get_snapshots(vm_config.name)
        if snapshots:
            newest = max(snapshots, key=lambda s: s.timestamp)
            logger.warning(
                "virsh domblklist failed for VM %s — assuming newest state "
                "snapshot %s is the active layer",
                vm_config.name,
                newest.name,
            )
            return str(newest.path)
        logger.warning(
            "virsh domblklist failed for VM %s and no snapshots in state — active layer unknown",
            vm_config.name,
        )
        return None

    def _plan_blockcommit(
        self,
        vm_config: VMConfig,
        candidates: list[SnapshotInfo],
    ) -> _CommitPlan | None:
        """Decide which snapshots are safe to commit now, and with which
        executor (adaptive lifecycle fork, design D2).

        Fork matrix:

        - VM running + ``lifecycle_mode="virsh"``: commit the non-active
          prefix live via ``virsh blockcommit``; defer the active layer
          with reason ``"vm_running"``.
        - VM running + ``lifecycle_mode="qemu-img"``: defer everything
          (offline-only mode) with reason ``"vm_running"``.
        - VM shut off (either mode): commit offline via ``qemu-img``,
          excluding the XML-referenced tip overlay; defer the tip with
          reason ``"active_layer"``.
        - VM paused / any other state: defer everything ``"vm_running"``.
        - ``virsh domstate`` failure: return ``None`` — legacy fallback
          (configured mode, full candidate set, no deferral).
        """
        domstate_result = self._shell.run(
            ["virsh", "domstate", "--domain", vm_config.name],
            timeout=30,
            check=True,
        )
        if not domstate_result.success:
            return None  # legacy fallback — non-fatal by design

        vm_state = domstate_result.stdout.strip().lower()

        if "shut off" in vm_state:
            # Offline path — qemu-img; never commit/delete the
            # XML-referenced tip (the domain would become unbootable).
            tip = self._detect_active_layer_path(vm_config)
            committable = [s for s in candidates if not _same_file(s.path, tip)]
            deferrable = [s for s in candidates if _same_file(s.path, tip)]
            return _CommitPlan(
                committable=committable,
                deferrable=deferrable,
                effective_mode="qemu-img",
                defer_reason="active_layer" if deferrable else None,
            )

        if "running" in vm_state and vm_config.lifecycle_mode == "virsh":
            # Live path — virsh; the active layer cannot be committed.
            active = self._detect_active_layer_path(vm_config)
            committable = [s for s in candidates if not _same_file(s.path, active)]
            deferrable = [s for s in candidates if _same_file(s.path, active)]
            return _CommitPlan(
                committable=committable,
                deferrable=deferrable,
                effective_mode="virsh",
                defer_reason="vm_running" if deferrable else None,
            )

        # running + qemu-img mode, paused, or any other state → defer all
        return _CommitPlan(
            committable=[],
            deferrable=list(candidates),
            effective_mode=None,
            defer_reason="vm_running",
        )

    def _refresh_domain_backing_store(self, vm_config: VMConfig) -> None:
        """Strip stale ``<backingStore>`` elements from the domain XML.

        Offline commits (:class:`QemuImgCommitManager`) delete committed
        overlay files, but the inactive domain's persistent XML still
        references them in its ``<backingStore>`` chains — the domain
        would fail to start ("Cannot access backing file ... No such
        file or directory").  Removing all ``<backingStore>`` elements
        makes libvirt re-probe the (now shortened) chain from the qcow2
        headers on next start (design D8).

        Best-effort: any failure is logged as a WARNING and is non-fatal.
        """
        dumpxml = self._shell.run(
            ["virsh", "dumpxml", "--domain", vm_config.name],
            timeout=30,
            check=True,
        )
        if not dumpxml.success:
            logger.warning(
                "Could not refresh domain XML for VM %s after offline commit "
                "(dumpxml failed: %s) — if the domain fails to start, strip "
                "stale <backingStore> elements manually",
                vm_config.name,
                dumpxml.error,
            )
            return
        try:
            root = ET.fromstring(dumpxml.stdout)
        except ET.ParseError as exc:
            logger.warning(
                "Could not refresh domain XML for VM %s after offline commit "
                "(XML parse failed: %s) — manual <backingStore> cleanup may "
                "be needed",
                vm_config.name,
                exc,
            )
            return
        stripped = 0
        for disk in root.iter("disk"):
            for backing_store in disk.findall("backingStore"):
                disk.remove(backing_store)
                stripped += 1
        if stripped == 0:
            return
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".xml",
                prefix=f"qsnap-{vm_config.name}-",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp.write(ET.tostring(root, encoding="unicode"))
                tmp_path = tmp.name
        except OSError as exc:
            logger.warning(
                "Could not refresh domain XML for VM %s after offline commit "
                "(temp file failed: %s) — manual <backingStore> cleanup may "
                "be needed",
                vm_config.name,
                exc,
            )
            return
        try:
            define = self._shell.run(["virsh", "define", tmp_path], timeout=30, check=True)
            if define.success:
                logger.info(
                    "Refreshed domain XML for VM %s after offline commit "
                    "(stripped %d stale <backingStore> element(s))",
                    vm_config.name,
                    stripped,
                )
            else:
                logger.warning(
                    "virsh define failed for VM %s after offline commit: %s — "
                    "manual <backingStore> cleanup may be needed",
                    vm_config.name,
                    define.error,
                )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

    def _blockcommit_snapshots(
        self,
        vm_config: VMConfig,
        retention_result: RetentionResult,
    ) -> None:
        """Step 4: Blockcommit removed snapshots.

        Includes pre-commit chain verification (when
        ``chain_verify_before_commit`` is True) and post-commit chain
        length verification (when ``chain_verify_after_commit`` is True).
        """
        snapshots = self._state.get_snapshots(vm_config.name)
        to_merge = [s for s in snapshots if s.name in retention_result.remove]
        if not to_merge:
            return

        # Stale state self-healing: before blockcommit, verify every snapshot
        # file still exists on disk.  If a file was already blockcommitted by a
        # prior run that failed to update state (pre-acde50c bug), remove the
        # stale entry and skip it.  This prevents one stale entry from
        # short-circuiting ALL subsequent blockcommits.
        filtered: list[SnapshotInfo] = []
        for sn in to_merge:
            if os.path.exists(str(sn.path)):
                filtered.append(sn)
            else:
                self._state.remove_snapshot(vm_config.name, sn.name)
                logger.warning(
                    "Stale state entry: snapshot %s file not found on disk — removed from state",
                    sn.name,
                )
        to_merge = filtered
        if not to_merge:
            logger.info("All snapshots in to_merge were stale — skipping blockcommit")
            return

        if self._preserve_snapshots:
            logger.info(
                "[preserve] Skipping blockcommit of %d snapshots for VM %s",
                len(to_merge),
                vm_config.name,
            )
            return

        if self._dry_run:
            logger.info(
                "[dry-run] Would blockcommit %d snapshots for VM %s",
                len(to_merge),
                vm_config.name,
            )
            return

        # Adaptive lifecycle fork (design D2): decide from the current VM
        # power state which snapshots are safe to commit and with which
        # executor.  None → legacy fallback (configured mode, full remove
        # set, no deferral) — a failed domstate call is non-fatal.
        plan = self._plan_blockcommit(vm_config, to_merge)
        if plan is None:
            committable = to_merge
            effective_mode = vm_config.lifecycle_mode
        else:
            committable = plan.committable
            effective_mode = plan.effective_mode
            if plan.deferrable:
                self._state.add_deferred_blockcommit(
                    vm_config.name,
                    [s.name for s in plan.deferrable],
                    plan.defer_reason or "vm_running",
                )
                logger.info(
                    "Deferring blockcommit of %d snapshot(s) for VM %s (reason: %s): %s",
                    len(plan.deferrable),
                    vm_config.name,
                    plan.defer_reason,
                    ", ".join(s.name for s in plan.deferrable),
                )
            if not committable:
                return

        global_cfg = self._config.get_global()

        # Pre-commit chain verification
        if global_cfg.chain_verify_before_commit:
            verify_result = self._verify_backing_chain(vm_config)
            if not verify_result.success:
                logger.critical(
                    "Pre-commit chain verification failed for VM %s: %s. "
                    "Check file existence, run qemu-img check, or "
                    "restore from backup.",
                    vm_config.name,
                    verify_result.error,
                )
                # Do NOT defer — broken chain needs operator intervention
                return
        else:
            logger.info(
                "chain_verify_before_commit is disabled — "
                "skipping pre-commit chain check for VM %s",
                vm_config.name,
            )

        # Get chain length before commit for post-commit comparison
        chain_length_before = self._get_chain_length(vm_config)

        # Race guard (design D2): qemu-img writes into the base image —
        # only safe while the VM stays shut off.  Re-check immediately
        # before invoking the manager; a failed re-check is non-fatal.
        if effective_mode == "qemu-img":
            recheck = self._shell.run(
                ["virsh", "domstate", "--domain", vm_config.name],
                timeout=30,
                check=True,
            )
            if recheck.success and "shut off" not in recheck.stdout.strip().lower():
                self._state.add_deferred_blockcommit(
                    vm_config.name,
                    [s.name for s in committable],
                    "vm_running",
                )
                logger.info(
                    "VM %s no longer shut off — deferring offline blockcommit of %d snapshot(s)",
                    vm_config.name,
                    len(committable),
                )
                return

        # effective_mode is None only when committable is empty — and that
        # case returned above.
        assert effective_mode is not None
        manager = self._factory.create_lifecycle_manager(
            mode=effective_mode,
        )
        result = manager.blockcommit(vm_config, committable)

        # Check for MAC denial — defer if blocked by AppArmor/SELinux
        if (
            not result.success
            and result.error
            and ("apparmor" in result.error or "selinux" in result.error)
        ):
            reason = "apparmor" if "apparmor" in result.error else "selinux"
            self._state.add_deferred_blockcommit(
                vm_config.name,
                [s.name for s in committable],
                reason,
            )
            logger.info(
                "Blockcommit blocked by %s for VM %s — deferred to next VM shutdown",
                reason,
                vm_config.name,
            )
            return

        if not result.success:
            logger.error(
                "Blockcommit failed for VM %s: %s",
                vm_config.name,
                result.error,
            )
            return

        # Blockcommit succeeded — record audit trail + btrbk-style INFO log
        # (design D4, D5).  Emitted before post-commit verification; if
        # verification later detects a problem, a separate CRITICAL log
        # is emitted.
        merged_names = ", ".join(s.name for s in committable)
        logger.info(
            "[blockcommit] %s: merged %d snapshot(s) — %s",
            vm_config.name,
            len(committable),
            merged_names,
        )
        for sn in committable:
            self._actions.append(
                ActionRecord(
                    action="snapshot_delete",
                    vm_name=vm_config.name,
                    name=sn.name,
                    path=sn.path,
                )
            )
            # Unconditional state cleanup (design D5): state must reflect
            # disk reality before backup steps run — independent of
            # chain_verify_after_commit.  Removal also happens before the
            # post-commit measurement so it finds the current active layer.
            self._state.remove_snapshot(vm_config.name, sn.name)

        # Offline commits deleted overlay files that the (inactive) domain
        # XML may still reference in <backingStore> chains — refresh the
        # XML so the domain stays bootable (design D8).
        if effective_mode == "qemu-img":
            self._refresh_domain_backing_store(vm_config)

        # Post-commit chain verification
        if global_cfg.chain_verify_after_commit:
            if chain_length_before is None:
                logger.info(
                    "Pre-commit chain length unavailable — "
                    "skipping post-commit verification for VM %s",
                    vm_config.name,
                )
            else:
                chain_length_after = self._get_chain_length(vm_config)
                if chain_length_after is not None:
                    if chain_length_after >= chain_length_before:
                        logger.critical(
                            "Blockcommit may have failed for VM %s: "
                            "chain length unchanged "
                            "(before=%d, after=%d). "
                            "Snapshot paths for manual recovery: %s",
                            vm_config.name,
                            chain_length_before,
                            chain_length_after,
                            ", ".join(str(s.path) for s in committable),
                        )
                        return
                    else:
                        logger.info(
                            "Post-commit chain verification passed for VM %s",
                            vm_config.name,
                        )
                else:
                    logger.warning(
                        "Post-commit chain measurement failed for VM %s "
                        "(blockcommit itself succeeded)",
                        vm_config.name,
                    )
        else:
            logger.info(
                "chain_verify_after_commit is disabled — "
                "skipping post-commit chain check for VM %s",
                vm_config.name,
            )

    # ── backup steps (5) ───────────────────────────────────────────────

    def _execute_backup_steps(self, vm_config: VMConfig) -> bool:
        """Step 5: For each target — backup transfer → retention → cleanup.

        Returns True if any backup transfer failed.
        """
        snapshots = self._state.get_snapshots(vm_config.name)
        backup_failed = False
        for target in vm_config.targets:
            if self._backup_target(vm_config, target, snapshots):
                backup_failed = True
        return backup_failed

    def _transfer_with_retry(
        self,
        provider: IBackupProvider,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
        *,
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
    ) -> list[BackupResult]:
        """Transfer missing snapshots with exponential backoff retry.

        When ``target.backup_retry_max > 0``, wraps the provider's
        ``transfer_missing()`` call in a retry loop.  Only retries on
        transient errors (determined by ``is_retryable()``).  Non-
        retryable errors fail immediately.

        ``compression_type`` and ``stall_timeout`` are threaded from
        ``TargetConfig`` to the provider's ``transfer_missing()``.

        Returns the list of ``BackupResult`` objects from the last
        attempt.
        """
        max_retries = target.backup_retry_max
        base_seconds = parse_retry_duration(target.backup_retry_base)

        if max_retries <= 0:
            return provider.transfer_missing(
                vm_config,
                target,
                snapshots,
                compression_type=compression_type,
                stall_timeout=stall_timeout,
            )

        results: list[BackupResult] = []
        for attempt in range(1, max_retries + 1):
            results = provider.transfer_missing(
                vm_config,
                target,
                snapshots,
                compression_type=compression_type,
                stall_timeout=stall_timeout,
            )

            # Check if all transfers succeeded
            failed = [r for r in results if not r.success]
            if not failed:
                if attempt > 1:
                    logger.info(
                        "Backup transfer for VM %s target %s succeeded on retry attempt %d/%d",
                        vm_config.name,
                        target.path,
                        attempt,
                        max_retries,
                    )
                return results

            # Check if any failure is retryable
            non_retryable = [r for r in failed if r.error and not is_retryable(r.error)]

            # If any non-retryable error, fail immediately
            if non_retryable:
                return results

            # If this was the last attempt, log exhaustion
            if attempt >= max_retries:
                logger.warning(
                    "Backup transfer for VM %s target %s failed after %d retries",
                    vm_config.name,
                    target.path,
                    max_retries,
                )
                return results

            # Sleep and retry
            backoff = compute_backoff(base_seconds, attempt)
            logger.info(
                "Retrying backup transfer for VM %s target %s (attempt %d/%d, backoff %.1fs)",
                vm_config.name,
                target.path,
                attempt + 1,
                max_retries,
                backoff,
            )
            time.sleep(backoff)

        return results

    def _should_backup_onchange(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
    ) -> bool:
        """Return True if backup should proceed under ``onchange`` mode.

        Compares the latest snapshot's allocation against the last backup
        allocation recorded for this target (design D3).  Returns True
        when the allocation has changed or no baseline exists yet (first
        run).  Returns False when the allocation is unchanged — the VM
        disk has not grown since the last backup to this target.
        """
        if not snapshots:
            return False  # nothing to transfer
        last_backup_alloc = self._state.get_last_backup_allocation(str(target.path))
        if last_backup_alloc is None:
            return True  # first backup to this target
        current_alloc = snapshots[-1].allocation
        if current_alloc != last_backup_alloc:
            return True  # allocation changed
        return False

    def _backup_target(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
    ) -> bool:
        """Transfer missing snapshots to *target*, run retention, cleanup.

        Returns True if any backup transfer failed.
        """
        # Per-target onchange gate (design D3): skip backup when the VM
        # disk has not changed since the last backup to this target.
        if target.backup_create == "onchange":
            if not self._should_backup_onchange(vm_config, target, snapshots):
                logger.info(
                    "[backup] %s: target %s unchanged "
                    "(allocation %d == last backup) — skipping",
                    vm_config.name,
                    target.path,
                    snapshots[-1].allocation if snapshots else 0,
                )
                return False  # no failure, just skipped

        provider = self._factory.create_backup_provider(vm_config, target)
        backup_failed = False

        # Parse stall_timeout from target config (duration string → seconds).
        # "0s" disables stall detection → stall_timeout=0 → providers fall
        # back to fixed-timeout shell.run().
        stall_timeout = parse_stall_timeout(target.backup_stall_timeout)

        # Check for bucket-driven FULL backup necessity (design D1)
        if snapshots:
            all_fulls = self._state.get_full_backups(str(target.path))
            # Filter out phantom FULLs — entries in state whose files
            # no longer exist (deleted externally, disk failure, etc.).
            # Phantom FULLs block bucket-driven FULL creation because
            # the bucket strategy sees an existing FULL for the
            # period and skips creation.
            filtered_fulls: list[FullBackupInfo] = []
            for full in all_fulls:
                if os.path.exists(str(full.path)):
                    filtered_fulls.append(full)
                else:
                    self._state.remove_full_backup(str(target.path), full.name)
                    logger.warning(
                        "Phantom FULL entry: %s file not found on disk — removed from state",
                        full.name,
                    )
            all_fulls = filtered_fulls
            policy = self._parse_preserve(target.target_preserve, target.target_preserve_min)
            strategy = self._factory.create_bucket_full_strategy()
            should_full, bucket_level = strategy.should_create_full(
                target, policy, all_fulls, snapshots[-1].timestamp, datetime.now()
            )
            if should_full:
                if self._dry_run:
                    # Log FULL-would-be-created without executing (design D7).
                    # Single NBD path — no direct-convert fallback, so no
                    # method selection (live-vm-full-backup delta).
                    vm_state = (
                        "running" if is_vm_running(self._shell, vm_config.name) else "stopped"
                    )
                    logger.info(
                        "[dry-run] Would create FULL backup (bucket=%s, method=NBD, VM=%s)",
                        bucket_level,
                        vm_state,
                    )
                else:
                    most_recent = max(snapshots, key=lambda s: s.timestamp)
                    full_result = provider.create_full_backup(
                        vm_config.name,
                        most_recent,
                        target,
                        compress=target.compress,
                        bucket_level=bucket_level,
                        compression_type=target.compression_type,
                        stall_timeout=stall_timeout,
                    )
                    if full_result.success:
                        # ── Post-create FULL backup verification ────
                        global_cfg = self._config.get_global()
                        verify_error = verify_full_backup(
                            self._shell,
                            full_result.target_path,
                            global_cfg.full_verify_after_create,
                            source_path=most_recent.path,
                        )
                        if verify_error is not None:
                            self._shell.run(
                                ["rm", "-f", str(full_result.target_path)],
                                timeout=10,
                            )
                            backup_failed = True
                            logger.warning(
                                "FULL backup verification failed for VM %s "
                                "target %s — file deleted: %s",
                                vm_config.name,
                                target.path,
                                verify_error,
                            )
                        else:
                            full_name = full_result.target_path.stem
                            # Record FULL in state (caller's responsibility
                            # after verification — per core-orchestrator spec).
                            self._state.record_full_backup(
                                str(target.path),
                                f"{full_name}.qcow2",
                                most_recent.timestamp,
                                bucket_level,
                            )
                            # Audit trail + btrbk-style INFO log (design D4, D5).
                            self._actions.append(
                                ActionRecord(
                                    action="backup_full",
                                    vm_name=vm_config.name,
                                    name=full_name,
                                    path=full_result.target_path,
                                    size=full_result.bytes_transferred,
                                )
                            )
                            logger.info(
                                "[backup] %s: created FULL %s (%d B)",
                                vm_config.name,
                                full_name,
                                full_result.bytes_transferred,
                            )
                    else:
                        logger.warning(
                            "Full backup failed for VM %s target %s: %s",
                            vm_config.name,
                            target.path,
                            full_result.error,
                        )
                        backup_failed = True

        # Transfer missing snapshots (with retry when configured)
        if not self._dry_run:
            results = self._transfer_with_retry(
                provider,
                vm_config,
                target,
                snapshots,
                compression_type=target.compression_type,
                stall_timeout=stall_timeout,
            )
            failed = [r for r in results if not r.success]
            if failed:
                backup_failed = True
                failure_details = "; ".join(f"{r.snapshot_name}: {r.error}" for r in failed)
                logger.warning(
                    "Backup transfer failed for VM %s target %s: %d snapshot(s) failed — %s",
                    vm_config.name,
                    target.path,
                    len(failed),
                    failure_details,
                )

            # Audit trail + btrbk-style INFO log for successful transfers
            # (design D4, D5).
            for r in results:
                if r.success:
                    speed = (
                        r.bytes_transferred / (1024 * 1024) / r.duration if r.duration > 0 else 0.0
                    )
                    self._actions.append(
                        ActionRecord(
                            action="backup_transfer",
                            vm_name=vm_config.name,
                            name=r.snapshot_name,
                            path=r.target_path,
                            size=r.bytes_transferred,
                            duration=r.duration,
                        )
                    )
                    logger.info(
                        "[backup] %s: transferred %s → %s (%d B in %.1fs, %.1f MiB/s)",
                        vm_config.name,
                        r.snapshot_name,
                        target.path,
                        r.bytes_transferred,
                        r.duration,
                        speed,
                    )

            # Record incremental→FULL dependency for bitmap transfers
            # (spec: Core records dependency; design D4 — state
            # recording is Core's responsibility).  Bitmap incrementals
            # are backing-chained deltas; the provider verified them,
            # and Core now registers each as a dependent of its chain's
            # FULL anchor so retention cascade-deletion and ``check``
            # see the whole chain.  Failed transfers record nothing;
            # standalone full pulls (no backing file) have no anchor
            # and are skipped.
            for r in results:
                if not r.success:
                    continue
                anchor = self._resolve_chain_full_anchor(r.target_path)
                if anchor is not None:
                    self._state.record_incremental_dependency(
                        str(target.path),
                        r.snapshot_name,
                        anchor,
                    )

        # Update per-target backup allocation baseline after successful
        # transfer (design D3 — onchange gate uses this on the next run).
        # Only update when not in dry-run mode, no failures occurred,
        # and the target is in onchange mode (spec: core-orchestrator).
        if (
            not self._dry_run
            and not backup_failed
            and snapshots
            and target.backup_create == "onchange"
        ):
            self._state.set_last_backup_allocation(
                str(target.path), snapshots[-1].allocation
            )

        # Backup retention + cleanup
        backups, retention_result = self._evaluate_backup_retention(vm_config, target)
        self._cleanup_backups(vm_config, target, backups, retention_result)

        return backup_failed

    def _resolve_chain_full_anchor(self, backup_path: Path) -> str | None:
        """Walk a backup's backing chain to its FULL anchor (bitmap mode).

        Reads ``qemu-img info --output=json`` at each hop and returns the
        stem of the first chain member whose filename contains
        ``.FULL.`` — the anchor Core records via
        ``record_incremental_dependency`` (design D4).  Relative backing
        filenames resolve against the containing directory (qemu-img
        reports what was passed to ``create -b``; qsnap passes absolute
        paths, but restore/copied chains may be relative).  Returns
        ``None`` when the file has no backing chain (standalone full
        pull), the walk fails, or no ``.FULL.`` member exists.
        """
        current = Path(backup_path)
        for _ in range(64):  # bound the walk — real chains are short
            info_result = self._shell.run(
                ["qemu-img", "info", "--output=json", str(current)],
                timeout=60,
                check=True,
            )
            if not info_result.success:
                return None
            try:
                info = json.loads(info_result.stdout)
            except json.JSONDecodeError:
                return None
            backing = info.get("backing-filename")
            if not isinstance(backing, str) or not backing:
                return None
            backing_path = Path(backing)
            if not backing_path.is_absolute():
                backing_path = current.parent / backing_path
            if ".FULL." in backing_path.name:
                return backing_path.stem
            current = backing_path
        return None

    # ── prune steps (retention + lifecycle only) ───────────────────────

    def _execute_prune_steps(self, vm_config: VMConfig) -> bool:
        """Only retention and lifecycle cleanup for snapshots and backups.

        Returns False (no backup transfer, so no backup failure).
        """
        # Snapshot retention + lifecycle
        retention_result = self._evaluate_snapshot_retention(vm_config)
        if retention_result and retention_result.remove:
            self._blockcommit_snapshots(vm_config, retention_result)

        # Backup retention + cleanup
        for target in vm_config.targets:
            backups, retention_result = self._evaluate_backup_retention(vm_config, target)
            self._cleanup_backups(vm_config, target, backups, retention_result)

        return False

    # ── utilities ──────────────────────────────────────────────────────

    def _evaluate_backup_retention(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
    ) -> tuple[list[SnapshotInfo], RetentionResult | None]:
        """List backups on *target* and evaluate retention.

        Returns ``(backups, retention_result)``.  When no backups exist,
        ``retention_result`` is ``None``.
        """
        provider = self._factory.create_backup_provider(vm_config, target)
        backups = provider.list(target)
        if not backups:
            return [], None

        policy = self._parse_preserve(target.target_preserve, target.target_preserve_min)
        engine = self._factory.create_retention_engine(policy)
        items = [RetentionItem(name=b.name, timestamp=b.timestamp) for b in backups]
        dow = self._config.get_global().preserve_day_of_week
        retention_result = engine.evaluate(items, policy, datetime.now(), preserve_day_of_week=dow)
        return backups, retention_result

    def _cleanup_backups(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        backups: list[SnapshotInfo],
        retention_result: RetentionResult | None,
    ) -> None:
        """Delete backups flagged for removal by retention.

        Honours ``_preserve_backups`` and ``_dry_run``.

        Cascade deletion (design D2):
        - Before deleting a FULL (name matches ``*.FULL.*``), check
          ``state.get_incremental_dependencies()``.  If any dependent
          incremental is in the keep-set, skip deletion (ghost retention).
        - After deleting a FULL with no dependents in keep-set,
          cascade-delete orphaned incrementals not in keep-set.
        """
        if not retention_result or not retention_result.remove:
            return

        keep_set = set(retention_result.keep)
        to_delete = [b for b in backups if b.name in retention_result.remove]

        if self._preserve_backups:
            logger.info(
                "[preserve] Skipping deletion of %d backups for VM %s",
                len(to_delete),
                vm_config.name,
            )
            return

        if self._dry_run:
            for backup in to_delete:
                logger.info("[dry-run] Would delete backup: %s", backup.name)
            return

        provider = self._factory.create_backup_provider(vm_config, target)
        for backup in to_delete:
            is_full = ".FULL." in backup.name
            if is_full:
                # Check for dependent incrementals in keep-set (ghost retention)
                dependents = self._state.get_incremental_dependencies(str(target.path), backup.name)
                ghosted = [d for d in dependents if d in keep_set]
                if ghosted:
                    logger.info(
                        "[delete] %s: ghost-retained FULL %s (%d dependent(s) in keep-set)",
                        vm_config.name,
                        backup.name,
                        len(ghosted),
                    )
                    continue
                # No dependents in keep-set — verify FULL integrity before deletion
                # M1 (metadata) verification is NON-CONFIGURABLE — always enforced
                # to prevent data loss from cascade-deleting a corrupt FULL.
                m1_error = verify_full_backup(self._shell, backup.path, "metadata")
                if m1_error is not None:
                    logger.critical(
                        "FULL backup %s is corrupt — blocking deletion of "
                        "FULL and %d dependent incrementals to prevent "
                        "data loss. Run: qsnap check --deep %s. Error: %s",
                        backup.name,
                        len(dependents),
                        target.path,
                        m1_error,
                    )
                    continue

                # M2 verification (configurable via full_verify_before_delete)
                global_cfg = self._config.get_global()
                if global_cfg.full_verify_before_delete == "check":
                    m2_error = verify_full_backup(self._shell, backup.path, "check")
                    if m2_error is not None:
                        logger.critical(
                            "FULL backup %s failed M2 check — blocking "
                            "deletion of FULL and %d dependent "
                            "incrementals. Run: qsnap check --deep %s. "
                            "Error: %s",
                            backup.name,
                            len(dependents),
                            target.path,
                            m2_error,
                        )
                        continue

                # No dependents in keep-set AND M1 (and optionally M2) passed
                # — delete FULL
                provider.delete(backup)
                logger.info(
                    "[delete] %s: removed backup %s from %s",
                    vm_config.name,
                    backup.name,
                    target.path,
                )
                self._actions.append(
                    ActionRecord(
                        action="backup_delete",
                        vm_name=vm_config.name,
                        name=backup.name,
                        path=backup.path,
                    )
                )
                # Clean up state: remove FullBackupInfo from persistent state
                # to prevent phantom FULLs from blocking future FULL creation.
                self._state.remove_full_backup(str(target.path), backup.name)
                # Cascade-delete orphaned incrementals not in keep-set
                for dep_name in dependents:
                    if dep_name not in keep_set:
                        dep_backup = SnapshotInfo(
                            name=dep_name,
                            path=target.path / f"{dep_name}.qcow2",
                            timestamp=datetime.now(),
                            allocation=0,
                        )
                        provider.delete(dep_backup)
                        # Clean up state: remove the incremental→FULL
                        # dependency to prevent ghost retention on
                        # already-deleted incrementals.
                        self._state.remove_incremental_dependency(
                            str(target.path), dep_name, backup.name
                        )
                        logger.info(
                            "[delete] %s: removed backup %s from %s",
                            vm_config.name,
                            dep_name,
                            target.path,
                        )
                        self._actions.append(
                            ActionRecord(
                                action="backup_delete",
                                vm_name=vm_config.name,
                                name=dep_name,
                                path=target.path / f"{dep_name}.qcow2",
                            )
                        )
            else:
                provider.delete(backup)
                logger.info(
                    "[delete] %s: removed backup %s from %s",
                    vm_config.name,
                    backup.name,
                    target.path,
                )
                self._actions.append(
                    ActionRecord(
                        action="backup_delete",
                        vm_name=vm_config.name,
                        name=backup.name,
                        path=backup.path,
                    )
                )

    def _generate_snapshot_name(self, vm_config: VMConfig, disk: str) -> str:
        """Generate a unique snapshot name using the configured timestamp format.

        Reads ``GlobalConfig.timestamp_format`` to determine the timestamp
        pattern: ``short``→``%Y%m%d``, ``long``→``%Y%m%dT%H%M``,
        ``long-iso``→``%Y%m%dT%H%M%S%z``.

        The name format is ``{vm_name}.{timestamp}_{disk}`` (design D2)
        to support multi-disk VMs.

        If a snapshot file with the same name already exists, a collision
        suffix ``_N`` (starting at 1) is appended.
        """
        fmt = self._config.get_global().timestamp_format
        timestamp = format_snapshot_timestamp(datetime.now(), fmt)
        base_name = f"{vm_config.name}.{timestamp}_{disk}"

        # Collision suffix: append _N if the file already exists.
        name = base_name
        counter = 1
        while (vm_config.snapshot_dir / f"{name}.qcow2").exists():
            name = f"{base_name}_{counter}"
            counter += 1
        return name

    @staticmethod
    def _parse_preserve(
        preserve_str: str | None,
        preserve_min_str: str | None = None,
    ) -> RetentionPolicy:
        """Parse a preserve string like ``"24h 2d"`` into a RetentionPolicy.

        If *preserve_str* is None, returns the default policy (all zeros,
        ``preserve_min="all"`` — keep everything).

        If *preserve_min_str* is non-None, it overrides the default
        ``preserve_min`` value in the returned policy.
        """
        # Determine the effective preserve_min.
        if preserve_min_str is not None:
            effective_min = preserve_min_str
        elif preserve_str is None:
            effective_min = "all"
        elif preserve_str == "latest":
            effective_min = "latest"
        elif preserve_str == "all":
            effective_min = "all"
        else:
            effective_min = "0h"

        if preserve_str is None or preserve_str in ("latest", "all"):
            return RetentionPolicy(preserve_min=effective_min)

        counts: dict[str, int] = {
            "hourly": 0,
            "daily": 0,
            "weekly": 0,
            "monthly": 0,
            "yearly": 0,
        }
        anchors: dict[str, bool] = {
            "anchor_hourly": False,
            "anchor_daily": False,
            "anchor_weekly": False,
            "anchor_monthly": False,
            "anchor_yearly": False,
        }
        unit_map = {"h": "hourly", "d": "daily", "w": "weekly", "m": "monthly", "y": "yearly"}
        anchor_map = {
            "h": "anchor_hourly",
            "d": "anchor_daily",
            "w": "anchor_weekly",
            "m": "anchor_monthly",
            "y": "anchor_yearly",
        }
        # Regex: count, optional F prefix, bucket char.
        # Tokens like "7Fx" that don't match [hdwmy] are silently ignored.
        for match in re.finditer(r"(\d+)(F?)([hdwmy])", preserve_str):
            count = int(match.group(1))
            is_anchor = match.group(2) == "F"
            unit = match.group(3)
            counts[unit_map[unit]] = count
            if is_anchor:
                anchors[anchor_map[unit]] = True

        return RetentionPolicy(**counts, **anchors, preserve_min=effective_min)

    @staticmethod
    def _format_age(age: timedelta) -> str:
        """Format a ``timedelta`` as a human-readable age string.

        Returns strings like ``"3d"``, ``"2h"``, ``"5m"``.
        """
        total_seconds = int(age.total_seconds())
        if total_seconds >= 86400:
            return f"{total_seconds // 86400}d"
        if total_seconds >= 3600:
            return f"{total_seconds // 3600}h"
        if total_seconds >= 60:
            return f"{total_seconds // 60}m"
        return f"{total_seconds}s"

    @staticmethod
    def _build_remediation(reason: str) -> str:
        """Build remediation guidance for a deferred blockcommit reason.

        Provides actionable suggestions for AppArmor and SELinux blocks.
        """
        reason_lower = reason.lower()
        if "apparmor" in reason_lower:
            return (
                "Merge blocked by AppArmor. Consider: "
                "aa-disable /etc/apparmor.d/libvirt/libvirt-<uuid>. "
                "Or: shut down the VM to allow automatic merge."
            )
        if "selinux" in reason_lower:
            return (
                "Merge blocked by SELinux. Consider: "
                "setenforce 0 (temporarily) or "
                "audit2allow -w to generate a policy. "
                "Or: shut down the VM to allow automatic merge."
            )
        return (
            f"Deferred blockcommit blocked by: {reason}. "
            "Consider: shut down the VM to allow automatic merge."
        )
