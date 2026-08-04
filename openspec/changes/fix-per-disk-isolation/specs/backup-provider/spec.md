## ADDED Requirements

### Requirement: Backup results carry the source disk

`BitmapBackupProvider.transfer_missing()` SHALL return every `BackupResult` with `disk` set to the disk target of the snapshot being transferred (`snapshot.disk` from the per-snapshot iteration). `BitmapBackupProvider.create_full_backup()` SHALL return its `BackupResult` with `disk` set to the disk target of `source_snapshot.disk`. Core's FULL-creation bookkeeping in `_backup_target()` SHALL propagate the same disk into the `ActionRecord(action="backup_full")` it appends. A `BackupResult` produced by either method for a known snapshot SHALL NOT leave `disk` as `None`.

#### Scenario: Incremental transfer result carries disk
- **WHEN** `transfer_missing()` transfers snapshot `vm.20250101T120000_vdb_d4e5f6` (disk `vdb`)
- **THEN** the returned `BackupResult` has `disk="vdb"`

#### Scenario: Multi-disk transfer returns per-disk results
- **WHEN** `transfer_missing()` receives snapshots spanning disks `vda` and `vdb`
- **THEN** each returned `BackupResult.disk` matches the disk of the snapshot it reports on

#### Scenario: FULL creation result carries disk
- **WHEN** `create_full_backup()` creates a FULL from a source snapshot with `disk="vda"`
- **THEN** the returned `BackupResult` has `disk="vda"`

#### Scenario: Failed transfer result still carries disk
- **WHEN** `transfer_missing()` fails to transfer a snapshot of disk `vda`
- **THEN** the returned `BackupResult(success=False)` still has `disk="vda"`
