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

Recorded FULL backup names SHALL carry the `.qcow2` extension. `JsonStateManager.record_full_backup()` SHALL normalize the `name` argument to the extended form (appending `.qcow2` when missing) before persisting, and SHALL derive the stored `path` as `str(Path(target_path) / normalized_name)`. This invariant guarantees that `FullBackupInfo.path` resolves to the physical backup file on the target, which all existence-based consumers (`_detect_phantom_fulls`, startup validation, the `_backup_target` phantom filter, target consistency check, `reconcile`) rely upon.

#### Scenario: Full backup state saved and retrieved with disk
- **WHEN** `set_last_full_backup("/mnt/backup/vm", "FULL.20250714_vda_a1b2c3.qcow2", ts, "vda")` is called then `get_last_full_backup("/mnt/backup/vm")` is called
- **THEN** the returned `FullBackupInfo.name` is `"FULL.20250714_vda_a1b2c3.qcow2"`, `timestamp` equals `ts`, and `disk` is `"vda"`

#### Scenario: No full backup returns None
- **WHEN** `get_last_full_backup("/mnt/backup/nonexistent")` is called with no prior `set_last_full_backup`
- **THEN** the function returns `None`

#### Scenario: get_full_backups returns all per-disk FULLs
- **WHEN** two FULL backups are recorded for the same target but different disks (`"vda"` and `"vdb"`)
- **THEN** `get_full_backups(target_path)` returns both entries, each with its respective `disk` value

#### Scenario: Recorded name carries the .qcow2 extension and path resolves to the file
- **WHEN** `record_full_backup("/mnt/backup/vm", "vm.FULL.20260701T000000_vda_a1b2c3.qcow2", ts, "vda")` is called
- **THEN** `get_full_backups("/mnt/backup/vm")` returns one entry whose `name` is `"vm.FULL.20260701T000000_vda_a1b2c3.qcow2"`
- **AND** whose `path` is `Path("/mnt/backup/vm") / "vm.FULL.20260701T000000_vda_a1b2c3.qcow2"`

#### Scenario: Stem name passed to record_full_backup is normalized defensively
- **WHEN** `record_full_backup("/mnt/backup/vm", "vm.FULL.20260701T000000_vda_a1b2c3", ts, "vda")` is called with a stem (no extension)
- **THEN** the persisted entry `name` is `"vm.FULL.20260701T000000_vda_a1b2c3.qcow2"`
- **AND** the persisted `path` is `Path("/mnt/backup/vm") / "vm.FULL.20260701T000000_vda_a1b2c3.qcow2"`

### Requirement: Corrupted state file recovery
`JsonStateManager._load()` SHALL catch `json.JSONDecodeError` when reading a VM state file. On corruption, the system SHALL rename the corrupt file to `{vm_name}.json.broken.{timestamp}`, log a CRITICAL message, and return an empty state dict.

#### Scenario: Corrupt state file renamed and empty state returned
- **WHEN** `_load("myvm")` reads a state file containing binary garbage
- **THEN** the file is renamed to `myvm.json.broken.20250715T120000`
- **AND** a CRITICAL log message is emitted
- **AND** `get_last_allocation("myvm", "vda")` returns `None`

### Requirement: Idempotent FULL name normalization on load
`JsonStateManager._load_full_backups()` SHALL normalize persisted `_full_backups.json` entries to the extended form on load: for each entry, `.qcow2` SHALL be appended to `name` when missing, and to the filename component of `path` when missing, with each field checked independently (an entry with an already-extended `path` but a stem `name`, or vice versa, SHALL be repaired field by field without double-appending). Normalization SHALL run BEFORE the existing deduplication pass, so that a stem entry and its extended twin collapse into a single record. Normalization SHALL be idempotent and the repaired data SHALL be persisted back, following the same write-back pattern as the existing dedup migration.

#### Scenario: Stem entry normalized on load
- **WHEN** `_full_backups.json` contains `{"target": [{"name": "vm.FULL.20260701T000000_vda_a1b2c3", "path": "/mnt/backup/vm/vm.FULL.20260701T000000_vda_a1b2c3", "timestamp": "...", "disk": "vda"}]}`
- **THEN** `get_full_backups("target")` returns one entry with `name` `"vm.FULL.20260701T000000_vda_a1b2c3.qcow2"` and `path` `Path("/mnt/backup/vm/vm.FULL.20260701T000000_vda_a1b2c3.qcow2")`

