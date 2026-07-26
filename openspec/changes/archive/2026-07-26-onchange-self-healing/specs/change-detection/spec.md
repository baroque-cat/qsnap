## MODIFIED Requirements

### Requirement: Backup-side onchange gate

The backup-side onchange gate (`_should_backup_onchange`) SHALL determine whether backup transfer should proceed for a target in `backup_create = "onchange"` mode. The gate SHALL check whether any snapshot in state is not yet backed up to the target by calling `provider.list(target)` and comparing snapshot names. The gate SHALL NOT compare allocation values.

#### Scenario: Gate passes when new snapshots exist on target

- **WHEN** `backup_create = "onchange"` and a snapshot exists in state whose name does not appear in `provider.list(target)` results
- **THEN** the gate SHALL return True (proceed with backup)

#### Scenario: Gate skips when all snapshots already backed up

- **WHEN** `backup_create = "onchange"` and all snapshots in state have corresponding files on the target (all names match)
- **THEN** the gate SHALL return False (skip transfer) and log "no new snapshots — skipping transfer"

#### Scenario: Gate passes on first backup to target

- **WHEN** `backup_create = "onchange"` and the target has no existing backup files (`provider.list(target)` returns empty list)
- **THEN** the gate SHALL return True (proceed with backup)

#### Scenario: Gate works independently of snapshot_create mode

- **WHEN** `backup_create = "onchange"` and `snapshot_create = "always"` and a new snapshot was created
- **THEN** the gate SHALL detect the new snapshot is not on the target and return True

#### Scenario: Gate works for standalone qsnap backup

- **WHEN** `qsnap backup` is invoked (no snapshot steps) and there are snapshots in state not yet on the target
- **THEN** the gate SHALL return True (proceed with backup)

#### Scenario: Gate does not use last_backup_allocation

- **WHEN** the gate is evaluated
- **THEN** the system SHALL NOT read `last_backup_allocation` from `_target_state.json`
- **AND** SHALL NOT call `set_last_backup_allocation` after a successful backup

## ADDED Requirements

### Requirement: Onchange gate and retention separation

When the onchange gate skips transfer, the system SHALL still execute backup retention evaluation and cleanup for the target. This ensures expired backups are deleted even when no new snapshots exist.

#### Scenario: Retention runs when gate skips transfer

- **WHEN** `backup_create = "onchange"` and the gate returns False (no new snapshots) and expired backups exist on the target
- **THEN** the system SHALL skip the transfer section but SHALL still run `_evaluate_backup_retention()` and `_cleanup_backups()`

#### Scenario: Transfer skipped but retention cleans expired backups

- **WHEN** the gate skips transfer and retention evaluation marks backups for removal
- **THEN** the system SHALL delete the expired backups via `_cleanup_backups()` (including cascade deletion and ghost retention logic)
