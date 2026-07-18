## Requirements

### Requirement: IVMModuleFactory ABC
The system SHALL provide an `IVMModuleFactory` ABC with factory methods for creating every domain module type. Each method accepts the config dataclass relevant to that module type.

#### Scenario: IVMModuleFactory defines all creation methods
- **WHEN** IVMModuleFactory is inspected
- **THEN** it has abstract methods: `create_snapshot_provider`, `create_backup_provider`, `create_retention_engine`, `create_change_detector`, `create_lifecycle_manager`, `create_bucket_full_strategy`

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

### Requirement: DefaultFactory gates BitmapBackupProvider on libvirt version

`DefaultFactory.create_backup_provider()` SHALL, when `target.incremental_mode == "bitmap"`, call `is_libvirt_new_enough(shell)` from `qsnap.utils.nbd` BEFORE constructing `BitmapBackupProvider`. If the version check returns `False`, the factory SHALL log a WARNING and return `FileCopyBackupProvider(shell, state)`. If `True`, the factory SHALL construct and return `BitmapBackupProvider(shell)`.

#### Scenario: Bitmap mode with old libvirt falls back to FileCopy
- **WHEN** `create_backup_provider(vm_config, target)` is called with `target.incremental_mode == "bitmap"`
- **AND** `is_libvirt_new_enough(shell)` returns `False`
- **THEN** the factory returns `FileCopyBackupProvider(shell, state)`
- **AND** a WARNING is logged

#### Scenario: Bitmap mode with sufficient libvirt returns BitmapBackupProvider
- **WHEN** `create_backup_provider(vm_config, target)` is called with `target.incremental_mode == "bitmap"`
- **AND** `is_libvirt_new_enough(shell)` returns `True`
- **THEN** the factory returns `BitmapBackupProvider(shell)`

#### Scenario: Non-bitmap mode bypasses version check
- **WHEN** `create_backup_provider(vm_config, target)` is called with `target.incremental_mode != "bitmap"`
- **THEN** no call to `is_libvirt_new_enough(shell)` is made
- **THEN** `FileCopyBackupProvider` is returned directly
