# Core Orchestrator

## Purpose

Core is the pipeline runner and dependency-injection host: it coordinates environment validation, deferred-operation draining, change detection, per-disk snapshot creation, per-disk retention evaluation, per-disk adaptive blockcommit lifecycle, and per-target backup steps. Modules are stateless workers invoked through ABC interfaces; Core owns the execution order and all VM-state-aware decisions.
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
2. Startup state-vs-disk validation (`_validate_state_at_startup`)
3. Deferred blockcommit check (state-adaptive drain per `deferred-operations` capability)
4. Change detection — if `snapshot_create` mode requires it
5. Snapshot creation — if detector says we should, or if mode is "always"
6. Snapshot retention evaluation — per-disk, which snapshots to keep/remove
7. Snapshots to merge: pre-commit backing chain integrity verification (per `chain_verify_before_commit`)
8. Snapshot lifecycle — **adaptive blockcommit** per disk: Core SHALL determine the VM power state via `virsh domstate` and the active overlay path per-disk via `virsh domblklist`, split the remove set into committable and deferrable subsets per disk, execute the committable subset with the mechanism valid for the current state, and defer the rest. MAC denial deferral applies as before.
9. Post-commit chain length verification (per `chain_verify_after_commit`)
10. For each target: backup transfer (with retry per `backup_retry_max`) → backup verification → backup retention → cleanup

The `--preserve-snapshots` and `--dry-run` guards SHALL run before any `virsh` state-detection calls.

The adaptive fork in step 8 SHALL behave as follows per disk:

| VM state (`domstate`) | `lifecycle_mode` | Committable subset | Executor | Deferred subset and reason |
|---|---|---|---|---|
| running | `virsh` | remove set minus the active layer | `BlockCommitManager` | active layer, reason `"vm_running"` |
| running | `qemu-img` | (none) | — | entire remove set, reason `"vm_running"` |
| shut off | any | remove set minus the XML-referenced tip overlay | `QemuImgCommitManager` | tip overlay, reason `"active_layer"` |
| paused / other | any | (none) | — | entire remove set, reason `"vm_running"` |
| domstate call failed | any | entire remove set (legacy fallback) | manager for configured mode | (none) |

The active-layer path SHALL be obtained from `virsh domblklist` (via `parse_domblklist_path_map()`) per-disk; on failure Core SHALL fall back to the newest snapshot of that disk recorded in `IStateManager` and log a WARNING. When the executor is `QemuImgCommitManager`, Core SHALL re-check `virsh domstate` immediately before invoking the manager; if the VM is no longer shut off, Core SHALL defer the committable subset with reason `"vm_running"` instead.

After any successful commit (any branch), Core SHALL remove the committed snapshots from `IStateManager` unconditionally — independent of `chain_verify_after_commit` — and append one `ActionRecord("snapshot_delete")` per committed snapshot.

After any successful OFFLINE commit (executor `QemuImgCommitManager`, main path or deferred drain), Core SHALL refresh the domain's persistent XML so it no longer references deleted overlay files: dump the XML via `virsh dumpxml`, remove every `<backingStore>` element from every `<disk>` element, and redefine the domain via `virsh define`. With no `<backingStore>` recorded, libvirt re-probes the shortened chain from qcow2 headers on next start. Refresh failures SHALL be non-fatal WARNINGs (the commit itself already succeeded).

After all VMs are processed, `_check_deferred_thresholds()` SHALL be called.

Core SHALL NOT directly instantiate `BitmapBackupProvider` or any other domain module. ALL module instantiation SHALL go through `IVMModuleFactory`. This includes orphan checkpoint detection — `Core._detect_orphan_checkpoints()` SHALL obtain the backup provider via `self._factory.create_backup_provider(vm_config, target)`.

Core SHALL NOT hardcode a disk target fallback. `Core._resolve_disks(vm_config)` SHALL return `vm_config.disks` — the explicitly configured disk list. There is no auto-discovery.

Core SHALL NOT import from `qsnap.modules.backup` or `qsnap.modules.*` except through the factory.

#### Scenario: Pipeline with always mode
- **WHEN** a VM has `snapshot_create = "always"` and the pipeline runs
- **THEN** validation runs first, then a snapshot is created for each disk regardless of change detection result

#### Scenario: Orphan checkpoint detection uses factory

- **WHEN** `Core._detect_orphan_checkpoints()` needs a backup provider
- **THEN** it SHALL call `self._factory.create_backup_provider(vm_config, target)`
- **AND** it SHALL NOT directly import or instantiate `BitmapBackupProvider`

#### Scenario: Pipeline with onchange mode, no changes
- **WHEN** a VM has `snapshot_create = "onchange"` and no disk has changed
- **THEN** no snapshot is created, but retention is still evaluated

#### Scenario: Non-active snapshots committed live when VM is running (virsh mode)
- **WHEN** `lifecycle_mode = "virsh"`, `virsh domstate` returns "running", and the remove set contains only non-active snapshots for a disk
- **THEN** `factory.create_lifecycle_manager(mode="virsh")` is used for that disk
- **AND** `manager.blockcommit()` is called with the full remove set for that disk
- **AND** no deferred entry is created for that disk

#### Scenario: Active layer deferred when VM is running (virsh mode)
- **WHEN** `lifecycle_mode = "virsh"`, `virsh domstate` returns "running", and the remove set contains the active overlay for a disk (per `domblklist`)
- **THEN** the non-active prefix is committed live via `BlockCommitManager` for that disk
- **AND** the active snapshot is deferred via `add_deferred_blockcommit()` with disk and reason `"vm_running"`
- **AND** an INFO log records the split decision

#### Scenario: qemu-img mode defers everything when VM is running
- **WHEN** `lifecycle_mode = "qemu-img"` and `virsh domstate` returns "running"
- **THEN** no manager is invoked for any disk
- **AND** the entire remove set for each disk is deferred with reason `"vm_running"`
- **AND** the pipeline continues to backup steps

#### Scenario: Blockcommit deferred when VM is paused
- **WHEN** `virsh domstate` returns "paused"
- **THEN** no manager is invoked regardless of `lifecycle_mode`
- **AND** the entire remove set for each disk is deferred with reason `"vm_running"`

#### Scenario: Offline commit via qemu-img when VM is shut off
- **WHEN** `virsh domstate` returns "shut off" (either lifecycle mode) and the remove set for a disk does not contain the XML-referenced tip overlay
- **THEN** `factory.create_lifecycle_manager(mode="qemu-img")` is used for that disk
- **AND** `manager.blockcommit()` is called with the full remove set for that disk
- **AND** no deferred entry is created for that disk

#### Scenario: XML-referenced tip excluded from offline commit
- **WHEN** `virsh domstate` returns "shut off" and the remove set for a disk contains the overlay referenced by the inactive domain XML (per `domblklist`)
- **THEN** the remaining snapshots for that disk are committed via `QemuImgCommitManager`
- **AND** the tip overlay is deferred with reason `"active_layer"`
- **AND** the tip file is never passed to the manager, so the domain remains bootable

#### Scenario: VM state check failure is non-fatal
- **WHEN** `virsh domstate` fails (e.g., VM not defined, libvirt not running)
- **THEN** blockcommit proceeds for each disk with the manager for the configured `lifecycle_mode` and the full remove set (legacy behavior)
- **AND** no deferral occurs

#### Scenario: Race guard before offline commit
- **WHEN** the plan selected the `QemuImgCommitManager` executor for a disk but the immediate `virsh domstate` re-check no longer returns "shut off"
- **THEN** the manager is not invoked for that disk
- **AND** the committable subset for that disk is deferred with reason `"vm_running"`

