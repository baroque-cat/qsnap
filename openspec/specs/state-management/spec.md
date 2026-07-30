## Requirements

### Requirement: IStateManager ABC
The system SHALL provide an `IStateManager` ABC with methods for reading and writing cross-run state per VM.

#### Scenario: IStateManager is an ABC
- **WHEN** attempting to instantiate IStateManager directly
- **THEN** TypeError is raised (cannot instantiate abstract class)

### Requirement: JsonStateManager implements IStateManager
The system SHALL provide a `JsonStateManager` that persists per-VM state as JSON files under a configurable directory (default `/var/lib/qsnap/state/`). The manager SHALL recover from corrupted state files by renaming them and starting fresh.

#### Scenario: Write and read allocation size
- **WHEN** `set_last_allocation("myvm", 1048576)` is called, then `get_last_allocation("myvm")`
- **THEN** the returned value is 1048576

#### Scenario: Missing state file returns None
- **WHEN** `get_last_allocation("newvm")` is called for a VM with no state file
- **THEN** the method returns None

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
`IStateManager` SHALL provide `get_deferred_operations(vm_name: str) -> list[DeferredBlockcommit]`, `add_deferred_blockcommit(vm_name: str, snapshots: list[str], reason: str)`, and `clear_deferred_operations(vm_name: str)`. `DeferredBlockcommit` SHALL be a frozen dataclass with fields `snapshots: list[str]`, `reason: str` (`"apparmor"` | `"selinux"`), `since: datetime`.

#### Scenario: Add and retrieve deferred operations
- **WHEN** `add_deferred_blockcommit("vm1", ["snap1.qcow2"], "apparmor")` is called
- **THEN** `get_deferred_operations("vm1")` returns one `DeferredBlockcommit`

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

### Requirement: IStateManager tracks last full backup per target

`IStateManager` SHALL provide `get_last_full_backup(target_path: str) -> FullBackupInfo | None` and `set_last_full_backup(target_path: str, name: str, timestamp: datetime) -> None` methods. `JsonStateManager` SHALL persist this under the `"target_full_backups"` key in a per-VM JSON file. The `set_last_full_backup` method SHALL NOT hardcode a `bucket_level` value.

#### Scenario: Full backup state saved and retrieved
- **WHEN** `set_last_full_backup("/mnt/backup/vm", "FULL.20250714", ts)` is called then `get_last_full_backup("/mnt/backup/vm")` is called
- **THEN** the returned `FullBackupInfo.name` is `"FULL.20250714"` and `timestamp` equals `ts`

#### Scenario: No full backup returns None
- **WHEN** `get_last_full_backup("/mnt/backup/nonexistent")` is called with no prior `set_last_full_backup`
- **THEN** the function returns `None`

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

### Requirement: IStateManager per-target backup allocation tracking

`IStateManager` SHALL provide `get_last_backup_allocation(target_path: str) -> int | None` and `set_last_backup_allocation(target_path: str, alloc: int) -> None` methods. These methods persist a per-target baseline used by the `backup_create="onchange"` gate in `Core._backup_target()`. The baseline stores the source disk's current allocation value (either `actual-size` from `qemu-img info` when `change_detection_mode="allocation-size"`, or the allocation-map hash from `qemu-img map` when `change_detection_mode="allocation-map"`). When no prior baseline exists for a target, `get_last_backup_allocation()` SHALL return `None` (first-run behavior — backup always proceeds). After a successful backup, Core SHALL call `set_last_backup_allocation(target_path, current_allocation)` to update the baseline. If the backup fails, the baseline SHALL NOT be updated.

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

#### Scenario: Baseline updated after successful backup
- **WHEN** a backup transfer to a target with `backup_create="onchange"` succeeds
- **THEN** Core SHALL call `set_last_backup_allocation(target_path, current_allocation)`
- **AND** the next run's gate SHALL compare against this new baseline

#### Scenario: Baseline not updated after failed backup
- **WHEN** a backup transfer to a target with `backup_create="onchange"` fails
- **THEN** Core SHALL NOT call `set_last_backup_allocation`
- **AND** the next run's gate SHALL compare against the old baseline

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

### Requirement: Clear last backup allocation

The `IStateManager` ABC SHALL provide a `clear_last_backup_allocation(target_path: str) -> bool` method that removes the `last_backup_allocation` baseline for a target. Returns True if an entry was found and removed, False otherwise.

#### Scenario: Clear existing baseline

- **WHEN** `clear_last_backup_allocation(target_path)` is called and an entry exists for `target_path` in `_target_state.json`
- **THEN** the entry SHALL be removed from the JSON file and True SHALL be returned

