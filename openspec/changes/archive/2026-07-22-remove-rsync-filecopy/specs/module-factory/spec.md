## REMOVED Requirements

### Requirement: DefaultFactory gates BitmapBackupProvider on libvirt version
**Reason**: The fallback to `FileCopyBackupProvider` is deleted together with the provider. There is a single backup provider; insufficient platform dependencies are hard errors, not mode switches.
**Migration**: Replaced by the hard-gate requirement below. Hosts must run libvirt >= 7.2 and have `python3-libnbd` installed.

## ADDED Requirements

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
