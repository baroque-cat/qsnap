## REMOVED Requirements

### Requirement: Backup-side onchange gate

**Reason**: The snapshot-name-based backup onchange gate (Approach B) is replaced by source-disk-based change detection. The new gate queries the VM's active disk directly via `IChangeDetector` and compares against a per-target baseline stored in `IStateManager.get_last_backup_allocation(target_path)`. This decouples backup decisions from snapshot creation.

**Migration**: The `Core._should_backup_onchange()` method is replaced with a new implementation that uses `factory.create_change_detector(vm_config.change_detection_mode)` and `IStateManager.get_last_backup_allocation(target_path)`. After a successful backup, `set_last_backup_allocation(target_path, current_allocation)` is called to update the baseline. The `provider.list(target)` call is no longer used for the onchange gate. See `specs/independent-target-onchange/spec.md` for the new requirements.

## ADDED Requirements

### Requirement: Source-disk-based backup onchange gate

The `backup_create="onchange"` gate SHALL determine whether backup transfer should proceed by querying the source VM's active disk directly via `IChangeDetector`. The gate SHALL be independent of snapshot creation. The gate SHALL use `IStateManager.get_last_backup_allocation(target_path)` as the per-target baseline. After a successful backup, Core SHALL call `IStateManager.set_last_backup_allocation(target_path, current_allocation)` to update the baseline. See `specs/independent-target-onchange/spec.md` for full requirements.

#### Scenario: Gate uses change detector, not snapshot names

- **WHEN** `backup_create="onchange"` is evaluated
- **THEN** the gate SHALL create a change detector via `factory.create_change_detector(vm_config.change_detection_mode)`
- **AND** SHALL call `detector.has_changed(vm_config)` to obtain `ChangeResult.current_allocation`
- **AND** SHALL compare `current_allocation` against `state.get_last_backup_allocation(target_path)`
- **AND** SHALL NOT call `provider.list(target)` for the gate decision

#### Scenario: Gate does not use last_backup_allocation (REMOVED)

- **WHEN** the gate is evaluated
- **THEN** the system SHALL read `last_backup_allocation` from `_target_state.json`
- **AND** SHALL call `set_last_backup_allocation` after a successful backup

### Requirement: Onchange gate and retention separation

When the onchange gate skips transfer, the system SHALL still execute backup retention evaluation and cleanup for the target. This ensures expired backups are deleted even when the source disk has not changed.

#### Scenario: Retention runs when gate skips transfer

- **WHEN** `backup_create="onchange"` and the gate returns False (disk unchanged) and expired backups exist on the target
- **THEN** the system SHALL skip the transfer section but SHALL still run `_evaluate_backup_retention()` and `_cleanup_backups()`

#### Scenario: Transfer skipped but retention cleans expired backups

- **WHEN** the gate skips transfer and retention evaluation marks backups for removal
- **THEN** the system SHALL delete the expired backups via `_cleanup_backups()` (including cascade deletion and per-chain retention logic)
