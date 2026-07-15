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
- **AND** `SnapshotInfo.content_hash` is preserved across write and read when non-None

#### Scenario: Hash persists across runs
- **WHEN** `record_snapshot("vm", SnapshotInfo(content_hash="abc123"))` is called and then `get_snapshots("vm")` is called
- **THEN** the returned `SnapshotInfo.content_hash` is `"abc123"`

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

`IStateManager` SHALL provide `get_last_full_backup(target_path: str) -> FullBackupInfo | None` and `set_last_full_backup(target_path: str, name: str, timestamp: datetime) -> None` methods. `JsonStateManager` SHALL persist this under the `"target_full_backups"` key in a per-VM JSON file.

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
