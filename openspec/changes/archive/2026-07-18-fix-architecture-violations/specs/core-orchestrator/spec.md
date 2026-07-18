## REMOVED Requirements

### Requirement: Core._should_create_bucket_full signature change

**Reason**: The `_should_create_bucket_full` method and its companions (`_active_buckets`, `_f_anchor_buckets`, `_period_key`) are extracted into `BucketFullStrategy` implementing `IBucketFullStrategy`. Core no longer contains these private methods.

**Migration**: Replace `self._should_create_bucket_full(target, policy, all_fulls, snapshot_ts)` calls with `self._factory.create_bucket_full_strategy().should_create_full(target, policy, all_fulls, snapshot_ts, now)`. The `_bucket_anchor_keys` helper (if needed) moves to `BucketFullStrategy` or `RetentionPolicy`.

## MODIFIED Requirements

### Requirement: Core._backup_target triggers full backup when due

`Core._backup_target(vm_config, target, snapshots)` SHALL, before the incremental transfer loop, call `state.get_full_backups(target.path)` to retrieve ALL full backups for the target. It SHALL obtain an `IBucketFullStrategy` via `self._factory.create_bucket_full_strategy()` and call `strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)` with the complete list of `FullBackupInfo` objects and the most recent snapshot's timestamp. Core SHALL NOT contain private methods `_should_create_bucket_full`, `_active_buckets`, `_f_anchor_buckets`, or `_period_key`.

#### Scenario: Full backup list passed to bucket strategy
- **WHEN** `_backup_target()` is called and the target has 2 existing FULL records
- **THEN** `state.get_full_backups(target.path)` returns a list of 2 `FullBackupInfo` objects
- **THEN** the list is passed to `strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)`

#### Scenario: First run creates full backup via strategy
- **WHEN** `get_full_backups(target.path)` returns an empty list (no previous FULLs)
- **THEN** `strategy.should_create_full(...)` returns `(True, bucket_level)` for the first active/F-marked bucket
- **THEN** a FULL is created

#### Scenario: Strategy obtained via factory
- **WHEN** `_backup_target()` runs
- **THEN** it calls `self._factory.create_bucket_full_strategy()` exactly once
- **AND** the resulting strategy object is used for the bucket decision
- **AND** no private bucket-related methods exist on Core

## ADDED Requirements

### Requirement: Core imports shared utilities from qsnap.utils

Core SHALL import `is_vm_running`, `nbd_full_export` from `qsnap.utils.nbd`, `verify_full_backup` from `qsnap.utils.verification`, and `file_sha256` from `qsnap.utils.hash`. Core SHALL NOT import from `qsnap.modules.backup` or `qsnap.modules.*` except through the factory.

#### Scenario: Core has no domain module imports
- **WHEN** `qsnap/core/__init__.py` is inspected
- **THEN** there is NO `from qsnap.modules.backup` import
- **AND** there is NO `from qsnap.modules.snapshot` import
- **AND** all utility imports come from `qsnap.utils`