#### Scenario: Mixed stem and extended twins deduplicate to one record
- **WHEN** `_full_backups.json` contains both `{"name": "vm.FULL.20260701T000000_vda_a1b2c3", ...}` and `{"name": "vm.FULL.20260701T000000_vda_a1b2c3.qcow2", ...}` for the same target
- **THEN** after normalization (which runs before dedup) exactly one entry remains
- **AND** its `name` carries the `.qcow2` extension

#### Scenario: Already-extended entries load unchanged
- **WHEN** `_full_backups.json` contains only entries whose `name` and `path` already carry `.qcow2`
- **THEN** `get_full_backups()` returns them unchanged (no double-append, no rewrite of content)

#### Scenario: Per-field repair of asymmetric entries
- **WHEN** an entry has a stem `name` but an already-extended `path`
- **THEN** only the `name` field gains `.qcow2`
- **AND** the `path` is left untouched

### Requirement: remove_full_backup is name-format tolerant
`JsonStateManager.remove_full_backup(target_path: str, name: str) -> bool` SHALL normalize the lookup `name` to the extended form (appending `.qcow2` when missing) before matching against stored entries. Both stem callers (e.g. `Core._cleanup_backups`, which passes `BackupInfo.name` from `provider.list()` — always a stem) and extended callers (which pass state-derived `full.name`) SHALL remove the same record. The method SHALL return `True` when a record was removed and `False` otherwise.

#### Scenario: Stem lookup removes an extended record
- **WHEN** `_full_backups.json` holds an entry named `"vm.FULL.20260701T000000_vda_a1b2c3.qcow2"` and `remove_full_backup(target, "vm.FULL.20260701T000000_vda_a1b2c3")` is called (stem, as produced by `provider.list()`)
- **THEN** the entry is removed and `True` is returned
- **AND** `get_full_backups(target)` returns an empty list

#### Scenario: Extended lookup removes the same record
- **WHEN** `remove_full_backup(target, "vm.FULL.20260701T000000_vda_a1b2c3.qcow2")` is called for the same stored entry
- **THEN** the entry is removed and `True` is returned

#### Scenario: Non-matching name leaves state untouched
- **WHEN** `remove_full_backup(target, "nonexistent.qcow2")` is called
- **THEN** no entry is removed and `False` is returned

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
`IStateManager` SHALL provide a `reset_vm_state(vm_name: str) -> None` method that atomically clears all per-VM state: snapshots list cleared to `[]`, `last_allocation` per-disk dict cleared to `{}`, and `deferred_operations` queue cleared to `[]`. This method clears state for ALL disks of the VM. `Core.restore()` SHALL NOT call this method — restore uses the per-disk `reset_vm_disk_state()` instead, so that disks not being restored keep their state.

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
`IStateManager` SHALL provide a `reset_target_state(target_path: str) -> None` method that atomically clears all per-target state by removing the target's entry from `_full_backups.json`, `_dependencies.json`, and `_target_state.json`. This method clears records of ALL VMs and ALL disks sharing the target. `Core.restore()` SHALL NOT call this method — restore uses the per-disk `reset_target_disk_state()` instead, so that other disks and other VMs sharing the target keep their records.

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

### Requirement: IStateManager reset_vm_disk_state method
`IStateManager` SHALL provide a `reset_vm_disk_state(vm_name: str, disk: str) -> None` method that atomically clears ONLY the given disk's per-VM state: all snapshot records with `SnapshotInfo.disk == disk` are removed (other disks' snapshots remain), the `disk` key is removed from the `last_allocation` dict (a legacy bare-integer `last_allocation` value SHALL be treated as absent, so `get_last_allocation` returns `None` afterwards), and all deferred operations with `DeferredBlockcommit.disk == disk` are removed. State of all other disks of the VM SHALL NOT be modified. The write SHALL be atomic (`.tmp` + `os.replace`). This method is used by `Core.restore()` after replacing one disk's base image.

#### Scenario: reset_vm_disk_state clears only the given disk
- **WHEN** `reset_vm_disk_state("myvm", "vda")` is called and the VM has snapshots, allocation baselines, and deferred operations for both `vda` and `vdb`
- **THEN** `get_snapshots("myvm")` returns only the `vdb` snapshots
- **AND** `get_last_allocation("myvm", "vda")` returns None
- **AND** `get_last_allocation("myvm", "vdb")` still returns its prior value
- **AND** `get_deferred_operations("myvm")` returns only the `vdb` deferred operations

#### Scenario: reset_vm_disk_state handles legacy bare-integer allocation
- **WHEN** the VM state file contains a legacy bare-integer `last_allocation` and `reset_vm_disk_state("myvm", "vda")` is called
- **THEN** no error is raised
- **AND** `get_last_allocation("myvm", "vda")` returns None afterwards

