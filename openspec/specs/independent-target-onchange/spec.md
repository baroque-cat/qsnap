# Independent Target Onchange

## Purpose

Source-disk-based per-disk change detection for the `backup_create="onchange"` gate. The gate opens when ANY disk has changed since its last backup to this target. Replaces snapshot-name comparison with direct `IChangeDetector` queries against a per-target per-disk baseline stored in `IStateManager`. Decouples backup decisions from snapshot creation.

## Requirements

### Requirement: Per-disk source-based backup onchange gate

The `backup_create="onchange"` gate (`Core._should_backup_onchange(vm_config, target)`) SHALL determine whether backup transfer should proceed by querying each configured disk directly via `IChangeDetector`, NOT by comparing snapshot names against target files. The gate SHALL iterate all disks in `vm_config.disks`, calling `detector.has_changed(vm_config, disk.target)` for each, and comparing `current_allocation` against `IStateManager.get_last_backup_allocation(target_path, disk.target)`. The gate opens (`should_proceed=True`) when ANY disk has changed since its last backup to this target. The returned tuple SHALL include a `dict[str, ChangeResult]` mapping each disk target to its `ChangeResult` for per-disk baseline updates. The gate SHALL be independent of snapshot creation — it SHALL work correctly even when no snapshots exist in state.

#### Scenario: Gate opens when any source disk has changed

- **WHEN** `backup_create="onchange"` and disk `vda`'s current allocation exceeds `IStateManager.get_last_backup_allocation(target_path, "vda")`
- **AND** disk `vdb`'s current allocation equals its baseline
- **THEN** the gate SHALL return `(True, {...})` (proceed with backup)
- **AND** the returned dict SHALL contain `ChangeResult` for both disks

#### Scenario: Gate skips when all disks unchanged

- **WHEN** `backup_create="onchange"` and every disk's current allocation equals its per-disk baseline
- **THEN** the gate SHALL return `(False, {...})` (skip transfer)
- **AND** SHALL log "no disk changed since last backup — skipping transfer"

#### Scenario: Gate opens on first run for a disk (no baseline)

- **WHEN** `backup_create="onchange"` and `IStateManager.get_last_backup_allocation(target_path, disk.target)` returns `None` for any disk
- **THEN** the gate SHALL return `True` (proceed with backup — first run for that disk)

#### Scenario: Gate works without snapshots in state

- **WHEN** `backup_create="onchange"` and `snapshot_create="ondemand"` and no snapshots exist in state
- **AND** a source disk has changed since the last backup
- **THEN** the gate SHALL return `True` (proceed with backup)
- **AND** the gate SHALL NOT depend on snapshot names or `provider.list(target)`

#### Scenario: Gate works when snapshot_create is always and disks unchanged

- **WHEN** `backup_create="onchange"` and `snapshot_create="always"` and a new snapshot was created
- **AND** no source disk has changed since the last backup (all per-disk baselines match)
- **THEN** the gate SHALL return `False` (skip transfer)
- **AND** the new snapshot SHALL NOT cause the gate to open

### Requirement: Change detector selection for backup gate

The backup onchange gate SHALL use the same `IChangeDetector` implementation selected by `VMConfig.change_detection_mode`. The detector SHALL be created via `factory.create_change_detector(vm_config.change_detection_mode)`. When `change_detection_mode="allocation-size"`, the gate SHALL compare `actual-size` values using strictly-greater-than (`current > last`). When `change_detection_mode="allocation-map"`, the gate SHALL compare allocation-map hashes using inequality (`current != last`).

#### Scenario: Allocation-size mode for per-disk backup gate

- **WHEN** `change_detection_mode="allocation-size"` and `backup_create="onchange"`
- **THEN** the gate SHALL use `AllocationSizeDetector` to query each disk
- **AND** SHALL compare `result.current_allocation` (actual-size) against per-disk baseline using strictly-greater-than

#### Scenario: Allocation-map mode for per-disk backup gate

- **WHEN** `change_detection_mode="allocation-map"` and `backup_create="onchange"`
- **THEN** the gate SHALL use `MapChangeDetector` to query each disk
- **AND** SHALL compare `result.current_allocation` (map hash) against per-disk baseline using inequality

### Requirement: Per-disk baseline update after successful backup

After a successful backup transfer (FULL or incremental) to a target with `backup_create="onchange"`, Core SHALL call `IStateManager.set_last_backup_allocation(target_path, disk.target, current_allocation)` for each disk, where `current_allocation` is the `ChangeResult.current_allocation` value from the `change_results` dict returned by `_should_backup_onchange()`. If the backup fails, no baselines SHALL be updated.

#### Scenario: Per-disk baselines updated after successful backup

- **WHEN** a backup transfer to a target with `backup_create="onchange"` succeeds
- **THEN** Core SHALL call `set_last_backup_allocation(target_path, disk.target, current_allocation)` for each disk
- **AND** the next run's gate SHALL compare against these new per-disk baselines

#### Scenario: Baselines not updated after failed backup

- **WHEN** a backup transfer to a target with `backup_create="onchange"` fails
- **THEN** Core SHALL NOT call `set_last_backup_allocation` for any disk
- **AND** the next run's gate SHALL compare against the old per-disk baselines
- **AND** the gate SHALL return `True` (changed — retry)

#### Scenario: Baselines not updated when gate skips

- **WHEN** the backup onchange gate returns `False` (all disks unchanged)
- **THEN** Core SHALL NOT call `set_last_backup_allocation` for any disk
- **AND** the per-disk baselines remain current (no change to record)

### Requirement: Onchange gate and retention separation

When the onchange gate skips transfer, the system SHALL still execute backup retention evaluation and cleanup for the target. This ensures expired backups are deleted even when no new data needs to be transferred.

#### Scenario: Retention runs when gate skips transfer

- **WHEN** `backup_create="onchange"` and the gate returns `False` (all disks unchanged) and expired backups exist on the target
- **THEN** the system SHALL skip the transfer section but SHALL still run `_evaluate_backup_retention()` and `_cleanup_backups()`

### Requirement: Detector fail-safe behavior for backup gate

When the change detector fails to query a disk (virsh or qemu-img command failure), the detector SHALL return `ChangeResult(changed=True)` (fail-safe). The backup gate SHALL interpret this as "disk changed" and proceed with the backup. Additionally, when the detector reports `changed=True`, `current_allocation==0`, and `last_allocation>0` (indicating a command failure), the gate SHALL treat the disk as changed regardless of the baseline comparison.

#### Scenario: Detector failure causes gate to open

- **WHEN** `backup_create="onchange"` and the change detector fails for disk `vda` (virsh domblklist or qemu-img error)
- **THEN** the detector SHALL return `ChangeResult(changed=True)` for that disk
- **AND** the gate SHALL return `True` (proceed with backup — fail-safe)

#### Scenario: Fail-safe catches detector failure disguised as no-change

- **WHEN** `backup_create="onchange"` and the change detector returns `changed=True`, `current_allocation=0`, and `last_allocation>0` (detector failed to query)
- **AND** the baseline comparison would indicate no-change (0 is not greater than last)
- **THEN** the gate SHALL override and treat the disk as changed (fail-safe)
