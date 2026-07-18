## ADDED Requirements

### Requirement: IBucketFullStrategy interface

The system SHALL provide an `IBucketFullStrategy` ABC in `qsnap/interfaces/bucket_strategy.py` with a single abstract method:

```python
def should_create_full(
    self,
    target: TargetConfig,
    policy: RetentionPolicy,
    all_fulls: list[FullBackupInfo],
    snapshot_ts: datetime,
    now: datetime,
) -> tuple[bool, str]:
```

The method SHALL return `(True, bucket_level)` when a new FULL backup should be created for the given bucket level, or `(False, "")` when no FULL is needed. `bucket_level` SHALL be one of `"hourly"`, `"daily"`, `"weekly"`, `"monthly"`, `"yearly"`.

#### Scenario: Interface defines single method
- **WHEN** `IBucketFullStrategy` is inspected
- **THEN** it has exactly one abstract method: `should_create_full`
- **AND** it inherits from `ABC`

### Requirement: BucketFullStrategy implements IBucketFullStrategy

The system SHALL provide a `BucketFullStrategy` class in `qsnap/modules/backup/bucket_strategy.py` that implements `IBucketFullStrategy`. It SHALL be a stateless worker — constructor accepts no dependencies (or optionally `IShell` for future use), and all logic is self-contained (pure computation on input parameters). It SHALL NOT inherit from Core.

#### Scenario: Bucket strategy returns True for first snapshot at new monthly period
- **WHEN** `should_create_full(target, policy, all_fulls=[], snapshot_ts=datetime(2026, 7, 1), now=...)` is called
- **AND** `policy.monthly > 0` (target has a monthly anchor bucket)
- **THEN** the method returns `(True, "monthly")`

#### Scenario: Bucket strategy returns False when period unchanged
- **WHEN** `should_create_full(target, policy, all_fulls=[FullBackupInfo(ts=datetime(2026,7,1), level="monthly")], snapshot_ts=datetime(2026,7,2), now=...)` is called
- **THEN** `all_fulls` already contains a FULL for the same monthly period
- **THEN** the method returns `(False, "")`

#### Scenario: Bucket strategy with multi-level anchors
- **WHEN** policy has `anchor_weekly` set and current timestamp starts a new weekly period
- **AND** the weekly bucket is the highest active bucket
- **THEN** the method returns `(True, "weekly")`

### Requirement: Factory creates IBucketFullStrategy

`IVMModuleFactory` SHALL gain an abstract method `create_bucket_full_strategy() -> IBucketFullStrategy`. `DefaultFactory` SHALL implement it by returning `BucketFullStrategy()`. `MockVMModuleFactory` SHALL implement it by returning `MockBucketFullStrategy`.

#### Scenario: DefaultFactory returns BucketFullStrategy
- **WHEN** `factory.create_bucket_full_strategy()` is called on a `DefaultFactory`
- **THEN** the returned value is an instance of `BucketFullStrategy`
- **AND** it satisfies `isinstance(result, IBucketFullStrategy)`

#### Scenario: MockFactory returns MockBucketFullStrategy
- **WHEN** `factory.create_bucket_full_strategy()` is called on a `MockVMModuleFactory`
- **THEN** the returned value satisfies `isinstance(result, IBucketFullStrategy)`

### Requirement: Core uses factory to obtain bucket strategy

`Core._backup_target()` SHALL obtain the bucket strategy via `self._factory.create_bucket_full_strategy()` and call `strategy.should_create_full(...)` instead of the removed `self._should_create_bucket_full(...)`. Core SHALL NOT contain bucket FULL strategy logic as private methods.

#### Scenario: Core delegates bucket decision to strategy
- **WHEN** `Core._backup_target(vm_config, target, snapshots)` runs
- **THEN** it calls `self._factory.create_bucket_full_strategy()` exactly once per target
- **THEN** it calls `strategy.should_create_full(target, policy, all_fulls, snapshot_ts, now)` to determine if a FULL is needed
- **THEN** no `_should_create_bucket_full`, `_active_buckets`, `_f_anchor_buckets`, or `_period_key` methods exist on Core
