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
import secrets
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast, overload
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
    ChangeResult,
    CheckResult,
    DeferredBlockcommit,
    DeferredSummary,
    FullBackupInfo,
    ReconcileResult,
    RestoreResult,
    RetentionItem,
    RetentionResult,
    ScheduleResult,
    SnapshotInfo,
    SnapshotResult,
    StateCheckResult,
)
from qsnap.utils.nbd import is_vm_running
from qsnap.utils.nbd_client import MISSING_LIBNBD_ERROR, is_libnbd_available
from qsnap.utils.parsing import (
    parse_domblklist_disks,
    parse_domblklist_path,
    parse_timestamp,
)
from qsnap.utils.retry import compute_backoff, is_retryable, parse_retry_duration
from qsnap.utils.time import parse_duration, parse_stall_timeout
from qsnap.utils.transaction import TransactionWriter
from qsnap.utils.verification import scan_backing_chain, verify_full_backup

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


@dataclass(frozen=True)
class _RetryResult:
    """Internal wrapper for _execute_with_retry operations that return
    aggregate results (e.g., transfer_missing returns list[BackupResult]).

    Carries a ``.success`` flag and ``.error`` string for
    _execute_with_retry's retryability check, plus a ``.payload`` with
    the actual operation output.
    """

    success: bool
    error: str | None
    payload: Any = None


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
        # Targets that need a forced FULL backup (auto-recovery detected
        # broken chains at startup).  Populated by
        # _validate_state_at_startup(); consumed and cleared by
        # _backup_target() (design D3: auto-recovery).
        self._force_full_targets: set[str] = set()

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

    @overload
    def list_backups(
        self,
        vm_filter: str | None = None,
        tree: Literal[False] = False,
    ) -> dict[str, list[SnapshotInfo]]: ...

    @overload
    def list_backups(
        self,
        vm_filter: str | None = None,
        tree: Literal[True] = ...,
    ) -> dict[str, list[tuple[str, dict[str, list[SnapshotInfo]]]]]: ...

    def list_backups(
        self,
        vm_filter: str | None = None,
        tree: bool = False,
    ) -> dict[str, list[SnapshotInfo]] | dict[str, list[tuple[str, dict[str, list[SnapshotInfo]]]]]:
        """Return all backups per VM (across all targets), sorted ascending.

        When *tree* is ``False`` (default), returns a flat list per VM
        sorted by timestamp (existing behavior).

        When *tree* is ``True``, returns per-VM, per-target chain
        grouping: ``{vm_name: [(target_path, {chain_id: [backups]})]}``.
        Chains are grouped by FULL anchor via
        ``_group_backups_by_chain()``.  Orphans (no FULL anchor) are
        grouped under the ``"__orphan__"`` key.
        """
        vms = self._filter_vms(vm_filter)
        if not tree:
            results: dict[str, list[SnapshotInfo]] = {}
            for vm in vms:
                all_backups: list[SnapshotInfo] = []
                for target in vm.targets:
                    provider = self._factory.create_backup_provider(vm, target)
                    all_backups.extend(provider.list(target))
                results[vm.name] = sorted(all_backups, key=lambda b: b.timestamp)
            return results

        # Tree mode: group by FULL anchor per target
        tree_results: dict[str, list[tuple[str, dict[str, list[SnapshotInfo]]]]] = {}
        for vm in vms:
            target_chains: list[tuple[str, dict[str, list[SnapshotInfo]]]] = []
            for target in vm.targets:
                provider = self._factory.create_backup_provider(vm, target)
                backups = provider.list(target)
                if not backups:
                    continue
                chains = self._group_backups_by_chain(backups)
                # Sort chains: FULLs by timestamp, orphans last
                sorted_chains = dict(
                    sorted(
                        chains.items(),
                        key=lambda kv: (
                            1 if kv[0] == "__orphan__" else 0,
                            min(b.timestamp for b in kv[1]),
                        ),
                    )
                )
                target_chains.append((str(target.path), sorted_chains))
            tree_results[vm.name] = target_chains
        return tree_results

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
        for vm in vms:
            # Snapshot retention
            snapshots = self._state.get_snapshots(vm.name)
            if not snapshots:
                snap_retention = RetentionResult(keep=[], remove=[])
            else:
                policy = RetentionPolicy(
                    chain_length=vm.snapshot_chain_length or 0, keep_generations=1
                )
                engine = self._factory.create_retention_engine(policy)
                items = [RetentionItem(name=s.name, timestamp=s.timestamp) for s in snapshots]
                snap_retention = engine.evaluate(items, policy, datetime.now())

            # Per-target backup retention
            backup_retentions: dict[str, RetentionResult] = {}
            for target in vm.targets:
                provider = self._factory.create_backup_provider(vm, target)
                backups = provider.list(target)
                if backups:
                    policy = RetentionPolicy(
                        chain_length=0,
                        keep_generations=target.target_keep_generations or 1,
                    )
                    engine = self._factory.create_retention_engine(policy)
                    items = [RetentionItem(name=b.name, timestamp=b.timestamp) for b in backups]
                    backup_retentions[str(target.path)] = engine.evaluate(
                        items, policy, datetime.now()
                    )
                else:
                    backup_retentions[str(target.path)] = RetentionResult(keep=[], remove=[])

            results[vm.name] = ScheduleResult(
                snapshots=snap_retention,
                backups=backup_retentions,
            )
        return results

    def schedule_summary(self, vm_filter: str | None = None) -> str:
        """Produce a human-readable count-based retention preview.

        Displays ``chain_length``, ``keep_generations``, current
        snapshot/chain counts, and real size data (base image
        actual-size from ``qemu-img info``, average incremental size
        from state history).  No synthetic timestamps or retention
        windows.
        """
        vms = self._filter_vms(vm_filter)

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

            base_gb = base_size / (1024**3)
            lines.append(f"  Current allocated: ~{base_gb:.1f} GB")

            # Snapshot retention
            snap_chain_length = vm.snapshot_chain_length or 0
            snapshots = self._state.get_snapshots(vm.name)
            snap_count = len(snapshots)
            lines.append("  Snapshots:")
            lines.append(f"    chain_length: {snap_chain_length}")
            lines.append("    keep_generations: 1")
            lines.append(f"    Current chain: {snap_count} snapshots")

            # Average incremental size from history (last 7 snapshots).
            if snapshots:
                recent = sorted(snapshots, key=lambda s: s.timestamp)[-7:]
                avg_alloc = sum(s.allocation for s in recent) / len(recent)
                avg_gb = avg_alloc / (1024**3)
                lines.append(
                    f"    Avg incremental: ~{avg_gb:.1f} GB (last {len(recent)} snapshots)"
                )

            # Per-target backup retention
            for target in vm.targets:
                tgt_chain_length = target.target_chain_length or 0
                tgt_keep_gens = target.target_keep_generations or 1
                all_fulls = self._state.get_full_backups(str(target.path))
                chain_count = len(all_fulls)

                lines.append(f"  Backups [{target.path}]:")
                lines.append(f"    chain_length: {tgt_chain_length}")
                lines.append(f"    keep_generations: {tgt_keep_gens}")
                lines.append(f"    Current chains: {chain_count}")
                lines.append(
                    f"    Compression: {target.compression_type} (compress={target.compress})"
                )

            lines.append("")

        return "\n".join(lines)

    def estimate(self, vm_filter: str | None = None) -> str:
        """Produce a human-readable size estimation report.

        Prints factual data: base image actual-size, compression_type,
        compress enabled/disabled, and count-based retention config.
        """
        vms = self._filter_vms(vm_filter)

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

            base_gb = base_size / (1024**3)
            lines.append(f"  Current allocated: ~{base_gb:.1f} GB")

            # Per-target factual data.
            for target in vm.targets:
                tgt_chain_length = target.target_chain_length or 0
                tgt_keep_gens = target.target_keep_generations or 1
                all_fulls = self._state.get_full_backups(str(target.path))
                chain_count = len(all_fulls)

                lines.append(f"  Backups [{target.path}]:")
                lines.append(f"    chain_length: {tgt_chain_length}")
                lines.append(f"    keep_generations: {tgt_keep_gens}")
                lines.append(f"    Current chains: {chain_count}")
                lines.append(
                    f"    Compression: {target.compression_type} (compress={target.compress})"
                )

            lines.append("")

        return "\n".join(lines)

    def check(
        self,
        vm_filter: str | None = None,
        deep: bool = False,
    ) -> dict[str, CheckResult]:
        """Verify backing-chain integrity for each VM via triple-source verification.

        Cross-references three sources of truth for snapshots:
        (1) qsnap state JSON, (2) disk qcow2 files, (3) libvirt domain XML.

        For backup targets, cross-references: (1) state JSON,
        (2) disk files, (3) libvirt checkpoints.

        When *deep* is True, also runs ``qemu-img check --output=json`` on
        each snapshot and backup file; files with ``corruptions > 0``,
        ``errors > 0``, or ``leaks > 0`` are reported as broken with status
        ``"warning"`` (or ``"critical"`` if unreadable).

        The check is read-only — it never modifies state, disk, or XML.

        Per-VM status aggregation:
        - ``"ok"`` — 0 corruptions, 0 errors, 0 leaks
        - ``"corrupted"`` — >0 corruptions/errors/leaks but readable
        - ``"broken"`` — images missing or unreadable
        """
        vms = self._filter_vms(vm_filter)
        results: dict[str, CheckResult] = {}
        for vm in vms:
            broken: list[str] = []
            corrupted = False
            unreadable = False

            # ── Triple-source snapshot verification ─────────────
            # 1. State source — snapshots recorded in state JSON
            snapshots = self._state.get_snapshots(vm.name)
            state_paths: dict[str, str] = {
                str(snap.path): snap.name for snap in snapshots
            }

            # 2. Disk source — qemu-img info --backing-chain on active
            #    layer (single call traverses entire chain)
            disk_paths = self._check_snapshot_chain(vm, broken)

            # 3. XML source — virsh dumpxml <disk><source> and
            #    <backingStore><source> file paths
            xml_paths = self._parse_domain_xml_source_paths(vm.name)

            # 4. domblklist — verify active layer matches newest snapshot
            self._verify_active_layer_match(vm, snapshots, broken)

            # 5. Cross-reference all three sources using the matrix
            self._cross_reference_snapshots(state_paths, disk_paths, xml_paths, broken)

            # ── Deep check on snapshots ────────────────────────
            if deep:
                for snap in snapshots:
                    status = self._deep_check_file(snap.path, snap.name, broken)
                    if status == "warning":
                        corrupted = True
                    elif status == "critical":
                        unreadable = True

            # ── Triple-source target verification ──────────────
            for target in vm.targets:
                provider = self._factory.create_backup_provider(vm, target)
                backups = provider.list(target)

                if deep:
                    for backup in backups:
                        status = self._deep_check_file(backup.path, backup.name, broken)
                        if status == "warning":
                            corrupted = True
                        elif status == "critical":
                            unreadable = True

                self._check_target_consistency(vm, target, provider, backups, broken)

            # ── Status aggregation ──────────────────────────────
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
        ``errors > 0``, or ``leaks > 0``, ``"critical"`` when unreadable.

        Uses a 7200-second (2-hour) timeout to accommodate large disks.
        """
        chk = self._shell.run(
            ["qemu-img", "check", "--force-share", "--output=json", str(path)],
            timeout=7200,
            check=True,
        )
        if not chk.success:
            broken.append(name)
            return "critical"
        try:
            data = json.loads(chk.stdout)
            corruptions = data.get("corruptions", 0)
            errors = data.get("errors", 0)
            leaks = data.get("leaks", 0)
            if corruptions > 0 or errors > 0 or leaks > 0:
                broken.append(name)
                return "warning"
        except json.JSONDecodeError:
            broken.append(name)
            return "critical"
        return "ok"

    # ── Triple-source check helpers ──────────────────────────────────

    def _check_snapshot_chain(
        self,
        vm: VMConfig,
        broken: list[str],
    ) -> set[str]:
        """Scan the backing chain of the active layer via ``scan_backing_chain``.

        Delegates to :func:`scan_backing_chain` instead of inline JSON
        parsing.  Returns ``ChainScanResult.paths`` as the set of file
        paths found on disk, and appends ``ChainScanResult.broken_files``
        items to the *broken* side-effect parameter.  When the scan
        command itself fails (``success is False``), the active layer
        name is appended to *broken* so the caller can report the issue.
        """
        active_layer = self._detect_active_layer_path(vm)
        if not active_layer:
            return set()

        scan = scan_backing_chain(self._shell, Path(active_layer))
        if not scan.success:
            broken.append(Path(active_layer).name)
        broken.extend(scan.broken_files)
        return scan.paths

    def _parse_domain_xml_source_paths(self, vm_name: str) -> set[str]:
        """Parse ``virsh dumpxml`` for all ``<source file="...">`` paths.

        Extracts file paths from both ``<disk><source>`` (active layer)
        and ``<backingStore><source>`` (backing chain) elements.

        Returns an empty set on failure (non-fatal).
        """
        xml_paths: set[str] = set()
        result = self._shell.run(
            ["virsh", "dumpxml", "--domain", vm_name],
            timeout=30,
            check=True,
        )
        if not result.success:
            return xml_paths
        try:
            root = ET.fromstring(result.stdout)
        except ET.ParseError:
            return xml_paths

        for disk_elem in root.iter("disk"):
            source = disk_elem.find("source")
            if source is not None:
                file_attr = source.get("file")
                if file_attr:
                    xml_paths.add(file_attr)
            for backing_store in disk_elem.findall(".//backingStore"):
                bs_source = backing_store.find("source")
                if bs_source is not None:
                    bs_file_attr = bs_source.get("file")
                    if bs_file_attr:
                        xml_paths.add(bs_file_attr)
        return xml_paths

    def _verify_active_layer_match(
        self,
        vm: VMConfig,
        snapshots: list[SnapshotInfo],
        broken: list[str],
    ) -> None:
        """Verify ``virsh domblklist`` active layer matches newest snapshot.

        If the active layer shown by domblklist does not match the newest
        snapshot in state, appends an issue to *broken*.
        """
        if not snapshots:
            return
        result = self._shell.run(
            ["virsh", "domblklist", "--domain", vm.name],
            timeout=30,
            check=True,
        )
        if not result.success:
            return
        try:
            disks = parse_domblklist_disks(result.stdout)
        except ValueError:
            return
        newest = max(snapshots, key=lambda s: s.timestamp)
        for _target_dev, source_path in disks:
            if source_path != str(newest.path):
                broken.append(
                    f"domblklist active layer ≠ newest snapshot in state "
                    f"(domblklist={source_path}, state={newest.path})",
                )

    def _cross_reference_snapshots(
        self,
        state_paths: dict[str, str],
        disk_paths: set[str],
        xml_paths: set[str],
        broken: list[str],
    ) -> None:
        """Apply the triple-source matrix to snapshot paths.

        | state_has | disk_has | xml_has | Classification |
        | yes       | yes      | yes    | OK (consistent) |
        | yes       | no       | no     | Phantom in state |
        | yes       | no       | yes    | Stale domain XML |
        | no        | yes      | yes    | Orphan (state incomplete) |
        | no        | yes      | no     | Orphan (untracked) |
        | no        | no       | no     | OK (legitimately deleted) |
        """
        all_paths = set(state_paths.keys()) | disk_paths | xml_paths
        for path in all_paths:
            state_has = path in state_paths
            disk_has = path in disk_paths
            xml_has = path in xml_paths

            if state_has and disk_has and xml_has:
                pass  # OK (consistent)
            elif state_has and not disk_has and not xml_has:
                # Phantom in state — WARNING, reconcile can fix
                logger.warning(
                    "phantom entry in state: %s — run reconcile to fix",
                    state_paths[path],
                )
            elif state_has and not disk_has and xml_has:
                # Stale domain XML — broken
                broken.append(state_paths[path])
                logger.warning(
                    "stale domain XML for %s — run reconcile to fix",
                    state_paths[path],
                )
            elif not state_has and disk_has and xml_has:
                # Orphan (state incomplete) — WARNING
                logger.warning(
                    "orphan file %s exists on disk and in XML but not in state",
                    path,
                )
            elif not state_has and disk_has and not xml_has:
                # Orphan (untracked) — WARNING
                logger.warning(
                    "orphan file %s exists on disk but not in state or XML",
                    path,
                )
            # no | no | no = OK (legitimately deleted)

    def _check_target_consistency(
        self,
        vm: VMConfig,
        target: TargetConfig,
        provider: IBackupProvider,
        backups: list[SnapshotInfo],
        broken: list[str],
    ) -> None:
        """Triple-source verification for backup targets.

        Cross-references: (1) state JSON (FULLs + incremental deps),
        (2) disk files (provider.list), (3) libvirt checkpoints
        (virsh checkpoint-list filtered by target_hash).
        """
        target_str = str(target.path)

        # 1. State source — FULLs and their incremental dependencies
        state_backup_paths: set[str] = set()
        fulls = self._state.get_full_backups(target_str)
        for full in fulls:
            state_backup_paths.add(str(full.path))
            deps = self._state.get_incremental_dependencies(target_str, full.name)
            for dep_name in deps:
                dep_path = target.path / f"{dep_name}.qcow2"
                state_backup_paths.add(str(dep_path))

        # 2. Disk source — files listed by provider
        disk_backup_paths: set[str] = {str(b.path) for b in backups}

        # Phantom FULLs — state has, disk does not
        for path in state_backup_paths - disk_backup_paths:
            broken.append(f"phantom backup: {Path(path).name}")

        # Orphan files — disk has, state does not
        for path in disk_backup_paths - state_backup_paths:
            logger.warning(
                "orphan backup file %s on target %s not tracked in state",
                path,
                target_str,
            )

        # 3. Checkpoint source — virsh checkpoint-list filtered by target_hash
        checkpoints = provider.list_checkpoints(vm.name)
        tgt_hash = provider.target_hash(target_str)
        target_checkpoints = [
            cp for cp in checkpoints if cp.startswith(f"qsnap-{tgt_hash}-")
        ]

        # Orphan checkpoints — target_hash does not match
        orphan_cps = [cp for cp in checkpoints if not cp.startswith(f"qsnap-{tgt_hash}-")]
        if orphan_cps:
            logger.warning(
                "orphan checkpoints detected for VM %s: %s",
                vm.name,
                orphan_cps,
            )

        # Missing checkpoint — no baseline for next incremental
        if fulls and not target_checkpoints:
            logger.warning(
                "no checkpoint for target %s — next incremental impossible",
                target_str,
            )

        # Multiple checkpoints — more than one for the same target
        if len(target_checkpoints) > 1:
            logger.warning(
                "multiple checkpoints for target %s: %s",
                target_str,
                target_checkpoints,
            )

        # Verify chain traversability for last incremental per chain
        for full in fulls:
            deps = self._state.get_incremental_dependencies(target_str, full.name)
            if deps:
                last_dep = deps[-1]
                last_dep_path = target.path / f"{last_dep}.qcow2"
                scan = scan_backing_chain(self._shell, last_dep_path)
                if not scan.success:
                    logger.error(
                        "chain scan failed for %s on target %s: %s",
                        last_dep,
                        target_str,
                        scan.error,
                    )
                    broken.append(f"backup chain broken at {last_dep}")
                if scan.broken_files:
                    for bf in scan.broken_files:
                        broken.append(f"backup chain broken at {Path(bf).name}")

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
        name: str,
        vm_filter: str | None = None,
    ) -> RestoreResult:
        """Replace a stopped VM's disk with a flattened standalone qcow2.

        Resolves *name* via ``_resolve_snapshot()``, verifies the VM is
        stopped, pre-verifies source chain integrity, creates a
        standalone image via ``qemu-img convert``, atomically replaces
        the VM's base image, strips ``<backingStore>`` from domain XML,
        resets all state, and performs best-effort checkpoint cleanup.

        Returns a ``RestoreResult``; never raises for expected failures.
        """
        # Step 1: Resolve the snapshot/backup
        try:
            snapshot_info, vm_config = self._resolve_snapshot(name, vm_filter)
        except FileNotFoundError:
            return RestoreResult(
                success=False,
                snapshot_name=name,
                restored_path=Path(),
                chain_files=[],
                error=f"Snapshot not found: {name}",
            )

        source_path = snapshot_info.path
        vm_name = vm_config.name
        base_image = vm_config.base_image
        snapshot_dir = vm_config.snapshot_dir

        # Step 2: Verify VM is stopped (design D3)
        if is_vm_running(self._shell, vm_name):
            return RestoreResult(
                success=False,
                snapshot_name=name,
                restored_path=base_image,
                chain_files=[],
                error="VM must be stopped for restore",
            )

        # Step 3: Pre-verify source chain integrity (design D6)
        chain_scan = scan_backing_chain(self._shell, source_path)
        if not chain_scan.success or chain_scan.broken_files:
            details = chain_scan.error or ", ".join(chain_scan.broken_files)
            return RestoreResult(
                success=False,
                snapshot_name=name,
                restored_path=base_image,
                chain_files=[],
                error=f"Source backing chain is broken: {details}",
            )

        # Temp path for the standalone image (design D2)
        tmp_path = snapshot_dir / f"{vm_name}.restored.qcow2.tmp"

        if self._dry_run:
            logger.info("[dry-run] Would convert %s to %s", source_path, tmp_path)
            logger.info("[dry-run] Would replace %s with %s", base_image, tmp_path)
            logger.info("[dry-run] Would delete old snapshot overlays for %s", vm_name)
            logger.info("[dry-run] Would strip <backingStore> and update <source file> in domain XML for %s", vm_name)
            logger.info("[dry-run] Would reset state for %s", vm_name)
            logger.info("[dry-run] Would clean up qsnap-* checkpoints for %s", vm_name)
            return RestoreResult(
                success=True,
                snapshot_name=name,
                restored_path=base_image,
                chain_files=[tmp_path],
                error=None,
            )

        # Step 4: Create standalone image at temporary path
        convert_result = self._shell.run(
            [
                "qemu-img",
                "convert",
                "--force-share",
                "-O",
                "qcow2",
                str(source_path),
                str(tmp_path),
            ],
            timeout=7200,
            check=True,
        )
        if not convert_result.success:
            # Original base image and snapshot chain remain intact (D2)
            return RestoreResult(
                success=False,
                snapshot_name=name,
                restored_path=base_image,
                chain_files=[],
                error=f"image conversion failed: {convert_result.error}",
            )

        # Step 5: Atomically replace base image FIRST (design D2)
        os.replace(str(tmp_path), str(base_image))

        # Step 6: Delete old snapshot overlay files from snapshot_dir
        old_snapshots = self._state.get_snapshots(vm_name)
        for snap in old_snapshots:
            if snap.path != tmp_path and snap.path != base_image:
                rm_result = self._shell.run(
                    ["rm", "-f", str(snap.path)],
                    timeout=30,
                    check=True,
                )
                if not rm_result.success:
                    logger.warning(
                        "Failed to delete old snapshot overlay %s: %s",
                        snap.path,
                        rm_result.error,
                    )

        # Step 7: Strip <backingStore> from domain XML AND update <source file>
        # (single dumpxml/define pass — combine both XML modifications)
        dumpxml = self._shell.run(
            ["virsh", "dumpxml", "--domain", vm_name],
            timeout=30,
            check=True,
        )
        if dumpxml.success:
            try:
                root = ET.fromstring(dumpxml.stdout)
                # Strip all <backingStore> elements from every <disk>
                for disk in root.iter("disk"):
                    for backing_store in disk.findall("backingStore"):
                        disk.remove(backing_store)
                    # Update <source file> to point to the restored base image
                    source = disk.find("source")
                    if source is not None:
                        source.set("file", str(base_image))
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".xml",
                    prefix=f"qsnap-restore-{vm_name}-",
                    delete=False,
                    encoding="utf-8",
                ) as tmp:
                    tmp.write(ET.tostring(root, encoding="unicode"))
                    tmp_xml_path = tmp.name
                define_result = self._shell.run(
                    ["virsh", "define", tmp_xml_path],
                    timeout=30,
                    check=True,
                )
                if not define_result.success:
                    logger.warning(
                        "virsh define failed for VM %s after restore: %s",
                        vm_name,
                        define_result.error,
                    )
                with contextlib.suppress(OSError):
                    os.unlink(tmp_xml_path)
            except ET.ParseError as exc:
                logger.warning(
                    "Failed to parse/update domain XML for VM %s after restore: %s",
                    vm_name,
                    exc,
                )
        else:
            logger.warning(
                "virsh dumpxml failed for VM %s after restore: %s",
                vm_name,
                dumpxml.error,
            )

        # Step 8: Reset all VM state (design D4)
        self._state.reset_vm_state(vm_name)
        for target in vm_config.targets:
            self._state.reset_target_state(str(target.path))

        # Step 9: Best-effort checkpoint cleanup (design D5)
        self._cleanup_checkpoints_after_restore(vm_config)

        return RestoreResult(
            success=True,
            snapshot_name=name,
            restored_path=base_image,
            chain_files=[base_image],
            error=None,
        )

    def _cleanup_checkpoints_after_restore(self, vm_config: VMConfig) -> None:
        """Best-effort cleanup of all qsnap-* checkpoints after restore.

        Lists all libvirt checkpoints with ``qsnap-`` prefix and deletes
        each via ``virsh checkpoint-delete --metadata``.  Failures are
        logged at WARNING level and do not block the restore operation
        (design D5).
        """
        # Use the first target's provider to list checkpoints; if no
        # targets, list directly via virsh.
        checkpoints: list[str] = []
        if vm_config.targets:
            provider = self._factory.create_backup_provider(
                vm_config, vm_config.targets[0]
            )
            checkpoints = provider.list_checkpoints(vm_config.name)
        else:
            # Fallback: list directly via virsh checkpoint-list
            result = self._shell.run(
                ["virsh", "checkpoint-list", "--name", "--domain", vm_config.name],
                timeout=30,
                check=True,
            )
            if result.success:
                checkpoints = [
                    line.strip()
                    for line in result.stdout.strip().splitlines()
                    if line.strip().startswith("qsnap-")
                ]

        if not checkpoints:
            return

        for cp in checkpoints:
            cmd = [
                "virsh",
                "checkpoint-delete",
                "--metadata",
                "--domain",
                vm_config.name,
                cp,
            ]
            result = self._shell.run(cmd, timeout=30, check=True)
            if result.success:
                logger.info(
                    "[restore] %s: deleted checkpoint %s",
                    vm_config.name,
                    cp,
                )
            else:
                logger.warning(
                    "[restore] %s: failed to delete checkpoint %s: %s",
                    vm_config.name,
                    cp,
                    result.error,
                )

    def fork(
        self,
        name: str,
        output_path: Path,
        vm_filter: str | None = None,
    ) -> RestoreResult:
        """Create a standalone qcow2 from a snapshot or backup.

        Resolves *name* via ``_resolve_snapshot()``, estimates the
        chain size, then runs ``qemu-img convert --force-share -O qcow2``
        to produce a standalone qcow2 at *output_path* with no backing
        dependencies.

        No XML manipulation, VM definition, or libvirt management is
        performed — creating a VM from the resulting image is the
        operator's responsibility.

        Returns a ``RestoreResult``; never raises for expected failures.
        """
        # Step 1: Resolve the snapshot/backup
        try:
            snapshot_info, _ = self._resolve_snapshot(name, vm_filter)
        except FileNotFoundError:
            return RestoreResult(
                success=False,
                snapshot_name=name,
                restored_path=output_path,
                chain_files=[],
                error=f"Snapshot not found: {name}",
            )

        source_path = snapshot_info.path

        # Step 2: Estimate chain size via qemu-img info --backing-chain
        # --force-share: the source may be the active layer of a running
        # VM with an exclusive write lock (design D1).
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
            name,
            size_str,
        )

        # Step 4: Execute image conversion (always direct, no NBD — D1)
        convert_result = self._shell.run(
            [
                "qemu-img",
                "convert",
                "--force-share",
                "-O",
                "qcow2",
                str(source_path),
                str(output_path),
            ],
            timeout=7200,
            check=True,
        )
        if not convert_result.success:
            return RestoreResult(
                success=False,
                snapshot_name=name,
                restored_path=output_path,
                chain_files=[],
                error=f"image conversion failed: {convert_result.error}",
            )

        return RestoreResult(
            success=True,
            snapshot_name=name,
            restored_path=output_path,
            chain_files=[output_path],
            error=None,
        )

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

    def _detect_phantom_snapshots(self, vm: VMConfig) -> list[SnapshotInfo]:
        """Return snapshots in state whose files don't exist on disk.

        Pure data — no state mutation, no file deletion, no dry-run
        gating.  Shared by :meth:`check_state` (reporting) and
        :meth:`reconcile` (repair).
        """
        snapshots = self._state.get_snapshots(vm.name)
        return [sn for sn in snapshots if not os.path.exists(str(sn.path))]

    def _detect_phantom_fulls(self, vm: VMConfig) -> list[tuple[TargetConfig, FullBackupInfo]]:
        """Return FULLs in state whose files don't exist on disk.

        Returns ``(TargetConfig, FullBackupInfo)`` tuples.  Pure data —
        no state mutation, no file deletion, no dry-run gating.
        """
        result: list[tuple[TargetConfig, FullBackupInfo]] = []
        for target in vm.targets:
            fulls = self._state.get_full_backups(str(target.path))
            for full in fulls:
                if not os.path.exists(str(full.path)):
                    result.append((target, full))
        return result

    def _detect_stale_deps(self, vm: VMConfig) -> list[tuple[str, str, TargetConfig]]:
        """Return incremental dependencies whose files don't exist on disk.

        Returns ``(dep_name, full_name, TargetConfig)`` tuples.  Pure
        data — no state mutation, no file deletion, no dry-run gating.
        """
        result: list[tuple[str, str, TargetConfig]] = []
        for target in vm.targets:
            target_path = str(target.path)
            fulls = self._state.get_full_backups(target_path)
            for full in fulls:
                deps = self._state.get_incremental_dependencies(target_path, full.name)
                for dep_name in deps:
                    dep_path = target.path / f"{dep_name}.qcow2"
                    if not os.path.exists(str(dep_path)):
                        result.append((dep_name, full.name, target))
        return result

    def _detect_broken_chains(self, vm: VMConfig) -> list[str]:
        """Return non-FULL backup names with broken backing chains.

        Uses :func:`scan_backing_chain` to verify chain integrity.
        Only checks qsnap-pattern files (``vm.name.*``).  Pure data —
        no state mutation, no file deletion, no dry-run gating.
        """
        result: list[str] = []
        for target in vm.targets:
            provider = self._factory.create_backup_provider(vm, target)
            backups = provider.list(target)
            for backup in backups:
                if ".FULL." in backup.name:
                    continue
                if not backup.name.startswith(f"{vm.name}."):
                    continue
                scan = scan_backing_chain(self._shell, backup.path)
                if scan.success is False or scan.broken_files:
                    result.append(backup.name)
        return result

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
            broken_chains: list[str] = []
            status_parts: list[str] = []

            # ── Phantom snapshots ────────────────────────────────────
            phantom_snaps = self._detect_phantom_snapshots(vm)
            phantom_snapshots = [
                f"{sn.name} (expected: {sn.path})" for sn in phantom_snaps
            ]
            if phantom_snapshots:
                status_parts.append("stale_snapshots")

            # ── Phantom FULLs ────────────────────────────────────────
            phantom_full_pairs = self._detect_phantom_fulls(vm)
            phantom_fulls = [
                f"{full.name} (target: {target.path})"
                for target, full in phantom_full_pairs
            ]
            if phantom_fulls:
                status_parts.append("stale_fulls")

            # ── Stale dependencies ───────────────────────────────────
            stale_dep_tuples = self._detect_stale_deps(vm)
            stale_deps = [
                f"{dep_name} → {full_name} (target: {target.path})"
                for dep_name, full_name, target in stale_dep_tuples
            ]
            if stale_deps:
                status_parts.append("stale_deps")

            # ── Broken backing chains ───────────────────────────────
            broken_chains = self._detect_broken_chains(vm)
            if broken_chains:
                status_parts.append("broken_chains")

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
                broken_chains=broken_chains,
            )
        return results

    def _detect_orphan_checkpoints(
        self,
        vm: VMConfig,
        *,
        auto_cleanup: bool = False,
    ) -> list[str]:
        """Detect (and optionally delete) orphaned libvirt checkpoints.

        Checkpoints are named ``qsnap-{target_hash}-{snapshot}`` where
        ``target_hash`` is an 8-char MD5 hash of the target path.  A
        checkpoint is orphaned when its hash does not match
        ``target_hash(str(target.path))`` for any target configured for
        this VM.

        When ``auto_cleanup=True``, deletes orphaned checkpoints via
        ``virsh checkpoint-delete --metadata`` through ``IShell.run()``.
        Returns the list of orphan checkpoint names (deleted or not).

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

        if auto_cleanup and orphans:
            for cp in orphans:
                cmd = [
                    "virsh",
                    "checkpoint-delete",
                    "--metadata",
                    "--domain",
                    vm.name,
                    cp,
                ]
                result = self._shell.run(cmd, timeout=30, check=True)
                if result.success:
                    logger.info(
                        "[reconcile] %s: deleted orphan checkpoint %s",
                        vm.name,
                        cp,
                    )
                else:
                    logger.warning(
                        "[reconcile] %s: failed to delete orphan checkpoint %s: %s",
                        vm.name,
                        cp,
                        result.error,
                    )
        return orphans

    def reconcile(
        self,
        vm_filter: str | None = None,
    ) -> dict[str, ReconcileResult]:
        """Actively repair state-vs-disk inconsistencies.

        For each VM (filtered):
        1. Remove phantom snapshots from state (file missing, XML doesn't
           reference)
        2. Supplement state from disk+XML (file exists, XML references,
           state doesn't)
        3. Refresh stale domain XML (``<backingStore>`` references missing
           files)
        4. Remove phantom FULLs from state + cascade-clean dependencies
        5. Clear stale baselines if no FULLs remain
        6. Remove stale incremental dependencies
        7. Delete orphaned libvirt checkpoints
        8. Supplement/delete orphan files on targets (intact chain →
           supplement, broken chain → CRITICAL log, truly orphan → delete)
        9. Delete truly orphan snapshot files (not in state, not in XML)

        Does NOT auto-rebase broken chains — only logs CRITICAL and leaves
        the chain for operator intervention.

        Returns per-VM ``ReconcileResult`` with counts of items fixed.

        When ``self._dry_run`` is True, reports what would be fixed
        without making any changes to state files, disk, or domain XML.
        """
        vms = self._filter_vms(vm_filter)
        results: dict[str, ReconcileResult] = {}
        for vm in vms:
            phantom_snapshots = 0
            phantom_fulls = 0
            stale_deps = 0
            baselines_cleared = 0
            orphan_ckpts = 0
            orphan_files = 0
            state_supplemented = 0
            xml_refreshed = False
            allocation_fixed = False
            errors: list[str] = []
            broken_chains: list[str] = []

            # Parse domain XML once for this VM (triple-source: XML)
            xml_paths = self._parse_domain_xml_source_paths(vm.name)

            # 1. Phantom snapshots (state has, disk doesn't, XML doesn't)
            try:
                phantom_snaps = self._detect_phantom_snapshots(vm)
                for sn in phantom_snaps:
                    # Check if XML still references this file
                    if str(sn.path) in xml_paths:
                        # Stale domain XML — don't remove from state,
                        # will be refreshed in step 3
                        logger.warning(
                            "[reconcile] %s: stale domain XML references "
                            "missing file %s — will refresh XML",
                            vm.name,
                            sn.path,
                        )
                        continue
                    # Phantom in state — remove
                    if self._dry_run:
                        logger.info(
                            "[dry-run reconcile] %s: would remove phantom snapshot %s",
                            vm.name,
                            sn.name,
                        )
                        phantom_snapshots += 1
                    else:
                        self._state.remove_snapshot(vm.name, sn.name)
                        logger.warning(
                            "[reconcile] %s: removed phantom snapshot %s (file not found: %s)",
                            vm.name,
                            sn.name,
                            sn.path,
                        )
                        phantom_snapshots += 1
            except Exception as exc:
                errors.append(f"phantom snapshots: {exc}")
                logger.warning(
                    "[reconcile] %s: error checking phantom snapshots: %s",
                    vm.name,
                    exc,
                )

            # 2. Supplement state from disk+XML reality + delete truly
            #    orphan snapshot files (not in state, not in XML)
            try:
                recorded = {sn.path.name for sn in self._state.get_snapshots(vm.name)}
                for qcow2_file in vm.snapshot_dir.glob(f"{vm.name}.*.qcow2"):
                    if qcow2_file.name in recorded:
                        # Cross-check: file in state + on disk → XML
                        # must also reference it.
                        if str(qcow2_file) not in xml_paths:
                            # Anomaly: in state + on disk but NOT in
                            # domain XML.  Remove from state to prevent
                            # blockcommit from operating on a snapshot
                            # the VM doesn't know about.  Keep file on
                            # disk as safety backup.
                            if self._dry_run:
                                logger.info(
                                    "[dry-run reconcile] %s: would remove "
                                    "snapshot %s from state — not in "
                                    "domain XML (file preserved)",
                                    vm.name,
                                    qcow2_file.name,
                                )
                                phantom_snapshots += 1
                            else:
                                self._state.remove_snapshot(
                                    vm.name, qcow2_file.stem
                                )
                                logger.warning(
                                    "[reconcile] %s: removed snapshot %s "
                                    "from state (file on disk but not "
                                    "in domain XML — file preserved)",
                                    vm.name,
                                    qcow2_file.name,
                                )
                                phantom_snapshots += 1
                        continue
                    if str(qcow2_file) in xml_paths:
                        # File on disk + in XML but not in state → supplement
                        if self._dry_run:
                            logger.info(
                                "[dry-run reconcile] %s: would supplement state "
                                "with snapshot %s (from disk+XML reality)",
                                vm.name,
                                qcow2_file.name,
                            )
                            state_supplemented += 1
                        else:
                            info = SnapshotInfo(
                                name=qcow2_file.stem,
                                path=qcow2_file,
                                timestamp=parse_timestamp(qcow2_file.name, qcow2_file),
                                allocation=0,
                            )
                            self._state.record_snapshot(vm.name, info)
                            logger.info(
                                "[reconcile] %s: state supplemented: %s recorded "
                                "from disk+XML reality",
                                vm.name,
                                qcow2_file.name,
                            )
                            state_supplemented += 1
                    else:
                        # Truly orphan — not in state, not in XML → delete
                        if self._dry_run:
                            logger.info(
                                "[dry-run reconcile] %s: would remove orphan snapshot file %s",
                                vm.name,
                                qcow2_file,
                            )
                            orphan_files += 1
                        else:
                            self._shell.run(
                                ["rm", "-f", str(qcow2_file)],
                                timeout=10,
                                check=True,
                            )
                            logger.warning(
                                "[reconcile] %s: removed orphan snapshot file "
                                "%s (not tracked in state or XML)",
                                vm.name,
                                qcow2_file,
                            )
                            orphan_files += 1
            except Exception as exc:
                errors.append(f"orphan snapshot files: {exc}")
                logger.warning(
                    "[reconcile] %s: error checking orphan snapshot files: %s",
                    vm.name,
                    exc,
                )

            # 3. Refresh stale domain XML (strip <backingStore> referencing
            #    non-existent files)
            try:
                stale_xml = any(not os.path.exists(p) for p in xml_paths)
                if stale_xml:
                    if self._dry_run:
                        logger.info(
                            "[dry-run reconcile] %s: would refresh stale domain XML",
                            vm.name,
                        )
                        xml_refreshed = True
                    else:
                        self._refresh_domain_backing_store(vm)
                        logger.warning(
                            "[reconcile] %s: stripped stale <backingStore> "
                            "from domain XML",
                            vm.name,
                        )
                        xml_refreshed = True
            except Exception as exc:
                errors.append(f"refresh domain XML: {exc}")
                logger.warning(
                    "[reconcile] %s: error refreshing domain XML: %s",
                    vm.name,
                    exc,
                )

            # 3b. last_allocation mismatch detection and correction
            try:
                last_alloc = self._state.get_last_allocation(vm.name)
                if last_alloc is not None:
                    # Determine active layer path
                    current_snaps = self._state.get_snapshots(vm.name)
                    if current_snaps:
                        active_path = str(current_snaps[-1].path)
                    else:
                        active_path = str(vm.base_image)
                    info_result = self._shell.run(
                        [
                            "qemu-img", "info", "--force-share",
                            "--output=json", active_path,
                        ],
                        timeout=30,
                        check=True,
                    )
                    if info_result.success:
                        try:
                            info = json.loads(info_result.stdout)
                            actual_size = info.get("actual-size")
                            if actual_size is not None and actual_size != last_alloc:
                                if self._dry_run:
                                    logger.info(
                                        "[dry-run reconcile] %s: would fix "
                                        "last_allocation: %d → %d",
                                        vm.name,
                                        last_alloc,
                                        actual_size,
                                    )
                                    allocation_fixed = True
                                else:
                                    self._state.set_last_allocation(
                                        vm.name, actual_size
                                    )
                                    logger.info(
                                        "[reconcile] %s: fixed last_allocation: "
                                        "%d → %d",
                                        vm.name,
                                        last_alloc,
                                        actual_size,
                                    )
                                    allocation_fixed = True
                        except (json.JSONDecodeError, TypeError):
                            pass
            except Exception as exc:
                errors.append(f"allocation mismatch: {exc}")
                logger.warning(
                    "[reconcile] %s: error checking allocation mismatch: %s",
                    vm.name,
                    exc,
                )

            # 2. Phantom FULLs with cascade cleanup
            try:
                phantom_full_pairs = self._detect_phantom_fulls(vm)
                for target, full in phantom_full_pairs:
                    target_path = str(target.path)
                    if self._dry_run:
                        logger.info(
                            "[dry-run reconcile] %s: would remove phantom FULL %s "
                            "(cascade deps)",
                            vm.name,
                            full.name,
                        )
                        phantom_fulls += 1
                    else:
                        self._state.remove_full_backup(target_path, full.name)
                        removed = self._state.remove_all_incremental_dependencies(
                            target_path, full.name
                        )
                        logger.warning(
                            "[reconcile] %s: removed phantom FULL %s "
                            "(cascade: %d deps cleaned)",
                            vm.name,
                            full.name,
                            removed,
                        )
                        phantom_fulls += 1
            except Exception as exc:
                errors.append(f"phantom FULLs: {exc}")
                logger.warning(
                    "[reconcile] %s: error checking phantom FULLs: %s",
                    vm.name,
                    exc,
                )

            # 3. Clear stale baseline if no FULLs remain
            for target in vm.targets:
                target_path = str(target.path)
                try:
                    remaining = self._state.get_full_backups(target_path)
                    if (
                        not remaining
                        and self._state.get_last_backup_allocation(target_path) is not None
                    ):
                        if self._dry_run:
                            logger.info(
                                "[dry-run reconcile] %s: would clear "
                                "last_backup_allocation for target %s",
                                vm.name,
                                target_path,
                            )
                            baselines_cleared += 1
                        else:
                            self._state.clear_last_backup_allocation(target_path)
                            logger.info(
                                "[reconcile] %s: cleared last_backup_allocation "
                                "for target %s (no FULLs remain)",
                                vm.name,
                                target_path,
                            )
                            baselines_cleared += 1
                except Exception as exc:
                    errors.append(f"baseline check for {target_path}: {exc}")
                    logger.warning(
                        "[reconcile] %s: error checking baseline for target %s: %s",
                        vm.name,
                        target_path,
                        exc,
                    )

            # 4. Stale incremental dependencies (incremental file missing)
            try:
                stale_dep_tuples = self._detect_stale_deps(vm)
                for dep_name, full_name, target in stale_dep_tuples:
                    target_path = str(target.path)
                    dep_path = target.path / f"{dep_name}.qcow2"
                    if self._dry_run:
                        logger.info(
                            "[dry-run reconcile] %s: would remove stale dep %s → %s",
                            vm.name,
                            dep_name,
                            full_name,
                        )
                        stale_deps += 1
                    else:
                        self._state.remove_incremental_dependency(
                            target_path, dep_name, full_name
                        )
                        logger.warning(
                            "[reconcile] %s: removed stale dependency "
                            "%s → %s (file not found: %s)",
                            vm.name,
                            dep_name,
                            full_name,
                            dep_path,
                        )
                        stale_deps += 1
            except Exception as exc:
                errors.append(f"stale deps: {exc}")
                logger.warning(
                    "[reconcile] %s: error checking stale deps: %s",
                    vm.name,
                    exc,
                )

            # 5. Orphan checkpoint auto-cleanup
            try:
                orphans = self._detect_orphan_checkpoints(vm, auto_cleanup=not self._dry_run)
                orphan_ckpts = len(orphans)
            except Exception as exc:
                errors.append(f"orphan checkpoints: {exc}")
                logger.warning(
                    "[reconcile] %s: error detecting orphan checkpoints: %s",
                    vm.name,
                    exc,
                )

            # 6. Orphan files on target directories — supplement state
            #    for intact chains, log CRITICAL for broken chains, delete
            #    truly orphan files.
            broken_chain_names = set(self._detect_broken_chains(vm))
            for name in broken_chain_names:
                broken_chains.append(name)
                logger.critical(
                    "[reconcile] %s: broken chain at %s — "
                    "blockcommit impossible, restore from "
                    "backup target",
                    vm.name,
                    name,
                )

            for target in vm.targets:
                target_path = str(target.path)
                try:
                    provider = self._factory.create_backup_provider(vm, target)
                    backups_on_disk = provider.list(target)

                    # Build known set from state (stems, no .qcow2 ext).
                    known_stems: set[str] = set()
                    fulls = self._state.get_full_backups(target_path)
                    for full in fulls:
                        known_stems.add(Path(full.name).stem)
                        deps = self._state.get_incremental_dependencies(target_path, full.name)
                        known_stems.update(deps)

                    for backup in backups_on_disk:
                        if backup.name in known_stems:
                            continue
                        # Only process files matching qsnap naming pattern.
                        if not backup.name.startswith(f"{vm.name}."):
                            logger.warning(
                                "[reconcile] %s: untracked .qcow2 on target %s "
                                "(not qsnap pattern, skipping): %s",
                                vm.name,
                                target_path,
                                backup.name,
                            )
                            continue

                        # Skip broken chains (already logged CRITICAL above)
                        if backup.name in broken_chain_names:
                            continue

                        # Check if chain leads to a FULL tracked in state
                        if ".FULL." not in backup.name:
                            anchor = self._resolve_chain_full_anchor(backup.path)
                            if anchor is not None:
                                # Supplement state — record dependency
                                if self._dry_run:
                                    logger.info(
                                        "[dry-run reconcile] %s: would supplement "
                                        "state with backup %s (intact chain to %s)",
                                        vm.name,
                                        backup.name,
                                        anchor,
                                    )
                                    state_supplemented += 1
                                else:
                                    self._state.record_incremental_dependency(
                                        target_path, backup.name, anchor
                                    )
                                    logger.info(
                                        "[reconcile] %s: state supplemented: %s "
                                        "recorded from disk reality",
                                        vm.name,
                                        backup.name,
                                    )
                                    state_supplemented += 1
                                continue

                        # Truly orphan — no chain, not in state → delete
                        if self._dry_run:
                            logger.info(
                                "[dry-run reconcile] %s: would remove orphan file %s on target %s",
                                vm.name,
                                backup.name,
                                target_path,
                            )
                            orphan_files += 1
                        else:
                            provider.delete(backup)
                            logger.warning(
                                "[reconcile] %s: removed orphan file %s from "
                                "target %s (not tracked in state)",
                                vm.name,
                                backup.name,
                                target_path,
                            )
                            orphan_files += 1
                except Exception as exc:
                    errors.append(f"orphan files for {target_path}: {exc}")
                    logger.warning(
                        "[reconcile] %s: error checking orphan files for target %s: %s",
                        vm.name,
                        target_path,
                        exc,
                    )

            results[vm.name] = ReconcileResult(
                vm_name=vm.name,
                phantom_snapshots_removed=phantom_snapshots,
                phantom_fulls_removed=phantom_fulls,
                stale_deps_removed=stale_deps,
                baselines_cleared=baselines_cleared,
                orphan_checkpoints_deleted=orphan_ckpts,
                orphan_files_removed=orphan_files,
                state_supplemented=state_supplemented,
                xml_refreshed=xml_refreshed,
                allocation_fixed=allocation_fixed,
                errors=errors,
                broken_chains=broken_chains,
            )
        return results

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

    def _validate_state_at_startup(self, vm_config: VMConfig) -> None:
        """Lightweight state-vs-disk check at pipeline start.

        Runs phantom FULL detection + stale baseline cleanup, then
        auto-recovery of broken backup chains, BEFORE the onchange
        gate and retention evaluation.  Non-fatal: logs warnings,
        never raises.

        Auto-recovery (spec: auto-recovery):
        - For each non-FULL backup, run ``qemu-img info --backing-chain``.
        - If the command fails (broken chain), delete the backup and
          clean its state dependency record.
        - If no valid FULL remains after recovery, set the force-full
          flag for that target.
        """
        for target in vm_config.targets:
            try:
                all_fulls = self._state.get_full_backups(str(target.path))
            except Exception as exc:
                logger.warning(
                    "[startup] %s: failed to load FULL backups for target %s: %s",
                    vm_config.name,
                    target.path,
                    exc,
                )
                continue

            if not all_fulls:
                # No FULLs in state — clear baseline if it exists
                try:
                    if self._state.get_last_backup_allocation(str(target.path)) is not None:
                        self._state.clear_last_backup_allocation(str(target.path))
                        logger.info(
                            "[startup] %s: cleared stale last_backup_allocation "
                            "for target %s (no FULLs in state)",
                            vm_config.name,
                            target.path,
                        )
                except Exception as exc:
                    logger.warning(
                        "[startup] %s: failed to clear baseline for target %s: %s",
                        vm_config.name,
                        target.path,
                        exc,
                    )
                continue

            # Check for phantom FULLs
            has_phantom = False
            for full in all_fulls:
                if not os.path.exists(str(full.path)):
                    try:
                        self._state.remove_full_backup(str(target.path), full.name)
                        removed = self._state.remove_all_incremental_dependencies(
                            str(target.path), full.name
                        )
                        logger.warning(
                            "[startup] %s: phantom FULL %s removed (cascade: %d deps cleaned)",
                            vm_config.name,
                            full.name,
                            removed,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[startup] %s: failed to remove phantom FULL %s: %s",
                            vm_config.name,
                            full.name,
                            exc,
                        )
                    has_phantom = True
            if has_phantom:
                # Re-check: if no FULLs remain, clear baseline
                try:
                    remaining = self._state.get_full_backups(str(target.path))
                    if not remaining:
                        self._state.clear_last_backup_allocation(str(target.path))
                        logger.info(
                            "[startup] %s: cleared last_backup_allocation "
                            "for target %s (no FULLs remain after phantom cleanup)",
                            vm_config.name,
                            target.path,
                        )
                except Exception as exc:
                    logger.warning(
                        "[startup] %s: failed to re-check FULLs for target %s: %s",
                        vm_config.name,
                        target.path,
                        exc,
                    )

        # Auto-recovery: detect and delete broken-chain backups (spec:
        # auto-recovery).  Runs BEFORE retention evaluation to ensure
        # per-chain grouping can resolve all chains.
        if self._preserve_backups or self._dry_run:
            # Skip auto-recovery when preserve or dry-run mode is
            # active — user wants to inspect state without modifications.
            return

        for target in vm_config.targets:
            try:
                provider = self._factory.create_backup_provider(vm_config, target)
                backups = provider.list(target)
            except Exception as exc:
                logger.warning(
                    "[startup] %s: failed to list backups for target %s: %s",
                    vm_config.name,
                    target.path,
                    exc,
                )
                continue

            if not backups:
                continue

            broken_count = 0
            full_exists = False
            for backup in backups:
                if ".FULL." in backup.name:
                    full_exists = True
                    continue
                # Non-FULL — verify backing chain integrity
                verify_result = self._shell.run(
                    [
                        "qemu-img", "info", "--force-share",
                        "--backing-chain", "--output=json",
                        str(backup.path),
                    ],
                    timeout=60,
                    check=True,
                )
                if not verify_result.success:
                    # Broken chain — log CRITICAL, preserve file for
                    # operator review.  Do NOT auto-delete — the
                    # operator must decide whether to restore from
                    # snapshots or delete manually.
                    try:
                        broken_count += 1
                        logger.critical(
                            "[startup] %s: broken backup chain at %s "
                            "on %s — preserving file for operator "
                            "review.  Run 'qsnap reconcile' to clean "
                            "up, or restore from snapshots.",
                            vm_config.name,
                            backup.name,
                            target.path,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[startup] %s: failed to check broken backup %s: %s",
                            vm_config.name,
                            backup.name,
                            exc,
                        )

            if broken_count:
                logger.critical(
                    "[startup] %s: %d broken-chain backup(s) on %s — "
                    "preserved for operator review",
                    vm_config.name,
                    broken_count,
                    target.path,
                )

            # If no valid FULL remains, force FULL creation on next backup
            if not full_exists:
                try:
                    remaining_fulls = self._state.get_full_backups(str(target.path))
                    valid_fulls = [
                        f for f in remaining_fulls if os.path.exists(str(f.path))
                    ]
                except Exception:
                    valid_fulls = []
                if not valid_fulls:
                    self._force_full_targets.add(str(target.path))
                    logger.info(
                        "[startup] %s: force-full flag set for target %s "
                        "(no valid FULL remains)",
                        vm_config.name,
                        target.path,
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

        # Step 1b: State-vs-disk validation (non-fatal, before onchange gate)
        self._validate_state_at_startup(vm_config)
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
        """Step 3: Evaluate which snapshots to keep/remove.

        After the retention engine produces keep/remove lists, Core
        post-processes the remove list to only include items forming a
        contiguous oldest prefix (spec: snapshot-oldest-prefix).  Items
        in the original remove list that are NOT in the oldest prefix
        are moved to the keep list (chain gap fillers) so blockcommit
        always processes a contiguous range from the base image.

        After the oldest-prefix filter, Core applies a ``preserve_min``
        post-processing filter (spec: snapshot-preserve-min) that
        guarantees the newest N snapshots are never blockcommitted,
        even when ``chain_length`` is exceeded.  When ``preserve_min``
        is 0 (default), the filter is inactive.
        """
        snapshots = self._state.get_snapshots(vm_config.name)
        if not snapshots:
            return None

        policy = RetentionPolicy(
            chain_length=vm_config.snapshot_chain_length or 0,
            keep_generations=1,
            preserve_min=vm_config.snapshot_preserve_min or 0,
        )
        engine = self._factory.create_retention_engine(policy)
        items = [RetentionItem(name=s.name, timestamp=s.timestamp) for s in snapshots]
        result = engine.evaluate(items, policy, datetime.now())

        # Oldest-prefix post-processing (spec: snapshot-oldest-prefix).
        sorted_snaps = sorted(snapshots, key=lambda s: s.timestamp)
        original_remove = set(result.remove)
        final_remove: list[str] = []
        final_keep: list[str] = list(result.keep)
        prefix_done = False
        for snap in sorted_snaps:
            if not prefix_done and snap.name in original_remove:
                final_remove.append(snap.name)
            elif snap.name in original_remove:
                # Non-prefix remove item → moved to keep (chain gap filler)
                final_keep.append(snap.name)
            else:
                # First keep item encountered → prefix stops
                prefix_done = True

        # preserve_min post-processing (spec: snapshot-preserve-min).
        # Applied AFTER the oldest-prefix filter. Guarantees the newest
        # N snapshots are never blockcommitted, even when chain_length is
        # exceeded.  Trims from the newest end of the remove list (moves
        # newest excess items to keep) so the oldest snapshots remain
        # eligible for blockcommit.
        preserve_min = policy.preserve_min
        if preserve_min > 0:
            max_removable = max(0, len(snapshots) - preserve_min)
            if len(final_remove) > max_removable:
                # final_remove is ordered oldest-first; keep the oldest
                # max_removable items, move the newest excess to keep.
                excess = final_remove[max_removable:]
                final_remove = final_remove[:max_removable]
                final_keep.extend(excess)

        return RetentionResult(keep=final_keep, remove=final_remove)

    def _verify_backing_chain(self, vm_config: VMConfig) -> ChainVerifyResult:
        """Verify backing chain integrity of the active disk image.

        Calls :func:`scan_backing_chain` on the most recent snapshot
        (or base image if no snapshots).  Converts ``ChainScanResult``
        → ``ChainVerifyResult``, mapping ``broken_files[0]`` to
        ``broken_file`` when non-empty.

        Returns ``ChainVerifyResult`` — never raises.
        """
        snapshots = self._state.get_snapshots(vm_config.name)
        # Use the most recent snapshot as the chain entry point, or
        # fall back to the base image if no snapshots exist.
        if snapshots:
            active_path = max(snapshots, key=lambda s: s.timestamp).path
        else:
            active_path = vm_config.base_image

        scan = scan_backing_chain(self._shell, active_path)

        if not scan.success:
            # scan_backing_chain failed (command or parse error) — try
            # to find the broken file for partial blockcommit recovery
            # (spec: blockcommit-recovery).
            broken = self._find_broken_chain_file(active_path)
            return ChainVerifyResult(
                success=False,
                error=scan.error or "scan_backing_chain failed",
                broken_file=broken,
            )

        if scan.broken_files:
            # Chain has integrity issues (missing files, wrong format,
            # cycles, etc.) — report the first broken file.
            first = scan.broken_files[0]
            return ChainVerifyResult(
                success=False,
                error=f"Backing chain broken: {', '.join(scan.broken_files)}",
                broken_file=Path(first),
            )

        return ChainVerifyResult(success=True, error=None, broken_file=None)

    def _find_broken_chain_file(self, start_path: Path) -> Path | None:
        """Walk the backing chain from *start_path* to find the first missing file.

        Used when ``qemu-img info --backing-chain`` fails to identify
        which specific file is broken (spec: blockcommit-recovery).
        Returns the path of the first missing file, or ``None`` if the
        chain walk itself fails or the chain is intact.
        """
        current = Path(start_path)
        for _ in range(64):  # bound the walk — real chains are short
            # Check if current file exists.
            existence = self._shell.run(
                ["test", "-f", str(current)],
                timeout=10,
                check=True,
            )
            if not existence.success:
                return current

            # Get backing-filename for the current file.
            info_result = self._shell.run(
                ["qemu-img", "info", "--output=json", str(current)],
                timeout=30,
                check=True,
            )
            if not info_result.success:
                return current

            try:
                info = json.loads(info_result.stdout)
            except json.JSONDecodeError:
                return None

            backing = info.get("backing-filename")
            if not isinstance(backing, str) or not backing:
                # No backing file — chain is complete, nothing broken.
                return None

            backing_path = Path(backing)
            if not backing_path.is_absolute():
                backing_path = current.parent / backing_path

            current = backing_path

        return None

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
        stuck: list[SnapshotInfo] = []
        if global_cfg.chain_verify_before_commit:
            verify_result = self._verify_backing_chain(vm_config)
            if not verify_result.success:
                if verify_result.broken_file is not None:
                    # Partial blockcommit (design D7): split the
                    # committable list at the broken file, commit the
                    # portion before the break, and auto-rebase the
                    # stuck snapshots onto the new base.
                    before_break, stuck = self._split_at_break(
                        vm_config, committable, verify_result.broken_file,
                    )
                    if not before_break:
                        msg = (
                            f"Snapshot chain broken for VM {vm_config.name}: "
                            f"{verify_result.error}. "
                            f"No snapshots can be committed before the break "
                            f"at {verify_result.broken_file}. "
                            f"Blockcommit cannot proceed. "
                            f"Run 'qsnap check --deep' and restore "
                            f"the chain before continuing."
                        )
                        logger.critical(msg)
                        raise RuntimeError(msg)
                    logger.warning(
                        "Pre-commit chain verification found break at %s — "
                        "partial blockcommit: %d snapshot(s) committable, "
                        "%d stuck (will be auto-rebased)",
                        verify_result.broken_file,
                        len(before_break),
                        len(stuck),
                    )
                    committable = before_break
                else:
                    msg = (
                        f"Snapshot chain verification failed for VM "
                        f"{vm_config.name}: {verify_result.error}. "
                        f"Blockcommit cannot proceed. "
                        f"Run 'qsnap check --deep' and restore "
                        f"the chain before continuing."
                    )
                    logger.critical(msg)
                    # Do NOT defer — broken chain needs operator intervention
                    raise RuntimeError(msg)
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
        result = manager.blockcommit(
            vm_config, committable,
            deep_verify=vm_config.blockcommit_deep_verify,
        )

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

        # Auto-rebase stuck snapshots (design D7): after a partial
        # blockcommit, snapshots at or after the chain break need to be
        # rebased onto the new base (the base image, now containing the
        # committed data).  Uses ``qemu-img rebase -u`` (unsafe mode)
        # because the original backing chain is broken.
        if stuck:
            self._auto_rebase_stuck(vm_config, stuck)

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

    def _split_at_break(
        self,
        vm_config: VMConfig,
        committable: list[SnapshotInfo],
        broken_file: Path,
    ) -> tuple[list[SnapshotInfo], list[SnapshotInfo]]:
        """Split committable snapshots at the broken file (design D7).

        Walks the backing chain to determine which snapshots are
        before the broken file (safe to commit) and which are at or
        after the break (stuck — need auto-rebase).

        Returns ``(before_break, stuck)``.  When the chain cannot be
        walked (e.g., qemu-img fails), returns ``(committable, [])``
        as a conservative fallback — all snapshots are treated as
        committable, letting the blockcommit manager handle any
        failures.
        """
        # Walk the backing chain to get the ordered list of file paths.
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
            # qemu-img info --backing-chain failed — use per-file
            # queries to find the break point (spec: blockcommit-recovery).
            # For each committable snapshot (oldest first), query its
            # backing-filename.  Once we find a snapshot whose backing
            # file is the broken one (or that IS the broken file), all
            # subsequent snapshots are stuck.
            before_break: list[SnapshotInfo] = []
            stuck: list[SnapshotInfo] = []
            found_break = False
            for snap in sorted(committable, key=lambda s: s.timestamp):
                if found_break:
                    stuck.append(snap)
                    continue
                if str(snap.path) == str(broken_file):
                    # This snapshot IS the broken file — stuck.
                    stuck.append(snap)
                    found_break = True
                    continue
                # Query this snapshot's backing-filename.
                info_result = self._shell.run(
                    ["qemu-img", "info", "--output=json", str(snap.path)],
                    timeout=30,
                    check=True,
                )
                if not info_result.success:
                    # Can't query — treat as stuck (conservative).
                    stuck.append(snap)
                    found_break = True
                    continue
                try:
                    info = json.loads(info_result.stdout)
                except json.JSONDecodeError:
                    stuck.append(snap)
                    found_break = True
                    continue
                backing = info.get("backing-filename", "")
                if backing and str(Path(backing)) == str(broken_file):
                    # This snapshot's backing file is the broken one — stuck.
                    stuck.append(snap)
                    found_break = True
                else:
                    before_break.append(snap)
            return before_break, stuck

        try:
            chain_data = cast(list[dict[str, object]], json.loads(result.stdout))
        except json.JSONDecodeError:
            logger.warning(
                "Could not parse backing chain for split-at-break — "
                "treating all as committable"
            )
            return committable, []

        # chain_data is [active, snap_n, ..., snap_1, base] (newest to oldest).
        # Build an ordered list of paths (oldest to newest) and find the
        # broken file's position.
        chain_paths: list[str] = []
        for item in reversed(chain_data):
            image = cast(str, item.get("image") or item.get("filename", ""))
            if image:
                chain_paths.append(image)

        broken_str = str(broken_file)
        broken_idx: int | None = None
        for i, path in enumerate(chain_paths):
            if path == broken_str:
                broken_idx = i
                break

        if broken_idx is None:
            # Broken file not found in the chain — it might be a
            # backing-filename reference that doesn't correspond to an
            # actual chain entry.  Treat all as stuck (conservative).
            logger.warning(
                "Broken file %s not found in backing chain — "
                "treating all committable snapshots as stuck",
                broken_file,
            )
            return [], committable

        # Snapshots before broken_idx in the chain are committable.
        before_paths = set(chain_paths[:broken_idx])
        before_break = [s for s in committable if str(s.path) in before_paths]
        stuck = [s for s in committable if str(s.path) not in before_paths]

        return before_break, stuck

    def _auto_rebase_stuck(
        self,
        vm_config: VMConfig,
        stuck: list[SnapshotInfo],
    ) -> None:
        """Rebase stuck snapshots onto the base image (design D7).

        After a partial blockcommit, snapshots at or after the chain
        break need to be rebased onto the base image (which now
        contains the committed data).  Uses ``qemu-img rebase -u``
        (unsafe mode) because the original backing chain is broken —
        data consistency is not guaranteed, but the chain is made
        traversable for future blockcommit attempts.

        Snapshots whose files no longer exist on disk are removed from
        state (self-healing).
        """
        new_base = vm_config.base_image
        for snap in sorted(stuck, key=lambda s: s.timestamp, reverse=True):
            exists = self._shell.run(
                ["test", "-f", str(snap.path)], timeout=10, check=True,
            )
            if not exists.success:
                # File already gone — clean up state.
                self._state.remove_snapshot(vm_config.name, snap.name)
                logger.info(
                    "Stuck snapshot %s file not found — removed from state",
                    snap.name,
                )
                continue
            rebase_cmd = [
                "qemu-img",
                "rebase",
                "-u",
                "-b",
                str(new_base),
                "-F",
                "qcow2",
                str(snap.path),
            ]
            result = self._shell.run(rebase_cmd, timeout=60, check=True)
            if not result.success:
                logger.warning(
                    "Failed to auto-rebase stuck snapshot %s onto %s: %s",
                    snap.path,
                    new_base,
                    result.error,
                )
            else:
                logger.info(
                    "[blockcommit] %s: auto-rebased stuck snapshot %s onto %s",
                    vm_config.name,
                    snap.name,
                    new_base,
                )

    # ── backup steps (5) ───────────────────────────────────────────────

    def _execute_backup_steps(self, vm_config: VMConfig) -> bool:
        """Step 5: For each target — backup transfer → retention → cleanup.

        Returns True if any backup transfer failed.
        """
        # State-vs-disk validation (non-fatal, before onchange gate).
        # Ensures standalone ``qsnap backup`` also gets self-healing.
        self._validate_state_at_startup(vm_config)
        snapshots = self._state.get_snapshots(vm_config.name)
        backup_failed = False
        for target in vm_config.targets:
            if self._backup_target(vm_config, target, snapshots):
                backup_failed = True
        return backup_failed

    def _execute_with_retry(
        self,
        operation: Callable[[], Any],
        target: TargetConfig,
        *,
        is_retryable_fn: Callable[[str], bool] = is_retryable,
    ) -> Any:
        """Execute *operation* with exponential backoff retry.

        When ``target.backup_retry_max <= 0``, executes *operation*
        exactly once (no retry loop).  When ``> 0``, loops up to
        ``target.backup_retry_max`` times.  On each attempt:

        1. Call ``operation()`` and check ``result.success``.
        2. If ``True``, return *result* immediately.
        3. If ``False``, check ``is_retryable_fn(result.error or "")``.
        4. If not retryable, return *result* immediately.
        5. If retryable and more attempts remain, compute backoff via
           ``compute_backoff(base_seconds, attempt)`` and sleep.
        6. If all attempts exhausted, return the last *result*.

        The *operation* callable must return an object with a
        ``success`` boolean attribute and an ``error`` string attribute
        (non-None iff success is False).
        """
        max_retries = target.backup_retry_max
        base_seconds = parse_retry_duration(target.backup_retry_base)

        if max_retries <= 0:
            return operation()

        result: Any = None
        for attempt in range(1, max_retries + 1):
            result = operation()
            if getattr(result, "success", True):
                if attempt > 1:
                    logger.info(
                        "Operation for target %s succeeded on retry "
                        "attempt %d/%d",
                        target.path,
                        attempt,
                        max_retries,
                    )
                return result

            error = getattr(result, "error", None) or ""
            if not is_retryable_fn(error):
                return result

            if attempt >= max_retries:
                logger.warning(
                    "Operation for target %s failed after %d retries",
                    target.path,
                    max_retries,
                )
                return result

            backoff = compute_backoff(base_seconds, attempt)
            logger.info(
                "Retrying operation for target %s (attempt %d/%d, "
                "backoff %.1fs)",
                target.path,
                attempt + 1,
                max_retries,
                backoff,
            )
            time.sleep(backoff)

        return result

    def _transfer_with_retry(
        self,
        provider: IBackupProvider,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
        *,
        compression_type: str = "zstd",
        stall_timeout: int = 1800,
        convert_parallel: int = 4,
        convert_out_of_order: bool = True,
    ) -> list[BackupResult]:
        """Transfer missing snapshots with exponential backoff retry.

        Delegates the retry loop to :meth:`_execute_with_retry`.  Only
        retries on transient errors (determined by ``is_retryable()``).
        Non-retryable errors fail immediately.

        ``compression_type`` and ``stall_timeout`` are threaded from
        ``TargetConfig`` to the provider's ``transfer_missing()``.

        Returns the list of ``BackupResult`` objects from the last
        attempt.
        """
        def operation() -> _RetryResult:
            results = provider.transfer_missing(
                vm_config,
                target,
                snapshots,
                compression_type=compression_type,
                stall_timeout=stall_timeout,
                convert_parallel=convert_parallel,
                convert_out_of_order=convert_out_of_order,
            )
            failed = [r for r in results if not r.success]
            if not failed:
                return _RetryResult(success=True, error=None, payload=results)
            # Combine all failure errors — if any is non-retryable,
            # the combined string will contain a non-retryable pattern
            # and _execute_with_retry will short-circuit.
            combined_error = "; ".join(r.error or "" for r in failed)
            return _RetryResult(success=False, error=combined_error, payload=results)

        result = self._execute_with_retry(operation, target)
        return cast(_RetryResult, result).payload  # type: ignore[return-value]

    def _should_backup_onchange(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
    ) -> tuple[bool, ChangeResult]:
        """Return ``(should_proceed, change_result)`` for ``onchange`` mode.

        Source-disk-based change detection (spec: independent-target-onchange):
        queries the VM's active disk directly via ``IChangeDetector``,
        independent of snapshot existence.  Compares
        ``change_result.current_allocation`` against the per-target
        baseline stored in ``IStateManager.get_last_backup_allocation``.

        For ``allocation-size`` mode: ``changed = current > last``.
        For ``allocation-map`` mode: ``changed = current != last``.
        When ``last`` is ``None`` (first run): return ``True`` (proceed).

        The gate does NOT call ``provider.list(target)`` — it is
        independent of snapshot names and target file listing.
        """
        detector = self._factory.create_change_detector(
            vm_config.change_detection_mode
        )
        change_result = detector.has_changed(vm_config)

        last = self._state.get_last_backup_allocation(str(target.path))
        if last is None:
            # First run (or after clear) — always proceed.
            return True, change_result

        current = change_result.current_allocation
        if vm_config.change_detection_mode == "allocation-size":
            changed = current > last
        elif vm_config.change_detection_mode == "allocation-map":
            changed = current != last
        else:
            # Unrecognized mode — fail-safe: proceed with backup.
            logger.warning(
                "[backup] %s: unrecognized change_detection_mode %r — "
                "proceeding fail-safe",
                vm_config.name,
                vm_config.change_detection_mode,
            )
            changed = True

        # Fail-safe: when the detector reports changed=True due to a
        # command failure (virsh/qemu-img error), proceed with the backup
        # regardless of the per-target baseline comparison (spec:
        # independent-target-onchange, detector fail-safe behavior).
        # Distinguish real failure from first-run short-circuit: when
        # last_allocation > 0 (snapshot-side baseline exists) but
        # current_allocation == 0, the detector failed to query the disk.
        # When last_allocation == 0, it's a first-run short-circuit — the
        # per-target comparison handles it (first run → last is None → True).
        if (
            change_result.changed
            and change_result.last_allocation > 0
            and change_result.current_allocation == 0
            and not changed
        ):
            changed = True

        if not changed:
            logger.info(
                "[backup] %s: disk unchanged since last backup — skipping transfer",
                vm_config.name,
            )

        return changed, change_result

    def _backup_target(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        snapshots: list[SnapshotInfo],
    ) -> bool:
        """Transfer missing snapshots to *target*, run retention, cleanup.

        Returns True if any backup transfer failed.
        """
        # Per-target onchange gate: skip transfer when the source disk
        # has not changed since the last backup to this target (spec:
        # independent-target-onchange).  Retention + cleanup still run
        # even when transfer is skipped.
        skip_transfer = False
        change_result: ChangeResult | None = None
        if target.backup_create == "onchange":
            should_proceed, change_result = self._should_backup_onchange(
                vm_config, target
            )
            if not should_proceed:
                skip_transfer = True

        provider = self._factory.create_backup_provider(vm_config, target)
        backup_failed = False

        # Parse stall_timeout from target config (duration string → seconds).
        # "0s" disables stall detection → stall_timeout=0 → providers fall
        # back to fixed-timeout shell.run().
        stall_timeout = parse_stall_timeout(target.backup_stall_timeout)

        full_verification_failed = False
        if not skip_transfer:
            # Count-based FULL backup decision (design D2).
            # The decision is a simple count check: create a new
            # FULL when the incremental count in the newest chain
            # exceeds target_chain_length, or when no FULLs exist
            # (first backup to target).
            if snapshots:
                all_fulls = self._state.get_full_backups(str(target.path))
                # Filter out phantom FULLs — entries in state whose files
                # no longer exist (deleted externally, disk failure, etc.).
                filtered_fulls: list[FullBackupInfo] = []
                for full in all_fulls:
                    if os.path.exists(str(full.path)):
                        filtered_fulls.append(full)
                    else:
                        # Cascade cleanup: remove FULL + all linked
                        # dependencies (phantom cascade).
                        self._state.remove_full_backup(str(target.path), full.name)
                        removed = self._state.remove_all_incremental_dependencies(
                            str(target.path), full.name
                        )
                        logger.warning(
                            "Phantom FULL entry: %s file not found — removed from state "
                            "(cascade: %d dependency record(s) cleaned)",
                            full.name,
                            removed,
                        )
                # Clear last_backup_allocation if no FULLs remain (target is empty)
                if not filtered_fulls and all_fulls:
                    self._state.clear_last_backup_allocation(str(target.path))
                    logger.info(
                        "Cleared last_backup_allocation for target %s — no FULLs remain",
                        target.path,
                    )
                all_fulls = filtered_fulls

                # Determine whether a new FULL is needed.
                if str(target.path) in self._force_full_targets:
                    should_full = True
                    self._force_full_targets.discard(str(target.path))
                    logger.info(
                        "[backup] %s: force-full flag active for target %s — "
                        "creating FULL unconditionally",
                        vm_config.name,
                        target.path,
                    )
                elif not all_fulls:
                    # First backup to target — always create a FULL.
                    should_full = True
                else:
                    # Count incrementals in the newest chain.
                    newest_full = max(all_fulls, key=lambda f: f.timestamp)
                    deps = self._state.get_incremental_dependencies(
                        str(target.path), newest_full.name
                    )
                    incremental_count = len(deps)
                    chain_length = target.target_chain_length
                    should_full = chain_length is not None and incremental_count > chain_length

                if should_full:
                    if self._dry_run:
                        # Log FULL-would-be-created without executing.
                        vm_state = (
                            "running" if is_vm_running(self._shell, vm_config.name) else "stopped"
                        )
                        chain_length = target.target_chain_length or 0
                        logger.info(
                            "[dry-run] Would create FULL backup "
                            "(chain_length=%d, method=NBD, VM=%s)",
                            chain_length,
                            vm_state,
                        )
                    else:
                        most_recent = max(snapshots, key=lambda s: s.timestamp)
                        global_cfg = self._config.get_global()

                        def _create_full_operation() -> BackupResult:
                            """Create FULL backup, verify, record — or rollback on failure."""
                            full_result = provider.create_full_backup(
                                vm_config.name,
                                most_recent,
                                target,
                                compress=target.compress,
                                compression_type=target.compression_type,
                                stall_timeout=stall_timeout,
                                convert_parallel=target.convert_parallel,
                                convert_out_of_order=target.convert_out_of_order,
                            )
                            if not full_result.success:
                                return full_result

                            # ── Post-create FULL backup verification ────
                            # (verify-before-delete gate — design D3).
                            verify_error = verify_full_backup(
                                self._shell,
                                full_result.target_path,
                                global_cfg.full_verify_after_create,
                                source_path=most_recent.path,
                            )
                            if verify_error is not None:
                                # Rollback: delete FULL file + checkpoint +
                                # state records (design D4).
                                self._shell.run(
                                    ["rm", "-f", str(full_result.target_path)],
                                    timeout=10,
                                )
                                self._cleanup_failed_checkpoint(
                                    vm_config, target, full_result
                                )
                                full_name = full_result.target_path.stem
                                self._state.remove_full_backup(
                                    str(target.path), f"{full_name}.qcow2"
                                )
                                logger.warning(
                                    "FULL backup verification failed for VM %s "
                                    "target %s — rolled back: %s",
                                    vm_config.name,
                                    target.path,
                                    verify_error,
                                )
                                return BackupResult(
                                    success=False,
                                    snapshot_name=full_result.snapshot_name,
                                    source_path=full_result.source_path,
                                    target_path=full_result.target_path,
                                    bytes_transferred=full_result.bytes_transferred,
                                    error=verify_error,
                                    duration=full_result.duration,
                                )

                            # Verification passed — record + log.
                            full_name = full_result.target_path.stem
                            self._state.record_full_backup(
                                str(target.path),
                                f"{full_name}.qcow2",
                                most_recent.timestamp,
                            )
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
                            return full_result

                        full_result = self._execute_with_retry(
                            _create_full_operation, target
                        )
                        if not full_result.success:
                            # All retries exhausted or non-retryable
                            # failure — CRITICAL, keep old generations
                            # (verify-before-delete gate).
                            full_verification_failed = True
                            backup_failed = True
                            logger.critical(
                                "FULL backup creation failed for VM %s "
                                "target %s — old generations preserved",
                                vm_config.name,
                                target.path,
                            )

            # Transfer missing snapshots (with retry when configured)
            if not self._dry_run:
                results = self._transfer_with_retry(
                    provider,
                    vm_config,
                    target,
                    snapshots,
                    compression_type=target.compression_type,
                    stall_timeout=stall_timeout,
                    convert_parallel=target.convert_parallel,
                    convert_out_of_order=target.convert_out_of_order,
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
                            r.bytes_transferred / (1024 * 1024) / r.duration
                            if r.duration > 0
                            else 0.0
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

        # Update per-target baseline after successful backup (onchange
        # mode).  The baseline is the source disk's current_allocation
        # at the time of the last successful backup.  Not updated on
        # failure (fail-safe — next run retries) or when the gate
        # skipped transfer (baseline already current).  Not updated in
        # dry-run (no actual transfer occurred).
        if (
            change_result is not None
            and not skip_transfer
            and not backup_failed
            and not self._dry_run
        ):
            self._state.set_last_backup_allocation(
                str(target.path), change_result.current_allocation
            )

        # Backup retention + cleanup — runs unless FULL verification
        # failed (verify-before-delete gate: old generations must not
        # be deleted when the new FULL is unverified).  When transfer
        # is skipped, retention + cleanup still runs to clean expired
        # backups.
        if not full_verification_failed:
            backups, retention_result = self._evaluate_backup_retention(vm_config, target)
            self._cleanup_backups(vm_config, target, backups, retention_result)

        return backup_failed

    def _cleanup_failed_checkpoint(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        full_result: BackupResult,
    ) -> None:
        """Delete libvirt checkpoints created during a failed FULL attempt.

        Lists checkpoints via ``virsh checkpoint-list --name``, filters
        for ``qsnap-{target_hash}-*`` prefix, and deletes each via
        ``virsh checkpoint-delete --metadata``.  Non-fatal — logs
        warnings on failure (spec: core-orchestrator).
        """
        provider = self._factory.create_backup_provider(vm_config, target)
        target_hash = provider.target_hash(str(target.path))
        prefix = f"qsnap-{target_hash}-"
        checkpoints = provider.list_checkpoints(vm_config.name)
        failed_checkpoints = [cp for cp in checkpoints if cp.startswith(prefix)]
        if not failed_checkpoints:
            return
        for cp in failed_checkpoints:
            cmd = [
                "virsh",
                "checkpoint-delete",
                "--metadata",
                "--domain",
                vm_config.name,
                cp,
            ]
            result = self._shell.run(cmd, timeout=30, check=True)
            if result.success:
                logger.info(
                    "[backup] %s: deleted checkpoint %s after failed FULL",
                    vm_config.name,
                    cp,
                )
            else:
                logger.warning(
                    "[backup] %s: failed to delete checkpoint %s after failed FULL: %s",
                    vm_config.name,
                    cp,
                    result.error,
                )

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

    def _group_backups_by_chain(
        self,
        backups: list[SnapshotInfo],
    ) -> dict[str, list[SnapshotInfo]]:
        """Group backups by their FULL anchor chain.

        Returns ``{chain_id: [backups in chain]}``.

        - FULL backups (filename contains ``.FULL.``) are their own
          ``chain_id`` (key = backup.name).
        - Incrementals are grouped by ``_resolve_chain_full_anchor()``
          which walks the backing chain to the FULL.
        - Orphans (anchor resolution returns ``None``) are grouped
          under ``"__orphan__"`` and placed in the remove list for
          auto-recovery cleanup (spec: per-chain-retention).
        """
        chains: dict[str, list[SnapshotInfo]] = {}
        for backup in backups:
            if ".FULL." in backup.name:
                chain_id = backup.name
            else:
                chain_id = self._resolve_chain_full_anchor(backup.path)
                if chain_id is None:
                    chain_id = "__orphan__"
            chains.setdefault(chain_id, []).append(backup)
        return chains

    def _evaluate_backup_retention(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
    ) -> tuple[list[SnapshotInfo], RetentionResult | None]:
        """List backups on *target* and evaluate per-chain retention.

        Groups backups by chain (FULL anchor), creates one
        ``RetentionItem`` per chain using the FULL's timestamp, and
        expands chain-level results to individual items.  Orphaned
        incrementals (broken backing chain) are placed in the remove
        list for auto-recovery cleanup.

        Returns ``(backups, retention_result)``.  When no backups
        exist, ``retention_result`` is ``None``.
        """
        provider = self._factory.create_backup_provider(vm_config, target)
        backups = provider.list(target)
        if not backups:
            return [], None

        chains = self._group_backups_by_chain(backups)

        # Build chain-level retention items (one per chain, FULL's timestamp).
        chain_items: list[RetentionItem] = []
        chain_map: dict[str, list[SnapshotInfo]] = {}
        for chain_id, chain_backups in chains.items():
            chain_map[chain_id] = chain_backups
            full_backup = next(
                (b for b in chain_backups if ".FULL." in b.name), None
            )
            representative = full_backup if full_backup else chain_backups[0]
            chain_items.append(
                RetentionItem(name=chain_id, timestamp=representative.timestamp)
            )

        policy = RetentionPolicy(
            chain_length=0, keep_generations=target.target_keep_generations or 1
        )
        engine = self._factory.create_retention_engine(policy)
        chain_result = engine.evaluate(
            chain_items, policy, datetime.now()
        )

        # Expand chain-level results to individual items.
        remove_chains = set(chain_result.remove)

        final_keep: list[str] = []
        final_remove: list[str] = []

        for chain_id, chain_backups in chain_map.items():
            if chain_id == "__orphan__":
                # Broken chain — preserve files for operator review.
                # Already logged as CRITICAL by startup validation.
                for b in chain_backups:
                    final_keep.append(b.name)
            elif chain_id in remove_chains:
                for b in chain_backups:
                    final_remove.append(b.name)
            else:
                for b in chain_backups:
                    final_keep.append(b.name)

        return backups, RetentionResult(keep=final_keep, remove=final_remove)

    def _cleanup_backups(
        self,
        vm_config: VMConfig,
        target: TargetConfig,
        backups: list[SnapshotInfo],
        retention_result: RetentionResult | None,
    ) -> None:
        """Delete backups flagged for removal by per-chain retention.

        Honours ``_preserve_backups`` and ``_dry_run``.

        Per-chain deletion (spec: per-chain-retention):
        - FULLs: M1 verification (non-configurable), M2 if configured,
          then delete + state cleanup (remove_full_backup +
          remove_all_incremental_dependencies).
        - Incrementals: resolve FULL anchor before deletion, then
          delete + state cleanup (remove_incremental_dependency).
        - No cascade-deletion, no backing_refs.
        - Post-cleanup: verify chain integrity of all keep-set items.
        """
        if not retention_result or not retention_result.remove:
            # Nothing to delete, but still run post-cleanup verification
            # on keep-set items (spec: per-chain-retention).
            if retention_result and retention_result.keep:
                self._verify_keep_set_chains(
                    backups, set(retention_result.keep), target
                )
            return

        keep_set = set(retention_result.keep)
        # Process newest-first (descending timestamp) so that children
        # (dependents) are deleted before parents (backing files).
        to_delete = [b for b in backups if b.name in retention_result.remove]
        to_delete.reverse()

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
                # M1 (metadata) verification is NON-CONFIGURABLE — always
                # enforced to prevent data loss from deleting a corrupt FULL.
                m1_error = verify_full_backup(self._shell, backup.path, "metadata")
                if m1_error is not None:
                    logger.critical(
                        "FULL backup %s is corrupt — blocking deletion. "
                        "Run: qsnap check --deep %s. Error: %s",
                        backup.name,
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
                            "deletion. Run: qsnap check --deep %s. Error: %s",
                            backup.name,
                            target.path,
                            m2_error,
                        )
                        continue

                # M1 (and optionally M2) passed — delete FULL
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
                # Clean up state: remove FULL + all dependency records.
                self._state.remove_full_backup(str(target.path), backup.name)
                self._state.remove_all_incremental_dependencies(
                    str(target.path), backup.name
                )
            else:
                # Incremental — resolve FULL anchor BEFORE deletion
                # (the file is needed to walk the backing chain).
                anchor = self._resolve_chain_full_anchor(backup.path)
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
                # Clean up state: remove the incremental→FULL dependency.
                if anchor is not None:
                    self._state.remove_incremental_dependency(
                        str(target.path), backup.name, anchor
                    )
                else:
                    # Anchor resolution failed (broken chain) — search
                    # all FULLs for this target to find and remove the
                    # orphaned dependency record (spec: per-chain-retention).
                    for full_info in self._state.get_full_backups(str(target.path)):
                        deps = self._state.get_incremental_dependencies(
                            str(target.path), full_info.name
                        )
                        if backup.name in deps:
                            self._state.remove_incremental_dependency(
                                str(target.path), backup.name, full_info.name
                            )

        # Post-cleanup chain integrity verification (spec:
        # per-chain-retention).  Verify that all keep-set items with
        # backing chains have intact chains.
        self._verify_keep_set_chains(backups, keep_set, target)

    def _verify_keep_set_chains(
        self,
        backups: list[SnapshotInfo],
        keep_set: set[str],
        target: TargetConfig,
    ) -> None:
        """Verify backing chain integrity of keep-set incremental backups.

        Calls :func:`scan_backing_chain` for each incremental in the
        keep-set (non-FULL items).  If the scan fails or reports
        broken files, a CRITICAL log is emitted so the operator can
        run ``qsnap check --deep`` (spec: per-chain-retention).
        """
        for backup in backups:
            if backup.name in keep_set and ".FULL." not in backup.name:
                scan = scan_backing_chain(self._shell, backup.path)
                if not scan.success or scan.broken_files:
                    logger.critical(
                        "post-cleanup verification FAILED for %s — "
                        "backing chain is broken. Run: qsnap check --deep %s",
                        backup.name,
                        target.path,
                    )

    def _generate_snapshot_name(self, vm_config: VMConfig, disk: str) -> str:
        """Generate a unique snapshot name with seconds-resolution timestamp and hex suffix.

        The name format is ``{vm_name}.{YYYYMMDDTHHMMSS}_{disk}_{6hex}``
        (design D2) to support multi-disk VMs.  The 6-character hex
        suffix (``secrets.token_hex(3)``) guarantees uniqueness even
        when two snapshots are created within the same second.

        If a snapshot file with the same name already exists
        (astronomically unlikely with the hex suffix), a collision
        suffix ``_N`` (starting at 1) is appended.
        """
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        hex_suffix = secrets.token_hex(3)
        base_name = f"{vm_config.name}.{timestamp}_{disk}_{hex_suffix}"

        # Collision suffix: append _N if the file already exists.
        name = base_name
        counter = 1
        while (vm_config.snapshot_dir / f"{name}.qcow2").exists():
            name = f"{base_name}_{counter}"
            counter += 1
        return name

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