#### Scenario: State entries removed unconditionally after commit
- **WHEN** a blockcommit succeeds and `chain_verify_after_commit` is disabled
- **THEN** the committed snapshots are still removed from `IStateManager`
- **AND** subsequent backup steps operate on the survivor list only

#### Scenario: Domain XML refreshed after offline commit
- **WHEN** an offline commit via `QemuImgCommitManager` succeeds and committed overlay files are deleted
- **THEN** the domain's persistent XML no longer contains `<backingStore>` elements referencing the deleted files in any disk
- **AND** `virsh start` on the domain succeeds (libvirt re-probes the shortened chain)

#### Scenario: preserve="all" with VM running — no blockcommit attempted
- **WHEN** `snapshot_preserve = "all"` and the VM is running
- **THEN** the retention engine keeps all snapshots
- **AND** `_blockcommit_snapshots()` is not called (empty remove list)
- **AND** no blockcommit error occurs

### Requirement: Error isolation between VMs
An error processing one VM SHALL NOT prevent other VMs from being processed.

#### Scenario: One VM fails, others succeed
- **WHEN** the pipeline for "vm1" raises an error, but "vm2" is also configured
- **THEN** "vm2" is still processed, and the error for "vm1" is logged

### Requirement: Core.snapshot() runs only snapshot steps
`Core.snapshot(vm_filter=None)` SHALL execute only steps 1-9 (change detection, snapshot creation, snapshot retention, lifecycle). No backup steps.

#### Scenario: snapshot command skips backup
- **WHEN** `core.snapshot()` is called
- **THEN** backup methods on the factory are never called

### Requirement: Core.backup() runs only backup steps
`Core.backup(vm_filter=None)` SHALL execute only step 10 (backup transfer, backup retention, cleanup). No snapshot steps.

#### Scenario: backup command skips snapshot creation
- **WHEN** `core.backup()` is called
- **THEN** snapshot providers are never invoked, only backup steps run

### Requirement: Core.prune() runs only retention steps
`Core.prune(vm_filter=None)` SHALL execute only retention and lifecycle cleanup for both snapshots and backups.

#### Scenario: prune command skips creation steps
- **WHEN** `core.prune()` is called
- **THEN** no snapshots or backups are created, only retention evaluation and cleanup run

### Requirement: Dry-run mode
Core SHALL support dry-run mode where all pipeline steps are evaluated but no mutation occurs (no snapshot creation, no blockcommit, no file copy, no file deletion, no state writes, no XML changes, no transaction log). Dry-run mode SHALL be activated via the `dry_run` boolean property on the Core instance, settable by the CLI `--dry-run` / `-n` flag. The dry-run SHALL NOT accumulate mutation `ActionRecord` entries in `PipelineResult.actions` — since no mutations occur, no mutation actions are recorded. `error` records ARE accumulated in dry-run (a failed VM is reported regardless of mode), so in dry-run `PipelineResult.actions` contains only error records. Dry-run SHALL accumulate prediction records in `PipelineResult.predictions` (see capability `dry-run-prediction`). The `PipelineResult.dry_run` flag SHALL be set to `True` to indicate the run was a dry-run.

In dry-run mode, Core SHALL simulate the snapshots that would be created and thread them through snapshot retention, backup steps, the per-disk FULL decision, and the incremental transfer prediction, so that all predictions reflect the post-run world (capability `dry-run-prediction`). `Core._check_deferred_operations()` SHALL be guarded: no blockcommit execution and no state writes occur in dry-run.

#### Scenario: Dry-run logs planned actions
- **WHEN** `core.run()` is called in dry-run mode
- **THEN** each planned action is logged at INFO level with VM and disk context, but no IShell mutating commands are executed
- **AND** `PipelineResult.dry_run` is `True`
- **AND** `PipelineResult.actions` contains only `error` records (empty when no VM failed)
- **AND** `PipelineResult.predictions` contains one record per predicted mutation

#### Scenario: Dry-run activated from CLI
- **WHEN** `qsnap -n run` is executed
- **THEN** `Core.dry_run` is set to `True` before `core.run()` is called

#### Scenario: Dry-run predictions reflect post-run state
- **WHEN** `core.run()` is called in dry-run mode for a VM whose snapshot count would exceed `snapshot_chain_length` after the would-be-created snapshots
- **THEN** the retention prediction includes the would-be-created snapshots in its input
- **AND** the predicted remove set matches what a real run would remove

#### Scenario: Dry-run does not drain the deferred queue
- **WHEN** `core.run()` is called in dry-run mode with queued deferred blockcommits
- **THEN** no lifecycle manager blockcommit is executed
- **AND** the deferred queue and snapshot state are unchanged
- **AND** a per-disk prediction of the would-be drain is logged

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

### Requirement: Per-disk snapshot creation with configured disk list
`Core._create_snapshot()` SHALL operate on `vm_config.disks` (the explicitly configured disk list, via `_resolve_disks()`). It SHALL NOT auto-discover disks via `virsh domblklist`. Core SHALL first generate one snapshot name per disk with naming convention `{vm_name}.{timestamp}_{disk_target}_{6hex}.qcow2` (the `_{6hex}` suffix is `secrets.token_hex(3)`), using that disk's effective snapshot directory (`vm_config.snapshot_dir_for(disk)`), then create ALL disks' snapshots in ONE `ISnapshotProvider.create_multi()` call with `quiesce=vm_config.snapshot_quiesce` (guest-agent freeze covers all disks; there is NO first-disk-only quiesce). State recording is all-or-nothing: ONLY when every disk's `SnapshotResult` succeeds SHALL Core record the snapshots in state as `SnapshotInfo` with `disk=disk.target`; if ANY disk fails, Core SHALL record NOTHING for the batch, SHALL NOT roll back previously recorded state of earlier runs, and SHALL raise `RuntimeError`, aborting the remaining steps of this VM (spec: VM-level failure isolation). Other VMs are processed normally.

#### Scenario: VM with multiple disks (vda, vdb)
- **WHEN** VM config has disks `vda` and `vdb`
- **THEN** one `create_multi` call creates both snapshots
- **AND** snapshot files are named `{vm}.{ts}_vda_{6hex}.qcow2` and `{vm}.{ts}_vdb_{6hex}.qcow2`
- **AND** `SnapshotInfo` records have `disk="vda"` and `disk="vdb"` respectively
- **AND** quiesce (when enabled) applies to the whole batch, not to a single disk

#### Scenario: vdb fails — nothing recorded, VM aborts
- **WHEN** the batch creation succeeds for `vda`'s file but fails for `vdb`
- **THEN** NEITHER snapshot is recorded in state (all-or-nothing)
- **AND** created batch files are best-effort removed by the provider
- **AND** `RuntimeError` is raised, aborting the remaining steps of this VM
- **AND** other VMs are processed normally

#### Scenario: Single-disk VM uses the same batch path
- **WHEN** VM config has one disk `vda`
- **THEN** one `create_multi` call with one spec creates the snapshot
- **AND** on success the snapshot is recorded in state

#### Scenario: onchange gate is VM-wide, snapshots cover all disks
- **WHEN** `snapshot_create = "onchange"` and disk `vda` changed but `vdb` did not
- **THEN** the gate `any(detector.has_changed(vm, disk).changed for disk in disks)` is `True`
- **AND** snapshots are created for ALL disks (`vda` and `vdb`)

