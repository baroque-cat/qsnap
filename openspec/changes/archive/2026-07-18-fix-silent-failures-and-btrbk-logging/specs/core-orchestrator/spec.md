## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Post-commit chain verification after blockcommit

When `chain_verify_after_commit = true` and blockcommit succeeded, Core SHALL verify the chain length decreased. See `specs/chain-integrity-verification/spec.md`. The "Post-commit chain verification passed" INFO message SHALL be logged ONLY when the post-commit verification actually ran (i.e., inside the `else` branch where `chain_length_before` was not `None` and `remove_snapshot()` was called). When `chain_length_before` is `None` and verification is skipped, the message SHALL NOT be logged.

#### Scenario: Post-commit chain check passes
- **WHEN** chain length decreased after blockcommit AND `chain_length_before` was not `None`
- **THEN** verification passes and "Post-commit chain verification passed" is logged

#### Scenario: Post-commit skipped when chain_length_before is None
- **WHEN** `chain_length_before` is `None` and `chain_verify_after_commit` is `True`
- **THEN** "Post-commit chain verification skipped" is logged (existing message)
- **AND** "Post-commit chain verification passed" is NOT logged
- **AND** merged snapshots remain in state (no `remove_snapshot()` call)

### Requirement: Dry-run mode

Core SHALL support dry-run mode where all pipeline steps are evaluated but no mutation occurs. In dry-run mode, `_backup_target()` SHALL pass `full_verify_before_rebase` to the backup provider (retention evaluation and bucket strategy still execute as pure logic). The dry-run SHALL NOT accumulate `ActionRecord` entries — since no mutations occur, no actions are recorded. The `PipelineResult.dry_run` flag SHALL be set to `True` to indicate the run was a dry-run.

#### Scenario: Dry-run logs planned actions
- **WHEN** `core.run()` is called in dry-run mode
- **THEN** each planned action is logged at INFO level, but no IShell mutating commands are executed
- **AND** `PipelineResult.dry_run` is `True`
- **AND** `PipelineResult.actions` is empty (no mutations occurred, so no `ActionRecord` entries are accumulated)

#### Scenario: Dry-run activated from CLI
- **WHEN** `qsnap -n run` is executed
- **THEN** `Core.dry_run` is set to `True` before `core.run()` is called
