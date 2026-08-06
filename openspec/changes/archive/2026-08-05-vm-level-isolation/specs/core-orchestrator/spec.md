## ADDED Requirements

### Requirement: VM-level failure isolation
The VM pipeline is the atomic unit of execution. A definitive per-disk failure — snapshot creation failure, missing snapshot directory, broken backing chain before commit, non-MAC blockcommit failure, post-commit chain length unchanged, or FULL/incremental backup failure after retries — SHALL abort the remaining steps of that VM by raising from the failing step. The per-VM `try/except` in `Core._run_pipeline()` SHALL catch the exception, record `VMRunResult(success=False, error=...)`, and continue with the next VM. Already-completed steps of the aborted VM SHALL NOT be rolled back. MAC denials (AppArmor/SELinux) are deferred operations, not failures, and SHALL NOT abort the VM.

#### Scenario: Disk failure aborts remaining steps of the VM
- **WHEN** snapshot creation for disk `vdb` fails after `vda` succeeded
- **THEN** the `vda` snapshot remains recorded in state (no rollback)
- **AND** retention, blockcommit, and backup steps for this VM are skipped
- **AND** `VMRunResult(success=False)` is recorded with the error

#### Scenario: Other VMs continue after a VM aborts
- **WHEN** the pipeline for "vm1" aborts on a broken chain
- **THEN** "vm2" is still processed normally

#### Scenario: MAC denial does not abort the VM
- **WHEN** `lifecycle.blockcommit()` returns `CommitResult(success=False)` with an AppArmor or SELinux error
- **THEN** the snapshots are added to the deferred queue with reason `"apparmor"` / `"selinux"`
- **AND** no exception is raised and the pipeline continues

### Requirement: BackupAbortError marks backup-stage failures
`qsnap.core` SHALL define `BackupAbortError(RuntimeError)`. It SHALL be raised only by the backup stage (FULL creation failure after retries, incremental transfer failure after retries). `Core._run_pipeline()` SHALL set `VMRunResult.backup_failed = isinstance(exc, BackupAbortError)` so the CLI can map backup-stage aborts to exit code 10.

#### Scenario: Backup abort sets backup_failed
- **WHEN** `_backup_target()` raises `BackupAbortError`
- **THEN** the per-VM except handler records `VMRunResult(success=False, backup_failed=True)`

### Requirement: Per-disk snapshot creation with configured disk list
`Core._create_snapshot()` SHALL iterate over `vm_config.disks` (the explicitly configured disk list, via `_resolve_disks()`). It SHALL NOT auto-discover disks via `virsh domblklist`. For each disk, it SHALL create one snapshot with naming convention `{vm_name}.{timestamp}_{disk_target}_{6hex}.qcow2` (the `_{6hex}` suffix is `secrets.token_hex(3)`), using that disk's effective snapshot directory (`vm_config.snapshot_dir_for(disk)`). Quiesce (guest-agent freeze) is VM-wide — it SHALL be applied only to the first disk's snapshot (index==0). Each successful snapshot SHALL be recorded in state as `SnapshotInfo` with `disk=disk.target`. Partial failure SHALL NOT be tolerated: if the snapshot directory is missing or any disk's snapshot creation fails, Core SHALL raise `RuntimeError`, aborting the remaining steps of this VM (spec: VM-level failure isolation).

#### Scenario: VM with multiple disks (vda, vdb)
- **WHEN** VM config has disks `vda` and `vdb`
- **THEN** two snapshots are created: one for each disk
- **AND** snapshot files are named `{vm}.{ts}_vda_{6hex}.qcow2` and `{vm}.{ts}_vdb_{6hex}.qcow2`
- **AND** `SnapshotInfo` records have `disk="vda"` and `disk="vdb"` respectively
- **AND** quiesce is applied only to the first disk's snapshot

#### Scenario: vda succeeds, vdb fails — VM aborts
- **WHEN** snapshot of `vda` succeeds but `vdb` fails
- **THEN** `vda` snapshot is recorded in state (no rollback)
- **AND** `RuntimeError` is raised, aborting the remaining steps of this VM
- **AND** other VMs are processed normally

#### Scenario: onchange gate is VM-wide, snapshots cover all disks
- **WHEN** `snapshot_create = "onchange"` and disk `vda` changed but `vdb` did not
- **THEN** the gate `any(detector.has_changed(vm, disk).changed for disk in disks)` is `True`
- **AND** snapshots are created for ALL disks (`vda` and `vdb`)

