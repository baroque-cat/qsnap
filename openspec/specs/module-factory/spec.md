# Module Factory

## Purpose

The abstract factory (`IVMModuleFactory`) that creates all domain module instances for a VM. Core holds a reference to the factory interface and calls it per-VM, keeping Core unaware of concrete module types. Production injects `DefaultFactory`; tests inject mocks.

## Requirements

### Requirement: IVMModuleFactory ABC
The system SHALL provide an `IVMModuleFactory` ABC with factory methods for creating every domain module type. Each method accepts the config dataclass relevant to that module type. The method `create_bucket_full_strategy()` SHALL NOT exist on the interface.

#### Scenario: IVMModuleFactory defines all creation methods
- **WHEN** IVMModuleFactory is inspected
- **THEN** it has abstract methods: `create_snapshot_provider`, `create_backup_provider`, `create_retention_engine`, `create_change_detector`, `create_lifecycle_manager`
- **AND** it does NOT have `create_bucket_full_strategy`

### Requirement: Factory returns ABC interface types
Each factory method SHALL return an instance implementing the corresponding ABC interface (e.g., `create_snapshot_provider` returns `ISnapshotProvider`).

#### Scenario: Factory method return type contract
- **WHEN** `create_snapshot_provider(vm_config)` is called
- **THEN** the returned value satisfies `isinstance(result, ISnapshotProvider)`

### Requirement: DefaultFactory receives IShell and IStateManager
`DefaultFactory` SHALL accept `IShell` and `IStateManager` in its constructor, storing them for injection into module constructors.

#### Scenario: DefaultFactory holds shell and state references
- **WHEN** DefaultFactory is created with a mock shell and mock state manager
- **THEN** both are stored and available for module construction

### Requirement: DefaultFactory does not create bucket full strategy

`DefaultFactory` SHALL NOT import `BucketFullStrategy` or `IBucketFullStrategy`. The file `qsnap/interfaces/bucket_strategy.py` SHALL NOT exist. The file `qsnap/modules/backup/bucket_strategy.py` SHALL NOT exist.

#### Scenario: Factory has no bucket full strategy method
- **WHEN** `DefaultFactory` is inspected
- **THEN** it does NOT have a `create_bucket_full_strategy` method
- **AND** it does NOT import `BucketFullStrategy` or `IBucketFullStrategy`
