# State Management

## Purpose

Persists cross-run per-VM and per-target state (allocation baselines, snapshots, backup records, deferred operations) via an ABC interface with atomic JSON-file persistence. All allocation baselines are per-disk.

## Requirements

### Requirement: IStateManager ABC
The system SHALL provide an `IStateManager` ABC with methods for reading and writing cross-run state per VM and per target, with disk-scoped allocation tracking.

#### Scenario: IStateManager is an ABC
- **WHEN** attempting to instantiate IStateManager directly
- **THEN** TypeError is raised (cannot instantiate abstract class)

### Requirement: JsonStateManager implements IStateManager
The system SHALL provide a `JsonStateManager` that persists per-VM state as JSON files under a configurable directory (default `/var/lib/qsnap/state/`). The manager SHALL recover from corrupted state files by renaming them and starting fresh.

#### Scenario: Write and read per-disk allocation size
- **WHEN** `set_last_allocation("myvm", "vda", 1048576)` is called, then `get_last_allocation("myvm", "vda")`
- **THEN** the returned value is 1048576

#### Scenario: Missing state file returns None
- **WHEN** `get_last_allocation("newvm", "vda")` is called for a VM with no state file
- **THEN** the method returns None

#### Scenario: Per-disk allocation baselines are independent
- **WHEN** `set_last_allocation("myvm", "vda", 1000)` and `set_last_allocation("myvm", "vdb", 2000)` are called
- **THEN** `get_last_allocation("myvm", "vda")` returns 1000
- **AND** `get_last_allocation("myvm", "vdb")` returns 2000

#### Scenario: Record and list snapshots
- **WHEN** `record_snapshot("myvm", SnapshotInfo(...))` is called for two snapshots
- **THEN** `get_snapshots("myvm")` returns a list with both entries, sorted by creation time

#### Scenario: content_hash silently ignored on deserialization
- **WHEN** an old state file containing `content_hash` is loaded
- **THEN** no error is raised
- **AND** the `content_hash` value is silently ignored
- **AND** all other fields are loaded normally

### Requirement: Atomic file writes
JsonStateManager SHALL use atomic write pattern: write to a temporary file, then rename over the target, to prevent corruption on crash.

#### Scenario: Atomic write
- **WHEN** state is written for a VM
- **THEN** a temporary file is created, written, and renamed — no partial state file is ever visible

### Requirement: IStateManager deferred operations methods
`IStateManager` SHALL provide `get_deferred_operations(vm_name: str) -> list[DeferredBlockcommit]`, `add_deferred_blockcommit(vm_name: str, disk: str, snapshots: list[str], reason: str)`, and `clear_deferred_operations(vm_name: str)`. `DeferredBlockcommit` SHALL be a frozen dataclass with fields `snapshots: list[str]`, `reason: str`, `since: datetime`, `disk: str`, and `last_warned_at: datetime | None`.

#### Scenario: Add and retrieve deferred operations
- **WHEN** `add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "apparmor")` is called
- **THEN** `get_deferred_operations("vm1")` returns one `DeferredBlockcommit` with `disk="vda"`

#### Scenario: Clear deferred operations
- **WHEN** `clear_deferred_operations("vm1")` is called
- **THEN** `get_deferred_operations("vm1")` returns an empty list

#### Scenario: Deferred operations persisted to JSON
- **WHEN** `JsonStateManager` writes deferred operations for a VM
- **THEN** they are stored in the VM's state JSON file under the `deferred_operations` key
- **THEN** they are loaded correctly on the next qsnap run

#### Scenario: No deferred operations — empty list
- **WHEN** `get_deferred_operations("vm_new")` is called for a VM with no deferred state
- **THEN** the method returns an empty list

