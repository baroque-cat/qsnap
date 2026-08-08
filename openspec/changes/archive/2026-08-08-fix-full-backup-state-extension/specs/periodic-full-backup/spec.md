# Periodic Full Backup — delta

## MODIFIED Requirements

### Requirement: IStateManager tracks full backups per target

`IStateManager` SHALL provide `get_full_backups(target_path) -> list[FullBackupInfo]` returning all FULLs for a target, and `record_full_backup(target_path, name, timestamp)` to append a new FULL. The `bucket_level` parameter is REMOVED. `JsonStateManager` SHALL persist this as a list per target path in `_full_backups.json`. Old JSON entries containing `bucket_level` SHALL be read-tolerantly (field silently ignored).

When Core records a newly created FULL backup after verification, it SHALL pass the backup name WITH the `.qcow2` extension, derived from the stem-form `BackupResult.snapshot_name` returned by the provider (`f"{result.snapshot_name}.qcow2"`). The provider contract is unchanged: `BackupResult.snapshot_name` remains a stem (the backup identifier); Core owns the stem-to-filename derivation at the state boundary. This guarantees that the recorded `FullBackupInfo.path` resolves to the physical file on the target, which all existence-based consumers rely upon.

#### Scenario: Full backup recorded and retrieved
- **WHEN** `record_full_backup("/mnt/backup/vm", "vm.FULL.20260701T000000_vda_a1b2c3.qcow2", ts)` is called then `get_full_backups("/mnt/backup/vm")` is called
- **THEN** the returned list contains a `FullBackupInfo` with `name="vm.FULL.20260701T000000_vda_a1b2c3.qcow2"`, `timestamp=ts`

#### Scenario: Old JSON with bucket_level is read-tolerant
- **WHEN** `_full_backups.json` contains an entry with `"bucket_level": "monthly"`
- **THEN** the entry is loaded without error
- **AND** the `bucket_level` field is silently ignored

#### Scenario: Core records the FULL with the .qcow2 extension after verification
- **WHEN** a FULL backup succeeds verification and `BackupResult.snapshot_name` is `"vm.FULL.20260701T000000_vda_a1b2c3"` (stem)
- **THEN** Core calls `record_full_backup(str(target.path), "vm.FULL.20260701T000000_vda_a1b2c3.qcow2", ...)`
- **AND** the recorded `FullBackupInfo.path` equals the physical file path on the target
