## MODIFIED Requirements

### Requirement: Transfer missing snapshots to backup target

The system SHALL copy snapshots missing from the target storage into the `target.path` directory. Before copying, the system SHALL determine which snapshots already exist on the target (via `list()`). For incremental backups (`target.incremental == True`) the system SHALL execute `qemu-img rebase -u -b <new_backing_path>` to rebuild the backing file path on the target.

When `rate_limit` is set to a value other than `"no"`, the system SHALL use `rsync --bwlimit=<limit_kib> --partial --progress` instead of `cp` for snapshot file transfers. When `rate_limit` is `"no"`, the system SHALL use `cp` as before. If `rate_limit` is set but `rsync` is unavailable, the system SHALL log a WARNING and fall back to `cp`.

#### Scenario: New snapshot copied to empty target

- **WHEN** target is empty (list() returns [])
- **AND** there is one snapshot to copy
- **AND** `rate_limit` is `"no"` (default)
- **THEN** the snapshot is copied (`cp`) to `target.path/<snapshot.name>.qcow2`
- **AND** `BackupResult(success=True, bytes_transferred=<file_size>)` is returned

#### Scenario: Transfer with rate limit uses rsync

- **WHEN** `rate_limit` is `"100M"`
- **AND** `transfer_missing()` is called for a snapshot
- **THEN** the shell executes `rsync --bwlimit=102400 --partial --progress <source> <target>`

#### Scenario: Snapshot already exists on target — skipped

- **WHEN** target already contains a snapshot with the same name
- **THEN** that snapshot is NOT copied again
- **AND** it does not appear in the returned `BackupResult` list

#### Scenario: Incremental backup — rebase backing path

- **WHEN** `target.incremental == True`
- **AND** the copied snapshot has a backing file
- **THEN** after copying, `qemu-img rebase -u -b <new_relative_path> <target_file>` is executed
- **AND** `<new_relative_path>` is the backing file name (without path) in the same target directory

#### Scenario: Rebase to FULL anchor when present

- **WHEN** target directory contains a FULL anchor file `vm.FULL.*.qcow2`
- **THEN** newly transferred incrementals are rebased to `./vm.FULL.YYYYMMDD.qcow2` instead of the source backing filename

#### Scenario: Fallback to cp when rsync unavailable with rate_limit set

- **WHEN** `rate_limit` is `"100M"` and `which rsync` returns non-zero
- **THEN** a WARNING is logged: "rsync not found — rate limiting disabled for target <path>"
- **AND** the transfer proceeds using `cp`
