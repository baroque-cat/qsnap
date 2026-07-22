## MODIFIED Requirements

### Requirement: Core triggers full backup before incremental transfer

`Core._backup_target()` SHALL retrieve ALL full backups via `state.get_full_backups(target.path)` and pass the complete list to `IBucketFullStrategy.should_create_full()` (obtained via `self._factory.create_bucket_full_strategy()`). The first backup to a target SHALL always be a FULL. When `should_create_full()` returns `(True, bucket_level)`, Core SHALL call `provider.create_full_backup(vm_config.name, most_recent, target, compress=target.compress, bucket_level=bucket_level)` — the NBD pull-model is the single FULL backup path (see `live-vm-full-backup`). After FULL creation, Core SHALL record it via `IStateManager.record_full_backup()`.

Core SHALL NOT contain a private `_should_create_bucket_full()` method — the bucket decision logic is delegated to `IBucketFullStrategy` / `BucketFullStrategy` (a stateless pure-function worker) via the factory.

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

#### Scenario: Bucket strategy obtained via factory

- **WHEN** `Core._backup_target()` needs to decide whether to create a FULL
- **THEN** it calls `self._factory.create_bucket_full_strategy()`
- **AND** calls `strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)`
- **AND** Core does NOT contain a `_should_create_bucket_full` private method

#### Scenario: Dry-run logs FULL-would-be-created without executing

- **WHEN** `Core._backup_target()` is called in dry-run mode
- **AND** `should_create_full()` returns `(True, "weekly")`
- **THEN** an INFO log is emitted: "[dry-run] Would create FULL backup (bucket=weekly, method=NBD, VM=running)"
- **AND** `provider.create_full_backup()` is NOT called
- **AND** no `virsh backup-begin` or `qemu-img convert` is executed
