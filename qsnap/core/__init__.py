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
from datetime import datetime
from pathlib import Path

from qsnap.interfaces.config import IConfigFacade
from qsnap.interfaces.factory import IVMModuleFactory
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import RetentionPolicy, TargetConfig, VMConfig
from qsnap.models.results import (
    CheckResult,
    RestoreResult,
    RetentionItem,
    RetentionResult,
    ScheduleResult,
    SnapshotInfo,
    SnapshotResult,
)
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
                policy = self._parse_preserve(vm.snapshot_preserve)
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
                    policy = self._parse_preserve(target.target_preserve)
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
            results[vm.name] = CheckResult(
                vm_name=vm.name,
                status=status,
                broken_snapshots=broken,
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
        return PipelineResult(results=results)

    def _filter_vms(self, vm_filter: str | None) -> list[VMConfig]:
        vms = self._config.get_vms()
        if vm_filter is None:
            return vms
        return [vm for vm in vms if vm.name == vm_filter]

    # ── full pipeline ──────────────────────────────────────────────────

    def _execute_pipeline(self, vm_config: VMConfig) -> bool:
        """Execute the full pipeline for a single VM.

        Steps:
        1. Change detection (if ``snapshot_create`` mode requires it)
        2. Snapshot creation (if detector says we should, or mode is "always")
        3. Snapshot retention evaluation
        4. Snapshot lifecycle — blockcommit removed snapshots
        5. Per-target backup transfer → backup retention → cleanup

        Returns:
            True if any backup transfer failed (for EXIT_BACKUP_ABORT).
        """
        self._execute_snapshot_steps(vm_config)
        return self._execute_backup_steps(vm_config)

    # ── snapshot steps (1-4) ──────────────────────────────────────────

    def _execute_snapshot_steps(self, vm_config: VMConfig) -> bool:
        """Steps 1-4: change detection, snapshot, retention, lifecycle.

        Returns False (no backup steps, so no backup failure).
        """
        # Step 1: Change detection / ondemand check
        should_snapshot = True
        if vm_config.snapshot_create == "onchange":
            detector = self._factory.create_change_detector(vm_config.snapshot_create)
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

        policy = self._parse_preserve(vm_config.snapshot_preserve)
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

        manager = self._factory.create_lifecycle_manager()
        manager.blockcommit(vm_config, to_merge)

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

        # Transfer missing snapshots
        if not self._dry_run:
            results = provider.transfer_missing(vm_config, target, snapshots)
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

        policy = self._parse_preserve(target.target_preserve)
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
    def _parse_preserve(preserve_str: str | None) -> RetentionPolicy:
        """Parse a preserve string like ``"24h 2d"`` into a RetentionPolicy.

        If *preserve_str* is None, returns the default policy (all zeros,
        ``preserve_min="all"`` — keep everything).
        """
        if preserve_str is None:
            return RetentionPolicy()

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

        return RetentionPolicy(**counts, preserve_min="0h")
