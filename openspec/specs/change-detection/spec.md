# Change Detection

## Purpose

Detects whether a VM disk has changed by comparing the current state against the last recorded per-disk baseline. Supports two detection modes: `allocation-size` (compares `qemu-img info actual-size`) and `allocation-map` (compares `qemu-img map` region hashes). Detection is always per-disk — `disk` is a required parameter.

## Requirements

### Requirement: Change detection via allocation-size comparison
The system SHALL determine whether a VM disk has changed by comparing the current allocation-size of the active image with the per-disk last recorded value from `IStateManager.get_last_allocation(vm_name, disk)`. The current allocation-size SHALL be determined via `qemu-img info --output=json --force-share` on the active image, whose path is obtained via `virsh domblklist`.

The default `change_detection_mode` in `VMConfig` SHALL be `"allocation-map"`. The `"allocation-size"` mode SHALL remain available as an explicit configuration option.

#### Scenario: Allocation has grown — changes detected
- **WHEN** `IStateManager.get_last_allocation("myvm", "vda")` returns 65536
- **AND** `qemu-img info` on the active image returns `actual-size: 131072`
- **THEN** the module returns `ChangeResult(changed=True, last_allocation=65536, current_allocation=131072, disk="vda")`

#### Scenario: Allocation unchanged — no changes
- **WHEN** `IStateManager.get_last_allocation("myvm", "vda")` returns 65536
- **AND** `qemu-img info` on the active image returns `actual-size: 65536`
- **THEN** the module returns `ChangeResult(changed=False, last_allocation=65536, current_allocation=65536, disk="vda")`

#### Scenario: First run — no previous state for this disk
- **WHEN** `IStateManager.get_last_allocation("myvm", "vdb")` returns `None`
- **THEN** the module returns `ChangeResult(changed=True, last_allocation=0, current_allocation=0, disk="vdb")`
- **AND** this guarantees the first snapshot for this disk is created

#### Scenario: virsh or qemu-img command fails
- **WHEN** `virsh domblklist` or `qemu-img info` returns an error
- **THEN** the module returns `ChangeResult(changed=True)` with `disk` set to the queried disk (fail-safe: rather create an unnecessary snapshot than miss changes)

#### Scenario: Default change detection mode is allocation-map
- **WHEN** a `VMConfig` is constructed without an explicit `change_detection_mode`
- **THEN** `vm_config.change_detection_mode` equals `"allocation-map"`

#### Scenario: Explicit allocation-size still works
- **WHEN** a `VMConfig` is constructed with `change_detection_mode = "allocation-size"`
- **THEN** `vm_config.change_detection_mode` equals `"allocation-size"`
- **AND** `DefaultFactory.create_change_detector("allocation-size")` returns `AllocationSizeDetector`

### Requirement: Per-disk change detection with required disk parameter
`IChangeDetector.has_changed(vm_config, disk)` SHALL require the `disk: str` parameter. The `disk` parameter identifies the libvirt target device name (e.g. `"vda"`) for which change detection is performed. The `ChangeResult` SHALL include a required `disk` field.

#### Scenario: Per-disk change detection for vdb
- **WHEN** `detector.has_changed(vm_config, "vdb")` is called
- **THEN** allocation comparison uses the `vdb` disk path resolved from `virsh domblklist`

#### Scenario: ChangeResult carries disk field
- **WHEN** `detector.has_changed(vm_config, "vda")` returns a result
- **THEN** `result.disk` equals `"vda"`

### Requirement: MapChangeDetector implements IChangeDetector
The system SHALL provide a `MapChangeDetector` class implementing `IChangeDetector` in `qsnap/modules/change/map_detector.py`. It SHALL use `qemu-img map --output=json` for allocated-region comparison, comparing a hash of sorted `(offset, length)` tuples against the per-disk baseline from `IStateManager.get_last_allocation(vm_name, disk)`. `DefaultFactory.create_change_detector("allocation-map")` SHALL return `MapChangeDetector`.

#### Scenario: Allocation map differs — changes detected
- **WHEN** `qemu-img map --output=json` returns different allocated regions than the last recorded hash
- **THEN** `ChangeResult(changed=True)` is returned

#### Scenario: Map command fails — fail-safe
- **WHEN** `qemu-img map` returns non-zero exit code
- **THEN** `ChangeResult(changed=True)` is returned

### Requirement: Source-disk-based per-disk backup onchange gate
The `backup_create="onchange"` gate SHALL determine whether backup transfer should proceed by querying each configured disk via `IChangeDetector.has_changed(vm_config, disk.target)`. The gate opens when ANY disk has changed since its last backup to the target. Per-disk baselines are read from `IStateManager.get_last_backup_allocation(target_path, disk)`. After a successful backup, Core SHALL call `IStateManager.set_last_backup_allocation(target_path, disk, cr.current_allocation)` for each disk.

#### Scenario: Gate uses change detector with per-disk baselines
- **WHEN** `backup_create="onchange"` is evaluated
- **THEN** the gate SHALL create a change detector via `factory.create_change_detector(vm_config.change_detection_mode)`
- **AND** SHALL call `detector.has_changed(vm_config, disk.target)` for each configured disk
- **AND** SHALL compare `cr.current_allocation` against `state.get_last_backup_allocation(target_path, disk.target)`
- **AND** SHALL open the gate when any disk reports changed

#### Scenario: Per-disk baseline read and write
- **WHEN** the gate is evaluated
- **THEN** the system SHALL read `get_last_backup_allocation(target_path, disk)` for each disk from `_target_state.json`
- **AND** SHALL call `set_last_backup_allocation(target_path, disk, current_allocation)` after a successful backup for each disk

### Requirement: Onchange gate and retention separation
When the onchange gate skips transfer, the system SHALL still execute backup retention evaluation and cleanup for the target. This ensures expired backups are deleted even when no disk has changed.

#### Scenario: Retention runs when gate skips transfer
- **WHEN** `backup_create = "onchange"` and the gate returns False (no disk changed) and expired backups exist on the target
- **THEN** the system SHALL skip the transfer section but SHALL still run `_evaluate_backup_retention()` and `_cleanup_backups()`

#### Scenario: Transfer skipped but retention cleans expired backups
- **WHEN** the gate skips transfer and retention evaluation marks backups for removal
- **THEN** the system SHALL delete the expired backups via `_cleanup_backups()`
