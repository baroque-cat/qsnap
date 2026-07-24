## ADDED Requirements

### Requirement: IStateManager per-target backup allocation tracking

`IStateManager` SHALL provide `get_last_backup_allocation(target_path: str) -> int | None` and `set_last_backup_allocation(target_path: str, alloc: int) -> None` methods. These methods persist a per-target baseline allocation size used by the `backup_create="onchange"` gate in `Core._backup_target()`. When no prior baseline exists for a target, `get_last_backup_allocation()` SHALL return `None` (first-run behavior — backup always proceeds).

#### Scenario: Write and read per-target backup allocation

- **WHEN** `set_last_backup_allocation("/mnt/backup/vm1", 1048576)` is called, then `get_last_backup_allocation("/mnt/backup/vm1")`
- **THEN** the returned value is `1048576`

#### Scenario: Missing target state returns None

- **WHEN** `get_last_backup_allocation("/mnt/backup/newtarget")` is called for a target with no prior state
- **THEN** the method returns `None`

#### Scenario: Per-target state is independent

- **WHEN** `set_last_backup_allocation("/mnt/backup/targetA", 1000)` is called
- **AND** `set_last_backup_allocation("/mnt/backup/targetB", 2000)` is called
- **THEN** `get_last_backup_allocation("/mnt/backup/targetA")` returns `1000`
- **AND** `get_last_backup_allocation("/mnt/backup/targetB")` returns `2000`

### Requirement: JsonStateManager _target_state.json persistence

`JsonStateManager` SHALL persist per-target backup allocation state in a dedicated `_target_state.json` file under the state directory. The file SHALL use a JSON object keyed by `target_path` string, with each value being an object containing `last_backup_allocation` (integer). The file SHALL use atomic writes (`.tmp` + `os.replace`). If the file does not exist, all `get_last_backup_allocation()` calls SHALL return `None`. If the file is corrupted, the manager SHALL rename it to `_target_state.json.broken.{timestamp}`, log a CRITICAL message, and start fresh (all targets return `None`).

#### Scenario: _target_state.json written atomically

- **WHEN** `set_last_backup_allocation("/mnt/backup/vm1", 1048576)` is called
- **THEN** a temporary file `_target_state.json.tmp` is written
- **AND** then renamed to `_target_state.json` via `os.replace`

#### Scenario: Missing _target_state.json returns None

- **WHEN** `_target_state.json` does not exist in the state directory
- **AND** `get_last_backup_allocation("/mnt/backup/vm1")` is called
- **THEN** the method returns `None`

#### Scenario: Corrupted _target_state.json is renamed

- **WHEN** `_target_state.json` contains invalid JSON
- **AND** `get_last_backup_allocation("/mnt/backup/vm1")` is called
- **THEN** the file is renamed to `_target_state.json.broken.{timestamp}`
- **AND** a CRITICAL log message is emitted
- **AND** the method returns `None`
