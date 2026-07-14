## ADDED Requirements

### Requirement: MapChangeDetector implements IChangeDetector
The system SHALL provide a `MapChangeDetector` class implementing `IChangeDetector` in `qsnap/modules/change/map_detector.py`. It SHALL use `qemu-img map --output=json` to obtain the set of allocated disk regions and compare against the prior recorded state. Two map calls SHALL be considered "changed" if the set of `(offset, length)` allocated regions differs.

#### Scenario: Allocation map unchanged — no changes
- **WHEN** `qemu-img map --output=json` returns identical allocated regions as the last recorded state
- **THEN** `ChangeResult(changed=False)` is returned

#### Scenario: Allocation map changed — new region added
- **WHEN** the current map contains an additional allocated region vs. the last recorded state
- **THEN** `ChangeResult(changed=True)` is returned

#### Scenario: Zero-fill changes allocation map without total size change
- **WHEN** `actual-size` is unchanged but the allocation map shows regions shifted (zero-fill + new write)
- **THEN** `ChangeResult(changed=True)` is returned (catches changes missed by `AllocationSizeDetector`)

#### Scenario: qemu-img map command fails
- **WHEN** `qemu-img map` returns non-zero exit code
- **THEN** the module returns `ChangeResult(changed=True)` (fail-safe)

### Requirement: Factory selects MapChangeDetector for allocation-map mode
`DefaultFactory.create_change_detector(mode)` SHALL return `MapChangeDetector` when `mode == "allocation-map"`. All other modes return `AllocationSizeDetector` (backward-compatible).

#### Scenario: Map mode selected
- **WHEN** `factory.create_change_detector("allocation-map")` is called
- **THEN** a `MapChangeDetector` instance is returned

#### Scenario: Unrecognized mode falls back to allocation-size
- **WHEN** `factory.create_change_detector("unknown")` is called
- **THEN** an `AllocationSizeDetector` instance is returned (backward-compatible)