#### Scenario: reset_vm_disk_state for unknown VM or disk
- **WHEN** `reset_vm_disk_state("nonexistent", "vda")` or `reset_vm_disk_state("myvm", "vdz")` is called with no matching state
- **THEN** no error is raised
- **AND** no state file is created

#### Scenario: reset_vm_disk_state is atomic
- **WHEN** `reset_vm_disk_state("myvm", "vda")` is called
- **THEN** the state file is written atomically (`.tmp` + `os.replace`)

### Requirement: IStateManager reset_target_disk_state method
`IStateManager` SHALL provide a `reset_target_disk_state(target_path: str, vm_name: str, disk: str) -> None` method that atomically clears ONLY the given VM+disk's per-target state:

- `_full_backups.json`: FULL entries whose name starts with `{vm_name}.` AND whose `disk` equals `disk` are removed. Entries of other VMs sharing the target and entries of other disks of the same VM SHALL NOT be touched.
- `_dependencies.json`: dependency keys whose FULL backup belongs to `(vm_name, disk)` are removed. The disk SHALL be extracted from the FULL name via `parse_disk_from_snapshot_name()`; keys whose disk cannot be determined or does not match SHALL NOT be touched.
- `_target_state.json`: the `last_backup_allocation[disk]` entry for the target is removed; other disks' baselines remain.

All writes SHALL be atomic. This method is used by `Core.restore()` for each configured target after replacing one disk's base image.

#### Scenario: reset_target_disk_state clears only the given VM and disk
- **WHEN** `reset_target_disk_state("/mnt/backup/shared", "myvm", "vda")` is called and the target holds FULLs for `myvm`/`vda`, `myvm`/`vdb`, and `othervm`/`vda`
- **THEN** `get_full_backups("/mnt/backup/shared")` no longer contains the `myvm` `vda` FULLs
- **AND** the `myvm` `vdb` FULLs and the `othervm` `vda` FULLs remain
- **AND** `get_last_backup_allocation("/mnt/backup/shared", "vda")` returns None
- **AND** `get_last_backup_allocation("/mnt/backup/shared", "vdb")` still returns its prior value

#### Scenario: reset_target_disk_state removes only the disk's dependencies
- **WHEN** `_dependencies.json` holds FULL keys for `myvm` `vda` and `myvm` `vdb` and `reset_target_disk_state(target, "myvm", "vda")` is called
- **THEN** only the `vda` FULL keys (disk parsed from the FULL name) are removed
- **AND** the `vdb` FULL keys remain intact

#### Scenario: reset_target_disk_state for unknown target
- **WHEN** `reset_target_disk_state("/nonexistent", "myvm", "vda")` is called
- **THEN** no error is raised
- **AND** no state files are modified

#### Scenario: reset_target_disk_state is atomic
- **WHEN** `reset_target_disk_state(target, "myvm", "vda")` is called
- **THEN** each modified state file is written atomically (`.tmp` + `os.replace`)

### Requirement: Incremental dependency keys are key-format agnostic

`record_incremental_dependency(target_path, incremental_name, full_name)` SHALL accept incremental names in both legacy format (snapshot names, e.g. `vm.20260807T152956_vda_ec1148`) and backup-name format (freeze-ts names, e.g. `vm.20260808T031542_vda_a1b2c3`). `get_incremental_dependencies()` SHALL return all recorded incrementals for a FULL regardless of key format, and chain-length counting in Core SHALL count entries without inspecting their format. No migration pass is required: mixed generations coexist in `_dependencies.json` until legacy records expire through generation rotation.

#### Scenario: Mixed-generation dependencies counted together

- **WHEN** a FULL has 3 legacy snapshot-keyed incrementals and 2 backup-named incrementals
- **THEN** `get_incremental_dependencies(target, full_name)` returns all 5 names
- **AND** the chain-length decision sees count 5

#### Scenario: Legacy records expire naturally

- **WHEN** retention deletes an old generation containing legacy-keyed dependencies
- **THEN** those dependency records are removed with the generation
- **AND** no explicit migration step is ever required

### Requirement: Host boot_id tracking per VM

`IStateManager` SHALL provide `get_boot_id(vm_name) -> str | None` and `set_boot_id(vm_name, boot_id)` persisting the host boot identifier (`/proc/sys/kernel/random/boot_id`) in the per-VM state file as an optional field. Core SHALL record the current boot_id after each fully successful run for the VM. Absence of the field (pre-feature state files, first run) SHALL be well-defined: readers receive `None` and consumers SHALL treat it as "unknown", never as an error. The field SHALL be used for crash-evidence wording only (capability `bitmap-loss-recovery`), never for gating.