### Requirement: IStateManager tracks full backups per target with disk field
`IStateManager` SHALL provide `get_last_full_backup(target_path: str) -> FullBackupInfo | None`, `get_full_backups(target_path: str) -> list[FullBackupInfo]`, `set_last_full_backup(target_path: str, name: str, timestamp: datetime, disk: str) -> None`, and `record_full_backup(target_path: str, name: str, timestamp: datetime, disk: str) -> None`. `FullBackupInfo` SHALL be a frozen dataclass with fields `name: str`, `path: Path`, `timestamp: datetime`, `disk: str`. `JsonStateManager` SHALL persist full backups in the dedicated `_full_backups.json` file under the state directory.

#### Scenario: Full backup state saved and retrieved with disk
- **WHEN** `set_last_full_backup("/mnt/backup/vm", "FULL.20250714", ts, "vda")` is called then `get_last_full_backup("/mnt/backup/vm")` is called
- **THEN** the returned `FullBackupInfo.name` is `"FULL.20250714"`, `timestamp` equals `ts`, and `disk` is `"vda"`

#### Scenario: No full backup returns None
- **WHEN** `get_last_full_backup("/mnt/backup/nonexistent")` is called with no prior `set_last_full_backup`
- **THEN** the function returns `None`

#### Scenario: get_full_backups returns all per-disk FULLs
- **WHEN** two FULL backups are recorded for the same target but different disks (`"vda"` and `"vdb"`)
- **THEN** `get_full_backups(target_path)` returns both entries, each with its respective `disk` value

### Requirement: Corrupted state file recovery
`JsonStateManager._load()` SHALL catch `json.JSONDecodeError` when reading a VM state file. On corruption, the system SHALL rename the corrupt file to `{vm_name}.json.broken.{timestamp}`, log a CRITICAL message, and return an empty state dict.

#### Scenario: Corrupt state file renamed and empty state returned
- **WHEN** `_load("myvm")` reads a state file containing binary garbage
- **THEN** the file is renamed to `myvm.json.broken.20250715T120000`
- **AND** a CRITICAL log message is emitted
- **AND** `get_last_allocation("myvm", "vda")` returns `None`

### Requirement: State file rotation
`JsonStateManager._save()` SHALL rotate previous state file versions before writing the new one. Rotation SHALL keep up to `state_backup_count` previous versions.

#### Scenario: State files rotated on subsequent saves
- **WHEN** `_save()` is called and `state_backup_count = 2`
- **THEN** `vm.json` → `vm.json.1` → `vm.json.2` rotation occurs

### Requirement: IStateManager per-target per-disk backup allocation tracking
`IStateManager` SHALL provide `get_last_backup_allocation(target_path: str, disk: str) -> int | None`, `set_last_backup_allocation(target_path: str, disk: str, alloc: int) -> None`, and `clear_last_backup_allocation(target_path: str, disk: str) -> bool`. These methods persist a per-target per-disk baseline used by the `backup_create="onchange"` gate in `Core._backup_target()`. When no prior baseline exists for a target and disk, `get_last_backup_allocation()` SHALL return `None`. After a successful backup, Core SHALL call `set_last_backup_allocation(target_path, disk, current_allocation)` per disk.

#### Scenario: Write and read per-target per-disk backup allocation
- **WHEN** `set_last_backup_allocation("/mnt/backup/vm1", "vda", 1048576)` is called, then `get_last_backup_allocation("/mnt/backup/vm1", "vda")`
- **THEN** the returned value is `1048576`

#### Scenario: Missing target state returns None
- **WHEN** `get_last_backup_allocation("/mnt/backup/newtarget", "vda")` is called for a target with no prior state
- **THEN** the method returns `None`

#### Scenario: Per-target per-disk state is independent
- **WHEN** `set_last_backup_allocation("/mnt/backup/targetA", "vda", 1000)` and `set_last_backup_allocation("/mnt/backup/targetB", "vda", 2000)` are called
- **THEN** `get_last_backup_allocation("/mnt/backup/targetA", "vda")` returns `1000`
- **AND** `get_last_backup_allocation("/mnt/backup/targetB", "vda")` returns `2000`