### Requirement: Pre-commit chain verification before blockcommit
When `chain_verify_before_commit = true` and there are snapshots to merge, Core SHALL call `_verify_backing_chain(vm_config, disk)` per disk before `lifecycle.blockcommit()`. If verification fails for a disk, Core SHALL emit a CRITICAL log — including the broken file path when known and the hint to run `qsnap check --deep` — and raise `RuntimeError`, aborting the remaining steps of this VM. No partial blockcommit or automatic recovery is attempted. See `specs/chain-integrity-verification/spec.md`.

#### Scenario: Broken chain aborts the VM
- **WHEN** `_verify_backing_chain(vm_config, disk)` detects a missing file in the backing chain for a specific disk
- **THEN** a CRITICAL log is emitted with `Break at: {broken_file}` and the `qsnap check --deep` hint
- **AND** `RuntimeError` is raised, aborting the remaining steps of this VM
- **AND** remaining VMs are processed normally

### Requirement: backup_failed WARNING in Core._backup_target
`Core._backup_target()` SHALL emit a `logger.warning` when any incremental transfer returns `BackupResult(success=False)` after retries. The warning SHALL include the VM name, target path, count of failed snapshots, and the specific snapshot names with their error messages. Core SHALL then audit the successful transfers of the batch (ActionRecord + INFO log) and record their incremental dependencies, and raise `BackupAbortError` to abort the remaining steps of this VM. A FULL creation failure after retries SHALL log CRITICAL ("old generations preserved") and raise `BackupAbortError` without deleting old generations.

#### Scenario: Transfer failure warns, audits successes, then aborts
- **WHEN** `_backup_target()` receives 2 successful and 1 failed `BackupResult` from `transfer_missing()` after retries
- **THEN** a WARNING is logged: `"Backup transfer failed for VM <vm> target <target>: <N> snapshot(s) failed — <name>: <error>"`
- **AND** the 2 successful transfers are audited and their dependencies recorded
- **AND** `BackupAbortError` is raised, aborting the remaining steps of this VM

#### Scenario: FULL failure after retries aborts with old generations preserved
- **WHEN** `create_full_backup()` fails after all retries
- **THEN** a CRITICAL log is emitted ("old generations preserved")
- **AND** `BackupAbortError` is raised
- **AND** no old-generation backup is deleted

#### Scenario: No warning when all transfers succeed
- **WHEN** `_backup_target()` receives all `BackupResult(success=True)` from `transfer_missing()`
- **THEN** no WARNING is logged for backup failures
- **AND** no `BackupAbortError` is raised

## MODIFIED Requirements

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

## RENAMED Requirements

- FROM: `### Requirement: Per-disk snapshot creation with configured disk list`
- TO: `### Requirement: Per-disk snapshot creation with configured disk list (superseded)`

- FROM: `### Requirement: Pre-commit chain verification before blockcommit`
- TO: `### Requirement: Pre-commit chain verification before blockcommit (superseded)`

- FROM: `### Requirement: backup_failed WARNING in Core._backup_target`
- TO: `### Requirement: backup_failed WARNING in Core._backup_target (superseded)`

## REMOVED Requirements

### Requirement: Per-disk snapshot creation with configured disk list (superseded)
**Reason**: Replaced by the re-added requirement of the same original name — partial failure tolerance replaced by VM-level abort; naming pinned to `{vm}.{ts}_{disk}_{6hex}.qcow2`; onchange mixed-disk scenario added. Scenario names changed, which MODIFIED cannot express.
**Migration**: None (behavioral change covered by the re-added requirement).

### Requirement: Pre-commit chain verification before blockcommit (superseded)
**Reason**: Replaced by the re-added requirement of the same original name — verification failure now aborts the VM instead of skipping the disk or attempting partial blockcommit.
**Migration**: None (behavioral change covered by the re-added requirement).

### Requirement: backup_failed WARNING in Core._backup_target (superseded)
**Reason**: Replaced by the re-added requirement of the same original name — after the WARNING, Core now audits successful transfers and raises `BackupAbortError` (VM abort, exit 10) instead of continuing with a flag.
**Migration**: None (behavioral change covered by the re-added requirement).