#### Scenario: Boot id recorded on successful run

- **WHEN** a run completes successfully for a VM
- **THEN** the current host boot_id is stored in that VM's state

#### Scenario: Boot id change detected across a crash

- **WHEN** state holds boot_id A and the current host boot_id is B ≠ A
- **THEN** consumers can conclude the host restarted since the last successful run

#### Scenario: Missing boot id is unknown, not an error

- **WHEN** `get_boot_id` is called for a VM whose state predates this feature
- **THEN** it returns None and no exception is raised

### Requirement: Per-disk last-commit timestamp tracking

`IStateManager` SHALL provide `get_last_commit_ts(vm_name, disk) -> datetime | None` and `set_last_commit_ts(vm_name, disk, ts)` persisting, per VM and disk, the timestamp of the most recent successful blockcommit or `qemu-img commit` affecting that disk's chain. Core SHALL write the marker immediately after every successful commit. The marker SHALL be serialized in the per-VM state file as an optional field; absence SHALL mean "unknown" and consumers (recovery gate G1, capability `bitmap-loss-recovery`) SHALL treat unknown as "gate failed" (conservative FULL). No migration of existing state files SHALL be required.

#### Scenario: Marker written after successful blockcommit

- **WHEN** a blockcommit for disk `vda` completes successfully
- **THEN** `last_commit_ts[vm][vda]` is set to the commit time

#### Scenario: Marker written after successful offline commit

- **WHEN** a `qemu-img commit` for a stopped VM completes successfully
- **THEN** the same marker is written for that disk

#### Scenario: Absent marker is conservative

- **WHEN** gate G1 reads the marker for a disk with no recorded commit
- **THEN** it receives None and treats the gate as failed

### Requirement: Commit intent journal persistence

`JsonStateManager` SHALL persist the commit intent journal (spec: `commit-intent-journal`)
under the top-level key `commit_in_progress` of the per-VM state file as a list of objects
with keys `disk`, `snapshots`, `base`, `started_ts`. Writes SHALL go through the existing
atomic tmp-file + `os.replace` path used for all state mutations, and the journal SHALL be
written in the same atomic save as any other state mutation of that call. State files lacking
the key SHALL load as an empty journal. `InMemoryStateManager` SHALL implement the same
`set_commit_in_progress` / `get_commit_in_progress` / `clear_commit_in_progress` semantics
in memory.

#### Scenario: Journal round-trip through JSON state

- **WHEN** `set_commit_in_progress("vm1", "vda", ["s1"], "/data/img.qcow2", "20260812T150126")` is called on `JsonStateManager`
- **AND** the state file is re-read by a fresh manager instance
- **THEN** `get_commit_in_progress("vm1")` returns the identical `CommitIntent`

#### Scenario: Journal write is atomic with other state

- **WHEN** a state save includes a journal update
- **THEN** the file is written via tmp + `os.replace` and a concurrent reader never observes a partial document

#### Scenario: Legacy state file loads cleanly

- **WHEN** a state file written before this feature is loaded
- **THEN** `get_commit_in_progress` returns an empty list and all pre-existing fields are unaffected

### Requirement: collapse_in_progress phase key

Per-VM state (`{vm}.json`) SHALL support an additive key `collapse_in_progress`: a list of disk names currently in the hysteresis collapse phase. A missing key SHALL be treated as an empty list (no migration required; old code ignores unknown keys). `IStateManager` SHALL expose additive methods to set/clear the marker per disk and to read the list; concrete `JsonStateManager` SHALL persist it atomically (tmp + replace) like all other state writes. Dry-run SHALL NOT write the marker. `reset_vm_state` SHALL clear the key for the VM; `reset_vm_disk_state` SHALL remove one disk from the list. Mock implementations SHALL mirror the interface.

#### Scenario: Missing key reads as empty

- **WHEN** a state file has no `collapse_in_progress` key
- **THEN** readers observe an empty phase list

#### Scenario: Marker survives atomic write

- **WHEN** the marker is set for `vda` and the state file is reloaded
- **THEN** `vda` is present in `collapse_in_progress`

#### Scenario: Reset clears the marker

- **WHEN** `reset_vm_state("vm1")` runs while `collapse_in_progress = ["vda"]`
- **THEN** the key is cleared for `vm1`

#### Scenario: Old code tolerates the new key

- **WHEN** a pre-change qsnap binary reads a state file containing `collapse_in_progress`
- **THEN** loading succeeds and the key is ignored
