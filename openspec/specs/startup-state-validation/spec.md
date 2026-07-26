# Startup State Validation

## Purpose

Lightweight state-vs-disk validation at pipeline startup that detects and cleans phantom FULLs and stale `last_backup_allocation` baselines before the onchange gate runs. This ensures the gate sees correct state and phantom entries don't block legitimate FULL creation.

## Requirements

### Requirement: Startup state validation before onchange gate

The system SHALL perform a lightweight state-vs-disk validation at the start of the pipeline (before snapshot and backup steps) that detects and cleans phantom FULLs and stale `last_backup_allocation` baselines. This ensures the onchange gate sees correct state.

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
