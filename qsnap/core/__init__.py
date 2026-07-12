"""Core orchestrator — pipeline runner, dependency injection host.

Core is the only coordinator.  Modules do not call each other.  Core
invokes them in sequence via their ABC interfaces.

Constructor receives ``IConfigFacade``, ``IVMModuleFactory``,
``IStateManager``, and ``IShell`` via DI.  No global state, no hidden
imports.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from qsnap.interfaces.config import IConfigFacade
from qsnap.interfaces.factory import IVMModuleFactory
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import RetentionPolicy, TargetConfig, VMConfig
from qsnap.models.results import (
    RetentionItem,
    RetentionResult,
    SnapshotInfo,
    SnapshotResult,
)

logger = logging.getLogger(__name__)


# ── Pipeline result types ────────────────────────────────────────────────


@dataclass(frozen=True)
class VMRunResult:
    """Per-VM pipeline execution result."""

    vm_name: str
    success: bool
    error: str | None = None


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

    # ── properties ─────────────────────────────────────────────────────

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @dry_run.setter
    def dry_run(self, value: bool) -> None:
        self._dry_run = value

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

    # ── pipeline runner ────────────────────────────────────────────────

    def _run_pipeline(
        self,
        vm_filter: str | None,
        step_fn: object,
    ) -> PipelineResult:
        """Iterate VMs, call *step_fn* for each, isolate errors."""
        vms = self._filter_vms(vm_filter)
        results: list[VMRunResult] = []
        for vm in vms:
            try:
                step_fn(vm)  # type: ignore[operator]
                results.append(VMRunResult(vm_name=vm.name, success=True))
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

    def _execute_pipeline(self, vm_config: VMConfig) -> None:
        """Execute the full pipeline for a single VM.

        Steps:
        1. Change detection (if ``snapshot_create`` mode requires it)
        2. Snapshot creation (if detector says we should, or mode is "always")
        3. Snapshot retention evaluation
        4. Snapshot lifecycle — blockcommit removed snapshots
        5. Per-target backup transfer → backup retention → cleanup
        """
        self._execute_snapshot_steps(vm_config)
        self._execute_backup_steps(vm_config)

    # ── snapshot steps (1-4) ──────────────────────────────────────────

    def _execute_snapshot_steps(self, vm_config: VMConfig) -> None:
        # Step 1: Change detection
        should_snapshot = True
        if vm_config.snapshot_create == "onchange":
            detector = self._factory.create_change_detector(vm_config.snapshot_create)
            change_result = detector.has_changed(vm_config)
            should_snapshot = change_result.has_changed

        # Step 2: Snapshot creation
        if should_snapshot:
            self._create_snapshot(vm_config)

        # Step 3: Snapshot retention
        retention_result = self._evaluate_snapshot_retention(vm_config)

        # Step 4: Snapshot lifecycle (blockcommit removed snapshots)
        if retention_result and retention_result.remove:
            self._blockcommit_snapshots(vm_config, retention_result)

    def _create_snapshot(self, vm_config: VMConfig) -> SnapshotResult | None:
        """Step 2: Create a snapshot for *vm_config*."""
        snapshot_name = self._generate_snapshot_name(vm_config)
        snapshot_path = vm_config.snapshot_dir / f"{snapshot_name}.qcow2"
        disk = "vda"

        if self._dry_run:
            logger.info(
                "[dry-run] Would create snapshot %s for VM %s",
                snapshot_name,
                vm_config.name,
            )
            return None

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
            logger.error("Snapshot creation failed for %s: %s", vm_config.name, result.error)
        return result

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
        return engine.evaluate(items, policy, datetime.now())

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

    def _execute_backup_steps(self, vm_config: VMConfig) -> None:
        """Step 5: For each target — backup transfer → retention → cleanup."""
        snapshots = self._state.get_snapshots(vm_config.name)
        for target in vm_config.targets:
            self._backup_target(vm_config, target, snapshots)

    def _backup_target(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
    ) -> None:
        provider = self._factory.create_backup_provider(vm_config, target)

        # Transfer missing snapshots
        if not self._dry_run:
            provider.transfer_missing(vm_config, target, snapshots)

        # Backup retention
        backups = provider.list(target)
        if backups:
            policy = self._parse_preserve(target.target_preserve)
            engine = self._factory.create_retention_engine(policy)
            items = [RetentionItem(name=b.name, timestamp=b.timestamp) for b in backups]
            retention_result = engine.evaluate(items, policy, datetime.now())

            # Cleanup
            if retention_result.remove and not self._dry_run:
                to_delete = [b for b in backups if b.name in retention_result.remove]
                for backup in to_delete:
                    provider.delete(backup)

    # ── prune steps (retention + lifecycle only) ───────────────────────

    def _execute_prune_steps(self, vm_config: VMConfig) -> None:
        """Only retention and lifecycle cleanup for snapshots and backups."""
        # Snapshot retention + lifecycle
        retention_result = self._evaluate_snapshot_retention(vm_config)
        if retention_result and retention_result.remove:
            self._blockcommit_snapshots(vm_config, retention_result)

        # Backup retention + cleanup
        for target in vm_config.targets:
            provider = self._factory.create_backup_provider(vm_config, target)
            backups = provider.list(target)
            if backups:
                policy = self._parse_preserve(target.target_preserve)
                engine = self._factory.create_retention_engine(policy)
                items = [RetentionItem(name=b.name, timestamp=b.timestamp) for b in backups]
                retention_result = engine.evaluate(items, policy, datetime.now())
                if retention_result.remove and not self._dry_run:
                    to_delete = [b for b in backups if b.name in retention_result.remove]
                    for backup in to_delete:
                        provider.delete(backup)

    # ── utilities ──────────────────────────────────────────────────────

    @staticmethod
    def _generate_snapshot_name(vm_config: VMConfig) -> str:
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        return f"{vm_config.name}.{timestamp}"

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