#### Scenario: Baseline updated after successful backup
- **WHEN** a backup transfer to a target with `backup_create="onchange"` succeeds
- **THEN** Core SHALL call `set_last_backup_allocation(target_path, disk, current_allocation)` for each disk
- **AND** the next run's gate SHALL compare against this new baseline

#### Scenario: Baseline not updated after failed backup
- **WHEN** a backup transfer to a target with `backup_create="onchange"` fails
- **THEN** Core SHALL NOT call `set_last_backup_allocation`
- **AND** the next run's gate SHALL compare against the old baseline

### Requirement: JsonStateManager _target_state.json persistence
`JsonStateManager` SHALL persist per-target per-disk backup allocation state in a dedicated `_target_state.json` file under the state directory. The file SHALL use a JSON object keyed by `target_path` string, with each value being an object containing `last_backup_allocation` (a dict keyed by `disk: int`). The file SHALL use atomic writes (`.tmp` + `os.replace`).

#### Scenario: _target_state.json written atomically
- **WHEN** `set_last_backup_allocation("/mnt/backup/vm1", "vda", 1048576)` is called
- **THEN** a temporary file `_target_state.json.tmp` is written
- **AND** then renamed to `_target_state.json` via `os.replace`

#### Scenario: Missing _target_state.json returns None
- **WHEN** `_target_state.json` does not exist in the state directory
- **AND** `get_last_backup_allocation("/mnt/backup/vm1", "vda")` is called
- **THEN** the method returns `None`

#### Scenario: Corrupted _target_state.json is renamed
- **WHEN** `_target_state.json` contains invalid JSON
- **AND** `get_last_backup_allocation("/mnt/backup/vm1", "vda")` is called
- **THEN** the file is renamed to `_target_state.json.broken.{timestamp}`
- **AND** a CRITICAL log message is emitted
- **AND** the method returns `None`

### Requirement: Clear last backup allocation per disk
The `IStateManager` ABC SHALL provide a `clear_last_backup_allocation(target_path: str, disk: str) -> bool` method that removes the `last_backup_allocation` baseline for a specific target and disk. Returns True if an entry was found and removed, False otherwise.

#### Scenario: Clear existing baseline
- **WHEN** `clear_last_backup_allocation(target_path, "vda")` is called and an entry exists for `target_path`/`vda` in `_target_state.json`
- **THEN** the per-disk entry SHALL be removed from the JSON file and True SHALL be returned

#### Scenario: Clear non-existent baseline
- **WHEN** `clear_last_backup_allocation(target_path, "vda")` is called and no entry exists for `target_path`/`vda`
- **THEN** no file SHALL be modified and False SHALL be returned

### Requirement: Remove all incremental dependencies
The `IStateManager` ABC SHALL provide a `remove_all_incremental_dependencies(target_path: str, full_name: str) -> int` method that removes ALL incremental dependency records linked to a given FULL backup. Returns the count of removed entries.

#### Scenario: Remove all dependencies for existing FULL
- **WHEN** `remove_all_incremental_dependencies(target_path, full_name)` is called and dependencies exist for `full_name` in `_dependencies.json`
- **THEN** all dependency entries under `full_name` SHALL be removed and the count of removed entries SHALL be returned

#### Scenario: Remove dependencies for non-existent FULL
- **WHEN** `remove_all_incremental_dependencies(target_path, full_name)` is called and no dependencies exist for `full_name`
- **THEN** no file SHALL be modified and 0 SHALL be returned

### Requirement: Record incremental dependency without disk parameter
`record_incremental_dependency(target_path: str, incremental_name: str, full_name: str)` SHALL NOT accept a `disk` parameter. The disk is encoded in the FULL backup name. `JsonStateManager` SHALL normalize the `full_name` to stem form (stripping `.qcow2` extension) before storage.

