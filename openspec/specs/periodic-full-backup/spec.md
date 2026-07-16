# Periodic Full Backup

## Purpose

Periodic creation of standalone (anchor) full backups via `qemu-img convert` on backup targets. Full backups provide a self-contained restore point independent of the incremental chain. Incremental backups rebase to the most recent FULL anchor, protecting against chain corruption and simplifying restore.

## Requirements

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

`Core._backup_target()` SHALL retrieve ALL full backups via `state.get_full_backups(target.path)` and pass the complete list to `_should_create_bucket_full()`. The first backup to a target SHALL always be a FULL.

#### Scenario: First backup to target creates FULL
- **WHEN** `get_full_backups(target.path)` returns an empty list and a snapshot is available
- **THEN** a FULL backup is created immediately via `qemu-img convert`

#### Scenario: New weekly period triggers FULL (all-buckets mode)
- **WHEN** the policy has `weekly=4` active and no F-anchors, and the current snapshot's ISO week differs from the last weekly FULL's week
- **THEN** a FULL backup is created with `bucket_level="weekly"`

#### Scenario: F-anchor on weekly only triggers FULL at week boundaries
- **WHEN** the policy has `weekly=4, anchor_weekly=True, daily=7, anchor_daily=False`
- **AND** the current snapshot's day differs from the last daily FULL's day
- **THEN** no FULL is created (daily is not an F-anchor)
- **AND** if the current snapshot's week differs from the last weekly FULL's week, a FULL IS created

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

### Requirement: Bucket-driven FULL creation logic

`Core._should_create_bucket_full(target, policy, all_fulls, snapshot_ts) -> tuple[bool, str]` SHALL accept a list of ALL full backups for the target (instead of a single `last_full` record). It SHALL determine which buckets to check as follows:
1. If any `anchor_*` field on the policy is `True`, check only F-marked buckets in descending order (yearly → monthly → weekly → daily → hourly).
2. Otherwise, check ALL buckets where `policy.{bucket} > 0` in descending order.

For each checked bucket, it SHALL find the most recent FULL with matching `bucket_level` from `all_fulls`. It SHALL return `(True, bucket_level)` when: (a) no previous FULL exists for that bucket, or (b) the snapshot's timestamp falls in a new period of that bucket compared to the matching FULL's timestamp. It SHALL short-circuit on the first match — at most one FULL is created per snapshot. It SHALL return `(False, "")` if no checked bucket triggers a new FULL.

#### Scenario: Highest bucket is yearly with all-buckets mode
- **WHEN** policy has `yearly=1, monthly=12, weekly=4, daily=7, hourly=24` and no F-anchors
- **THEN** all buckets are checked: yearly, monthly, weekly, daily, hourly
- **THEN** FULLs are created at each bucket's boundary

#### Scenario: F-anchor overrides to daily-only
- **WHEN** policy has `yearly=1, monthly=12, weekly=4, daily=7, hourly=24, anchor_daily=True`
- **THEN** only daily bucket is checked
- **THEN** FULLs are created only at day boundaries

#### Scenario: No active buckets
- **WHEN** policy has all counts 0 and `preserve_min = "all"` and no F-anchors
- **THEN** `_should_create_bucket_full()` returns `(False, "")` and no FULL is created

#### Scenario: First backup to target creates FULL
- **WHEN** a target has no existing backups (`all_fulls` is empty) and a snapshot is available
- **THEN** `_should_create_bucket_full()` returns `(True, "yearly")` (first checked bucket that is active or F-marked)
