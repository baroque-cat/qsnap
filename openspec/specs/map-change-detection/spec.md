# Map Change Detection

## Purpose

Change detection via `qemu-img map` — compares allocated-region hashes to detect disk changes missed by allocation-size comparison, including zero-fill, fstrim, and region redistribution.

## Requirements

### Requirement: MapChangeDetector implements IChangeDetector
The system SHALL provide a `MapChangeDetector` class implementing `IChangeDetector` in `qsnap/modules/change/map_detector.py`. It SHALL use `qemu-img map --force-share --output=json` to obtain the set of allocated disk regions and compare a SHA-256 hash of sorted `(offset, length)` tuples against the per-disk prior recorded state from `IStateManager.get_last_allocation(vm_name, disk)`. The `--force-share` flag is REQUIRED because the active disk is locked by the running VM.

#### Scenario: Allocation map unchanged — no changes
- **WHEN** the current `qemu-img map` hash equals the per-disk last recorded value from `IStateManager.get_last_allocation(vm_name, disk)`
- **THEN** `ChangeResult(changed=False, disk=disk)` is returned

#### Scenario: Allocation map changed — new region added
- **WHEN** the current map hash differs from the last recorded per-disk value
- **THEN** `ChangeResult(changed=True, disk=disk)` is returned

#### Scenario: Zero-fill changes allocation map without total size change
- **WHEN** `actual-size` is unchanged but the allocation map shows regions shifted (zero-fill + new write)
- **THEN** `ChangeResult(changed=True)` is returned (catches changes missed by `AllocationSizeDetector`)

#### Scenario: qemu-img map command fails
- **WHEN** `qemu-img map` returns non-zero exit code
- **THEN** the module returns `ChangeResult(changed=True)` (fail-safe)

#### Scenario: Map on running VM uses --force-share
- **WHEN** `MapChangeDetector.has_changed(vm_config, "vda")` is called for a running VM
- **THEN** the `qemu-img map` command includes `--force-share`
- **AND** the command succeeds despite the VM holding a write lock on the active disk

#### Scenario: MapChangeDetector requires disk parameter
- **WHEN** `MapChangeDetector.has_changed(vm_config, "vdb")` is called
- **THEN** allocation comparison uses the `vdb` disk path resolved from `virsh domblklist`
- **AND** returns `ChangeResult` with `disk="vdb"`

### Requirement: Factory selects MapChangeDetector for allocation-map mode
`DefaultFactory.create_change_detector(mode)` SHALL return `MapChangeDetector` when `mode == "allocation-map"`. All other modes return `AllocationSizeDetector`.

#### Scenario: Map mode selected
- **WHEN** `factory.create_change_detector("allocation-map")` is called
- **THEN** a `MapChangeDetector` instance is returned

#### Scenario: Unrecognized mode falls back to allocation-size
- **WHEN** `factory.create_change_detector("unknown")` is called
- **THEN** an `AllocationSizeDetector` instance is returned
