## Requirements

### Requirement: IStateManager ABC
The system SHALL provide an `IStateManager` ABC with methods for reading and writing cross-run state per VM.

#### Scenario: IStateManager is an ABC
- **WHEN** attempting to instantiate IStateManager directly
- **THEN** TypeError is raised (cannot instantiate abstract class)

### Requirement: JsonStateManager implements IStateManager
The system SHALL provide a `JsonStateManager` that persists per-VM state as JSON files under a configurable directory (default `/var/lib/qsnap/state/`).

#### Scenario: Write and read allocation size
- **WHEN** `set_last_allocation("myvm", 1048576)` is called, then `get_last_allocation("myvm")`
- **THEN** the returned value is 1048576

#### Scenario: Missing state file returns None
- **WHEN** `get_last_allocation("newvm")` is called for a VM with no state file
- **THEN** the method returns None

#### Scenario: Record and list snapshots
- **WHEN** `record_snapshot("myvm", SnapshotInfo(...))` is called for two snapshots
- **THEN** `get_snapshots("myvm")` returns a list with both entries, sorted by creation time

### Requirement: Atomic file writes
JsonStateManager SHALL use atomic write pattern: write to a temporary file, then rename over the target, to prevent corruption on crash.

#### Scenario: Atomic write
- **WHEN** state is written for a VM
- **THEN** a temporary file is created, written, and renamed — no partial state file is ever visible