#### Scenario: Clear non-existent baseline

- **WHEN** `clear_last_backup_allocation(target_path)` is called and no entry exists for `target_path`
- **THEN** no file SHALL be modified and False SHALL be returned

### Requirement: Remove all incremental dependencies

The `IStateManager` ABC SHALL provide a `remove_all_incremental_dependencies(target_path: str, full_name: str) -> int` method that removes ALL incremental dependency records linked to a given FULL backup. Returns the count of removed entries.

#### Scenario: Remove all dependencies for existing FULL

- **WHEN** `remove_all_incremental_dependencies(target_path, full_name)` is called and dependencies exist for `full_name` in `_dependencies.json`
- **THEN** all dependency entries under `full_name` SHALL be removed and the count of removed entries SHALL be returned

#### Scenario: Remove dependencies for non-existent FULL

- **WHEN** `remove_all_incremental_dependencies(target_path, full_name)` is called and no dependencies exist for `full_name`
- **THEN** no file SHALL be modified and 0 SHALL be returned

### Requirement: IStateManager implementations must implement new methods

All concrete implementations of `IStateManager` (JsonStateManager, InMemoryStateManager) SHALL implement `clear_last_backup_allocation` and `remove_all_incremental_dependencies`. Contract tests SHALL verify these methods exist and return correct types.

#### Scenario: JsonStateManager implements clear_last_backup_allocation

- **WHEN** `JsonStateManager.clear_last_backup_allocation(target_path)` is called
- **THEN** the method SHALL remove the entry from `_target_state.json` atomically (write to `.tmp`, then `os.replace`)

#### Scenario: InMemoryStateManager implements clear_last_backup_allocation

- **WHEN** `InMemoryStateManager.clear_last_backup_allocation(target_path)` is called
- **THEN** the method SHALL remove the key from the in-memory dict and return True if it existed

### Requirement: Legacy dependency key migration on load

`JsonStateManager._load_dependencies()` SHALL migrate `_dependencies.json` keys from the extended form (with `.qcow2` extension) to the stem form (without `.qcow2`) on load. For each target path's dependency dict, any key ending in `.qcow2` SHALL be renamed to its stem form, preserving the value list. Migration SHALL be idempotent — loading an already-migrated file produces no changes.

#### Scenario: Legacy .qcow2 keys migrated to stem on load
- **WHEN** `_dependencies.json` contains `{"target": {"vm.FULL.20260727T000000_a1b2c3.qcow2": ["incr-001"]}}`
- **THEN** on load, the key is migrated to `"vm.FULL.20260727T000000_a1b2c3"` (stem form)
- **AND** `get_incremental_dependencies("target", "vm.FULL.20260727T000000_a1b2c3")` returns `["incr-001"]`

#### Scenario: Already-migrated file loaded unchanged
- **WHEN** `_dependencies.json` contains `{"target": {"vm.FULL.20260727T000000_a1b2c3": ["incr-001"]}}` (stem keys)
- **THEN** on load, no migration occurs and the data is returned as-is

#### Scenario: Mixed keys migrated correctly
- **WHEN** `_dependencies.json` contains both `"vm.FULL.20260727T000000_a1b2c3.qcow2"` and `"vm.FULL.20260715T000000_a1b2c3"` keys
- **THEN** on load, the `.qcow2` key is migrated to stem form
- **AND** the already-stem key is left unchanged

### Requirement: FullBackupInfo without bucket_level

`FullBackupInfo` SHALL NOT have a `bucket_level` field. The dataclass SHALL have exactly: `name: str`, `path: Path`, `timestamp: datetime`. Old JSON entries containing `bucket_level` SHALL be read-tolerantly — the field is silently ignored on load.

#### Scenario: FullBackupInfo constructed without bucket_level
- **WHEN** a `FullBackupInfo` is created with `name="vm.FULL.20260701T000000_a1b2c3"`, `path=Path(...)`, `timestamp=ts`
- **THEN** the instance has exactly three fields: `name`, `path`, `timestamp`
- **AND** accessing `.bucket_level` raises `AttributeError`

#### Scenario: Old JSON with bucket_level loaded without error
- **WHEN** `_full_backups.json` contains `{"bucket_level": "monthly"}`
- **THEN** the entry is loaded into `FullBackupInfo` without error
- **AND** the `bucket_level` value is silently ignored

### Requirement: record_full_backup without bucket_level parameter

`IStateManager.record_full_backup(target_path, name, timestamp)` SHALL NOT accept a `bucket_level` parameter. `JsonStateManager` SHALL NOT write `bucket_level` to JSON.

