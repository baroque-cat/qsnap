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
- **THEN** the module returns `ChangeResult(has_changed=True, last_allocation=65536, current_allocation=131072)`

#### Scenario: Allocation unchanged — no changes

- **WHEN** `IStateManager.get_last_allocation()` returns 65536
- **AND** `qemu-img info` on the active image returns `actual-size: 65536`
- **THEN** the module returns `ChangeResult(has_changed=False, last_allocation=65536, current_allocation=65536)`

#### Scenario: First run — no previous state

- **WHEN** `IStateManager.get_last_allocation()` returns `None`
- **THEN** the module returns `ChangeResult(has_changed=True, last_allocation=0, current_allocation=0)`
- **AND** this guarantees the first snapshot is created

#### Scenario: virsh or qemu-img command fails

- **WHEN** `virsh domblklist` or `qemu-img info` returns an error
- **THEN** the module returns `ChangeResult(has_changed=True)` (fail-safe: rather create an unnecessary snapshot than miss changes)