### Requirement: EXIT_BACKUP_ABORT wired into PipelineResult
`VMRunResult` SHALL have a `backup_failed: bool` field, set to `True` when the VM pipeline was aborted by `BackupAbortError` (backup-stage failure after retries). The CLI SHALL check `any(r.backup_failed)` BEFORE the generic failure check, so a run with any backup-stage abort returns exit code 10 (`EXIT_BACKUP_ABORT`) even though the overall result is a failure.

#### Scenario: Backup abort exit code
- **WHEN** `qsnap run` completes with one VM aborted by `BackupAbortError`
- **THEN** exit code is 10 (EXIT_BACKUP_ABORT)

#### Scenario: All backups succeed
- **WHEN** all backup tasks succeed
- **THEN** exit code is determined by overall pipeline success (0 or 1), not backup-specific

#### Scenario: Backup abort takes precedence over generic failure
- **WHEN** the result has `success=False` and at least one `VMRunResult.backup_failed=True`
- **THEN** exit code is 10, not 1

### Requirement: snapshot_create ondemand support
When `VMConfig.snapshot_create == "ondemand"`, `Core` SHALL check whether at least one backup target is reachable before creating a snapshot. If no targets are reachable, the snapshot step SHALL be skipped.

#### Scenario: Ondemand with reachable target
- **WHEN** `snapshot_create = "ondemand"` and the target directory exists
- **THEN** snapshot is created normally

#### Scenario: Ondemand with no reachable targets
- **WHEN** `snapshot_create = "ondemand"` and no target directory exists
- **THEN** snapshot creation is skipped with an INFO log message

### Requirement: Pre-flight environment validation before pipeline
Core SHALL execute `_validate_environment(vm_config)` before `_execute_pipeline(vm_config)`. Validation SHALL verify per-disk: (a) each distinct effective snapshot directory exists and is writable (via `test -d` and `test -w`), (b) each disk's `base_image` file exists (via `test -f`). Validation SHALL also verify: (c) `virsh` and `qemu-img` binaries are in PATH, (d) VM is defined in libvirt, (e) target paths exist (mode-dependent), (f) libnbd is available, (g) qemu-nbd compress driver is available when any target has `compress=true`. Validation failure SHALL return a `CheckResult` with `status = "validation_failed"` and prevent pipeline execution for that VM.

#### Scenario: All validations pass
- **WHEN** `_validate_environment()` checks a properly configured VM with multiple disks
- **THEN** each disk's base_image file is verified, each distinct snapshot dir is verified
- **AND** pipeline continues to `_execute_snapshot_steps()`

#### Scenario: snapshot_dir does not exist
- **WHEN** a disk's effective snapshot directory is missing
- **THEN** validation returns `CheckResult(status="validation_failed")` without executing any steps

#### Scenario: base_image missing for one disk
- **WHEN** `test -f` fails for one disk's `base_image`
- **THEN** validation returns `CheckResult(status="validation_failed")` with the disk name in the error

### Requirement: Deferred operations integrated into snapshot steps
`Core._execute_snapshot_steps()` SHALL check `IStateManager` for deferred blockcommit operations before step 2 (snapshot creation). The drain is per-disk and state-adaptive (see `specs/deferred-operations/spec.md`): on a shut-off VM, deferred blockcommits per disk SHALL be executed via the qemu-img executor (excluding the XML-referenced tip); on a running VM in `virsh` mode, entries whose snapshots are all non-active SHALL be executed live; otherwise they SHALL be skipped. After blockcommit steps (step 8), snapshots whose blockcommit was deferred or failed due to MAC denial SHALL be recorded as deferred operations per disk.

#### Scenario: Deferred blockcommits executed on shut-off VM
- **WHEN** `virsh domstate` returns "shut off" and deferred queue has 2 snapshots for a disk
- **THEN** the lifecycle manager's `blockcommit()` is called with the committable snapshots for that disk before any new snapshot creation

#### Scenario: Deferred blockcommits skipped on running VM in qemu-img mode
- **WHEN** VM is running, `lifecycle_mode = "qemu-img"`, and deferred queue has entries
- **THEN** pipeline logs INFO and proceeds to change detection

### Requirement: Post-transfer verification in backup steps
`Core._backup_target()` SHALL pass `target.verify` to the backup provider. The provider SHALL perform verification according to the configured level after transfer. Verification failures SHALL be reflected in `BackupResult` and counted in `backup_failed`.

#### Scenario: Metadata verification failure marks backup as failed
- **WHEN** target has `verify = "metadata"` and verification detects format mismatch
- **THEN** `backup_failed` flag is set to True in the pipeline result

### Requirement: Core._evaluate_snapshot_retention evaluates per-disk
`Core._evaluate_snapshot_retention(vm_config)` SHALL group all recorded snapshots by `SnapshotInfo.disk`, then evaluate retention independently per disk via `_evaluate_disk_retention()`. Each disk's snapshots SHALL be evaluated with the VM-level `snapshot_chain_length` and `snapshot_preserve_min` values. The per-disk keep/remove lists SHALL be merged into a single `RetentionResult`. Within each disk, the oldest-prefix and preserve-min post-processing filters SHALL be applied (see `snapshot-oldest-prefix` and `snapshot-preserve-min` specs).

The method SHALL construct a `RetentionPolicy(chain_length=vm_config.snapshot_chain_length or 0, keep_generations=1, preserve_min=vm_config.snapshot_preserve_min or 0)`. The method SHALL NOT call `_parse_preserve()`.

#### Scenario: Per-disk retention with chain_length
- **WHEN** VM has `snapshot_chain_length = 168`, disk `vda` has 200 snapshots, and disk `vdb` has 50 snapshots
- **THEN** the retention engine keeps the newest 168 `vda` snapshots and all 50 `vdb` snapshots
- **AND** marks the oldest 32 `vda` snapshots for removal

#### Scenario: Snapshot retention with no chain_length
- **WHEN** VM has `snapshot_chain_length = None` (unset)
- **THEN** the retention engine uses `chain_length=0` and marks all snapshots for removal (subject to preserve_min)

### Requirement: Hysteresis retention evaluation flow
`Core._evaluate_disk_retention` SHALL branch on the VM's resolved `snapshot_retention_mode`. In `"steady"` mode behavior is unchanged. In `"hysteresis"` mode Core SHALL: (1) read the disk's snapshot count `N`; (2) if `N ≤ H`, return an empty remove set; (3) if `N > H`, invoke the pure retention engine with effective keep-count `L`, apply the oldest-prefix filter and the preserve_min floor trim, and mark the resulting FULL oldest `N − L` set for commit within the same run — no per-run cap truncation and no persisted phase marker exist. The retention engine itself SHALL remain a pure function unaware of modes.

#### Scenario: Steady mode untouched
- **WHEN** the mode is `"steady"`
- **THEN** evaluation produces exactly the pre-existing keep/remove result

#### Scenario: Hysteresis collapse evaluation
- **WHEN** the mode is `"hysteresis"`, `H = 72`, `L = 24`, `N = 73`
- **THEN** the engine is invoked with effective keep-count 24
- **AND** the final remove set is ALL 49 oldest snapshots
- **AND** no phase marker is written anywhere

#### Scenario: Below threshold
- **WHEN** the mode is `"hysteresis"` and `N = 50`, `H = 72`
- **THEN** the remove set is empty

### Requirement: Scaled timeout budget for bulk collapse

