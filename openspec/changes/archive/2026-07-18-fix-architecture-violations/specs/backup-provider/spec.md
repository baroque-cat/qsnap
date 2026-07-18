## MODIFIED Requirements

### Requirement: Libvirt version check in BitmapBackupProvider

`DefaultFactory.create_backup_provider()` SHALL call `is_libvirt_new_enough()` from `qsnap.utils.nbd` before constructing `BitmapBackupProvider`. If the version is insufficient, the factory SHALL log a WARNING and return `FileCopyBackupProvider`. `BitmapBackupProvider.__init__()` SHALL NOT raise `RuntimeError` for any expected operational condition — it SHALL accept only `IShell` and trust that the factory only constructs it when appropriate.

#### Scenario: Libvirt too old — factory fallback
- **WHEN** libvirt version is 5.0 and `target.incremental_mode == "bitmap"`
- **THEN** `DefaultFactory` calls `is_libvirt_new_enough(shell)` which returns `False`
- **THEN** `DefaultFactory` logs a WARNING and returns `FileCopyBackupProvider(shell)`
- **AND** no `RuntimeError` is raised

#### Scenario: Libvirt sufficient — BitmapBackupProvider constructed
- **WHEN** libvirt version is 9.0 and `target.incremental_mode == "bitmap"`
- **THEN** `DefaultFactory` calls `is_libvirt_new_enough(shell)` which returns `True`
- **THEN** `DefaultFactory` constructs and returns `BitmapBackupProvider(shell)`

#### Scenario: BitmapBackupProvider constructor does not check version
- **WHEN** `BitmapBackupProvider(shell)` is constructed
- **THEN** no `virsh --version` call is made in the constructor
- **AND** no version-related `raise RuntimeError` exists in the constructor
