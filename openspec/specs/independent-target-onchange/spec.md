# Independent Target Onchange

## Purpose

Source-disk-based change detection for the `backup_create="onchange"` gate. Replaces the snapshot-name comparison (Approach B) with direct `IChangeDetector` queries against a per-target baseline stored in `IStateManager`. Decouples backup decisions from snapshot creation.

## Requirements

### Requirement: Source-disk-based backup onchange gate

The `backup_create="onchange"` gate (`Core._should_backup_onchange`) SHALL determine whether backup transfer should proceed by querying the source VM's active disk directly via `IChangeDetector`, NOT by comparing snapshot names against target files. The gate SHALL be independent of snapshot creation — it SHALL work correctly even when no snapshots exist in state.

#### Scenario: Gate opens when source disk has changed

- **WHEN** `backup_create="onchange"` and the source disk's current allocation (or map hash) differs from `IStateManager.get_last_backup_allocation(target_path)`
- **THEN** the gate SHALL return True (proceed with backup)

#### Scenario: Gate skips when source disk unchanged

- **WHEN** `backup_create="onchange"` and the source disk's current allocation (or map hash) equals `IStateManager.get_last_backup_allocation(target_path)`
- **THEN** the gate SHALL return False (skip transfer)
- **AND** SHALL log "disk unchanged since last backup — skipping transfer"

#### Scenario: Gate opens on first run (no baseline)

- **WHEN** `backup_create="onchange"` and `IStateManager.get_last_backup_allocation(target_path)` returns `None`
- **THEN** the gate SHALL return True (proceed with backup — first run)

#### Scenario: Gate works without snapshots in state

- **WHEN** `backup_create="onchange"` and `snapshot_create="ondemand"` and no snapshots exist in state
- **AND** the source disk has changed since the last backup
- **THEN** the gate SHALL return True (proceed with backup)
- **AND** the gate SHALL NOT depend on snapshot names or `provider.list(target)`

#### Scenario: Gate works when snapshot_create is always and disk unchanged

- **WHEN** `backup_create="onchange"` and `snapshot_create="always"` and a new snapshot was created
- **AND** the source disk has NOT changed since the last backup (allocation unchanged)
- **THEN** the gate SHALL return False (skip transfer)
- **AND** the new snapshot SHALL NOT cause the gate to open

### Requirement: Change detector selection for backup gate

The backup onchange gate SHALL use the same `IChangeDetector` implementation selected by `VMConfig.change_detection_mode`. The detector SHALL be created via `factory.create_change_detector(vm_config.change_detection_mode)`. When `change_detection_mode="allocation-size"`, the gate SHALL compare `actual-size` values. When `change_detection_mode="allocation-map"`, the gate SHALL compare allocation-map hashes.

#### Scenario: Allocation-size mode for backup gate

- **WHEN** `change_detection_mode="allocation-size"` and `backup_create="onchange"`
- **THEN** the gate SHALL use `AllocationSizeDetector` to query the source disk
- **AND** SHALL compare `result.current_allocation` (actual-size) against `get_last_backup_allocation(target_path)` using strictly-greater-than comparison

#### Scenario: Allocation-map mode for backup gate

- **WHEN** `change_detection_mode="allocation-map"` and `backup_create="onchange"`
- **THEN** the gate SHALL use `MapChangeDetector` to query the source disk
- **AND** SHALL compare `result.current_allocation` (map hash) against `get_last_backup_allocation(target_path)` using inequality comparison

### Requirement: Per-target baseline update after successful backup

After a successful backup transfer (FULL or incremental) to a target with `backup_create="onchange"`, Core SHALL call `IStateManager.set_last_backup_allocation(target_path, current_allocation)` where `current_allocation` is the `ChangeResult.current_allocation` value obtained at the start of the backup step. If the backup fails, the baseline SHALL NOT be updated.

#### Scenario: Baseline updated after successful backup

- **WHEN** a backup transfer to a target with `backup_create="onchange"` succeeds
- **THEN** Core SHALL call `set_last_backup_allocation(target_path, current_allocation)`
- **AND** the next run's gate SHALL compare against this new baseline

#### Scenario: Baseline not updated after failed backup

- **WHEN** a backup transfer to a target with `backup_create="onchange"` fails
- **THEN** Core SHALL NOT call `set_last_backup_allocation`
- **AND** the next run's gate SHALL compare against the old baseline
- **AND** the gate SHALL return True (changed — retry)

#### Scenario: Baseline not updated when gate skips

- **WHEN** the backup onchange gate returns False (disk unchanged)
- **THEN** Core SHALL NOT call `set_last_backup_allocation`
- **AND** the baseline remains current (no change to record)

### Requirement: Onchange gate and retention separation

When the onchange gate skips transfer, the system SHALL still execute backup retention evaluation and cleanup for the target. This ensures expired backups are deleted even when no new data needs to be transferred.

#### Scenario: Retention runs when gate skips transfer

- **WHEN** `backup_create="onchange"` and the gate returns False (disk unchanged) and expired backups exist on the target
- **THEN** the system SHALL skip the transfer section but SHALL still run `_evaluate_backup_retention()` and `_cleanup_backups()`

### Requirement: Detector fail-safe behavior for backup gate

When the change detector fails to query the source disk (virsh or qemu-img command failure), the detector SHALL return `ChangeResult(changed=True)` (fail-safe). The backup gate SHALL interpret this as "changed" and proceed with the backup.

#### Scenario: Detector failure causes gate to open

- **WHEN** `backup_create="onchange"` and the change detector fails (virsh domblklist or qemu-img error)
- **THEN** the detector SHALL return `ChangeResult(changed=True)`
- **AND** the gate SHALL return True (proceed with backup — fail-safe)