For the live bulk collapse Core SHALL pass an effective timeout of `blockcommit_timeout × len(committable)` to the lifecycle manager, preserving the documented per-layer budget semantics of `blockcommit_timeout` while letting a single multi-layer job run to completion. The intent log line SHALL show the scaled value. On the offline path the per-layer meaning is unchanged (each `qemu-img commit` iteration keeps the unscaled `blockcommit_timeout`). When the scaled budget expires, the outcome is `"unknown"` and the existing reconciliation/deferral machinery applies unchanged.

#### Scenario: Budget scales with the merge set

- **WHEN** `blockcommit_timeout = 1800` and the committable set has 49 snapshots
- **THEN** the lifecycle manager receives `timeout=88200` for the single bulk job

#### Scenario: Offline budget stays per layer

- **WHEN** the same 49-snapshot set is committed offline via `QemuImgCommitManager`
- **THEN** each `qemu-img commit` call uses the unscaled `blockcommit_timeout`

### Requirement: Pre-commit chain-length baseline derived from the integrity scan

When `chain_verify_before_commit` is enabled, Core SHALL obtain the pre-commit chain-length baseline (`chain_length_before`) from the result of the pre-commit backing-chain integrity scan instead of issuing a second full `qemu-img info --backing-chain` walk. A separate `_get_chain_length` measurement SHALL be issued only when the pre-commit verification is disabled or its result carries no measured length. Post-commit measurement remains an independent fresh walk. This removes one duplicated full-chain traversal per batch without weakening any verification.

#### Scenario: Baseline reused from the scan

- **WHEN** `chain_verify_before_commit = True` and the pre-commit scan succeeds over a 73-file chain
- **THEN** `chain_length_before` equals 73
- **AND** no additional `qemu-img info --backing-chain` command is executed before the commit

#### Scenario: Fallback when verification is disabled

- **WHEN** `chain_verify_before_commit = False`
- **THEN** Core measures the baseline via its own `qemu-img info --backing-chain` call as before

### Requirement: Core._evaluate_backup_retention uses count-based policy
`Core._evaluate_backup_retention(vm_config, target, backups)` SHALL group backups by chain via `_group_backups_by_chain()` (unchanged), construct a `RetentionPolicy(chain_length=0, keep_generations=target.keep_generations or 1)`, and pass chain-level items to `IRetentionEngine.evaluate()`. The method SHALL NOT call `_parse_preserve()`.

#### Scenario: Backup retention with keep_generations
- **WHEN** target has `target_keep_generations = 2` and 3 chains exist
- **THEN** the retention engine keeps the 2 newest chains and marks the oldest for removal

### Requirement: Core._backup_target triggers full backup when due
`Core._backup_target(vm_config, target)` SHALL, for each configured disk, count the incrementals in the newest chain by calling `state.get_full_backups(target.path)` and `state.get_incremental_dependencies(target_path, newest_full.name)` (counting legacy snapshot-name keys and backup-name keys alike). When `incremental_count > target.chain_length` (or no FULLs exist for the disk), Core SHALL request a FULL; otherwise a delta. The backup is created by a single `provider.run_backup(vm_config, target, disk)` invocation per disk per run. Core SHALL NOT obtain an `IBucketFullStrategy` from the factory and SHALL NOT pass snapshot data to the provider.

After a FULL creation, Core SHALL verify it (M1/M2 per `full_verify_after_create`). Only after verification succeeds SHALL Core record the FULL in state and evaluate retention + cleanup old generations. If verification fails, Core SHALL rollback (delete FULL file + checkpoint + state records) and retry up to `backup_retry_max` times. If retries are exhausted, Core SHALL log CRITICAL and keep old generations.

#### Scenario: Incremental count exceeds chain length triggers FULL
- **WHEN** the newest chain has 169 incrementals and `target.chain_length = 168`
- **THEN** the next `run_backup` for that disk is directed to create a FULL

#### Scenario: First run creates full backup
- **WHEN** `get_full_backups(target.path)` returns an empty list for a disk
- **THEN** a FULL is created for that disk

#### Scenario: Verified FULL triggers retention + cleanup
- **WHEN** a FULL is created and passes M1/M2 verification
- **THEN** Core records it via `state.record_full_backup()`
- **AND** evaluates retention (keep newest `keep_generations` chains)
- **AND** deletes old generations via `_cleanup_backups()`

#### Scenario: Failed FULL verification triggers rollback
- **WHEN** a FULL is created but fails M1/M2 verification
- **THEN** Core deletes the broken FULL file from disk via `provider.delete()`
- **AND** deletes the checkpoint via `_cleanup_failed_checkpoint()`
- **AND** removes any state records via `state.remove_full_backup()`
- **AND** retries FULL creation (up to `backup_retry_max`)

#### Scenario: Retries exhausted keeps old generations
- **WHEN** all retry attempts fail verification
- **THEN** Core logs CRITICAL
- **AND** old generations are NOT deleted (verify-before-delete gate)

### Requirement: Blockjob probe before backup

Before invoking a backup for a disk (`run_backup` FULL or delta, running VM), Core SHALL query `virsh blockjob --domain <vm> --path <disk>` via `IShell`. If a block job is active on the disk (e.g., a blockcommit orphaned by a client-side timeout), Core SHALL skip this disk's backup for the current run, log INFO "blockcommit in progress, backup deferred for disk <disk>", and SHALL NOT treat the skip as a failure and SHALL NOT update the disk's backup baseline. The next run re-evaluates the disk normally.

#### Scenario: Active block job defers the disk backup

- **WHEN** `virsh blockjob` reports an active job on `vda` at backup time
- **THEN** no `backup-begin` is started for `vda` in this run
- **AND** an INFO log names the disk and the deferral reason
- **AND** the run does not fail because of the skip

#### Scenario: No block job proceeds normally

- **WHEN** `virsh blockjob` reports no job for the disk
- **THEN** the backup proceeds unchanged

### Requirement: Deferred backups keep the onchange gate open

When a backup result is deferred (stopped VM, blockjob active), Core SHALL NOT call `set_last_backup_allocation` for that disk+target and SHALL NOT count the deferral as a failure. The onchange gate for that disk SHALL therefore remain open and the next eligible run SHALL perform the backup.

#### Scenario: Deferred result leaves baseline untouched

- **WHEN** `run_backup` returns `deferred=True` for disk `vda`
- **THEN** `set_last_backup_allocation(target, "vda", ...)` is NOT called
- **AND** no `BackupAbortError` is raised for this disk

### Requirement: Core.schedule_summary and estimate produce per-disk output
`Core.schedule_summary(vm_filter=None) -> str` and `Core.estimate(vm_filter=None) -> str` SHALL display per-disk base image actual-size lines (one per `DiskConfig` in `vm.disks`) using `qemu-img info --force-share`. The output SHALL show count-based retention information for each VM and each target. The methods SHALL NOT generate synthetic timestamps or compute retention windows.

#### Scenario: Summary includes per-disk base image sizes
- **WHEN** `schedule_summary()` is called for a VM with disks vda and vdb
- **THEN** output includes `[vda]` and `[vdb]` lines with base image actual-size

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
When `chain_verify_before_commit = true` and there are snapshots to merge, Core SHALL call `_verify_backing_chain(vm_config, disk)` per disk before `lifecycle.blockcommit()`. If verification fails for a disk, Core SHALL emit a CRITICAL log — including the broken file path when known and the hint to run `qsnap check --deep` — and raise `RuntimeError`, aborting the remaining steps of this VM. No partial blockcommit or automatic recovery is attempted. See `specs/chain-integrity-verification/spec.md`.

