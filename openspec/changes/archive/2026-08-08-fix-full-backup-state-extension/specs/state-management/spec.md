# State Management — delta

## MODIFIED Requirements

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

## ADDED Requirements

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
