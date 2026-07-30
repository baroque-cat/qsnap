# Change Detection

## Purpose

Detects whether a VM disk has changed by comparing the current allocation-size (`qemu-img info`) against the last recorded value (`IStateManager`).
Implements the `onchange` snapshot creation mode — only create a snapshot when the disk has actually grown.

## Requirements

### Requirement: Change detection via allocation-size comparison

The system SHALL determine whether a VM disk has changed by comparing the current allocation-size of the active image with the last recorded value from `IStateManager`. The current allocation-size SHALL be determined via `qemu-img info --output=json --force-share` on the active image, whose path is obtained via `virsh domblklist`.

#### Scenario: Allocation has grown — changes detected

- **WHEN** `IStateManager.get_last_allocation()` returns 65536
- **AND** `qemu-img info` on the active image returns `actual-size: 131072`
- **THEN** the module returns `ChangeResult(changed=True, last_allocation=65536, current_allocation=131072)`

#### Scenario: Allocation unchanged — no changes

- **WHEN** `IStateManager.get_last_allocation()` returns 65536
- **AND** `qemu-img info` on the active image returns `actual-size: 65536`
- **THEN** the module returns `ChangeResult(changed=False, last_allocation=65536, current_allocation=65536)`

#### Scenario: First run — no previous state

- **WHEN** `IStateManager.get_last_allocation()` returns `None`
- **THEN** the module returns `ChangeResult(changed=True, last_allocation=0, current_allocation=0)`
- **AND** this guarantees the first snapshot is created

#### Scenario: virsh or qemu-img command fails

- **WHEN** `virsh domblklist` or `qemu-img info` returns an error
- **THEN** the module returns `ChangeResult(changed=True)` (fail-safe: rather create an unnecessary snapshot than miss changes)

### Requirement: Per-disk change detection
`IChangeDetector.has_changed()` SHALL accept an optional `disk: str` parameter. When provided, change detection SHALL be scoped to that specific disk. When omitted, change detection SHALL apply to the first discovered disk (backward-compatible).

#### Scenario: Per-disk change detection for vdb
- **WHEN** `detector.has_changed(vm_config, disk="vdb")` is called
- **THEN** allocation comparison uses the `vdb` disk path from `virsh domblklist`

#### Scenario: Backward-compatible no-disk call
- **WHEN** `detector.has_changed(vm_config)` is called without `disk`
- **THEN** the first disk from `virsh domblklist` is used (existing behaviour)

### Requirement: MapChangeDetector implements IChangeDetector
The system SHALL provide a `MapChangeDetector` class implementing `IChangeDetector` in `qsnap/modules/change/map_detector.py`. It SHALL use `qemu-img map --output=json` for allocated-region comparison. `DefaultFactory.create_change_detector("allocation-map")` SHALL return `MapChangeDetector`.

#### Scenario: Allocation map differs — changes detected
- **WHEN** `qemu-img map --output=json` returns different allocated regions than the last recorded state
- **THEN** `ChangeResult(changed=True)` is returned

#### Scenario: Map command fails — fail-safe
- **WHEN** `qemu-img map` returns non-zero exit code
- **THEN** `ChangeResult(changed=True)` is returned

### Requirement: Source-disk-based backup onchange gate

The `backup_create="onchange"` gate SHALL determine whether backup transfer should proceed by querying the source VM's active disk directly via `IChangeDetector`. The gate SHALL be independent of snapshot creation. The gate SHALL use `IStateManager.get_last_backup_allocation(target_path)` as the per-target baseline. After a successful backup, Core SHALL call `IStateManager.set_last_backup_allocation(target_path, current_allocation)` to update the baseline. See `specs/independent-target-onchange/spec.md` for full requirements.

#### Scenario: Gate uses change detector, not snapshot names

- **WHEN** `backup_create="onchange"` is evaluated
- **THEN** the gate SHALL create a change detector via `factory.create_change_detector(vm_config.change_detection_mode)`
- **AND** SHALL call `detector.has_changed(vm_config)` to obtain `ChangeResult.current_allocation`
- **AND** SHALL compare `current_allocation` against `state.get_last_backup_allocation(target_path)`
- **AND** SHALL NOT call `provider.list(target)` for the gate decision

#### Scenario: Gate reads and writes last_backup_allocation

- **WHEN** the gate is evaluated
- **THEN** the system SHALL read `last_backup_allocation` from `_target_state.json`
- **AND** SHALL call `set_last_backup_allocation` after a successful backup

### Requirement: Onchange gate and retention separation

When the onchange gate skips transfer, the system SHALL still execute backup retention evaluation and cleanup for the target. This ensures expired backups are deleted even when the source disk has not changed.

#### Scenario: Retention runs when gate skips transfer

- **WHEN** `backup_create = "onchange"` and the gate returns False (disk unchanged) and expired backups exist on the target
- **THEN** the system SHALL skip the transfer section but SHALL still run `_evaluate_backup_retention()` and `_cleanup_backups()`

#### Scenario: Transfer skipped but retention cleans expired backups

- **WHEN** the gate skips transfer and retention evaluation marks backups for removal
- **THEN** the system SHALL delete the expired backups via `_cleanup_backups()` (including cascade deletion and per-chain retention logic)