#### Scenario: Broken chain aborts the VM
- **WHEN** `_verify_backing_chain(vm_config, disk)` detects a missing file in the backing chain for a specific disk
- **THEN** a CRITICAL log is emitted with `Break at: {broken_file}` and the `qsnap check --deep` hint
- **AND** `RuntimeError` is raised, aborting the remaining steps of this VM
- **AND** remaining VMs are processed normally

### Requirement: Post-commit chain verification after blockcommit
When `chain_verify_after_commit = true` and blockcommit succeeded, Core SHALL verify the chain length decreased per disk via `_get_chain_length(vm_config, disk)`. See `specs/chain-integrity-verification/spec.md`.

#### Scenario: Post-commit chain check passes
- **WHEN** chain length decreased after blockcommit AND `chain_length_before` was not `None`
- **THEN** verification passes and "Post-commit chain verification passed" is logged

#### Scenario: Post-commit skipped when chain_length_before is None
- **WHEN** `chain_length_before` is `None` and `chain_verify_after_commit` is `True`
- **THEN** "Post-commit chain verification skipped" is logged
- **AND** "Post-commit chain verification passed" is NOT logged
- **AND** merged snapshots are still removed from state

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
`Core` SHALL provide a `fork(name: str, output_path: Path, vm_filter: str | None = None) -> RestoreResult` method. It SHALL:
1. Resolve the snapshot/backup via `_resolve_snapshot()` which searches `IStateManager` and backup providers.
2. Estimate total chain size via `qemu-img info --force-share --backing-chain --output=json` and log the estimate.
3. If `self._dry_run` is True: log the planned conversion (source, output path, estimated size) at INFO level and return `RestoreResult(success=True)` WITHOUT executing any conversion or creating any file.
4. Execute the conversion via the shared standalone-image-conversion helper `convert_with_retry()` (`qemu-img convert --force-share -O qcow2 <source_path> <output_path>` with retry on retryable errors and best-effort partial-file cleanup).
5. Verify the output via `verify_standalone_image()` (M1 virtual-size equality with the source chain, M2 `qemu-img check`). On verification failure remove the output file and return a failed `RestoreResult`.
6. No XML manipulation, VM definition, or libvirt management is performed — creating a VM from the resulting image is the operator's responsibility.
7. No NBD pull-model is used — direct file read with `--force-share` is sufficient for all cases.

The `--as-vm`, `--storage`, and `--add-to-config` flags are REMOVED. The `deploy` subcommand is REMOVED. The `_append_vm_to_config()` method is REMOVED.

#### Scenario: fork from snapshot creates standalone qcow2
- **WHEN** `core.fork("myvm.20260701T1200", Path("/tmp/standalone.qcow2"))` is called
- **THEN** `qemu-img convert --force-share -O qcow2` is executed
- **AND** returns `RestoreResult(success=True, restored_path=Path("/tmp/standalone.qcow2"), chain_files=[Path("/tmp/standalone.qcow2")])`
- **AND** no `virsh dumpxml`, `virsh define`, or XML manipulation is performed

#### Scenario: fork from incremental backup flattens chain
- **WHEN** `core.fork("myvm.20260702T130000_def456", Path("/tmp/restored.qcow2"))` is called with an incremental backup
- **THEN** `qemu-img convert --force-share -O qcow2` flattens the entire backing chain (FULL + all dependents) into a single standalone file

#### Scenario: fork dry-run creates no file
- **WHEN** `core.fork("myvm.20260701T1200", Path("/tmp/standalone.qcow2"))` is called with `core.dry_run = True`
- **THEN** the chain-size estimate still runs (read-only)
- **AND** the planned conversion is logged at INFO level
- **AND** no `qemu-img convert` is executed and no output file exists afterwards
- **AND** returns `RestoreResult(success=True)`

#### Scenario: fork verifies output and removes it on verification failure
- **WHEN** conversion succeeds but `verify_standalone_image()` fails
- **THEN** the output file is removed
- **AND** returns `RestoreResult(success=False, error=<verification error>)`

### Requirement: Core.restore method
`Core` SHALL provide a `restore(name: str, vm_filter: str | None = None) -> RestoreResult` method. The `target_dir` parameter is REMOVED. Multi-disk: the restored disk is resolved from the snapshot record (`SnapshotInfo.disk`, falling back to `parse_disk_from_snapshot_name(name)`), and the result is written to THAT disk's base image (`vm_config.get_disk(disk).base_image`) — other disks of the VM are not touched. It SHALL:
1. Resolve the snapshot/backup via `_resolve_snapshot()` and determine the target disk.
2. Verify the VM is stopped via `is_vm_running()` — abort with error `"VM must be stopped for restore"` if running.
3. Pre-verify source chain integrity via `scan_backing_chain()` — abort if broken.
4. Create a standalone image at `<snapshot_dir>/<vm_name>.<disk>.restored.qcow2.tmp` via the shared helper `convert_with_retry()` (`qemu-img convert --force-share -O qcow2` with retry on retryable errors and partial-file cleanup).
5. Verify the temp image via `verify_standalone_image()` (M1 + M2) BEFORE replacement — on failure remove the temp file and abort without touching the base image.
6. Delete old snapshot overlay files of the restored disk only from `snapshot_dir` (best-effort, WARNING on failures; snapshots of other disks are kept).
7. Atomically replace the disk's base image via `os.replace(tmp_path, base_image)`.
8. Strip `<backingStore>` and update `<source file>` ONLY on the `<disk>` element whose `<target dev>` equals the restored disk, then `virsh define`.
9. Reset ONLY the restored disk's state via `IStateManager.reset_vm_disk_state(vm_name, disk)` and `IStateManager.reset_target_disk_state(target_path, vm_name, disk)` for each target — state of other disks and records of other VMs sharing a target are untouched.
10. Perform best-effort libvirt checkpoint cleanup for the restored disk ONLY: checkpoint names follow `qsnap-{target_hash}-{disk}-{timestamp}-{hex}`; only checkpoints whose third dash-separated segment equals the restored disk are deleted via `virsh checkpoint-delete --metadata`; legacy names without a disk segment are skipped with a WARNING.

The CLI SHALL offer `--dry-run` (log planned actions, execute nothing) and `--yes` (skip confirmation prompt). Without `--yes` and without `--dry-run`, the CLI SHALL prompt the operator for confirmation.

#### Scenario: restore from snapshot replaces VM disk
- **WHEN** `core.restore("myvm.20260701T1200")` is called on a stopped VM
- **THEN** `qemu-img convert --force-share -O qcow2` is executed to a temp file
- **AND** the temp file passes `verify_standalone_image()` before replacement
- **AND** `os.replace(tmp, base_image)` atomically replaces the base image
- **AND** domain XML is updated and redefined
- **AND** `reset_vm_disk_state(vm_name, disk)` and `reset_target_disk_state(target_path, vm_name, disk)` are called for each target

#### Scenario: restore aborts on running VM
- **WHEN** `core.restore("myvm.20260701T1200")` is called and the VM is running
- **THEN** returns `RestoreResult(success=False, error="VM must be stopped for restore")`

#### Scenario: restore aborts on broken source chain
- **WHEN** `core.restore("myvm.20260701T1200")` is called and `scan_backing_chain()` returns broken
- **THEN** returns `RestoreResult(success=False, error="Source backing chain is broken: ...")`

#### Scenario: restore aborts when temp image verification fails
- **WHEN** conversion succeeds but `verify_standalone_image()` fails on the temp file
- **THEN** the temp file is removed and the base image is NOT replaced
- **AND** returns `RestoreResult(success=False, error=<verification error>)`

