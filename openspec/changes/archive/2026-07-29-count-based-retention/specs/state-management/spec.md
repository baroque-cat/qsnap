## MODIFIED Requirements

### Requirement: IStateManager tracks last full backup per target

`IStateManager` SHALL provide `get_last_full_backup(target_path: str) -> FullBackupInfo | None` and `set_last_full_backup(target_path: str, name: str, timestamp: datetime) -> None` methods. `JsonStateManager` SHALL persist this under the `"target_full_backups"` key in a per-VM JSON file. The `set_last_full_backup` method SHALL NOT hardcode a `bucket_level` value.

#### Scenario: Full backup state saved and retrieved
- **WHEN** `set_last_full_backup("/mnt/backup/vm", "FULL.20250714", ts)` is called then `get_last_full_backup("/mnt/backup/vm")` is called
- **THEN** the returned `FullBackupInfo.name` is `"FULL.20250714"` and `timestamp` equals `ts`

#### Scenario: No full backup returns None
- **WHEN** `get_last_full_backup("/mnt/backup/nonexistent")` is called with no prior `set_last_full_backup`
- **THEN** the function returns `None`

## ADDED Requirements

### Requirement: FullBackupInfo without bucket_level

`FullBackupInfo` SHALL NOT have a `bucket_level` field. The dataclass SHALL have exactly: `name: str`, `path: Path`, `timestamp: datetime`. Old JSON entries containing `bucket_level` SHALL be read-tolerantly — the field is silently ignored on load.

#### Scenario: FullBackupInfo constructed without bucket_level
- **WHEN** a `FullBackupInfo` is created with `name="vm.FULL.20260701"`, `path=Path(...)`, `timestamp=ts`
- **THEN** the instance has exactly three fields: `name`, `path`, `timestamp`
- **AND** accessing `.bucket_level` raises `AttributeError`

#### Scenario: Old JSON with bucket_level loaded without error
- **WHEN** `_full_backups.json` contains `{"bucket_level": "monthly"}`
- **THEN** the entry is loaded into `FullBackupInfo` without error
- **AND** the `bucket_level` value is silently ignored

### Requirement: record_full_backup without bucket_level parameter

`IStateManager.record_full_backup(target_path, name, timestamp)` SHALL NOT accept a `bucket_level` parameter. `JsonStateManager` SHALL NOT write `bucket_level` to JSON.

#### Scenario: record_full_backup called without bucket_level
- **WHEN** `record_full_backup("/mnt/backup/vm", "vm.FULL.20260701", ts)` is called
- **THEN** the FULL is recorded in state with `name`, `path`, `timestamp` only
- **AND** no `bucket_level` key is written to JSON