#### Scenario: record_full_backup called without bucket_level
- **WHEN** `record_full_backup("/mnt/backup/vm", "vm.FULL.20260701T000000_a1b2c3", ts)` is called
- **THEN** the FULL is recorded in state with `name`, `path`, `timestamp` only
- **AND** no `bucket_level` key is written to JSON

### Requirement: IStateManager reset_vm_state method
`IStateManager` SHALL provide a `reset_vm_state(vm_name: str) -> None` method that atomically clears all per-VM state: snapshots list, `last_allocation` baseline, and `deferred_operations` queue. This method is used by `Core.restore()` to reset VM state after disk replacement.

#### Scenario: reset_vm_state clears all per-VM state
- **WHEN** `reset_vm_state("myvm")` is called and the VM has 5 snapshots, allocation baseline, and 2 deferred operations
- **THEN** `get_snapshots("myvm")` returns an empty list
- **AND** `get_last_allocation("myvm")` returns None
- **AND** `get_deferred_operations("myvm")` returns an empty list

#### Scenario: reset_vm_state is atomic
- **WHEN** `reset_vm_state("myvm")` is called
- **THEN** the state file is written atomically (`.tmp` + `os.replace`)
- **AND** no partial state is ever visible on crash

#### Scenario: reset_vm_state for nonexistent VM
- **WHEN** `reset_vm_state("nonexistent")` is called
- **THEN** no error is raised
- **AND** no state file is created

#### Scenario: JsonStateManager implements reset_vm_state
- **WHEN** `JsonStateManager.reset_vm_state("myvm")` is called
- **THEN** the VM's JSON file is loaded, `snapshots`, `last_allocation`, `deferred_operations` keys are cleared, and the file is saved atomically

#### Scenario: InMemoryStateManager implements reset_vm_state
- **WHEN** `InMemoryStateManager.reset_vm_state("myvm")` is called
- **THEN** the in-memory dict for `myvm` is cleared of snapshots, allocation, and deferred operations

### Requirement: IStateManager reset_target_state method
`IStateManager` SHALL provide a `reset_target_state(target_path: str) -> None` method that atomically clears all per-target state: full backup records, incremental dependencies, and `last_backup_allocation` baseline. This method is used by `Core.restore()` to reset target state after VM disk replacement.

#### Scenario: reset_target_state clears all per-target state
- **WHEN** `reset_target_state("/mnt/backup/myvm")` is called and the target has 2 FULLs, 5 dependencies, and a backup allocation baseline
- **THEN** `get_full_backups("/mnt/backup/myvm")` returns an empty list
- **AND** `get_incremental_dependencies("/mnt/backup/myvm", any_full)` returns an empty list
- **AND** `get_last_backup_allocation("/mnt/backup/myvm")` returns None

#### Scenario: reset_target_state is atomic
- **WHEN** `reset_target_state("/mnt/backup/myvm")` is called
- **THEN** all three state files (`_full_backups.json`, `_dependencies.json`, `_target_state.json`) are updated atomically

#### Scenario: reset_target_state for nonexistent target
- **WHEN** `reset_target_state("/nonexistent")` is called
- **THEN** no error is raised
- **AND** no state files are modified

#### Scenario: JsonStateManager implements reset_target_state
- **WHEN** `JsonStateManager.reset_target_state("/mnt/backup/myvm")` is called
- **THEN** the target's entry is removed from `_full_backups.json`
- **AND** the target's entry is removed from `_dependencies.json`
- **AND** the target's entry is removed from `_target_state.json`
- **AND** all three files are saved atomically

#### Scenario: InMemoryStateManager implements reset_target_state
- **WHEN** `InMemoryStateManager.reset_target_state("/mnt/backup/myvm")` is called
- **THEN** the in-memory dicts for full backups, dependencies, and target state are cleared for the target path

### Requirement: IStateManager implementations must implement reset methods
All concrete implementations of `IStateManager` (JsonStateManager, InMemoryStateManager) SHALL implement `reset_vm_state` and `reset_target_state`. Contract tests SHALL verify these methods exist and return correct types.

#### Scenario: JsonStateManager implements reset_vm_state
- **WHEN** `JsonStateManager.reset_vm_state(vm_name)` is called
- **THEN** the method SHALL clear the VM's state atomically

#### Scenario: InMemoryStateManager implements reset_vm_state
- **WHEN** `InMemoryStateManager.reset_vm_state(vm_name)` is called
- **THEN** the method SHALL clear the in-memory state for the VM