#### Scenario: restore dry-run shows planned actions
- **WHEN** `core.restore("myvm.20260701T1200")` is called in dry-run mode
- **THEN** all planned actions are logged at INFO level
- **AND** no `qemu-img convert`, `os.replace`, or `virsh define` is executed
- **AND** returns `RestoreResult(success=True)`

#### Scenario: restore keeps other disks' state and checkpoints
- **WHEN** `core.restore(...)` completes for disk `vda` of a VM that also has disk `vdb`
- **THEN** `vdb` snapshots, allocation baseline, deferred operations, FULL records, and dependencies remain in state
- **AND** `vdb` checkpoints remain in libvirt

### Requirement: Phantom FULL detection with cascade cleanup
The phantom FULL detection in `_backup_target()` SHALL, when a FULL backup file is missing on disk, remove the FULL record from `_full_backups.json` AND remove all linked incremental dependencies from `_dependencies.json` AND clear per-disk `last_backup_allocation` if no FULLs remain after cleanup.

#### Scenario: Phantom FULL triggers cascade dependency cleanup
- **WHEN** a FULL backup file does not exist on disk and the FULL record is removed from state
- **THEN** the system SHALL also call `remove_all_incremental_dependencies(target_path, full_name)` and log the count of cleaned dependency records

#### Scenario: Last phantom FULL clears per-disk baselines
- **WHEN** all FULL records for a target are removed as phantoms and no FULLs remain
- **THEN** the system SHALL call `clear_last_backup_allocation(target_path, disk.target)` for each disk and log an INFO message

#### Scenario: Phantom FULL with remaining valid FULLs does not clear baseline
- **WHEN** a phantom FULL is removed but other valid FULL records remain for the target
- **THEN** the system SHALL NOT clear `last_backup_allocation`

### Requirement: Backup target pipeline with gate/retention separation
The `_backup_target()` method SHALL separate the onchange gate from retention execution. When the gate skips transfer, retention evaluation and cleanup SHALL still run. When a target is SUSPENDED by a space-classified error (per-target ENOSPC isolation), retention evaluation and cleanup SHALL also still run for that target, so deletions can free space (self-heal).

#### Scenario: Gate skip does not block retention
- **WHEN** `backup_create = "onchange"` and the gate returns False (no disk changed)
- **THEN** the system SHALL skip the bucket FULL check and `transfer_missing()` section
- **AND** SHALL still execute `_evaluate_backup_retention()` and `_cleanup_backups()`

#### Scenario: Suspended target still runs retention and cleanup
- **WHEN** target A's transfer was suspended by a space error
- **THEN** `_evaluate_backup_retention()` and `_cleanup_backups()` still execute for target A
- **AND** other targets of the VM are processed normally

### Requirement: Startup state validation in pipeline
The `_validate_state_at_startup()` method SHALL be called before snapshot steps and before backup steps. It SHALL run phantom FULL detection, stale baseline cleanup per-disk (per `IStateManager.get_last_backup_allocation(target_path, disk.target)`), and auto-recovery of broken backup chains, BEFORE the onchange gate and retention evaluation. Non-fatal: logs warnings, never raises.

#### Scenario: Pipeline calls startup validation
- **WHEN** `_execute_pipeline(vm_config)` is called
- **THEN** `_validate_state_at_startup(vm_config)` SHALL be called before `_execute_snapshot_steps(vm_config)`

#### Scenario: Standalone backup calls startup validation
- **WHEN** `_execute_backup_steps(vm_config)` is called (via `qsnap backup`)
- **THEN** `_validate_state_at_startup(vm_config)` SHALL be called before the target iteration loop

### Requirement: Post-create FULL backup verification with source_path
When `GlobalConfig.full_verify_after_create` is set, Core SHALL call `verify_full_backup()` after `create_full_backup()` completes. When the mode is `"hash"`, Core SHALL pass `source_path=most_recent.path` for `qemu-img compare` content verification. On success, `record_full_backup()` is called; on failure, the FULL file is deleted and NOT recorded.

#### Scenario: Failed post-create verification deletes the FULL
- **WHEN** `full_verify_after_create = "hash"` and `verify_full_backup()` reports a mismatch
- **THEN** the new FULL file is deleted and no `record_full_backup()` call occurs

### Requirement: backup_failed WARNING in Core._backup_target
`Core._backup_target()` SHALL emit a `logger.warning` when any backup returns `BackupResult(success=False)` after retries. The warning SHALL include the VM name, target path, failed disk(s), and each failure's error message, attributed to the target and disk (not to snapshots). Core SHALL first complete backup attempts for ALL disks of the target, audit the successful backups of the batch (ActionRecord + INFO log) and record their incremental dependencies, and only then raise `BackupAbortError` to abort the remaining steps of this VM. A FULL creation failure after retries SHALL log CRITICAL ("old generations preserved") and raise `BackupAbortError` without deleting old generations.

#### Scenario: Disk failure warns with target and disk attribution, audits successes, then aborts
- **WHEN** `_backup_target()` has 2 successful disk backups and 1 failed disk backup after retries
- **THEN** a WARNING is logged: `"Backup to target <target> failed for VM <vm>: disk <disk> — <error>"`
- **AND** the 2 successful backups are audited and their dependencies recorded
- **AND** `BackupAbortError` is raised after all disks were attempted, aborting the remaining steps of this VM

#### Scenario: FULL failure after retries aborts with old generations preserved
- **WHEN** FULL creation fails after all retries
- **THEN** a CRITICAL log is emitted ("old generations preserved")
- **AND** `BackupAbortError` is raised
- **AND** no old-generation backup is deleted

#### Scenario: No warning when all backups succeed
- **WHEN** `_backup_target()` receives all `BackupResult(success=True)` (including deferred)
- **THEN** no WARNING is logged for backup failures
- **AND** no `BackupAbortError` is raised

### Requirement: ActionRecord accumulation in Core pipeline
Core SHALL accumulate `ActionRecord` instances during pipeline execution (see `specs/action-audit-trail/spec.md` for the full spec). Core SHALL attach the accumulated list to `PipelineResult.actions` at the end of `_run_pipeline()`.

#### Scenario: Actions attached to PipelineResult
- **WHEN** `_run_pipeline()` completes after executing pipeline steps
- **THEN** `PipelineResult.actions` contains all `ActionRecord` entries accumulated during execution

### Requirement: Per-operation INFO logging in Core
Core SHALL emit `logger.info` messages in btrbk-style format for each pipeline operation.

#### Scenario: Snapshot creation INFO
- **WHEN** `_create_snapshot()` successfully creates a snapshot for a disk
- **THEN** an INFO message is logged: `"[snapshot] <vm_name>/<disk_target>: created <snapshot_name> (<size> B)"`

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

### Requirement: Per-target backup onchange gate
When `TargetConfig.backup_create == "onchange"` and snapshots exist, `Core._backup_target()` SHALL call `_should_backup_onchange(vm_config, target)` before proceeding with backup transfer. The gate SHALL be per-disk: it opens when ANY disk has changed since its last backup to this target. See `specs/independent-target-onchange/spec.md` for the gate logic.

#### Scenario: always mode — gate bypassed
- **WHEN** `backup_create = "always"` (default)
- **THEN** `_should_backup_onchange()` is NOT called
- **AND** the backup transfer proceeds unconditionally

#### Scenario: No snapshots — backup skipped
- **WHEN** `backup_create = "onchange"` but no snapshots exist
- **THEN** `_should_backup_onchange()` returns `False` (nothing to transfer)
- **AND** the backup transfer is skipped
- **AND** an INFO log message is emitted

