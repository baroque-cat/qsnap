# Periodic Full Backup

## Purpose

Periodic creation of standalone (anchor) full backups via `qemu-img convert` on backup targets. Full backups provide a self-contained restore point independent of the incremental chain.

## Requirements

### Requirement: Core triggers full backup before incremental transfer

`Core._backup_target()` SHALL retrieve ALL full backups via `state.get_full_backups(target.path)` and pass the complete list to `_should_create_bucket_full()`. The first backup to a target SHALL always be a FULL. When `_should_create_bucket_full()` returns `(True, bucket_level)`, Core SHALL call `provider.create_full_backup(vm_config.name, most_recent, target, compress=target.compress, bucket_level=bucket_level)` — the NBD pull-model is the single FULL backup path (see `live-vm-full-backup`). After FULL creation, Core SHALL record it via `IStateManager.record_full_backup()`.

#### Scenario: First backup to target creates FULL
- **WHEN** `get_full_backups(target.path)` returns an empty list and a snapshot is available
- **THEN** a FULL backup is created via `provider.create_full_backup()` using NBD

#### Scenario: New weekly period triggers FULL (all-buckets mode)
- **WHEN** the policy has `weekly=4` active and no F-anchors, and the current snapshot's ISO week differs from the last weekly FULL's week
- **THEN** a FULL backup is created with `bucket_level="weekly"`

#### Scenario: F-anchor on weekly only triggers FULL at week boundaries
- **WHEN** the policy has `weekly=4, anchor_weekly=True, daily=7, anchor_daily=False`
- **AND** the current snapshot's day differs from the last daily FULL's day
- **THEN** no FULL is created (daily is not an F-anchor)
- **AND** if the current snapshot's week differs from the last weekly FULL's week, a FULL IS created

#### Scenario: FULL creation works for bitmap targets
- **WHEN** `_should_create_bucket_full()` returns `(True, "monthly")` for a target
- **THEN** `BitmapBackupProvider.create_full_backup()` is called
- **AND** the FULL is created via NBD full export
- **AND** the FULL is recorded in state with `bucket_level="monthly"`

#### Scenario: Dry-run logs FULL-would-be-created without executing
- **WHEN** `Core._backup_target()` is called in dry-run mode
- **AND** `_should_create_bucket_full()` returns `(True, "weekly")`
- **THEN** an INFO log is emitted: "[dry-run] Would create FULL backup (bucket=weekly, method=NBD, VM=running)"
- **AND** `provider.create_full_backup()` is NOT called
- **AND** no `virsh backup-begin` or `qemu-img convert` is executed

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
