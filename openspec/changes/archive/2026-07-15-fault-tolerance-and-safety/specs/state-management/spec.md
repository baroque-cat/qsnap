## MODIFIED Requirements

### Requirement: JsonStateManager implements IStateManager
The system SHALL provide a `JsonStateManager` that persists per-VM state as JSON files under a configurable directory (default `/var/lib/qsnap/state/`). The manager SHALL recover from corrupted state files by renaming them and starting fresh.

#### Scenario: Write and read allocation size
- **WHEN** `set_last_allocation("myvm", 1048576)` is called, then `get_last_allocation("myvm")`
- **THEN** the returned value is 1048576

#### Scenario: Missing state file returns None
- **WHEN** `get_last_allocation("newvm")` is called for a VM with no state file
- **THEN** the method returns None

## ADDED Requirements

### Requirement: Corrupted state file recovery
`JsonStateManager._load()` SHALL catch `json.JSONDecodeError` when reading a VM state file. On corruption, the system SHALL rename the corrupt file to `{vm_name}.json.broken.{timestamp}`, log a CRITICAL message, and return an empty state dict. See `specs/state-recovery/spec.md` for full semantics.

#### Scenario: Corrupt state file renamed and empty state returned
- **WHEN** `_load("myvm")` reads a state file containing binary garbage
- **THEN** the file is renamed to `myvm.json.broken.20250715T120000`
- **AND** a CRITICAL log message is emitted
- **AND** `get_last_allocation("myvm")` returns `None`

### Requirement: State file rotation
`JsonStateManager._save()` SHALL rotate previous state file versions before writing the new one. Rotation SHALL keep up to `state_backup_count` previous versions. See `specs/state-recovery/spec.md` for full semantics.

#### Scenario: State files rotated on subsequent saves
- **WHEN** `_save()` is called and `state_backup_count = 2`
- **THEN** `vm.json` → `vm.json.1` → `vm.json.2` rotation occurs
