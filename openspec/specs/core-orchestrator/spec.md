# Core Orchestrator

## Purpose

Core is the pipeline runner and dependency-injection host: it coordinates environment validation, deferred-operation draining, change detection, snapshot creation, retention evaluation, adaptive blockcommit lifecycle, and per-target backup steps. Modules are stateless workers invoked through ABC interfaces; Core owns the execution order and all VM-state-aware decisions.

## Requirements

### Requirement: Core initialization with dependency injection
Core SHALL accept `IConfigFacade`, `IVMModuleFactory`, `IStateManager`, and `IShell` via its constructor. No global state, no hidden imports.

#### Scenario: Core receives all dependencies at construction
- **WHEN** Core is instantiated with a mock config, mock factory, mock state, and mock shell
- **THEN** it stores all four and is ready for pipeline execution

### Requirement: Core.run() executes the full pipeline
`Core.run(vm_filter=None)` SHALL iterate over all configured VMs (filtered by optional filter) and execute the pipeline for each.

#### Scenario: run with all VMs
- **WHEN** `core.run()` is called and config has 2 VMs
- **THEN** `_execute_pipeline()` is called twice, once for each VM

#### Scenario: run with filter matching one VM
- **WHEN** `core.run(vm_filter="vm1")` is called
- **THEN** only VM "vm1" is processed

### Requirement: Pipeline step order
`Core._execute_pipeline(vm_config)` SHALL execute steps in this order:
1. Pre-flight environment validation (including stale file cleanup per `auto_cleanup`, compress driver availability check)
2. Deferred blockcommit check — state-adaptive drain per the `deferred-operations` capability
3. Change detection — if `snapshot_create` mode requires it
4. Snapshot creation — if detector says we should, or if mode is "always"
5. Snapshot retention evaluation — which snapshots to keep/remove
6. Snapshots to merge: pre-commit backing chain integrity verification (per `chain_verify_before_commit`)
7. Snapshot lifecycle — **adaptive blockcommit**: Core SHALL determine the VM power state via `virsh domstate` and the active overlay path via `virsh domblklist`, split the remove set into committable and deferrable subsets, execute the committable subset with the mechanism valid for the current state, and defer the rest. MAC denial deferral applies as before.
8. Post-commit chain length verification (per `chain_verify_after_commit`)
9. For each target: backup transfer (with retry per `backup_retry_max`) → backup verification → backup retention → cleanup

The `--preserve-snapshots` and `--dry-run` guards SHALL run before any `virsh` state-detection calls.

The adaptive fork in step 7 SHALL behave as follows:

| VM state (`domstate`) | `lifecycle_mode` | Committable subset | Executor | Deferred subset and reason |
|---|---|---|---|---|
| running | `virsh` | remove set minus the active layer | `BlockCommitManager` | active layer, reason `"vm_running"` |
| running | `qemu-img` | (none) | — | entire remove set, reason `"vm_running"` |
| shut off | any | remove set minus the XML-referenced tip overlay | `QemuImgCommitManager` | tip overlay, reason `"active_layer"` |
| paused / other | any | (none) | — | entire remove set, reason `"vm_running"` |
| domstate call failed | any | entire remove set (legacy fallback) | manager for configured mode | (none) |

The active-layer path SHALL be obtained from `virsh domblklist` (via `parse_domblklist_path()`); on failure Core SHALL fall back to the newest snapshot recorded in `IStateManager` and log a WARNING. When the executor is `QemuImgCommitManager`, Core SHALL re-check `virsh domstate` immediately before invoking the manager; if the VM is no longer shut off, Core SHALL defer the committable subset with reason `"vm_running"` instead.

After any successful commit (any branch), Core SHALL remove the committed snapshots from `IStateManager` unconditionally — independent of `chain_verify_after_commit` — and append one `ActionRecord("snapshot_delete")` per committed snapshot.

After any successful OFFLINE commit (executor `QemuImgCommitManager`, main path or deferred drain), Core SHALL refresh the domain's persistent XML so it no longer references deleted overlay files: dump the XML via `virsh dumpxml`, remove every `<backingStore>` element from every `<disk>` element, and redefine the domain via `virsh define`. With no `<backingStore>` recorded, libvirt re-probes the shortened chain from qcow2 headers on next start. Refresh failures SHALL be non-fatal WARNINGs (the commit itself already succeeded).

After all VMs are processed, `_check_deferred_thresholds()` SHALL be called.

Core SHALL NOT directly instantiate `BitmapBackupProvider` or any other domain module. ALL module instantiation SHALL go through `IVMModuleFactory`. This includes orphan checkpoint detection — `Core._detect_orphan_checkpoints()` SHALL obtain the backup provider via `self._factory.create_backup_provider(vm_config, target)`.

