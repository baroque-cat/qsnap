"""Core orchestrator — pipeline runner, dependency injection host.

Core is the only coordinator.  Modules do not call each other.  Core
invokes them in sequence via their ABC interfaces.

Constructor receives ``IConfigFacade``, ``IVMModuleFactory``,
``IStateManager``, and ``IShell`` via DI.  No global state, no hidden
imports.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from qsnap.interfaces.config import IConfigFacade
from qsnap.interfaces.factory import IVMModuleFactory
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import RetentionPolicy, TargetConfig, VMConfig
from qsnap.models.results import (
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
)
from qsnap.retention.time_based import _parse_duration
from qsnap.utils.parsing import parse_domblklist_disks
from qsnap.utils.time import format_snapshot_timestamp

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

    results: list[VMRunResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """True iff every VM succeeded."""
        return all(r.success for r in self.results)


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

    # ── properties ─────────────────────────────────────────────────────

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

    def list_deferred(
        self, vm_filter: str | None = None
    ) -> list[DeferredSummary]:
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
                policy = self._parse_preserve(
                    vm.snapshot_preserve, vm.snapshot_preserve_min
                )
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
        chain length, bucket breakdown, and storage estimates for each
        VM and each target.
        """
        vms = self._filter_vms(vm_filter)
        dow = self._config.get_global().preserve_day_of_week
        now = datetime.now()

        lines: list[str] = []

        for vm in vms:
            lines.append(f"=== {vm.name} ===")

            # Snapshot retention
            snap_policy = self._parse_preserve(
                vm.snapshot_preserve, vm.snapshot_preserve_min
            )
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
            lines.append(f"    Policy: hourly={snap_policy.hourly} daily={snap_policy.daily} "
                         f"weekly={snap_policy.weekly} monthly={snap_policy.monthly} "
                         f"yearly={snap_policy.yearly} preserve_min={snap_policy.preserve_min}")
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
                lines.append(f"    Policy: hourly={tgt_policy.hourly} daily={tgt_policy.daily} "
                             f"weekly={tgt_policy.weekly} monthly={tgt_policy.monthly} "
                             f"yearly={tgt_policy.yearly} preserve_min={tgt_policy.preserve_min}")
                lines.append(f"    Simulated items: {len(tgt_items)}")
                lines.append(f"    Expected kept:   {len(tgt_result.keep)}")
                lines.append(f"    Expected remove: {len(tgt_result.remove)}")
                for bucket in ("preserve_min", "hourly", "daily", "weekly", "monthly", "yearly"):
                    info = tgt_explain.get(bucket, {})
                    count = info.get("count", 0)
                    if count > 0:
                        lines.append(f"    {bucket}: {count}")

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

    @staticmethod
    def _should_create_full(
        target: TargetConfig,
        last_full: FullBackupInfo | None,
    ) -> bool:
        """Check if a full backup should be created based on ``full_every``.

        Returns ``True`` when ``full_every`` is non-zero and either no
        previous full backup exists or the configured interval has elapsed.
        """
        if target.full_every == "0d":
            return False

        match = re.match(r"(\d+)([hdwmy])", target.full_every)
        if not match:
            return False

        count = int(match.group(1))
        if count == 0:
            return False

        if last_full is None:
            return True

        unit = match.group(2)
        unit_hours = {"h": 1, "d": 24, "w": 168, "m": 720, "y": 8760}
        interval = timedelta(hours=count * unit_hours[unit])
        elapsed = datetime.now() - last_full.timestamp
        return elapsed >= interval

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
        ``"corrupted"``.
        """
        vms = self._filter_vms(vm_filter)
        results: dict[str, CheckResult] = {}
        for vm in vms:
            broken: list[str] = []
            corrupted = False
            snapshots = self._state.get_snapshots(vm.name)
            for snap in snapshots:
                if deep:
                    if self._deep_check_file(snap.path, snap.name, broken):
                        corrupted = True
                else:
                    result = self._shell.run(
                        ["qemu-img", "info", "--backing-chain", str(snap.path)],
                        timeout=30,
                    )
                    if not result.success:
                        broken.append(snap.name)

            # Deep check also inspects backup files on targets
            if deep:
                for target in vm.targets:
                    provider = self._factory.create_backup_provider(vm, target)
                    backups = provider.list(target)
                    for backup in backups:
                        if self._deep_check_file(backup.path, backup.name, broken):
                            corrupted = True

            if corrupted:
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
                    warn_age = _parse_duration(global_cfg.deferred_warn_age)
                    crit_age = _parse_duration(global_cfg.deferred_crit_age)

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
        return results

    def _deep_check_file(
        self, path: Path, name: str, broken: list[str]
    ) -> bool:
        """Run ``qemu-img check`` on a single file.

        Appends *name* to *broken* if the file is corrupt or unreadable.
        Returns ``True`` when ``corruptions > 0`` was detected.
        """
        chk = self._shell.run(
            ["qemu-img", "check", "--output=json", str(path)],
            timeout=60,
        )
        if not chk.success:
            broken.append(name)
            return False
        try:
            data = json.loads(chk.stdout)
            if data.get("corruptions", 0) > 0:
                broken.append(name)
                return True
        except json.JSONDecodeError:
            broken.append(name)
        return False

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
        vms = self._filter_vms(vm_filter)

        # Search snapshots and backups for the named snapshot
        source_path: Path | None = None
        for vm in vms:
            # Search in IStateManager
            snapshots = self._state.get_snapshots(vm.name)
            for snap in snapshots:
                if snap.name == snapshot_name:
                    source_path = snap.path
                    break
            if source_path:
                break

            # Search in backup targets
            for target in vm.targets:
                provider = self._factory.create_backup_provider(vm, target)
                backups = provider.list(target)
                for backup in backups:
                    if backup.name == snapshot_name:
                        source_path = backup.path
                        break
                if source_path:
                    break
            if source_path:
                break

        if source_path is None:
            return RestoreResult(
                success=False,
                snapshot_name=snapshot_name,
                restored_path=target_dir,
                chain_files=[],
                error=f"Snapshot '{snapshot_name}' not found",
            )

        # Get backing chain via qemu-img info --backing-chain --output=json
        result = self._shell.run(
            ["qemu-img", "info", "--backing-chain", "--output=json", str(source_path)],
            timeout=30,
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
            image = item.get("image")
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

    # ── pipeline runner ────────────────────────────────────────────────

    def _run_pipeline(
        self,
        vm_filter: str | None,
        step_fn: Callable[[VMConfig], bool],
    ) -> PipelineResult:
        """Iterate VMs, call *step_fn* for each, isolate errors."""
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
                results.append(
                    VMRunResult(
                        vm_name=vm.name,
                        success=False,
                        error=str(exc),
                    )
                )

        # Post-pipeline: check deferred operation thresholds (non-fatal)
        self._check_deferred_thresholds()

        return PipelineResult(results=results)

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
            warn_age = _parse_duration(global_cfg.deferred_warn_age)
            crit_age = _parse_duration(global_cfg.deferred_crit_age)
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
                self._state.update_deferred_warning(
                    vm.name, oldest_idx, now
                )

    def _filter_vms(self, vm_filter: str | None) -> list[VMConfig]:
        vms = self._config.get_vms()
        if vm_filter is None:
            return vms
        return [vm for vm in vms if vm.name == vm_filter]

    # ── full pipeline ──────────────────────────────────────────────────

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

        # (a) snapshot_dir exists and is writable
        dir_check = self._shell.run(
            ["test", "-d", str(vm_config.snapshot_dir)],
            timeout=10, check=True,
        )
        if not dir_check.success:
            broken.append(
                f"snapshot_dir not found: {vm_config.snapshot_dir}"
            )
        else:
            write_check = self._shell.run(
                ["test", "-w", str(vm_config.snapshot_dir)],
                timeout=10, check=True,
            )
            if not write_check.success:
                broken.append(
                    f"snapshot_dir not writable: {vm_config.snapshot_dir}"
                )

        # (b) base_image file exists
        img_check = self._shell.run(
            ["test", "-f", str(vm_config.base_image)],
            timeout=10, check=True,
        )
        if not img_check.success:
            broken.append(
                f"base_image not found: {vm_config.base_image}"
            )

        # (c) virsh and qemu-img in PATH
        for binary in ("virsh", "qemu-img"):
            result = self._shell.run(
                ["which", binary], timeout=10, check=True,
            )
            if not result.success:
                broken.append(f"{binary} not in PATH")

        # (d) VM defined in libvirt
        dominfo = self._shell.run(
            ["virsh", "dominfo", "--domain", vm_config.name],
            timeout=30, check=True,
        )
        if not dominfo.success:
            broken.append(
                f"VM not defined in libvirt: {vm_config.name}"
            )

        # (e) Target paths exist (mode-dependent)
        for target in vm_config.targets:
            target_check = self._shell.run(
                ["test", "-d", str(target.path)],
                timeout=10, check=True,
            )
            if not target_check.success:
                if vm_config.snapshot_create == "ondemand":
                    logger.info(
                        "Target %s unreachable (ondemand mode)",
                        target.path,
                    )
                else:
                    broken.append(
                        f"target directory not found: {target.path}"
                    )

        # (f) rsync availability when rate limiting is configured
        needs_rsync = any(t.rate_limit != "no" for t in vm_config.targets)
        if needs_rsync:
            rsync_check = self._shell.run(
                ["which", "rsync"], timeout=10, check=True,
            )
            if not rsync_check.success:
                for target in vm_config.targets:
                    if target.rate_limit != "no":
                        logger.warning(
                            "rsync not found — rate limiting disabled "
                            "for target %s",
                            target.path,
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
        # Step 1: Pre-flight validation (skipped in dry-run mode)
        if not self._dry_run:
            validation = self._validate_environment(vm_config)
            if validation.status != "ok":
                error_msg = "; ".join(validation.broken_snapshots)
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
            detector = self._factory.create_change_detector(
                vm_config.change_detection_mode
            )
            disks = self._resolve_disks(vm_config)
            should_snapshot = any(
                detector.has_changed(vm_config, disk=disk).changed
                for disk in disks
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

        Before creating new snapshots, check if there are pending deferred
        blockcommits. If the VM is shut off, execute them and clear the
        queue on success. If the VM is running, skip with an INFO log.
        """
        deferred = self._state.get_deferred_operations(vm_config.name)
        if not deferred:
            return

        # Check VM state
        domstate_cmd = [
            "virsh", "domstate", "--domain", vm_config.name,
        ]
        state_result = self._shell.run(domstate_cmd, timeout=30)
        vm_state = state_result.stdout.strip().lower() if state_result.success else ""

        if "shut off" in vm_state:
            # Execute deferred blockcommits
            manager = self._factory.create_lifecycle_manager(
                mode=vm_config.lifecycle_mode,
            )
            failed_entries: list[DeferredBlockcommit] = []
            for entry in deferred:
                snapshots = [
                    s for s in self._state.get_snapshots(vm_config.name)
                    if s.name in entry.snapshots
                ]
                if not snapshots:
                    logger.warning(
                        "Deferred snapshots not found for VM %s: %s",
                        vm_config.name,
                        entry.snapshots,
                    )
                    failed_entries.append(entry)
                    continue
                result = manager.blockcommit(vm_config, snapshots)
                if result.success:
                    logger.info(
                        "Deferred blockcommit succeeded for VM %s "
                        "(was blocked by %s)",
                        vm_config.name,
                        entry.reason,
                    )
                else:
                    # Still failing — keep for next run
                    logger.warning(
                        "Deferred blockcommit still failing for VM %s: %s",
                        vm_config.name,
                        result.error,
                    )
                    failed_entries.append(entry)

            # Update deferred queue: clear all, then re-add only failures
            if len(failed_entries) < len(deferred):
                self._state.clear_deferred_operations(vm_config.name)
                for entry in failed_entries:
                    self._state.add_deferred_blockcommit(
                        vm_config.name, entry.snapshots, entry.reason,
                    )
        else:
            logger.info(
                "Skipping %d deferred blockcommits — VM is running",
                len(deferred),
            )

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
                    content_hash=result.content_hash,
                )
                self._state.record_snapshot(vm_config.name, info)
                self._state.set_last_allocation(vm_config.name, result.new_allocation)
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
        Otherwise auto-discovers all disks.  Falls back to ``["vda"]``
        when discovery fails.
        """
        if vm_config.disks is not None:
            return vm_config.disks

        domblklist_cmd = ["virsh", "domblklist", "--domain", vm_config.name]
        result = self._shell.run(domblklist_cmd, timeout=30)
        if not result.success:
            logger.warning(
                "domblklist failed for VM %s, falling back to vda: %s",
                vm_config.name,
                result.error,
            )
            return ["vda"]
        disks = parse_domblklist_disks(result.stdout)
        if not disks:
            logger.warning(
                "domblklist returned no disks for VM %s, falling back to vda",
                vm_config.name,
            )
            return ["vda"]
        return [d[0] for d in disks]

    def _evaluate_snapshot_retention(
        self,
        vm_config: VMConfig,
    ) -> RetentionResult | None:
        """Step 3: Evaluate which snapshots to keep/remove."""
        snapshots = self._state.get_snapshots(vm_config.name)
        if not snapshots:
            return None

        policy = self._parse_preserve(
            vm_config.snapshot_preserve, vm_config.snapshot_preserve_min
        )
        engine = self._factory.create_retention_engine(policy)
        items = [RetentionItem(name=s.name, timestamp=s.timestamp) for s in snapshots]
        dow = self._config.get_global().preserve_day_of_week
        return engine.evaluate(items, policy, datetime.now(), preserve_day_of_week=dow)

    def _blockcommit_snapshots(
        self,
        vm_config: VMConfig,
        retention_result: RetentionResult,
    ) -> None:
        """Step 4: Blockcommit removed snapshots."""
        snapshots = self._state.get_snapshots(vm_config.name)
        to_merge = [s for s in snapshots if s.name in retention_result.remove]
        if not to_merge:
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

        manager = self._factory.create_lifecycle_manager(
            mode=vm_config.lifecycle_mode,
        )
        result = manager.blockcommit(vm_config, to_merge)

        # Check for MAC denial — defer if blocked by AppArmor/SELinux
        if not result.success and result.error and (
            "apparmor" in result.error or "selinux" in result.error
        ):
                reason = "apparmor" if "apparmor" in result.error else "selinux"
                self._state.add_deferred_blockcommit(
                    vm_config.name,
                    [s.name for s in to_merge],
                    reason,
                )
                logger.info(
                    "Blockcommit blocked by %s for VM %s — "
                    "deferred to next VM shutdown",
                    reason,
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

    def _backup_target(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
    ) -> bool:
        """Transfer missing snapshots to *target*, run retention, cleanup.

        Returns True if any backup transfer failed.
        """
        provider = self._factory.create_backup_provider(vm_config, target)
        backup_failed = False

        # Check for full backup necessity (full_every interval)
        if not self._dry_run and snapshots:
            last_full = self._state.get_last_full_backup(str(target.path))
            if self._should_create_full(target, last_full):
                most_recent = max(snapshots, key=lambda s: s.timestamp)
                full_result = provider.create_full_backup(
                    most_recent, target, compress=target.full_compress
                )
                if full_result.success:
                    full_name = full_result.target_path.stem
                    self._state.set_last_full_backup(
                        str(target.path),
                        f"{full_name}.qcow2",
                        most_recent.timestamp,
                    )
                    logger.info(
                        "Created full backup for VM %s target %s: %s",
                        vm_config.name,
                        target.path,
                        full_name,
                    )
                else:
                    logger.warning(
                        "Full backup failed for VM %s target %s: %s",
                        vm_config.name,
                        target.path,
                        full_result.error,
                    )
                    backup_failed = True

        # Transfer missing snapshots
        if not self._dry_run:
            results = provider.transfer_missing(
                vm_config, target, snapshots, rate_limit=target.rate_limit
            )
            if any(not r.success for r in results):
                backup_failed = True

        # Backup retention + cleanup
        backups, retention_result = self._evaluate_backup_retention(vm_config, target)
        self._cleanup_backups(vm_config, target, backups, retention_result)

        return backup_failed

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
            backups, retention_result = self._evaluate_backup_retention(
                vm_config, target
            )
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

        policy = self._parse_preserve(
            target.target_preserve, target.target_preserve_min
        )
        engine = self._factory.create_retention_engine(policy)
        items = [RetentionItem(name=b.name, timestamp=b.timestamp) for b in backups]
        dow = self._config.get_global().preserve_day_of_week
        retention_result = engine.evaluate(
            items, policy, datetime.now(), preserve_day_of_week=dow
        )
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
        """
        if not retention_result or not retention_result.remove:
            return

        to_delete = [b for b in backups if b.name in retention_result.remove]
        if self._preserve_backups:
            logger.info(
                "[preserve] Skipping deletion of %d backups for VM %s",
                len(to_delete),
                vm_config.name,
            )
        elif not self._dry_run:
            provider = self._factory.create_backup_provider(vm_config, target)
            for backup in to_delete:
                provider.delete(backup)

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
        else:
            effective_min = "0h"

        if preserve_str is None or preserve_str == "latest":
            return RetentionPolicy(preserve_min=effective_min)

        counts: dict[str, int] = {
            "hourly": 0,
            "daily": 0,
            "weekly": 0,
            "monthly": 0,
            "yearly": 0,
        }
        unit_map = {"h": "hourly", "d": "daily", "w": "weekly", "m": "monthly", "y": "yearly"}
        for match in re.finditer(r"(\d+)([hdwmy])", preserve_str):
            count = int(match.group(1))
            unit = match.group(2)
            counts[unit_map[unit]] = count

        return RetentionPolicy(**counts, preserve_min=effective_min)

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
