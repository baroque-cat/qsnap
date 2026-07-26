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

### Requirement: Onchange gate and retention separation

When the onchange gate skips transfer, the system SHALL still execute backup retention evaluation and cleanup for the target. This ensures expired backups are deleted even when no new snapshots exist.

#### Scenario: Retention runs when gate skips transfer

- **WHEN** `backup_create = "onchange"` and the gate returns False (no new snapshots) and expired backups exist on the target
- **THEN** the system SHALL skip the transfer section but SHALL still run `_evaluate_backup_retention()` and `_cleanup_backups()`

#### Scenario: Transfer skipped but retention cleans expired backups

- **WHEN** the gate skips transfer and retention evaluation marks backups for removal
- **THEN** the system SHALL delete the expired backups via `_cleanup_backups()` (including cascade deletion and ghost retention logic)
