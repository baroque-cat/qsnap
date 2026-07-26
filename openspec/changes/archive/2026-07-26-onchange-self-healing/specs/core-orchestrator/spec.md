## MODIFIED Requirements

### Requirement: Phantom FULL detection with cascade cleanup

The phantom FULL detection in `_backup_target()` SHALL, when a FULL backup file is missing on disk, remove the FULL record from `_full_backups.json` AND remove all linked incremental dependencies from `_dependencies.json` AND clear `last_backup_allocation` if no FULLs remain after cleanup.

#### Scenario: Phantom FULL triggers cascade dependency cleanup

- **WHEN** a FULL backup file does not exist on disk and the FULL record is removed from state
- **THEN** the system SHALL also call `remove_all_incremental_dependencies(target_path, full_name)` and log the count of cleaned dependency records

#### Scenario: Last phantom FULL clears baseline

- **WHEN** all FULL records for a target are removed as phantoms and no FULLs remain
- **THEN** the system SHALL call `clear_last_backup_allocation(target_path)` and log an INFO message

#### Scenario: Phantom FULL with remaining valid FULLs does not clear baseline

- **WHEN** a phantom FULL is removed but other valid FULL records remain for the target
- **THEN** the system SHALL NOT clear `last_backup_allocation`

### Requirement: Backup target pipeline with gate/retention separation

The `_backup_target()` method SHALL separate the onchange gate from retention execution. When the gate skips transfer, retention evaluation and cleanup SHALL still run.

#### Scenario: Gate skip does not block retention

- **WHEN** `backup_create = "onchange"` and the gate returns False (no new snapshots)
- **THEN** the system SHALL skip the bucket FULL check and `transfer_missing()` section
- **AND** SHALL still execute `_evaluate_backup_retention()` and `_cleanup_backups()`

### Requirement: Startup state validation in pipeline

The `_execute_pipeline()` method SHALL call `_validate_state_at_startup()` before `_execute_snapshot_steps()` and `_execute_backup_steps()`. The `_execute_backup_steps()` method SHALL also call `_validate_state_at_startup()` for standalone `qsnap backup` invocations.

#### Scenario: Pipeline calls startup validation

- **WHEN** `_execute_pipeline(vm_config)` is called
- **THEN** `_validate_state_at_startup(vm_config)` SHALL be called before `_execute_snapshot_steps(vm_config)`

#### Scenario: Standalone backup calls startup validation

- **WHEN** `_execute_backup_steps(vm_config)` is called (via `qsnap backup`)
- **THEN** `_validate_state_at_startup(vm_config)` SHALL be called before the target iteration loop
