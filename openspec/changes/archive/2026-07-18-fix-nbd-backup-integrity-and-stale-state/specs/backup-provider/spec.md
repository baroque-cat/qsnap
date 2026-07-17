## MODIFIED Requirements

### Requirement: Transfer missing snapshots to backup target

The system SHALL copy snapshots missing from the target storage into the `target.path` directory using `rsync` exclusively. Before copying, the system SHALL determine which snapshots already exist on the target (via `list()`). `transfer_missing()` SHALL NOT create FULL backups — FULL creation is the responsibility of `Core._backup_target()` via the bucket-driven mechanism. For incremental backups (`target.incremental == True`) the system SHALL execute `qemu-img rebase -u -b <new_backing_path> -F qcow2` to rebuild the backing file path on the target. The `-F qcow2` flag SHALL be included to specify the backing file format, as required by QEMU 6.1+.

When `rate_limit` is set to a value other than `"no"`, the system SHALL use `rsync --bwlimit=<limit_kib> --partial <source> <target>`. When `rate_limit` is `"no"`, the system SHALL use `rsync --partial <source> <target>`. The system SHALL NOT use `cp` under any circumstances. If `rsync` is not available in PATH, the transfer SHALL fail with a `BackupResult(success=False, error="rsync not found")`.

When `target.copy_base` is `False` (default), the system SHALL NOT copy `base.qcow2` to the target. The first backup to a target SHALL be created by `Core._backup_target()` via the bucket-driven FULL mechanism; `transfer_missing()` SHALL NOT call `create_full_backup()`. When `target.copy_base` is `True`, the system MAY copy `base.qcow2` to the target (legacy behavior).

Before copying, the system SHALL verify each snapshot file exists on disk via `os.path.exists()`. If the file does not exist, the system SHALL call `IStateManager.remove_snapshot()` for that snapshot, log a WARNING, and skip the transfer. This prevents attempts to rsync snapshot files that were already blockcommitted but remain in state due to a prior bug.

#### Scenario: New snapshot copied to empty target via rsync
- **WHEN** target is empty (list() returns []) and there is at least one FULL recorded in state for this target
- **AND** there is one snapshot to copy
- **AND** `rate_limit` is `"no"` (default)
- **THEN** the snapshot is copied via `rsync --partial <source> <target>` to `target.path/<snapshot.name>.qcow2`
- **AND** `BackupResult(success=True, bytes_transferred=<file_size>)` is returned

#### Scenario: Transfer with rate limit uses rsync --bwlimit
- **WHEN** `rate_limit` is `"100M"`
- **AND** `transfer_missing()` is called for a snapshot
- **THEN** the shell executes `rsync --bwlimit=102400 --partial <source> <target>`

#### Scenario: Snapshot already exists on target — skipped
- **WHEN** target already contains a snapshot with the same name
- **THEN** that snapshot is NOT copied again
- **AND** it does not appear in the returned `BackupResult` list

#### Scenario: Incremental backup — rebase backing path with -F qcow2
- **WHEN** `target.incremental == True`
- **AND** the copied snapshot has a backing file
- **THEN** after copying, `qemu-img rebase -u -b <new_relative_path> -F qcow2 <target_file>` is executed
- **AND** `<new_relative_path>` is the backing file name (without path) in the same target directory

#### Scenario: Rebase to FULL anchor when present
- **WHEN** target directory contains a FULL anchor file `vm.FULL.*.qcow2`
- **AND** M1 verification of the FULL anchor passes
- **THEN** newly transferred incrementals are rebased to `./vm.FULL.YYYYMMDD.qcow2` using `-F qcow2`
- **AND** the dependency is recorded via `IStateManager.record_incremental_dependency()`

#### Scenario: No FULL anchor preserves existing behavior
- **WHEN** target directory has no `vm.FULL.*.qcow2` files
- **THEN** incremental rebase uses the source backing filename as before
- **AND** `-F qcow2` is included in the rebase command

#### Scenario: Non-incremental backup — no rebase
- **WHEN** `target.incremental == False`
- **THEN** the snapshot is copied without calling `qemu-img rebase`
- **AND** the backing path remains as-is (absolute source path)

#### Scenario: rsync unavailable — transfer fails
- **WHEN** `which rsync` returns non-zero
- **THEN** `BackupResult(success=False, error="rsync not found")` is returned
- **AND** no fallback to `cp` is attempted

#### Scenario: Copy fails — disk full or permission error
- **WHEN** `rsync` returns a non-zero exit code
- **THEN** the module returns `BackupResult(success=False, error=<stderr>)`

### Requirement: Rebase error handling in FileCopyBackupProvider
`FileCopyBackupProvider.transfer_missing()` SHALL return `BackupResult(success=False, error=<message>)` when `qemu-img rebase -u` fails. It SHALL NOT silently swallow the error.

#### Scenario: Rebase fails due to invalid backing path
- **WHEN** `qemu-img rebase -u -b /nonexistent/base.qcow2 -F qcow2 /target/snap.qcow2` returns non-zero
- **THEN** the backup for that snapshot is marked `success=False` with the rebase error message

#### Scenario: Rebase fails due to missing -F flag on QEMU 6.1+
- **WHEN** `qemu-img rebase -u -b ./FULL.qcow2 /target/snap.qcow2` is called WITHOUT `-F qcow2`
- **AND** QEMU version is 6.1 or higher
- **THEN** the rebase returns non-zero with "backing format must be specified"
- **AND** the fix ensures `-F qcow2` is always included in the command

## ADDED Requirements

### Requirement: Snapshot file existence guard before rsync
Before calling `rsync` to transfer a snapshot from state to target, `FileCopyBackupProvider.transfer_missing()` SHALL verify the source snapshot file exists on disk. If not, the snapshot entry is stale (blockcommitted but not cleaned from state). The entry SHALL be removed from state and the snapshot SHALL be skipped.

#### Scenario: Snapshot file exists — transfer proceeds
- **WHEN** `os.path.exists(snapshot.path)` returns True
- **THEN** rsync transfer proceeds normally

#### Scenario: Snapshot file does not exist — entry cleaned and skipped
- **WHEN** `os.path.exists(snapshot.path)` returns False
- **THEN** the snapshot is removed from state via `remove_snapshot()`
- **AND** a WARNING is logged
- **AND** the snapshot is skipped (no rsync attempted)

### Requirement: transfer_missing SHALL NOT create FULL backups
`FileCopyBackupProvider.transfer_missing()` SHALL NOT call `create_full_backup()` under any circumstances. FULL backup creation is the sole responsibility of `Core._backup_target()` via the bucket-driven mechanism. This ensures every FULL backup passes through the Core verification pipeline (post-create M1/M2/M3, pre-deletion M1/M2) before being recorded in state.

#### Scenario: Empty target without FULL — no FULL auto-created by transfer
- **WHEN** `transfer_missing()` is called for a snapshot on a target with no existing backups
- **AND** no FULLs are recorded in state for this target
- **THEN** `create_full_backup()` is NOT called
- **AND** the snapshot is transferred normally via rsync
- **AND** the rebase path still includes `-F qcow2`
