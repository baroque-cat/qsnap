## MODIFIED Requirements

### Requirement: Startup state validation before onchange gate

The system SHALL perform a lightweight state-vs-disk validation at the start of the pipeline (before snapshot and backup steps) that detects and cleans phantom FULLs, stale `last_backup_allocation` baselines, AND broken backup chains on targets. This ensures the onchange gate sees correct state and per-chain grouping can resolve all chains.

#### Scenario: Startup validation cleans phantom FULLs

- **WHEN** the pipeline starts for a VM and a FULL backup record exists in state whose file does not exist on disk
- **THEN** the system SHALL remove the phantom FULL record from `_full_backups.json`, remove all linked incremental dependencies from `_dependencies.json`, and log a WARNING

#### Scenario: Startup validation clears stale baseline when no FULLs remain

- **WHEN** the pipeline starts for a VM and after phantom cleanup no FULL records remain for a target, but `last_backup_allocation` still exists in `_target_state.json`
- **THEN** the system SHALL clear the `last_backup_allocation` entry and log an INFO message

#### Scenario: Startup validation clears stale baseline when no FULLs in state

- **WHEN** the pipeline starts for a VM and no FULL backup records exist in state for a target, but `last_backup_allocation` exists in `_target_state.json`
- **THEN** the system SHALL clear the `last_backup_allocation` entry and log an INFO message

#### Scenario: Startup validation is non-fatal

- **WHEN** the pipeline starts and state validation encounters an error (e.g., corrupt JSON file)
- **THEN** the system SHALL log a WARNING and continue the pipeline without raising an exception

#### Scenario: Startup validation runs for standalone backup

- **WHEN** `qsnap backup` is invoked (standalone backup without snapshot steps)
- **THEN** the system SHALL run state validation at the start of `_execute_backup_steps` before processing targets

#### Scenario: Startup validation does not delete checkpoints

- **WHEN** the pipeline starts and orphaned libvirt checkpoints are detected
- **THEN** the system SHALL NOT automatically delete them (only `qsnap reconcile` does auto-cleanup)
- **AND** SHALL leave checkpoint cleanup to the explicit `reconcile` command

## ADDED Requirements

### Requirement: Broken backup chain auto-recovery at startup

After phantom FULL cleanup, the system SHALL detect and auto-recover broken backup chains on each target. For each non-FULL backup on the target, the system SHALL run `qemu-img info --force-share --backing-chain --output=json` via `IShell`. If the command fails (broken chain), the backup SHALL be deleted from the target, its dependency record SHALL be cleaned via `remove_incremental_dependency()`, and a WARNING log SHALL be emitted. This SHALL run BEFORE retention evaluation.

#### Scenario: Broken-chain backups auto-deleted at startup

- **WHEN** a target has FULL + inc1 + inc2 + inc3, and inc2's backing file has been deleted
- **THEN** the system detects inc2 and inc3 as broken (inc3 transitively broken through inc2)
- **AND** inc2 and inc3 are deleted from the target
- **AND** `remove_incremental_dependency()` is called for each
- **AND** a WARNING log is emitted with the count of broken-chain backups
- **AND** FULL and inc1 remain (intact chain)

#### Scenario: No broken chains — no recovery needed

- **WHEN** all backups on a target have intact backing chains
- **THEN** no auto-recovery is performed
- **AND** the pipeline continues normally

#### Scenario: Auto-recovery forces FULL when no valid FULL remains

- **WHEN** auto-recovery deletes all backups on a target (no FULL remains)
- **THEN** the target path is added to `_force_full_targets`
- **AND** on the next `_backup_target()` call, a new FULL is created unconditionally
- **AND** the force-full flag is cleared after FULL creation

#### Scenario: Auto-recovery error does not abort pipeline

- **WHEN** `qemu-img info --backing-chain` times out for one backup
- **THEN** a WARNING is logged for that backup
- **AND** the backup is NOT deleted (left for next run)
- **AND** auto-recovery continues with the next backup
- **AND** the pipeline continues normally
