## MODIFIED Requirements

### Requirement: Immediate deletion of failed backup files after verification failure

When a definitive per-snapshot failure occurs in `BitmapBackupProvider.transfer_missing()` (temporal mismatch, backup-begin failure, transfer error, verification error, chain-to-FULL not traversable, or checkpoint missing), the provider SHALL delete the partially-transferred target file via `self._shell.run(["rm", "-f", str(target_file)], timeout=10)` where applicable, append `BackupResult(success=False, disk=snapshot.disk)` with the error, and `break` out of the snapshot loop — no further snapshots are attempted in this batch. Skip paths (backup already exists on target, stale-state entry) still `continue`. The provider returns the partial results list; Core decides the abort (`BackupAbortError`, spec: `core-orchestrator` VM-level failure isolation). Immediate deletion prevents the failed file from being discovered by retention cleanup (which lists `*.qcow2` files and would delete it with a misleading `[delete] removed backup` log message).

#### Scenario: Failed backup file deleted immediately after verification failure

- **WHEN** verification returns an error string for a snapshot transfer
- **THEN** a WARNING is logged: "backup verification failed for <snapshot>: <error>"
- **AND** `rm -f <target_file>` is executed via `IShell.run()` with a 10-second timeout
- **AND** `BackupResult(success=False, error=<verify_error>, disk=<disk>)` is appended to results
- **AND** the loop `break`s — remaining snapshots in the batch are not attempted
- **AND** the target file does NOT exist on disk after this step

#### Scenario: Failed backup file not found by retention cleanup

- **WHEN** verification fails and the file is deleted immediately
- **AND** retention cleanup runs `provider.list(target)` via `glob("*.qcow2")`
- **THEN** the failed file is NOT in the list of backups
- **AND** no `[delete] removed backup` log is emitted for the failed file

#### Scenario: Bitmap NBD convert failure does not leave partial file

- **WHEN** `qemu-img convert` from NBD fails in `BitmapBackupProvider.transfer_missing()`
- **THEN** the partial target file SHALL be deleted via `rm -f` before appending `BackupResult(success=False)`
- **AND** the loop `break`s (no further snapshots attempted)
- **AND** the NBD socket is cleaned up in the `finally` block

#### Scenario: Skip paths do not break the loop

- **WHEN** a snapshot already has a backup on the target, or its source file is gone (stale state)
- **THEN** the loop `continue`s to the next snapshot
- **AND** no `BackupResult(success=False)` is appended for these cases