### Requirement: Core._cleanup_failed_checkpoint rollback method
Core SHALL provide a private method `_cleanup_failed_checkpoint(vm_config, target, full_result)` that deletes exactly the single libvirt checkpoint created during a failed FULL attempt, identified by `full_result.checkpoint`. The method SHALL delete that checkpoint via `virsh checkpoint-delete --metadata --domain <vm> <checkpoint>`. When `full_result.checkpoint` is `None` (no checkpoint was created, e.g. a stopped-VM FULL), the method SHALL delete nothing. The method SHALL NOT filter checkpoints by the `qsnap-{target_hash}-*` prefix, SHALL NOT delete checkpoints belonging to other disks, and SHALL NOT delete the previous baseline checkpoint of the same disk. Deletion failure SHALL be non-fatal (WARNING log).

#### Scenario: Checkpoint cleaned up after failed FULL
- **WHEN** FULL verification fails for a running VM and `_cleanup_failed_checkpoint()` is called with `full_result.checkpoint = "qsnap-ab12cd34-vda-20260807T020000-9f8e7d"`
- **THEN** exactly that checkpoint is deleted via `virsh checkpoint-delete --metadata`
- **AND** no orphaned checkpoint from the failed attempt remains for the next `transfer_missing()` call

#### Scenario: Multi-disk rollback leaves other disks untouched
- **WHEN** a FULL for disk `vda` fails verification on a VM whose target also holds baseline checkpoints for disks `vdb` and `vdc`
- **THEN** only the failed attempt's `vda` checkpoint is deleted
- **AND** the `vdb` and `vdc` checkpoints are NOT deleted

#### Scenario: Previous baseline of the failed disk is preserved
- **WHEN** disk `vda` already holds a baseline checkpoint from the last successful transfer and a new FULL attempt for `vda` fails verification
- **THEN** only the successor checkpoint created by the failed attempt is deleted
- **AND** the previous baseline checkpoint remains intact

#### Scenario: Stopped-VM FULL failure deletes nothing
- **WHEN** FULL verification fails after a stopped-VM FULL (which created no checkpoint, `full_result.checkpoint is None`)
- **THEN** `_cleanup_failed_checkpoint()` issues no `virsh checkpoint-delete` call
- **AND** every existing checkpoint remains intact

#### Scenario: Checkpoint deletion failure is non-fatal
- **WHEN** `virsh checkpoint-delete` fails during `_cleanup_failed_checkpoint()`
- **THEN** a WARNING is logged
- **AND** the rollback continues (FULL file removal and state cleanup still complete)

### Requirement: Core imports shared utilities from qsnap.utils
Core SHALL import `is_vm_running` from `qsnap.utils.nbd`, `verify_full_backup` and `scan_backing_chain` from `qsnap.utils.verification`, and `compute_backoff`, `is_retryable`, `parse_retry_duration` from `qsnap.utils.retry`. Core SHALL NOT import from `qsnap.modules.backup` or `qsnap.modules.*` except through the factory.

#### Scenario: Core has no domain module imports
- **WHEN** `qsnap/core/__init__.py` is inspected
- **THEN** there is NO `from qsnap.modules.backup` import
- **AND** there is NO `from qsnap.modules.snapshot` import
- **AND** all utility imports come from `qsnap.utils`

### Requirement: Core._check_deferred_operations is per-disk
`Core._check_deferred_operations(vm_config)` SHALL drain deferred operations per disk. Each `DeferredBlockcommit` entry has a `disk` field. Entries whose disk is no longer configured SHALL be dropped. Per-disk `_plan_blockcommit(vm_config, disk, snapshots)` determines which snapshots are committable and with which executor. An entry leaves the queue only when ALL of its snapshots are committed.

#### Scenario: Deferred entry dropped when disk removed from config
- **WHEN** a deferred entry references a disk not in `vm_config.disks`
- **THEN** the entry is dropped with a WARNING log

### Requirement: VM-level failure isolation
The VM pipeline is the atomic unit of execution. A definitive per-disk failure — snapshot creation failure, missing snapshot directory, broken backing chain before commit, non-MAC and non-space blockcommit failure, post-commit chain length unchanged, or non-space FULL/delta backup failure after retries — SHALL abort the remaining steps of that VM by raising from the failing step, with ONE exception for the backup phase: a failed backup of one disk SHALL NOT prevent backup attempts for the remaining disks of the same target; Core aggregates per-disk backup results and aborts the VM only after all disks were attempted (successful disks are audited and recorded first). The per-VM `try/except` in `Core._run_pipeline()` SHALL catch the exception, record `VMRunResult(success=False, error=...)`, and continue with the next VM. Already-completed steps of the aborted VM SHALL NOT be rolled back. MAC denials (AppArmor/SELinux) are deferred operations, not failures, and SHALL NOT abort the VM. Space-classified errors (ENOSPC) in backups SHALL NOT abort the VM either: they suspend only the affected target (per-target isolation), and the VM's remaining targets continue. Space-classified blockcommit failures are deferred with reason `"enospc"`.

#### Scenario: Disk failure aborts remaining steps of the VM
- **WHEN** snapshot creation for disk `vdb` fails after `vda` succeeded
- **THEN** no snapshot of the failed batch is recorded in state (all-or-nothing)
- **AND** retention, blockcommit, and backup steps for this VM are skipped
- **AND** `VMRunResult(success=False)` is recorded with the error

#### Scenario: Other VMs continue after a VM aborts
- **WHEN** the pipeline for "vm1" aborts on a broken chain
- **THEN** "vm2" is still processed normally

#### Scenario: Backup failure of one disk does not abandon other disks
- **WHEN** the backup of disk `vda` fails definitively and disk `vdb` is pending
- **THEN** the `vdb` backup is still attempted, and on success is audited and recorded
- **AND** the VM aborts after all disks were attempted

#### Scenario: MAC denial does not abort the VM
- **WHEN** `lifecycle.blockcommit()` returns `CommitResult(success=False)` with an AppArmor or SELinux error
- **THEN** the snapshots are added to the deferred queue with reason `"apparmor"` / `"selinux"`
- **AND** no exception is raised and the pipeline continues

#### Scenario: Space error suspends one target, VM continues
- **WHEN** target A's backup fails with "No space left on device"
- **THEN** target A is suspended (remaining backups skipped)
- **AND** target B of the same VM is backed up normally
- **AND** `VMRunResult` reflects the space-limited run without a VM abort

### Requirement: BackupAbortError marks backup-stage failures
`qsnap.core` SHALL define `BackupAbortError(RuntimeError)`. It SHALL be raised only by the backup stage for NON-space failures (FULL creation failure after retries, backup transfer failure after retries, verification failures). Its message SHALL attribute the failure to the target path and disk(s) with the underlying reason(s) — it SHALL NOT reference snapshots. Space-classified failures (`is_space_error` returns `True`) SHALL NOT raise `BackupAbortError`; they SHALL trigger per-target suspension instead, so the verify-before-delete gate and the ENOSPC isolation contract do not interfere. `Core._run_pipeline()` SHALL set `VMRunResult.backup_failed = isinstance(exc, BackupAbortError)` so the CLI can map backup-stage aborts to exit code 10.

#### Scenario: Backup abort sets backup_failed
- **WHEN** `_backup_target()` raises `BackupAbortError`
- **THEN** the per-VM except handler records `VMRunResult(success=False, backup_failed=True)`
- **AND** the error message names the target path and failed disk(s)

