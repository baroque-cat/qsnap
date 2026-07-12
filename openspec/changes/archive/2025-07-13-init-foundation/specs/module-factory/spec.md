## ADDED Requirements

### Requirement: IVMModuleFactory ABC
The system SHALL provide an `IVMModuleFactory` ABC with factory methods for creating every domain module type. Each method accepts the config dataclass relevant to that module type.

#### Scenario: IVMModuleFactory defines all creation methods
- **WHEN** IVMModuleFactory is inspected
- **THEN** it has abstract methods: `create_snapshot_provider`, `create_backup_provider`, `create_retention_engine`, `create_change_detector`, `create_lifecycle_manager`

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

### Requirement: Unimplemented factory methods raise NotImplementedError
When a module does not yet exist, the corresponding `create_*` method SHALL raise `NotImplementedError` with a clear message indicating which module is pending.

#### Scenario: Calling create_lifecycle_manager before it exists
- **WHEN** `factory.create_lifecycle_manager()` is called but no LifecycleManager module exists yet
- **THEN** NotImplementedError is raised with message "LifecycleManager not yet implemented"