#### Scenario: Incremental dependency recorded
- **WHEN** `record_incremental_dependency("/mnt/backup/vm", "vm.20250713T120000_vda_a1b2c3", "vm.FULL.20250713_a1b2c3")` is called
- **THEN** the dependency is stored in `_dependencies.json` under the stem form of the full name
- **AND** `get_incremental_dependencies("/mnt/backup/vm", "vm.FULL.20250713_a1b2c3")` returns the incremental name

### Requirement: Legacy dependency key migration on load
`JsonStateManager._load_dependencies()` SHALL migrate `_dependencies.json` keys from the extended form (with `.qcow2` extension) to the stem form (without `.qcow2`) on load. Migration SHALL be idempotent.

#### Scenario: Legacy .qcow2 keys migrated to stem on load
- **WHEN** `_dependencies.json` contains `{"target": {"vm.FULL.20260727T000000_a1b2c3.qcow2": ["incr-001"]}}`
- **THEN** on load, the key is migrated to `"vm.FULL.20260727T000000_a1b2c3"` (stem form)
- **AND** `get_incremental_dependencies("target", "vm.FULL.20260727T000000_a1b2c3")` returns `["incr-001"]`

#### Scenario: Already-migrated file loaded unchanged
- **WHEN** `_dependencies.json` contains `{"target": {"vm.FULL.20260727T000000_a1b2c3": ["incr-001"]}}` (stem keys)
- **THEN** on load, no migration occurs and the data is returned as-is

### Requirement: IStateManager reset_vm_state method
`IStateManager` SHALL provide a `reset_vm_state(vm_name: str) -> None` method that atomically clears all per-VM state: snapshots list cleared to `[]`, `last_allocation` per-disk dict cleared to `{}`, and `deferred_operations` queue cleared to `[]`. This method is used by `Core.restore()` to reset VM state after disk replacement.

#### Scenario: reset_vm_state clears all per-VM state
- **WHEN** `reset_vm_state("myvm")` is called and the VM has 5 snapshots, per-disk allocation baselines, and 2 deferred operations
- **THEN** `get_snapshots("myvm")` returns an empty list
- **AND** `get_last_allocation("myvm", "vda")` returns None
- **AND** `get_last_allocation("myvm", "vdb")` returns None
- **AND** `get_deferred_operations("myvm")` returns an empty list

#### Scenario: reset_vm_state is atomic
- **WHEN** `reset_vm_state("myvm")` is called
- **THEN** the state file is written atomically (`.tmp` + `os.replace`)
- **AND** no partial state is ever visible on crash

#### Scenario: reset_vm_state for nonexistent VM
- **WHEN** `reset_vm_state("nonexistent")` is called
- **THEN** no error is raised
- **AND** no state file is created

### Requirement: IStateManager reset_target_state method
`IStateManager` SHALL provide a `reset_target_state(target_path: str) -> None` method that atomically clears all per-target state by removing the target's entry from `_full_backups.json`, `_dependencies.json`, and `_target_state.json`. This method is used by `Core.restore()` to reset target state after VM disk replacement.

#### Scenario: reset_target_state clears all per-target state
- **WHEN** `reset_target_state("/mnt/backup/myvm")` is called and the target has 2 FULLs, 5 dependencies, and per-disk backup allocation baselines
- **THEN** `get_full_backups("/mnt/backup/myvm")` returns an empty list
- **AND** `get_incremental_dependencies("/mnt/backup/myvm", any_full)` returns an empty list
- **AND** `get_last_backup_allocation("/mnt/backup/myvm", "vda")` returns None

#### Scenario: reset_target_state is atomic
- **WHEN** `reset_target_state("/mnt/backup/myvm")` is called
- **THEN** all three state files (`_full_backups.json`, `_dependencies.json`, `_target_state.json`) are updated atomically

#### Scenario: reset_target_state for nonexistent target
- **WHEN** `reset_target_state("/nonexistent")` is called
- **THEN** no error is raised
- **AND** no state files are modified
