## MODIFIED Requirements

### Requirement: MapChangeDetector implements IChangeDetector

The system SHALL provide a `MapChangeDetector` class implementing `IChangeDetector` in `qsnap/modules/change/map_detector.py`. It SHALL use `qemu-img map --force-share --output=json` to obtain the set of allocated disk regions and compare against the prior recorded state. The `--force-share` flag is REQUIRED because the active disk is locked by the running VM. Without `--force-share`, `qemu-img map` fails with a lock error. Two map calls SHALL be considered "changed" if the set of `(offset, length)` allocated regions differs.

#### Scenario: Allocation map unchanged — no changes
- **WHEN** `qemu-img map --force-share --output=json` returns identical allocated regions as the last recorded state
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

#### Scenario: Map on running VM uses --force-share
- **WHEN** `MapChangeDetector.has_changed()` is called for a running VM
- **THEN** the `qemu-img map` command includes `--force-share`
- **AND** the command succeeds despite the VM holding a write lock on the active disk