#### Scenario: Space failure does not raise BackupAbortError
- **WHEN** a backup fails with a space-classified error
- **THEN** no `BackupAbortError` is raised
- **AND** the target is suspended and `VMRunResult.backup_failed` is `False`

### Requirement: Space-limited flag wired into PipelineResult
`PipelineResult` SHALL carry a `space_limited: bool` field (default `False`). Core SHALL set it `True` when any VM/target during the run was limited by a space-classified error: reactive transfer ENOSPC, proactive strict free-space gate rejection, blockcommit deferred with reason `"enospc"`, or state-write ENOSPC. Dry-run runs SHALL always report `space_limited=False` (no transfers execute). The CLI uses this field to select exit code 4 (spec: cli-interface Exit codes).

#### Scenario: Space-limited run flagged
- **WHEN** one target was suspended by ENOSPC during `qsnap run`
- **THEN** `PipelineResult.space_limited` is `True`

#### Scenario: Clean run not flagged
- **WHEN** all transfers and commits succeed and no gate rejected
- **THEN** `PipelineResult.space_limited` is `False`

#### Scenario: Dry-run never flagged
- **WHEN** `qsnap --dry-run run` executes
- **THEN** `PipelineResult.space_limited` is `False` regardless of predictions

### Requirement: Proactive free-space gate integrated into backup steps
`Core._backup_target()` SHALL invoke the free-space gate (spec: enospc-fault-handling "Proactive free-space gate before transfers") before attempting each FULL or incremental transfer. In `strict` mode a failed gate SHALL route into the same per-target suspension path as a reactive ENOSPC (no transfer attempted, retention/cleanup still run, other targets continue). The gate SHALL be skipped entirely in dry-run mode (prediction only: Core SHALL record a prediction entry naming the target and the would-be estimate check).

#### Scenario: Strict gate rejection suspends target without transfer
- **WHEN** `free_space_check = "strict"` and the estimate exceeds free space for target A
- **THEN** no `backup-begin`/`qemu-img convert` is attempted for target A
- **AND** target A is suspended exactly as for reactive ENOSPC

#### Scenario: Dry-run predicts the gate
- **WHEN** dry-run is active and the gate would fail for a target
- **THEN** a prediction entry is recorded
- **AND** no transfer, suspension flag, or exit-code state is mutated

### Requirement: Commit path intent-journal orchestration

In `Core._blockcommit_one_disk` (main path and deferred drain), Core SHALL write the commit
intent record for the disk (`set_commit_in_progress`) before invoking the lifecycle manager
and SHALL clear it only after the outcome is finalized per the `commit-intent-journal` spec.
On `outcome="success"` the state-write order SHALL be: `set_last_commit_ts` →
`remove_snapshot` per merged snapshot → `clear_commit_in_progress` (last). On definitive
`outcome="failure"` the intent SHALL be cleared before failure classification. On
`outcome="unknown"` the intent SHALL be kept until reconciliation finalizes the outcome.

#### Scenario: Success path ordering

- **WHEN** the manager returns `CommitResult(success=True, outcome="success")` for merge set `["s1"]`
- **THEN** state writes occur in the order `set_last_commit_ts`, `remove_snapshot("s1")`, `clear_commit_in_progress`

#### Scenario: Unknown path keeps intent

- **WHEN** the manager returns `outcome="unknown"`
- **THEN** `clear_commit_in_progress` is NOT called before reconciliation finishes

### Requirement: Unknown commit outcome dispatches reconciliation

When the lifecycle manager returns `outcome="unknown"`, Core SHALL invoke the reconciliation
helper (spec: `commit-reconciliation`) and act on its result: `late_success` → converge
state and continue; `job_active` → defer with reason `"blockjob_active"` and continue the VM
pipeline; `failure` → clear intent and raise `RuntimeError`; `inconclusive` → defer with
reason `"vm_state_unknown"` and continue. Core SHALL NOT raise on the timeout alone.

#### Scenario: Timeout no longer aborts before reconciliation

- **WHEN** the manager returns `CommitResult(success=False, outcome="unknown")`
- **AND** reconciliation returns `late_success`
- **THEN** no `RuntimeError` is raised and the VM pipeline reaches the backup steps

### Requirement: Block-job probe before blockcommit

Before invoking the lifecycle manager for a disk, Core SHALL probe the disk via the shared
block-job helper (spec: `blockjob-protocol`). Only probe result `"none"` SHALL proceed to
commit. `"active"` with an intent record for the disk SHALL trigger reconciliation instead of
a new commit; `"active"` without intent SHALL defer with reason `"blockjob_active"`;
`"error"` SHALL defer with reason `"vm_state_unknown"`. Deferrals SHALL NOT abort the VM
pipeline. The probe applies on the live (`virsh`) executor path; the offline (`qemu-img`)
path skips the probe (it errors on inactive domains) and relies on the fail-closed offline
race guard below.

#### Scenario: Active unknown job defers the commit

- **WHEN** the probe returns `"active"` for `vda` and no intent record exists
- **THEN** the merge set is deferred with reason `"blockjob_active"` and no commit command runs

### Requirement: Block-job probe before snapshot creation

Before `_create_snapshot` for a running VM, Core SHALL probe every disk about to be
snapshotted (spec: `blockjob-protocol`). Any `"active"` or `"error"` result SHALL skip
snapshot creation for the whole VM this run with a WARNING; the change-detection baseline
SHALL remain untouched so the onchange gate stays open. No deferred entry SHALL be created.

#### Scenario: Snapshot creation skipped while a job is active

- **WHEN** the probe for any disk returns `"active"`
- **THEN** no `virsh snapshot-create-as` runs for this VM this run and the baseline is unchanged

### Requirement: Fail-closed offline race guard

When the plan selected `QemuImgCommitManager`, Core re-checks `virsh domstate` immediately
before invoking the manager. If the re-check reports a non-shut-off state, Core defers the
committable subset with reason `"vm_running"` (existing behavior). If the re-check call
FAILS (`ShellResult.success is False`), Core SHALL defer the committable subset with reason
`"vm_state_unknown"` and SHALL NOT invoke `QemuImgCommitManager` — unknown VM state fails
closed. This guard SHALL apply on BOTH commit call sites: the main commit path
(`_blockcommit_one_disk`) and the deferred-queue drain path, in each case before any intent
record is written.

#### Scenario: Recheck failure defers instead of committing

- **WHEN** the plan selected the offline executor and the immediate `virsh domstate` re-check fails
- **THEN** the committable subset is deferred with reason `"vm_state_unknown"`
- **AND** no `qemu-img commit` command is issued

### Requirement: Configurable commit timeout pass-through

Core SHALL pass `GlobalConfig.blockcommit_timeout` as the `timeout` keyword argument on every
lifecycle-manager invocation (main commit path and deferred drain). No hard-coded timeout
value SHALL remain in the commit path.

#### Scenario: Configured timeout reaches the manager

- **WHEN** `blockcommit_timeout = 900` and a commit runs
- **THEN** the manager's `blockcommit` is called with `timeout=900`

### Requirement: Intent recovery in the deferred-operations step

During the deferred-operations step (step 0 of the snapshot steps), Core SHALL process stale
commit intent records per the `commit-intent-journal` crash-recovery requirement, before
evaluating new commits. Dry-run SHALL only predict recovery actions, never write state.

#### Scenario: Stale intent resolved before new commit evaluation

- **WHEN** step 0 runs with a stale intent record for `vda` and no active block job
- **THEN** the intent is reconciled (converged or cleared) before retention/blockcommit evaluation for `vda`