Core SHALL NOT hardcode a disk target fallback. When `virsh domblklist` fails or returns no disks in `Core._resolve_disks()`, Core SHALL return an empty list and log a WARNING. The caller SHALL skip snapshot creation when the disk list is empty.

#### Scenario: Pipeline with always mode
- **WHEN** a VM has `snapshot_create = "always"` and the pipeline runs
- **THEN** validation runs first, then a snapshot is created regardless of change detection result

#### Scenario: Orphan checkpoint detection uses factory

- **WHEN** `Core._detect_orphan_checkpoints()` needs a backup provider
- **THEN** it SHALL call `self._factory.create_backup_provider(vm_config, target)`
- **AND** it SHALL NOT directly import or instantiate `BitmapBackupProvider`

#### Scenario: domblklist failure returns empty list

- **WHEN** `virsh domblklist` fails or returns no disk entries
- **THEN** `Core._resolve_disks()` returns an empty list
- **AND** a WARNING is logged
- **AND** snapshot creation is skipped for this VM
- **AND** no hardcoded disk target (e.g., `"vda"`) is used as fallback

#### Scenario: Pipeline with onchange mode, no changes
- **WHEN** a VM has `snapshot_create = "onchange"` and the change detector reports `has_changed = False`
- **THEN** no snapshot is created, but retention is still evaluated

#### Scenario: Non-active snapshots committed live when VM is running (virsh mode)
- **WHEN** `lifecycle_mode = "virsh"`, `virsh domstate` returns "running", and the remove set contains only non-active snapshots
- **THEN** `factory.create_lifecycle_manager(mode="virsh")` is used
- **AND** `manager.blockcommit()` is called with the full remove set
- **AND** no deferred entry is created

#### Scenario: Active layer deferred when VM is running (virsh mode)
- **WHEN** `lifecycle_mode = "virsh"`, `virsh domstate` returns "running", and the remove set contains the active overlay (per `domblklist`)
- **THEN** the non-active prefix is committed live via `BlockCommitManager`
- **AND** the active snapshot is deferred via `add_deferred_blockcommit()` with reason `"vm_running"`
- **AND** an INFO log records the split decision

#### Scenario: qemu-img mode defers everything when VM is running
- **WHEN** `lifecycle_mode = "qemu-img"` and `virsh domstate` returns "running"
- **THEN** no manager is invoked
- **AND** the entire remove set is deferred with reason `"vm_running"`
- **AND** the pipeline continues to backup steps

#### Scenario: Blockcommit deferred when VM is paused
- **WHEN** `virsh domstate` returns "paused"
- **THEN** no manager is invoked regardless of `lifecycle_mode`
- **AND** the entire remove set is deferred with reason `"vm_running"`

#### Scenario: Offline commit via qemu-img when VM is shut off
- **WHEN** `virsh domstate` returns "shut off" (either lifecycle mode) and the remove set does not contain the XML-referenced tip overlay
- **THEN** `factory.create_lifecycle_manager(mode="qemu-img")` is used
- **AND** `manager.blockcommit()` is called with the full remove set
- **AND** no deferred entry is created

#### Scenario: XML-referenced tip excluded from offline commit
- **WHEN** `virsh domstate` returns "shut off" and the remove set contains the overlay referenced by the inactive domain XML (per `domblklist`)
- **THEN** the remaining snapshots are committed via `QemuImgCommitManager`
- **AND** the tip overlay is deferred with reason `"active_layer"`
- **AND** the tip file is never passed to the manager, so the domain remains bootable

#### Scenario: VM state check failure is non-fatal
- **WHEN** `virsh domstate` fails (e.g., VM not defined, libvirt not running)
- **THEN** blockcommit proceeds with the manager for the configured `lifecycle_mode` and the full remove set (legacy behavior)
- **AND** no deferral occurs

#### Scenario: Race guard before offline commit
- **WHEN** the plan selected the `QemuImgCommitManager` executor but the immediate `virsh domstate` re-check no longer returns "shut off"
- **THEN** the manager is not invoked
- **AND** the committable subset is deferred with reason `"vm_running"`

#### Scenario: State entries removed unconditionally after commit
- **WHEN** a blockcommit succeeds and `chain_verify_after_commit` is disabled
- **THEN** the committed snapshots are still removed from `IStateManager`
- **AND** subsequent backup steps operate on the survivor list only

#### Scenario: Domain XML refreshed after offline commit
- **WHEN** an offline commit via `QemuImgCommitManager` succeeds and committed overlay files are deleted
- **THEN** the domain's persistent XML no longer contains `<backingStore>` elements referencing the deleted files
- **AND** `virsh start` on the domain succeeds (libvirt re-probes the shortened chain)

#### Scenario: preserve="all" with VM running — no blockcommit attempted
- **WHEN** `snapshot_preserve = "all"` and the VM is running
- **THEN** the retention engine keeps all snapshots (after D1 fix)
- **AND** `_blockcommit_snapshots()` is not called (empty remove list)
- **AND** no blockcommit error occurs

