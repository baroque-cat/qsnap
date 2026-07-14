## MODIFIED Requirements

### Requirement: IStateManager tracks SnapshotInfo content_hash

`IStateManager.record_snapshot(vm_name, snapshot_info)` SHALL persist the `SnapshotInfo.content_hash` field if non-None. `IStateManager.get_snapshots(vm_name)` SHALL return `SnapshotInfo` objects with the stored `content_hash`.

#### Scenario: Hash persists across runs
- **WHEN** `record_snapshot("vm", SnapshotInfo(content_hash="abc123"))` is called and then `get_snapshots("vm")` is called
- **THEN** the returned `SnapshotInfo.content_hash` is `"abc123"`

## ADDED Requirements

### Requirement: IStateManager tracks last full backup per target

`IStateManager` SHALL provide `get_last_full_backup(target_path: str) -> FullBackupInfo | None` and `set_last_full_backup(target_path: str, name: str, timestamp: datetime) -> None` methods.

#### Scenario: Full backup state saved and retrieved
- **WHEN** `set_last_full_backup("/mnt/backup/vm", "FULL.20250714", ts)` is called then `get_last_full_backup("/mnt/backup/vm")` is called
- **THEN** the returned `FullBackupInfo.name` is `"FULL.20250714"` and `timestamp` equals `ts`

#### Scenario: No full backup returns None
- **WHEN** `get_last_full_backup("/mnt/backup/nonexistent")` is called with no prior `set_last_full_backup`
- **THEN** the function returns `None`
