## MODIFIED Requirements

### Requirement: IVMModuleFactory ABC
The system SHALL provide an `IVMModuleFactory` ABC with factory methods for creating every domain module type. Each method accepts the config dataclass relevant to that module type. The method `create_bucket_full_strategy()` SHALL NOT exist on the interface.

#### Scenario: IVMModuleFactory defines all creation methods
- **WHEN** IVMModuleFactory is inspected
- **THEN** it has abstract methods: `create_snapshot_provider`, `create_backup_provider`, `create_retention_engine`, `create_change_detector`, `create_lifecycle_manager`
- **AND** it does NOT have `create_bucket_full_strategy`

## REMOVED Requirements

### Requirement: DefaultFactory returns BitmapBackupProvider with hard dependency gates

**Reason**: The requirement itself is still valid, but it references `create_bucket_full_strategy` indirectly. The requirement is modified to remove that reference.

**Migration**: The factory still returns `BitmapBackupProvider` with the same hard dependency gates. Only the `create_bucket_full_strategy()` method is removed.

## ADDED Requirements

### Requirement: DefaultFactory does not create bucket full strategy

`DefaultFactory` SHALL NOT import `BucketFullStrategy` or `IBucketFullStrategy`. The file `qsnap/interfaces/bucket_strategy.py` SHALL NOT exist. The file `qsnap/modules/backup/bucket_strategy.py` SHALL NOT exist.

#### Scenario: Factory has no bucket full strategy method
- **WHEN** `DefaultFactory` is inspected
- **THEN** it does NOT have a `create_bucket_full_strategy` method
- **AND** it does NOT import `BucketFullStrategy` or `IBucketFullStrategy`