### Requirement: Error isolation between VMs
An error processing one VM SHALL NOT prevent other VMs from being processed.

#### Scenario: One VM fails, others succeed
- **WHEN** the pipeline for "vm1" raises an error, but "vm2" is also configured
- **THEN** "vm2" is still processed, and the error for "vm1" is logged

### Requirement: Core.snapshot() runs only snapshot steps
`Core.snapshot(vm_filter=None)` SHALL execute only steps 1-4 (change detection, snapshot creation, snapshot retention, lifecycle). No backup steps.

#### Scenario: snapshot command skips backup
- **WHEN** `core.snapshot()` is called
- **THEN** backup methods on the factory are never called

### Requirement: Core.backup() runs only backup steps
`Core.backup(vm_filter=None)` SHALL execute only step 5 (backup transfer, backup retention, cleanup). No snapshot steps.

#### Scenario: backup command skips snapshot creation
- **WHEN** `core.backup()` is called
- **THEN** snapshot providers are never invoked, only backup steps run

### Requirement: Core.prune() runs only retention steps
`Core.prune(vm_filter=None)` SHALL execute only retention and lifecycle cleanup for both snapshots and backups.

#### Scenario: prune command skips creation steps
- **WHEN** `core.prune()` is called
- **THEN** no snapshots or backups are created, only retention evaluation and cleanup run

### Requirement: Dry-run mode
Core SHALL support dry-run mode where all pipeline steps are evaluated but no mutation occurs (no snapshot creation, no blockcommit, no file deletion). Dry-run mode SHALL be activated via the `dry_run` boolean property on the Core instance, settable by the CLI `--dry-run` / `-n` flag. In dry-run mode, `_backup_target()` SHALL pass `full_verify_before_rebase` to the backup provider (retention evaluation and bucket strategy still execute as pure logic). The dry-run SHALL NOT accumulate `ActionRecord` entries — since no mutations occur, no actions are recorded. The `PipelineResult.dry_run` flag SHALL be set to `True` to indicate the run was a dry-run.

#### Scenario: Dry-run logs planned actions
- **WHEN** `core.run()` is called in dry-run mode
- **THEN** each planned action is logged at INFO level, but no IShell mutating commands are executed
- **AND** `PipelineResult.dry_run` is `True`
- **AND** `PipelineResult.actions` is empty (no mutations occurred, so no `ActionRecord` entries are accumulated)

#### Scenario: Dry-run activated from CLI
- **WHEN** `qsnap -n run` is executed
- **THEN** `Core.dry_run` is set to `True` before `core.run()` is called

### Requirement: Preserve flags on Core
Core SHALL expose `preserve_snapshots: bool` and `preserve_backups: bool` properties, both defaulting to `False`. When `preserve_snapshots` is `True`, `_blockcommit_snapshots()` SHALL be skipped. When `preserve_backups` is `True`, backup deletion in `_backup_target()` and `_execute_prune_steps()` SHALL be skipped. Retention evaluation SHALL still execute for schedule printing purposes.

#### Scenario: Preserve snapshots skips blockcommit
- **WHEN** `core.preserve_snapshots = True` and retention evaluation returns 3 snapshots to remove
- **THEN** `_blockcommit_snapshots()` is not called

#### Scenario: Preserve backups skips backup deletion
- **WHEN** `core.preserve_backups = True` and backup retention evaluation returns 2 backups to remove
- **THEN** `provider.delete()` for those backups is not called

### Requirement: Core.print_schedule() method
Core SHALL provide a `print_schedule(vm_filter=None)` method that evaluates retention policy for all VMs and targets without executing any mutations.

#### Scenario: Schedule shows keep/remove decisions
- **WHEN** `core.print_schedule("vm1")` is called
- **THEN** the result shows which snapshots and backups would be kept/removed by the current retention policy

#### Scenario: Schedule does not mutate filesystem
- **WHEN** `core.print_schedule()` is called
- **THEN** no IShell mutating commands (virsh snapshot-create-as, virsh blockcommit, cp, rm) are executed

### Requirement: Error result collection across pipeline steps
When `--preserve` flags are active, snapshot and backup creation steps that fail SHALL still collect results in `VMRunResult`, but deletion steps SHALL be skipped without error.

#### Scenario: Preserve mode with failed backup
- **WHEN** `qsnap --preserve run` is executed and a backup transfer fails
- **THEN** the error is reported in the result, but no backup deletion is attempted

### Requirement: Dynamic disk resolution in snapshot creation
`Core._create_snapshot()` SHALL resolve the active disk(s) via `virsh domblklist --domain <vm>` rather than using a hardcoded `"vda"` string. It SHALL iterate over all discovered disks, creating one snapshot file per disk.

#### Scenario: VM with a single disk named sda
- **WHEN** `virsh domblklist` returns `sda /path/to/image.qcow2`
- **THEN** snapshot is created for disk `sda`, not `vda`

