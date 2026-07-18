## ADDED Requirements

### Requirement: BitmapBackupProvider domjobabort after NBD incremental transfer

`BitmapBackupProvider.transfer_missing()` SHALL call `virsh domjobabort --domain <vm_name>` in its `finally` block before socket cleanup, mirroring the pattern already implemented in `qsnap/utils/nbd.py:nbd_full_export()`. The abort SHALL use a 30-second timeout. On abort failure, a WARNING SHALL be logged but the error SHALL NOT be propagated (the abort is best-effort — the backup job may have already terminated).

#### Scenario: Domjobabort called after successful transfer
- **WHEN** `BitmapBackupProvider.transfer_missing()` completes a successful `qemu-img convert`
- **THEN** `virsh domjobabort --domain <vm_name>` is called in the `finally` block
- **AND** the NBD socket is removed after the abort

#### Scenario: Domjobabort called after failed transfer
- **WHEN** `BitmapBackupProvider.transfer_missing()` encounters a `qemu-img convert` failure
- **THEN** `virsh domjobabort --domain <vm_name>` is still called in the `finally` block
- **AND** the NBD socket is removed after the abort

#### Scenario: Domjobabort failure is non-fatal
- **WHEN** `virsh domjobabort` returns a non-zero exit code
- **THEN** a WARNING is logged with the error message
- **AND** execution continues to socket cleanup

### Requirement: BitmapBackupProvider accepts IStateManager

`BitmapBackupProvider.__init__()` SHALL accept an optional `state: IStateManager | None = None` parameter, mirroring `FileCopyBackupProvider.__init__()`. When `state` is not `None`, `create_full_backup()` SHALL call `self._state.record_full_backup(target_path, full_name, timestamp, bucket_level)` after successful FULL creation and atomic rename.

#### Scenario: Constructor accepts IStateManager
- **WHEN** `BitmapBackupProvider(shell=mock_shell, state=mock_state)` is instantiated
- **THEN** `isinstance(provider, IBackupProvider)` is True
- **AND** the provider stores the state reference

#### Scenario: Constructor works without IStateManager
- **WHEN** `BitmapBackupProvider(shell=mock_shell)` is instantiated (no state argument)
- **THEN** `isinstance(provider, IBackupProvider)` is True
- **AND** `self._state` is `None`

#### Scenario: create_full_backup records FULL in state
- **WHEN** `BitmapBackupProvider.create_full_backup(...)` succeeds and `self._state` is not `None`
- **THEN** `self._state.record_full_backup(target_path, full_name, timestamp, bucket_level)` is called
- **AND** the FULL is recorded before the method returns `BackupResult(success=True)`

#### Scenario: create_full_backup skips state recording when state is None
- **WHEN** `BitmapBackupProvider.create_full_backup(...)` succeeds and `self._state` is `None`
- **THEN** no error is raised
- **AND** the method returns `BackupResult(success=True)` without recording in state

### Requirement: Factory passes IStateManager to BitmapBackupProvider

`DefaultFactory.create_backup_provider(vm_config, target)` SHALL pass `self._state` as the `state` parameter when constructing `BitmapBackupProvider`, identical to how it is passed to `FileCopyBackupProvider`.

#### Scenario: Factory constructs BitmapBackupProvider with state
- **WHEN** `target.incremental_mode == "bitmap"` and factory has `self._state`
- **THEN** `BitmapBackupProvider(shell=self._shell, state=self._state)` is returned

## MODIFIED Requirements

### Requirement: Rebase error handling in FileCopyBackupProvider

`FileCopyBackupProvider.transfer_missing()` SHALL return `BackupResult(success=False, error=<message>)` when `qemu-img rebase -u` fails. Before returning the failure result, the method SHALL emit a `logger.warning` with the snapshot name and the rebase error message. The system SHALL NOT silently swallow the error.

#### Scenario: Rebase fails due to invalid backing path
- **WHEN** `qemu-img rebase -u -b /nonexistent/base.qcow2 /target/snap.qcow2` returns non-zero
- **THEN** the backup for that snapshot is marked `success=False` with the rebase error message
- **AND** a WARNING is logged: `"rebase to FULL failed for <snapshot>: <error>"`

### Requirement: FileCopyBackupProvider verify_backup failure logging

`FileCopyBackupProvider.transfer_missing()` SHALL emit `logger.warning` before returning `BackupResult(success=False)` in the `verify_backup()` failure path (verification detected mismatch) and the JSON decode failure path (backing info parse failure). The warning SHALL include the snapshot name and the specific error.

#### Scenario: Verification failure logged
- **WHEN** `verify_backup()` returns an error string
- **THEN** a WARNING is logged: `"backup verification failed for <snapshot>: <error>"`
- **AND** `BackupResult(success=False)` is returned

#### Scenario: JSON decode failure logged
- **WHEN** `qemu-img info --backing-chain` JSON parsing fails
- **THEN** a WARNING is logged: `"backing info parse failed for <snapshot>: <error>"`
- **AND** `BackupResult(success=False)` is returned

### Requirement: FileCopyBackupProvider rsync failure logging

`FileCopyBackupProvider.transfer_missing()` SHALL emit `logger.warning` before returning `BackupResult(success=False)` in the rsync failure path. The warning SHALL include the snapshot name and the rsync error message.

#### Scenario: Rsync failure logged
- **WHEN** `rsync` returns a non-zero exit code
- **THEN** a WARNING is logged: `"rsync failed for <snapshot>: <error>"`
- **AND** `BackupResult(success=False)` is returned
