## ADDED Requirements

### Requirement: MapChangeDetector implements IChangeDetector
The system SHALL provide a `MapChangeDetector` class implementing `IChangeDetector` in `qsnap/modules/change/map_detector.py`. It SHALL use `qemu-img map --output=json` for allocated-region comparison. `DefaultFactory.create_change_detector("allocation-map")` SHALL return `MapChangeDetector`.

#### Scenario: Allocation map differs — changes detected
- **WHEN** `qemu-img map --output=json` returns different allocated regions than the last recorded state
- **THEN** `ChangeResult(changed=True)` is returned

#### Scenario: Map command fails — fail-safe
- **WHEN** `qemu-img map` returns non-zero exit code
- **THEN** `ChangeResult(changed=True)` is returned

## MODIFIED Requirements

None. All existing `AllocationSizeDetector` requirements are unchanged. `MapChangeDetector` is an additional implementation selectable via factory mode.