#### Scenario: VM with multiple disks (vda, vdb)
- **WHEN** `virsh domblklist` returns both `vda` and `vdb`
- **THEN** two snapshots are created: one for each disk
- **THEN** snapshot files are named `{vm}.{ts}_vda.qcow2` and `{vm}.{ts}_vdb.qcow2`

#### Scenario: Explicit disk list in config overrides auto-discovery
- **WHEN** `VMConfig.disks` is `["vda"]` for a VM that also has `vdb`
- **THEN** only `vda` is snapshotted

### Requirement: Multi-disk snapshot result collection
When multiple disks are snapshotted, the `_create_snapshot()` method SHALL collect all `SnapshotResult` objects. If any disk fails, the partial results SHALL be logged, but the method SHALL continue processing the next VM in the pipeline.

#### Scenario: vda succeeds, vdb fails
- **WHEN** snapshot of `vda` succeeds but `vdb` fails
- **THEN** `vda` snapshot is recorded in state; `vdb` error is logged
- **THEN** the pipeline continues to retention evaluation

### Requirement: Backup retention in print_schedule
`Core.print_schedule()` SHALL evaluate and return retention decisions for backup targets in addition to snapshots. The result SHALL include per-target keep/remove lists via `ScheduleResult`.

#### Scenario: Schedule shows snapshot and backup decisions
- **WHEN** `core.print_schedule("vm1")` is called and VM has one target
- **THEN** the result shows snapshot retention (keep/remove) AND per-target backup retention (keep/remove)

### Requirement: check --deep via qemu-img check
`Core.check()` SHALL accept a `deep: bool = False` parameter. When `deep=True`, it SHALL execute `qemu-img check --output=json` on each snapshot and backup file. Files with `corruptions > 0` SHALL be reported as broken in `CheckResult`.

#### Scenario: Deep check finds corruption
- **WHEN** `qemu-img check --output=json` returns `{"corruptions": 2}`
- **THEN** the snapshot is marked as broken in `CheckResult` with status `"corrupted"`

#### Scenario: Deep check on clean image
- **WHEN** `qemu-img check` returns `{"corruptions": 0}`
- **THEN** the snapshot is marked as healthy

### Requirement: EXIT_BACKUP_ABORT wired into PipelineResult
`VMRunResult` SHALL gain a `backup_failed: bool` field. When at least one backup task failed, `Core` SHALL return exit code 10 (`EXIT_BACKUP_ABORT`).

#### Scenario: Backup abort exit code
- **WHEN** `qsnap run` completes with one snapshot success and one backup failure
- **THEN** exit code is 10 (EXIT_BACKUP_ABORT)

#### Scenario: All backups succeed
- **WHEN** all backup tasks succeed
- **THEN** exit code is determined by overall pipeline success (0 or 1), not backup-specific

### Requirement: snapshot_create ondemand support
When `VMConfig.snapshot_create == "ondemand"`, `Core` SHALL check whether at least one backup target is reachable before creating a snapshot. If no targets are reachable, the snapshot step SHALL be skipped.

#### Scenario: Ondemand with reachable target
- **WHEN** `snapshot_create = "ondemand"` and the target directory exists
- **THEN** snapshot is created normally

#### Scenario: Ondemand with no reachable targets
- **WHEN** `snapshot_create = "ondemand"` and no target directory exists
- **THEN** snapshot creation is skipped with an INFO log message

### Requirement: Pre-flight environment validation before pipeline
Core SHALL execute `_validate_environment(vm_config)` before `_execute_pipeline(vm_config)`. Validation SHALL verify: (a) `snapshot_dir` exists and is writable, (b) `base_image` file exists, (c) `virsh` and `qemu-img` binaries are in PATH, (d) VM is defined in libvirt. Validation failure SHALL return a `CheckResult` with `status = "validation_failed"` and prevent pipeline execution for that VM.

#### Scenario: All validations pass
- **WHEN** `_validate_environment()` checks a properly configured VM
- **THEN** pipeline continues to `_execute_snapshot_steps()`

#### Scenario: snapshot_dir does not exist
- **WHEN** `snapshot_dir` path is missing
- **THEN** pipeline returns `VMRunResult(success=False, error="snapshot_dir not found: ...")` without executing any steps

### Requirement: Deferred operations integrated into snapshot steps
`Core._execute_snapshot_steps()` SHALL check `IStateManager` for deferred blockcommit operations before step 2 (snapshot creation). The drain is state-adaptive (see `specs/deferred-operations/spec.md`): on a shut-off VM deferred blockcommits SHALL be executed via the qemu-img executor (excluding the XML-referenced tip); on a running VM in `virsh` mode, entries whose snapshots are all non-active SHALL be executed live; otherwise they SHALL be skipped. After blockcommit steps (step 4), snapshots whose blockcommit was deferred or failed due to MAC denial SHALL be recorded as deferred operations.

