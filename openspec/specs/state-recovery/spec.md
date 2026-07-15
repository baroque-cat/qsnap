## Purpose

Provides resilience for the JSON state manager by recovering from corrupted state files on load and rotating previous state file versions on save, ensuring cross-run state is never silently lost.

## Requirements

### Requirement: Corrupted state file recovery on load
`JsonStateManager._load()` SHALL catch `json.JSONDecodeError` when reading a VM state file. On corruption, the system SHALL rename the corrupt file to `{vm_name}.json.broken.{timestamp}`, log a CRITICAL message, and return an empty state dict (`last_allocation: None`, `snapshots: []`, `deferred_operations: []`).

#### Scenario: Corrupt state file renamed and empty state returned
- **WHEN** `_load("myvm")` reads a state file containing binary garbage
- **THEN** the file is renamed to `myvm.json.broken.20250715T120000`
- **AND** a CRITICAL log message is emitted with the renamed file path
- **AND** `get_last_allocation("myvm")` returns `None`
- **AND** `get_snapshots("myvm")` returns `[]`
- **AND** `get_deferred_operations("myvm")` returns `[]`

#### Scenario: Clean state file loads normally
- **WHEN** `_load("myvm")` reads a valid JSON state file
- **THEN** no rename occurs, no CRITICAL log is emitted
- **AND** state is loaded as before

#### Scenario: Missing state file returns None gracefully
- **WHEN** `_load("newvm")` is called and no state file exists
- **THEN** `None` is returned silently — no error, no log

### Requirement: State file rotation on save
`JsonStateManager._save()` SHALL rotate previous state file versions before writing the new one. Rotation SHALL keep up to `state_backup_count` previous versions (default 2): `{vm_name}.json` → `{vm_name}.json.1` → `{vm_name}.json.2`. Version `.2` (the oldest) SHALL be discarded when exceeded. Rotation SHALL use `shutil.move` (atomic rename) for each shift.

#### Scenario: First save creates state file only
- **WHEN** a VM has no previous state file
- **THEN** `_save()` creates `vm.json` at the state path
- **AND** no backup files are created

#### Scenario: Subsequent saves rotate state files
- **WHEN** `_save()` is called and `vm.json` already exists
- **THEN** `vm.json` is moved to `vm.json.1`
- **AND** the new state is written to `vm.json`
- **AND** if `vm.json.1` already existed, it is moved to `vm.json.2`

#### Scenario: Backup count limit enforced
- **WHEN** `state_backup_count = 1` and `vm.json.1` already exists
- **THEN** `vm.json.1` is overwritten (not shifted to `.2`)
- **AND** no `vm.json.2` is created

#### Scenario: state_backup_count = 0 disables rotation
- **WHEN** `state_backup_count = 0`
- **THEN** `_save()` writes `vm.json` directly without any rotation
- **AND** existing `.1`/`.2` backup files are not removed

### Requirement: GlobalConfig state_backup_count field
`GlobalConfig` SHALL include a `state_backup_count: int` field with default value `2`.

#### Scenario: Default state_backup_count
- **WHEN** `GlobalConfig` is constructed without `state_backup_count`
- **THEN** `state_backup_count` is `2`
