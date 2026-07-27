# Auto-Recovery

## Purpose

Detects and auto-recovers broken backup chains at pipeline startup. Ensures per-chain grouping can resolve all chains before retention evaluation. Forces fresh FULL creation when no valid FULL remains on a target.

## Requirements

### Requirement: Auto-recovery of broken backup chains at startup

`Core._validate_state_at_startup()` SHALL detect and auto-recover broken backup chains on each target. For each non-FULL backup on the target, Core SHALL run `qemu-img info --force-share --backing-chain --output=json`. If the command fails (broken chain), the backup SHALL be deleted, its state dependency record SHALL be cleaned, and a WARNING log SHALL be emitted. This SHALL run BEFORE retention evaluation to ensure per-chain grouping can resolve all chains.

#### Scenario: Broken-chain backups auto-deleted at startup

- **WHEN** a target has FULL + inc1 + inc2 + inc3, and inc2's backing file has been deleted
- **THEN** `_validate_state_at_startup()` detects inc2 and inc3 as broken (inc3 transitively broken through inc2)
- **AND** inc2 and inc3 are deleted from the target
- **AND** `remove_incremental_dependency()` is called for each
- **AND** a WARNING log is emitted with the count of broken-chain backups
- **AND** FULL and inc1 remain (intact chain)

#### Scenario: No broken chains — no recovery needed

- **WHEN** all backups on a target have intact backing chains
- **THEN** no auto-recovery is performed
- **AND** the pipeline continues normally

### Requirement: Force FULL creation when no valid FULL remains

After auto-recovery deletes broken-chain backups, if no valid FULL backup remains on the target (all FULLs deleted or missing), Core SHALL set a force-full flag for that target. `Core._backup_target()` SHALL check this flag before evaluating the bucket strategy. When the flag is set, a new FULL backup SHALL be created unconditionally, and the flag SHALL be cleared.

#### Scenario: Force FULL after all FULLs lost

- **WHEN** auto-recovery deletes all backups on a target (no FULL remains)
- **THEN** the target path is added to `_force_full_targets`
- **AND** on the next `_backup_target()` call, `should_full` is `True` regardless of bucket strategy
- **AND** a new FULL is created
- **AND** the flag is cleared

#### Scenario: Force FULL not triggered when FULL exists

- **WHEN** auto-recovery deletes broken incrementals but the FULL remains intact
- **THEN** the force-full flag is NOT set
- **AND** `_backup_target()` evaluates the bucket strategy normally

### Requirement: Auto-recovery is non-fatal

Auto-recovery SHALL log WARNING/INFO for each action but SHALL NOT raise exceptions or abort the pipeline. If auto-recovery encounters an error (e.g., `qemu-img info` timeout), the error SHALL be logged and the backup SHALL be left in place for the next run.

#### Scenario: Auto-recovery error does not abort pipeline

- **WHEN** `qemu-img info --backing-chain` times out for one backup
- **THEN** a WARNING is logged for that backup
- **AND** the backup is NOT deleted (left for next run)
- **AND** auto-recovery continues with the next backup
- **AND** the pipeline continues normally