#### Scenario: Deferred blockcommits executed on shut-off VM
- **WHEN** `virsh domstate` returns "shut off" and deferred queue has 2 snapshots
- **THEN** the lifecycle manager's `blockcommit()` is called with the committable snapshots before any new snapshot creation

#### Scenario: Deferred blockcommits skipped on running VM in qemu-img mode
- **WHEN** VM is running, `lifecycle_mode = "qemu-img"`, and deferred queue has entries
- **THEN** pipeline logs INFO and proceeds to change detection

### Requirement: Post-transfer verification in backup steps
`Core._backup_target()` SHALL pass `target.verify` to the backup provider. The provider SHALL perform verification according to the configured level after transfer. Verification failures SHALL be reflected in `BackupResult` and counted in `backup_failed`.

#### Scenario: Metadata verification failure marks backup as failed
- **WHEN** target has `verify = "metadata"` and verification detects format mismatch
- **THEN** `backup_failed` flag is set to True in the pipeline result

### Requirement: Core._parse_preserve accepts optional preserve_min parameter

`Core._parse_preserve(preserve_str, preserve_min_str=None)` SHALL accept an optional `preserve_min_str` parameter. When provided and non-None, it SHALL override the default `preserve_min` value. When `None`, existing behavior SHALL be preserved.

#### Scenario: Explicit preserve_min overrides default
- **WHEN** `_parse_preserve("24h 2d", "3h")` is called
- **THEN** returns `RetentionPolicy(hourly=24, daily=2, preserve_min="3h")`

#### Scenario: No preserve_min uses existing default
- **WHEN** `_parse_preserve("24h 2d", None)` is called
- **THEN** returns `RetentionPolicy(hourly=24, daily=2, preserve_min="0h")`

### Requirement: Core._evaluate_snapshot_retention uses vm_config.snapshot_preserve_min

`Core._evaluate_snapshot_retention(vm_config, snapshots)` SHALL pass `vm_config.snapshot_preserve_min` to `_parse_preserve()`.

#### Scenario: Snapshot retention with preserve_min
- **WHEN** VM has `snapshot_preserve_min = "3h"`
- **THEN** `_parse_preserve()` is called with that value

### Requirement: Core._evaluate_backup_retention uses target.target_preserve_min

`Core._evaluate_backup_retention(vm_config, target, backups)` SHALL pass `target.target_preserve_min` to `_parse_preserve()`.

#### Scenario: Backup retention with preserve_min
- **WHEN** target has `target_preserve_min = "6h"`
- **THEN** `_parse_preserve()` is called with that value

### Requirement: Core._backup_target triggers full backup when due

`Core._backup_target(vm_config, target, snapshots)` SHALL, before the incremental transfer loop, call `state.get_full_backups(target.path)` to retrieve ALL full backups for the target. It SHALL obtain an `IBucketFullStrategy` via `self._factory.create_bucket_full_strategy()` and call `strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)` with the complete list of `FullBackupInfo` objects and the most recent snapshot's timestamp. Core SHALL NOT contain private methods `_should_create_bucket_full`, `_active_buckets`, `_f_anchor_buckets`, or `_period_key`.

#### Scenario: Full backup list passed to bucket strategy
- **WHEN** `_backup_target()` is called and the target has 2 existing FULL records
- **THEN** `state.get_full_backups(target.path)` returns a list of 2 `FullBackupInfo` objects
- **THEN** the list is passed to `strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)`

#### Scenario: First run creates full backup via strategy
- **WHEN** `get_full_backups(target.path)` returns an empty list (no previous FULLs)
- **THEN** `strategy.should_create_full(...)` returns `(True, bucket_level)` for the first active/F-marked bucket
- **THEN** a FULL is created

#### Scenario: Strategy obtained via factory
- **WHEN** `_backup_target()` runs
- **THEN** it calls `self._factory.create_bucket_full_strategy()` exactly once
- **AND** the resulting strategy object is used for the bucket decision
- **AND** no private bucket-related methods exist on Core

### Requirement: Core imports shared utilities from qsnap.utils

Core SHALL import `is_vm_running`, `nbd_full_export` from `qsnap.utils.nbd`, `verify_full_backup` from `qsnap.utils.verification`, and `file_sha256` from `qsnap.utils.hash`. Core SHALL NOT import from `qsnap.modules.backup` or `qsnap.modules.*` except through the factory.

#### Scenario: Core has no domain module imports
- **WHEN** `qsnap/core/__init__.py` is inspected
- **THEN** there is NO `from qsnap.modules.backup` import
- **AND** there is NO `from qsnap.modules.snapshot` import
- **AND** all utility imports come from `qsnap.utils`

### Requirement: Core.schedule_summary produces retention simulation

