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
