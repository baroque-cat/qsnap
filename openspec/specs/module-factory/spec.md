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

### Requirement: DefaultFactory returns BitmapBackupProvider with hard dependency gates

`DefaultFactory.create_backup_provider()` SHALL always return a `BitmapBackupProvider` — it is the sole backup provider. Before constructing it, the factory SHALL verify: (a) `is_libvirt_new_enough(shell)` returns `True` (libvirt >= 7.2); if the version is insufficient, the factory SHALL raise `RuntimeError` with an actionable message requiring a libvirt upgrade — there SHALL be no fallback to any other provider; (b) `is_libnbd_available()` returns `True`; if libnbd is missing, the factory SHALL raise `RuntimeError` naming the `python3-libnbd` system package. The factory SHALL pass `self._state` as the `state` parameter to the provider constructor.

#### Scenario: Sufficient platform returns BitmapBackupProvider

- **WHEN** `create_backup_provider(vm_config, target)` is called
- **AND** `is_libvirt_new_enough(shell)` returns `True` and the `nbd` module is importable
- **THEN** the factory returns `BitmapBackupProvider(shell, state)`

#### Scenario: Old libvirt is a hard error

- **WHEN** `is_libvirt_new_enough(shell)` returns `False` (libvirt < 7.2)
- **THEN** the factory raises `RuntimeError` with a message requiring libvirt >= 7.2
- **AND** no provider is returned and no fallback occurs

#### Scenario: Missing libnbd is a hard error

- **WHEN** the `nbd` module is not importable
- **THEN** the factory raises `RuntimeError` naming the `python3-libnbd` package
- **AND** no provider is returned and no fallback occurs