`Core.schedule_summary(vm_filter=None) -> str` SHALL generate synthetic timestamp data for each VM, pass it through `TimeBasedRetention.evaluate()` and `explain()`, and format a human-readable summary showing expected chain length, bucket breakdown, and estimated storage for snapshots and per-target backups.

#### Scenario: Summary includes all VMs when no filter
- **WHEN** `schedule_summary()` is called with no filter
- **THEN** output includes sections for every configured VM and every target

#### Scenario: Summary filters by VM name
- **WHEN** `schedule_summary(vm_filter="debiantest")` is called
- **THEN** output includes only the "debiantest" VM section

### Requirement: Post-pipeline deferred threshold check

At the end of `Core._run_pipeline()`, the system SHALL call `_check_deferred_thresholds()` which iterates over all VMs, retrieves their deferred operations from `IStateManager`, and compares count and age against `GlobalConfig` thresholds (`deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age`). WARNING or CRITICAL log messages SHALL be emitted for threshold violations. The check SHALL NOT affect the pipeline exit code.

#### Scenario: Deferred threshold WARNING logged

- **WHEN** a VM has 5 deferred operations and `deferred_warn_count = "5"`
- **AND** `run()` completes successfully
- **THEN** a WARNING log message is emitted for that VM
- **AND** exit code is 0

#### Scenario: Deferred threshold CRITICAL logged

- **WHEN** a VM has 10 deferred operations and `deferred_crit_count = "10"`
- **AND** `run()` completes successfully
- **THEN** a CRITICAL log message is emitted for that VM

### Requirement: Core.list_deferred() method

Core SHALL expose a `list_deferred(vm_filter=None)` method returning per-VM deferred operation summaries: VM name, snapshot count, reason, and age of the oldest operation. The method SHALL use `IStateManager.get_deferred_operations()` to retrieve data.

#### Scenario: list_deferred returns summaries for all VMs

- **WHEN** `core.list_deferred()` is called with two VMs having deferred operations
- **THEN** the result contains two entries with vm_name, snapshots count, reason, and age

#### Scenario: list_deferred with VM filter

- **WHEN** `core.list_deferred(vm_filter="vm-home")` is called
- **THEN** only the "vm-home" entry is returned

### Requirement: Core.check() includes deferred status with remediation

`Core.check()` SHALL include deferred operation count, age, and reason for each VM. When deferred operations are present, the output SHALL include actionable remediation guidance.

#### Scenario: Check includes deferred status

- **WHEN** `core.check()` is called on a VM with 3 deferred operations (reason: apparmor)
- **THEN** the output includes the deferred count and reason
- **AND** the output includes remediation guidance: "Merge blocked by AppArmor. Consider: aa-disable /etc/apparmor.d/libvirt/libvirt-<uuid>"

### Requirement: Pre-commit chain verification before blockcommit
When `chain_verify_before_commit = true` and there are snapshots to merge, Core SHALL call `_verify_backing_chain(vm_config)` before `lifecycle.blockcommit()`. If verification fails, blockcommit SHALL be skipped. See `specs/chain-integrity-verification/spec.md`.

#### Scenario: Chain verification blocks broken chain
- **WHEN** `_verify_backing_chain()` detects a missing file in the backing chain
- **THEN** blockcommit is skipped for this VM
- **AND** a CRITICAL log is emitted
- **AND** remaining VMs are processed normally

### Requirement: Post-commit chain verification after blockcommit
When `chain_verify_after_commit = true` and blockcommit succeeded, Core SHALL verify the chain length decreased. See `specs/chain-integrity-verification/spec.md`. The "Post-commit chain verification passed" INFO message SHALL be logged ONLY when the post-commit verification actually ran (i.e., inside the `else` branch where `chain_length_before` was not `None` and `remove_snapshot()` was called). When `chain_length_before` is `None` and verification is skipped, the message SHALL NOT be logged.

#### Scenario: Post-commit chain check passes
- **WHEN** chain length decreased after blockcommit AND `chain_length_before` was not `None`
- **THEN** verification passes and "Post-commit chain verification passed" is logged

#### Scenario: Post-commit skipped when chain_length_before is None
- **WHEN** `chain_length_before` is `None` and `chain_verify_after_commit` is `True`
- **THEN** "Post-commit chain verification skipped" is logged (existing message)
- **AND** "Post-commit chain verification passed" is NOT logged
- **AND** merged snapshots are still removed from state (state cleanup is unconditional per the pipeline step order requirement)

### Requirement: Retry wrapper for backup transfers
Core's `_backup_target()` method SHALL wrap provider transfer calls in a retry loop when `target.backup_retry_max > 0`. See `specs/backup-retry/spec.md`.

#### Scenario: Backup retried on transient error
- **WHEN** a transfer fails with "Connection refused" and `backup_retry_max = 3`
- **THEN** the transfer is retried with exponential backoff

