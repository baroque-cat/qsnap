## MODIFIED Requirements

### Requirement: Libvirt version check for NBD API

`DefaultFactory.create_backup_provider()` SHALL call `is_libvirt_new_enough(shell)` from `qsnap.utils.nbd` before constructing `BitmapBackupProvider`. If the version is insufficient, the factory SHALL log a WARNING and return `FileCopyBackupProvider`. `BitmapBackupProvider.__init__()` SHALL NOT perform version checking — it SHALL NOT call `virsh --version` and SHALL NOT raise `RuntimeError` for an old libvirt.

#### Scenario: Libvirt too old
- **WHEN** `virsh --version` returns a version older than 6.0
- **THEN** `is_libvirt_new_enough(shell)` returns `False`
- **THEN** `DefaultFactory` does NOT construct `BitmapBackupProvider`
- **THEN** `DefaultFactory` logs a WARNING and returns `FileCopyBackupProvider(shell, state)`

#### Scenario: Libvirt sufficient
- **WHEN** `virsh --version` returns a version 6.0 or newer
- **THEN** `is_libvirt_new_enough(shell)` returns `True`
- **THEN** `DefaultFactory` constructs and returns `BitmapBackupProvider(shell)`

#### Scenario: BitmapBackupProvider constructor is version-check-free
- **WHEN** `BitmapBackupProvider(shell)` is instantiated
- **THEN** no `virsh --version` shell call is made in `__init__`
- **AND** no `RuntimeError` is raised for version reasons
- **AND** the only parameter is `shell: IShell`
