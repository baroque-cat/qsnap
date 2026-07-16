## REMOVED Requirements

### Requirement: TargetConfig supports full_every and full_compress

**Reason**: `full_every` is replaced by bucket-driven FULL creation. `full_compress` is renamed to `compress`.
**Migration**: Remove `full_every` from TOML configs (FULLs are now automatic). Replace `full_compress = true` with `compress = true`. Config parsing logs a deprecation WARNING if `full_every` is found, and maps `full_compress` to `compress` if `compress` is not explicitly set.

## MODIFIED Requirements

### Requirement: FileCopyBackupProvider creates full backups via qemu-img convert

`FileCopyBackupProvider.create_full_backup(source_snapshot, target, compress=False, bucket_level="monthly")` SHALL run `qemu-img convert [-c] -f qcow2 -O qcow2 <source> <target_path>/vm.FULL.YYYYMMDD.qcow2` (with `-c` when `compress=True`). The method SHALL accept a `bucket_level` parameter indicating which retention bucket triggered this FULL. After creation, the FULL SHALL be recorded via `IStateManager.record_full_backup(target_path, name, timestamp, bucket_level)`. The method SHALL return a `BackupResult`.

#### Scenario: Uncompressed full backup
- **WHEN** `create_full_backup(snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** `qemu-img convert` is called WITHOUT `-c` and a `BackupResult(success=True)` is returned
- **AND** the FULL is recorded in state with `bucket_level="monthly"`

#### Scenario: Compressed full backup
- **WHEN** `create_full_backup(snapshot, target, compress=True, bucket_level="yearly")` is called
- **THEN** `qemu-img convert -c` is called and a `BackupResult(success=True)` is returned
- **AND** the FULL is recorded in state with `bucket_level="yearly"`

### Requirement: Core triggers full backup before incremental transfer

`Core._backup_target()` SHALL determine whether to create a FULL backup using `_should_create_bucket_full(target, policy, last_full, snapshot_ts)`. This method identifies the highest active retention bucket (yearly > monthly > weekly > daily > hourly) and checks whether the current snapshot's timestamp falls in a new period of that bucket level compared to the last FULL. If yes, a FULL is created before the incremental transfer loop. The first backup to a target SHALL always be a FULL.

#### Scenario: First backup to target creates FULL
- **WHEN** a target has no existing backups and a snapshot is available
- **THEN** a FULL backup is created immediately via `qemu-img convert`

#### Scenario: New monthly period triggers FULL
- **WHEN** the highest active bucket is monthly (count > 0) and the current snapshot's month differs from the last FULL's month
- **THEN** a FULL backup is created with `bucket_level="monthly"`

#### Scenario: Same bucket period skips FULL
- **WHEN** the highest active bucket is monthly and the current snapshot is in the same month as the last FULL
- **THEN** no FULL backup is created; the snapshot is transferred as an incremental

#### Scenario: Policy with no buckets and preserve_min=all
- **WHEN** all bucket counts are 0 and `preserve_min = "all"`
- **THEN** no FULL is ever created (chain grows indefinitely, nothing is deleted)

### Requirement: Incremental backups rebase to the FULL anchor

`FileCopyBackupProvider.transfer_missing()` SHALL check for an existing FULL anchor. When an anchor exists, newly transferred incrementals SHALL be rebased via `qemu-img rebase -u -b ./vm.FULL.YYYYMMDD.qcow2` to point at the FULL. After rebase, the dependency SHALL be recorded via `IStateManager.record_incremental_dependency(target_path, incremental_name, full_name)`. The FULL anchor SHALL be selected by timestamp parsed from the filename (not file mtime).

#### Scenario: New incremental rebased to FULL
- **WHEN** target directory contains `vm.FULL.20260701.qcow2` and a new incremental `vm.20260702.qcow2` is transferred
- **THEN** `qemu-img rebase -u -b ./vm.FULL.20260701.qcow2 vm.20260702.qcow2` is called
- **AND** `record_incremental_dependency(target_path, "vm.20260702.qcow2", "vm.FULL.20260701.qcow2")` is called

#### Scenario: No FULL anchor uses source backing
- **WHEN** target directory has no `vm.FULL.*.qcow2` files
- **THEN** incremental rebase uses the source backing filename as before

### Requirement: IStateManager tracks full backups per target

`IStateManager` SHALL provide `get_full_backups(target_path) -> list[FullBackupInfo]` returning all FULLs for a target, and `record_full_backup(target_path, name, timestamp, bucket_level)` to append a new FULL. `JsonStateManager` SHALL persist this as a list per target path in `_full_backups.json`.

#### Scenario: Full backup recorded and retrieved
- **WHEN** `record_full_backup("/mnt/backup/vm", "vm.FULL.20260701", ts, "monthly")` is called then `get_full_backups("/mnt/backup/vm")` is called
- **THEN** the returned list contains a `FullBackupInfo` with `name="vm.FULL.20260701"`, `timestamp=ts`, `bucket_level="monthly"`

## ADDED Requirements

### Requirement: Bucket-driven FULL creation logic

`Core._should_create_bucket_full(target, policy, last_full, snapshot_ts) -> tuple[bool, str]` SHALL determine the highest active bucket by checking counts from yearly down to hourly. It SHALL return `(True, bucket_level)` when: (a) no previous FULL exists (first backup), or (b) the snapshot's timestamp falls in a new period of the highest bucket compared to the last FULL's timestamp. It SHALL return `(False, "")` otherwise. If no buckets are active (all counts 0), it SHALL return `(False, "")`.

#### Scenario: Highest bucket is yearly
- **WHEN** policy has `yearly=1, monthly=12, weekly=4, daily=7, hourly=24`
- **THEN** the highest active bucket is "yearly" and FULLs are created at year boundaries

#### Scenario: Highest bucket is daily
- **WHEN** policy has `yearly=0, monthly=0, weekly=0, daily=3, hourly=6`
- **THEN** the highest active bucket is "daily" and FULLs are created at day boundaries

#### Scenario: No active buckets
- **WHEN** policy has all counts 0 and `preserve_min = "all"`
- **THEN** `_should_create_bucket_full()` returns `(False, "")` and no FULL is created
