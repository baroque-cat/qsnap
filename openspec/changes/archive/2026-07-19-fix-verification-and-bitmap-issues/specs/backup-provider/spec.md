## ADDED Requirements

### Requirement: Immediate deletion of failed backup files after verification failure

When `verify_backup()` returns a non-None error string in `FileCopyBackupProvider.transfer_missing()` or `BitmapBackupProvider.transfer_missing()`, the provider SHALL delete the partially-transferred target file via `self._shell.run(["rm", "-f", str(target_file)], timeout=10)` immediately after logging the WARNING, before appending `BackupResult(success=False)` and before `continue`. This prevents the failed file from being discovered by retention cleanup (which uses `glob("*.qcow2")` and would delete it with a misleading `[delete] removed backup` log message).

#### Scenario: Failed backup file deleted immediately after verification failure

- **WHEN** `verify_backup()` returns an error string for a snapshot transfer
- **THEN** a WARNING is logged: "backup verification failed for <snapshot>: <error>"
- **AND** `rm -f <target_file>` is executed via `IShell.run()` with a 10-second timeout
- **AND** `BackupResult(success=False, error=<verify_error>)` is appended to results
- **AND** the loop `continue`s to the next snapshot
- **AND** the target file does NOT exist on disk after this step

#### Scenario: Failed backup file not found by retention cleanup

- **WHEN** verification fails and the file is deleted immediately
- **AND** retention cleanup runs `provider.list(target)` via `glob("*.qcow2")`
- **THEN** the failed file is NOT in the list of backups
- **AND** no `[delete] removed backup` log is emitted for the failed file

#### Scenario: rsync failure does not leave partial file

- **WHEN** `rsync` returns a non-zero exit code
- **AND** `rsync --partial` left a partial file on the target
- **THEN** the partial file SHALL also be deleted via `rm -f` before appending `BackupResult(success=False)`
- **AND** the target file does NOT exist on disk after this step

#### Scenario: Bitmap NBD convert failure does not leave partial file

- **WHEN** `qemu-img convert` from NBD fails in `BitmapBackupProvider.transfer_missing()`
- **THEN** the partial target file SHALL be deleted via `rm -f` before appending `BackupResult(success=False)`
- **AND** the NBD socket is cleaned up in the `finally` block (existing behavior)

### Requirement: Compression for rsync incremental transfers

`FileCopyBackupProvider.transfer_missing()` SHALL add the `--compress` flag to the rsync command when `target.compress=True` (default). This provides transfer-level compression, reducing bandwidth for network targets. When `target.compress=False`, no `--compress` flag SHALL be added. The `--compress` flag compresses the transfer stream, not the file on disk — the target file is identical to the source after transfer.

#### Scenario: rsync with compression flag

- **WHEN** `transfer_missing()` is called with `target.compress=True` and `rate_limit="no"`
- **THEN** the rsync command SHALL be `rsync --compress --partial --progress <source> <target>`
- **AND** the target file on disk is a byte-for-byte copy of the source (compression is transfer-only)

#### Scenario: rsync with compression and rate limit

- **WHEN** `transfer_missing()` is called with `target.compress=True` and `rate_limit="100M"`
- **THEN** the rsync command SHALL be `rsync --bwlimit=<kib> --compress --partial --progress <source> <target>`

#### Scenario: rsync without compression

- **WHEN** `transfer_missing()` is called with `target.compress=False`
- **THEN** the rsync command SHALL NOT include `--compress`
- **AND** the command is `rsync --partial --progress <source> <target>` (existing behavior)

#### Scenario: Compression does not affect hash verification

- **WHEN** rsync transfers with `--compress` and `verify="hash"`
- **THEN** the target file's SHA-256 matches the source snapshot's `content_hash`
- **AND** hash verification passes (rsync `--compress` is transfer-level, file bytes are identical)
