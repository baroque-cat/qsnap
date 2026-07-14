## ADDED Requirements

### Requirement: Pre-flight environment validation before pipeline
Core SHALL execute `_validate_environment(vm_config)` before `_execute_pipeline(vm_config)`. Validation SHALL verify: (a) `snapshot_dir` exists and is writable, (b) `base_image` file exists, (c) `virsh` and `qemu-img` binaries are in PATH, (d) VM is defined in libvirt. Validation failure SHALL return a `CheckResult` with `status = "validation_failed"` and prevent pipeline execution for that VM.

#### Scenario: All validations pass
- **WHEN** `_validate_environment()` checks a properly configured VM
- **THEN** pipeline continues to `_execute_snapshot_steps()`

#### Scenario: snapshot_dir does not exist
- **WHEN** `snapshot_dir` path is missing
- **THEN** pipeline returns `VMRunResult(success=False, error="snapshot_dir not found: ...")` without executing any steps

### Requirement: Deferred operations integrated into snapshot steps
`Core._execute_snapshot_steps()` SHALL check `IStateManager` for deferred blockcommit operations before step 2 (snapshot creation). If VM is shut off, deferred blockcommits SHALL be executed. If VM is running, deferred operations SHALL be skipped. After blockcommit steps (step 4), if any snapshot's blockcommit failed due to MAC denial, those snapshots SHALL be recorded as deferred operations.

#### Scenario: Deferred blockcommits executed on shut-off VM
- **WHEN** `virsh domstate` returns "shut off" and deferred queue has 2 snapshots
- **THEN** `BlockCommitManager.blockcommit()` is called with those snapshots before any new snapshot creation

#### Scenario: Deferred blockcommits skipped on running VM
- **WHEN** VM is running and deferred queue has entries
- **THEN** pipeline logs INFO and proceeds to change detection

### Requirement: Post-transfer verification in backup steps
`Core._backup_target()` SHALL pass `target.verify` to the backup provider. The provider SHALL perform verification according to the configured level after transfer. Verification failures SHALL be reflected in `BackupResult` and counted in `backup_failed`.

#### Scenario: Metadata verification failure marks backup as failed
- **WHEN** target has `verify = "metadata"` and verification detects format mismatch
- **THEN** `backup_failed` flag is set to True in the pipeline result

## MODIFIED Requirements

### Requirement: Pipeline step order
`Core._execute_pipeline(vm_config)` SHALL execute steps in this order:
1. Pre-flight environment validation
2. Deferred blockcommit check (if VM is shut off)
3. Change detection — if `snapshot_create` mode requires it
4. Snapshot creation — if detector says we should, or if mode is "always"
5. Snapshot retention evaluation — which snapshots to keep/remove
6. Snapshot lifecycle — blockcommit removed snapshots with MAC denial deferral
7. For each target: backup transfer → backup verification → backup retention → cleanup

#### Scenario: Pipeline with always mode
- **WHEN** a VM has `snapshot_create = "always"` and the pipeline runs
- **THEN** validation runs first, then a snapshot is created regardless of change detection result

#### Scenario: Pipeline with onchange mode, no changes
- **WHEN** a VM has `snapshot_create = "onchange"` and the change detector reports `has_changed = False`
- **THEN** no snapshot is created, but retention is still evaluated