### Requirement: Deferred blockcommit with deep verify
When executing deferred blockcommit operations and `vm_config.blockcommit_deep_verify = true`, Core SHALL pass `deep_verify=True` to the lifecycle manager selected by the state-adaptive drain (see `specs/deferred-operations/spec.md`). See `specs/deep-verification-circuit/spec.md`.

#### Scenario: Deep verify passed to deferred blockcommit
- **WHEN** VM is shut off, `blockcommit_deep_verify = true`, and deferred commits execute
- **THEN** `manager.blockcommit(vm_config, to_merge, deep_verify=True)` is called

### Requirement: Core.fork method
`Core` SHALL provide a `fork(snapshot_name: str, new_vm_name: str, storage_dir: Path, add_to_config: bool = False, vm_filter: str | None = None) -> RestoreResult` method. It SHALL:
1. Resolve the snapshot via `IStateManager` and backup providers (reuse restore resolution).
2. Determine the snapshot's full chain via `qemu-img info --backing-chain --output=json`.
3. Estimate and log total chain size.
4. Execute `qemu-img convert -O qcow2 <snapshot-path> <storage_dir>/<new_vm_name>/<new_vm_name>.qcow2`.
5. Obtain source VM XML via `virsh dumpxml <source-vm>`.
6. Modify XML: new name, new UUID (uuidgen), new disk source paths, MAC removed.
7. Execute `virsh define <modified-xml-path>`.
8. Optionally append `[[vm]]` block to qsnap config file.

#### Scenario: fork succeeds
- **WHEN** `core.fork("myvm.20260701T1200", "myvm-clone", Path("/var/lib/libvirt/images"), add_to_config=False)` is called
- **THEN** returns `RestoreResult(success=True, restored_path=Path("/var/lib/libvirt/images/myvm-clone/myvm-clone.qcow2"))`

### Requirement: Core.deploy method
`Core` SHALL provide a `deploy(backup_name: str, new_vm_name: str, storage_dir: Path, add_to_config: bool = False, vm_filter: str | None = None) -> RestoreResult` method. It SHALL delegate to `Core.fork()` with the same parameters.

#### Scenario: deploy delegates to fork
- **WHEN** `core.deploy("vm.FULL.20260701.monthly", "recovered-vm", Path("/var/lib/libvirt/images"))` is called
- **THEN** `core.fork("vm.FULL.20260701.monthly", "recovered-vm", Path("/var/lib/libvirt/images"))` is called internally
- **THEN** returns the same `RestoreResult`

### Requirement: Phantom FULL detection

Before using `get_full_backups()` for bucket-driven FULL creation decisions, Core SHALL verify each FULL file exists on disk via `os.path.exists()`. Entries whose files do not exist SHALL be removed from state via `remove_full_backup()` with a WARNING log. This prevents phantom FULLs (deleted externally but still in state) from blocking new FULL creation.

#### Scenario: Phantom FULL removed before bucket decision
- **WHEN** state contains a FULL record whose file no longer exists on disk
- **THEN** the record is removed via `remove_full_backup()` with a WARNING before `should_create_full()` is evaluated

### Requirement: Post-create FULL backup verification with source_path

When `GlobalConfig.full_verify_after_create` is set, Core SHALL call `verify_full_backup()` after `create_full_backup()` completes. When the mode is `"hash"`, Core SHALL pass `source_path=most_recent.path` for `qemu-img compare` content verification. On success, `record_full_backup()` is called; on failure, the FULL file is deleted and NOT recorded.

#### Scenario: Failed post-create verification deletes the FULL
- **WHEN** `full_verify_after_create = "hash"` and `verify_full_backup()` reports a mismatch
- **THEN** the new FULL file is deleted and no `record_full_backup()` call occurs

### Requirement: backup_failed WARNING in Core._backup_target

`Core._backup_target()` SHALL emit a `logger.warning` when `backup_failed` is set to `True` due to any incremental transfer returning `BackupResult(success=False)`. The warning SHALL include the VM name, target path, count of failed snapshots, and the specific snapshot names with their error messages.

#### Scenario: backup_failed warning with transfer failures
- **WHEN** `_backup_target()` receives 2 successful and 1 failed `BackupResult` from `transfer_missing()`
- **THEN** `backup_failed` is set to `True`
- **AND** a WARNING is logged: `"Backup transfer failed for VM <vm> target <target>: <N> snapshot(s) failed — <name>: <error>"`

#### Scenario: No warning when all transfers succeed
- **WHEN** `_backup_target()` receives all `BackupResult(success=True)` from `transfer_missing()`
- **THEN** no WARNING is logged for backup_failed
- **AND** `backup_failed` is `False`

### Requirement: ActionRecord accumulation in Core pipeline

Core SHALL accumulate `ActionRecord` instances during pipeline execution (see `specs/action-audit-trail/spec.md` for the full spec). Core SHALL attach the accumulated list to `PipelineResult.actions` at the end of `_run_pipeline()`.

