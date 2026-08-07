# Core Orchestrator — delta

## ADDED Requirements

### Requirement: Blockjob probe before backup

Before invoking a backup for a disk (`run_backup` FULL or delta, running VM), Core SHALL
query `virsh blockjob --domain <vm> --path <disk>` via `IShell`. If a block job is active on
the disk (e.g., a blockcommit orphaned by a client-side timeout), Core SHALL skip this disk's
backup for the current run, log INFO "blockcommit in progress, backup deferred for disk
<disk>", and SHALL NOT treat the skip as a failure and SHALL NOT update the disk's backup
baseline. The next run re-evaluates the disk normally.

#### Scenario: Active block job defers the disk backup

- **WHEN** `virsh blockjob` reports an active job on `vda` at backup time
- **THEN** no `backup-begin` is started for `vda` in this run
- **AND** an INFO log names the disk and the deferral reason
- **AND** the run does not fail because of the skip

#### Scenario: No block job proceeds normally

- **WHEN** `virsh blockjob` reports no job for the disk
- **THEN** the backup proceeds unchanged

### Requirement: Deferred backups keep the onchange gate open

When a backup result is deferred (stopped VM, blockjob active), Core SHALL NOT call
`set_last_backup_allocation` for that disk+target and SHALL NOT count the deferral as a
failure. The onchange gate for that disk SHALL therefore remain open and the next eligible
run SHALL perform the backup.

#### Scenario: Deferred result leaves baseline untouched

- **WHEN** `run_backup` returns `deferred=True` for disk `vda`
- **THEN** `set_last_backup_allocation(target, "vda", ...)` is NOT called
- **AND** no `BackupAbortError` is raised for this disk

## MODIFIED Requirements

### Requirement: Core._backup_target triggers full backup when due
`Core._backup_target(vm_config, target)` SHALL, for each configured disk, count the
incrementals in the newest chain by calling `state.get_full_backups(target.path)` and
`state.get_incremental_dependencies(target_path, newest_full.name)` (counting legacy
snapshot-name keys and backup-name keys alike). When `incremental_count > target.chain_length`
(or no FULLs exist for the disk), Core SHALL request a FULL; otherwise a delta. The backup is
created by a single `provider.run_backup(vm_config, target, disk)` invocation per disk per
run. Core SHALL NOT obtain an `IBucketFullStrategy` from the factory and SHALL NOT pass
snapshot data to the provider.

After a FULL creation, Core SHALL verify it (M1/M2 per `full_verify_after_create`). Only
after verification succeeds SHALL Core record the FULL in state and evaluate retention +
cleanup old generations. If verification fails, Core SHALL rollback (delete FULL file +
checkpoint + state records) and retry up to `backup_retry_max` times. If retries are
exhausted, Core SHALL log CRITICAL and keep old generations.

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

### Requirement: backup_failed WARNING in Core._backup_target
`Core._backup_target()` SHALL emit a `logger.warning` when any backup returns
`BackupResult(success=False)` after retries. The warning SHALL include the VM name, target
path, failed disk(s), and each failure's error message, attributed to the target and disk
(not to snapshots). Core SHALL first complete backup attempts for ALL disks of the target,
audit the successful backups of the batch (ActionRecord + INFO log) and record their
incremental dependencies, and only then raise `BackupAbortError` to abort the remaining steps
of this VM. A FULL creation failure after retries SHALL log CRITICAL ("old generations
preserved") and raise `BackupAbortError` without deleting old generations.

#### Scenario: Disk failure warns with target and disk attribution, audits successes, then aborts
- **WHEN** `_backup_target()` has 2 successful disk backups and 1 failed disk backup after
  retries
- **THEN** a WARNING is logged: `"Backup to target <target> failed for VM <vm>: disk <disk>
  — <error>"`
- **AND** the 2 successful backups are audited and their dependencies recorded
- **AND** `BackupAbortError` is raised after all disks were attempted, aborting the remaining
  steps of this VM

#### Scenario: FULL failure after retries aborts with old generations preserved
- **WHEN** FULL creation fails after all retries
- **THEN** a CRITICAL log is emitted ("old generations preserved")
- **AND** `BackupAbortError` is raised
- **AND** no old-generation backup is deleted

#### Scenario: No warning when all backups succeed
- **WHEN** `_backup_target()` receives all `BackupResult(success=True)` (including deferred)
- **THEN** no WARNING is logged for backup failures
- **AND** no `BackupAbortError` is raised

### Requirement: VM-level failure isolation
The VM pipeline is the atomic unit of execution. A definitive per-disk failure — snapshot
creation failure, missing snapshot directory, broken backing chain before commit, non-MAC and
non-space blockcommit failure, post-commit chain length unchanged, or non-space FULL/delta
backup failure after retries — SHALL abort the remaining steps of that VM by raising from the
failing step, with ONE exception for the backup phase: a failed backup of one disk SHALL NOT
prevent backup attempts for the remaining disks of the same target; Core aggregates per-disk
backup results and aborts the VM only after all disks were attempted (successful disks are
audited and recorded first). The per-VM `try/except` in `Core._run_pipeline()` SHALL catch
the exception, record `VMRunResult(success=False, error=...)`, and continue with the next VM.
Already-completed steps of the aborted VM SHALL NOT be rolled back. MAC denials
(AppArmor/SELinux) are deferred operations, not failures, and SHALL NOT abort the VM.
Space-classified errors (ENOSPC) in backups SHALL NOT abort the VM either: they suspend only
the affected target (per-target isolation), and the VM's remaining targets continue.
Space-classified blockcommit failures are deferred with reason `"enospc"`.

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
- **WHEN** `lifecycle.blockcommit()` returns `CommitResult(success=False)` with an AppArmor or
  SELinux error
- **THEN** the snapshots are added to the deferred queue with reason `"apparmor"` / `"selinux"`
- **AND** no exception is raised and the pipeline continues

#### Scenario: Space error suspends one target, VM continues
- **WHEN** target A's backup fails with "No space left on device"
- **THEN** target A is suspended (remaining backups skipped)
- **AND** target B of the same VM is backed up normally
- **AND** `VMRunResult` reflects the space-limited run without a VM abort

### Requirement: BackupAbortError marks backup-stage failures
`qsnap.core` SHALL define `BackupAbortError(RuntimeError)`. It SHALL be raised only by the
backup stage for NON-space failures (FULL creation failure after retries, backup transfer
failure after retries, verification failures). Its message SHALL attribute the failure to the
target path and disk(s) with the underlying reason(s) — it SHALL NOT reference snapshots.
Space-classified failures (`is_space_error` returns `True`) SHALL NOT raise
`BackupAbortError`; they SHALL trigger per-target suspension instead, so the
verify-before-delete gate and the ENOSPC isolation contract do not interfere.
`Core._run_pipeline()` SHALL set `VMRunResult.backup_failed = isinstance(exc,
BackupAbortError)` so the CLI can map backup-stage aborts to exit code 10.

#### Scenario: Backup abort sets backup_failed
- **WHEN** `_backup_target()` raises `BackupAbortError`
- **THEN** the per-VM except handler records `VMRunResult(success=False, backup_failed=True)`
- **AND** the error message names the target path and failed disk(s)

#### Scenario: Space failure does not raise BackupAbortError
- **WHEN** a backup fails with a space-classified error
- **THEN** no `BackupAbortError` is raised
- **AND** the target is suspended and `VMRunResult.backup_failed` is `False`
