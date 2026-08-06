# Core Orchestrator — Delta

## MODIFIED Requirements

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

### Requirement: VM-level failure isolation
The VM pipeline is the atomic unit of execution. A definitive per-disk failure — snapshot creation failure, missing snapshot directory, broken backing chain before commit, non-MAC and non-space blockcommit failure, post-commit chain length unchanged, or non-space FULL/incremental backup failure after retries — SHALL abort the remaining steps of that VM by raising from the failing step. The per-VM `try/except` in `Core._run_pipeline()` SHALL catch the exception, record `VMRunResult(success=False, error=...)`, and continue with the next VM. Already-completed steps of the aborted VM SHALL NOT be rolled back. MAC denials (AppArmor/SELinux) are deferred operations, not failures, and SHALL NOT abort the VM. Space-classified errors (ENOSPC) in backup transfers SHALL NOT abort the VM either: they suspend only the affected target (per-target isolation), and the VM's remaining targets continue. Space-classified blockcommit failures are deferred with reason `"enospc"`.

#### Scenario: Disk failure aborts remaining steps of the VM
- **WHEN** snapshot creation for disk `vdb` fails after `vda` succeeded
- **THEN** no snapshot of the failed batch is recorded in state (all-or-nothing)
- **AND** retention, blockcommit, and backup steps for this VM are skipped
- **AND** `VMRunResult(success=False)` is recorded with the error

#### Scenario: Other VMs continue after a VM aborts
- **WHEN** the pipeline for "vm1" aborts on a broken chain
- **THEN** "vm2" is still processed normally

#### Scenario: MAC denial does not abort the VM
- **WHEN** `lifecycle.blockcommit()` returns `CommitResult(success=False)` with an AppArmor or SELinux error
- **THEN** the snapshots are added to the deferred queue with reason `"apparmor"` / `"selinux"`
- **AND** no exception is raised and the pipeline continues

#### Scenario: Space error suspends one target, VM continues
- **WHEN** target A's incremental transfer fails with "No space left on device"
- **THEN** target A is suspended (remaining transfers skipped)
- **AND** target B of the same VM is backed up normally
- **AND** `VMRunResult` reflects the space-limited run without a VM abort

### Requirement: BackupAbortError marks backup-stage failures
`qsnap.core` SHALL define `BackupAbortError(RuntimeError)`. It SHALL be raised only by the backup stage for NON-space failures (FULL creation failure after retries, incremental transfer failure after retries, verification failures). Space-classified failures (`is_space_error` returns `True`) SHALL NOT raise `BackupAbortError`; they SHALL trigger per-target suspension instead, so the verify-before-delete gate (verification failure aborts before any cleanup) and the ENOSPC isolation contract do not interfere. `Core._run_pipeline()` SHALL set `VMRunResult.backup_failed = isinstance(exc, BackupAbortError)` so the CLI can map backup-stage aborts to exit code 10.

#### Scenario: Backup abort sets backup_failed
- **WHEN** `_backup_target()` raises `BackupAbortError`
- **THEN** the per-VM except handler records `VMRunResult(success=False, backup_failed=True)`

#### Scenario: Space failure does not raise BackupAbortError
- **WHEN** a transfer fails with a space-classified error
- **THEN** no `BackupAbortError` is raised
- **AND** the target is suspended and `VMRunResult.backup_failed` is `False`

## ADDED Requirements

### Requirement: Space-limited flag wired into PipelineResult
`PipelineResult` SHALL carry a `space_limited: bool` field (default `False`). Core SHALL
set it `True` when any VM/target during the run was limited by a space-classified error:
reactive transfer ENOSPC, proactive strict free-space gate rejection, blockcommit
deferred with reason `"enospc"`, or state-write ENOSPC. Dry-run runs SHALL always report
`space_limited=False` (no transfers execute). The CLI uses this field to select exit
code 4 (spec: cli-interface Exit codes).

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
`Core._backup_target()` SHALL invoke the free-space gate (spec: enospc-fault-handling
"Proactive free-space gate before transfers") before attempting each FULL or incremental
transfer. In `strict` mode a failed gate SHALL route into the same per-target suspension
path as a reactive ENOSPC (no transfer attempted, retention/cleanup still run, other
targets continue). The gate SHALL be skipped entirely in dry-run mode (prediction only:
Core SHALL record a prediction entry naming the target and the would-be estimate check).

#### Scenario: Strict gate rejection suspends target without transfer
- **WHEN** `free_space_check = "strict"` and the estimate exceeds free space for target A
- **THEN** no `backup-begin`/`qemu-img convert` is attempted for target A
- **AND** target A is suspended exactly as for reactive ENOSPC

#### Scenario: Dry-run predicts the gate
- **WHEN** dry-run is active and the gate would fail for a target
- **THEN** a prediction entry is recorded
- **AND** no transfer, suspension flag, or exit-code state is mutated