#### Scenario: Actions attached to PipelineResult
- **WHEN** `_run_pipeline()` completes after executing pipeline steps
- **THEN** `PipelineResult.actions` contains all `ActionRecord` entries accumulated during execution

### Requirement: Per-operation INFO logging in Core

Core SHALL emit `logger.info` messages in btrbk-style format for each pipeline operation:

#### Scenario: Snapshot creation INFO
- **WHEN** `_create_snapshot()` successfully creates a snapshot
- **THEN** an INFO message is logged: `"[snapshot] <vm_name>: created <snapshot_name> (<size> B)"`

#### Scenario: Snapshot deletion INFO
- **WHEN** `_blockcommit_snapshots()` successfully merges snapshots
- **THEN** an INFO message is logged: `"[blockcommit] <vm_name>: merged <N> snapshot(s) — <name1>, <name2>, ..."`

#### Scenario: Backup transfer INFO
- **WHEN** `_backup_target()` successfully transfers an incremental
- **THEN** an INFO message is logged: `"[backup] <vm_name>: transferred <snapshot_name> → <target_path> (<size> B in <duration>s, <speed> MiB/s)"`

#### Scenario: FULL backup creation INFO
- **WHEN** `_backup_target()` successfully creates a FULL backup
- **THEN** an INFO message is logged: `"[backup] <vm_name>: created FULL <full_name> (<size> B)"`

#### Scenario: Backup deletion INFO
- **WHEN** `_cleanup_backups()` successfully deletes a backup file
- **THEN** an INFO message is logged: `"[delete] <vm_name>: removed backup <backup_name> from <target_path>"`

#### Scenario: Ghost retention INFO
- **WHEN** `_cleanup_backups()` ghost-retains a FULL with dependents in keep-set
- **THEN** an INFO message is logged: `"[delete] <vm_name>: ghost-retained FULL <full_name> (<N> dependent(s) in keep-set)"`

### Requirement: Per-target backup onchange gate

When `TargetConfig.backup_create == "onchange"` and snapshots exist, `Core._backup_target()` SHALL call `_should_backup_onchange(vm_config, target, snapshots)` before proceeding with backup transfer. If `_should_backup_onchange()` returns `False`, the backup transfer SHALL be skipped entirely for this target — no `create_full_backup()`, no `transfer_missing()`, no NBD export. The skip SHALL be logged at INFO level. If `_should_backup_onchange()` returns `True`, the existing backup logic SHALL proceed unchanged.

#### Scenario: First backup — always proceeds
- **WHEN** `backup_create = "onchange"` and `get_last_backup_allocation(target_path)` returns `None`
- **THEN** `_should_backup_onchange()` returns `True`
- **AND** the backup transfer proceeds

#### Scenario: No change — backup skipped
- **WHEN** `backup_create = "onchange"` and the latest snapshot's allocation equals `get_last_backup_allocation(target_path)`
- **THEN** `_should_backup_onchange()` returns `False`
- **AND** the backup transfer is skipped
- **AND** an INFO log message is emitted: "skipping target (no change since last backup)"

#### Scenario: Allocation grew — backup proceeds
- **WHEN** `backup_create = "onchange"` and the latest snapshot's allocation is greater than `get_last_backup_allocation(target_path)`
- **THEN** `_should_backup_onchange()` returns `True`
- **AND** the backup transfer proceeds

#### Scenario: always mode — gate bypassed
- **WHEN** `backup_create = "always"` (default)
- **THEN** `_should_backup_onchange()` is NOT called
- **AND** the backup transfer proceeds unconditionally

#### Scenario: No snapshots — backup skipped
- **WHEN** `backup_create = "onchange"` but no snapshots exist
- **THEN** `_should_backup_onchange()` returns `False` (nothing to transfer)
- **AND** the backup transfer is skipped
- **AND** an INFO log message is emitted

### Requirement: backup_create baseline update after successful transfer

After a successful backup transfer (`_transfer_with_retry()` returns results), when `TargetConfig.backup_create == "onchange"`, `Core._backup_target()` SHALL update the per-target baseline by calling `set_last_backup_allocation(str(target.path), latest_snapshot.allocation)` where `latest_snapshot` is the most recent snapshot by timestamp. The baseline SHALL NOT be updated on transfer failure.

#### Scenario: Baseline updated after successful transfer
- **WHEN** `backup_create = "onchange"` and the backup transfer succeeds
- **THEN** `set_last_backup_allocation(target_path, latest.allocation)` is called
- **AND** the next run's `_should_backup_onchange()` compares against the updated baseline

#### Scenario: Baseline NOT updated on transfer failure
- **WHEN** `backup_create = "onchange"` and the backup transfer fails
- **THEN** `set_last_backup_allocation()` is NOT called
- **AND** the baseline remains at the last successful backup's allocation
