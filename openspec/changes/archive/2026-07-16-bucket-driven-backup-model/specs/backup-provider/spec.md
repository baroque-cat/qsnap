## MODIFIED Requirements

### Requirement: Transfer missing snapshots to backup target

The system SHALL copy snapshots missing from the target storage into the `target.path` directory using `rsync` exclusively. Before copying, the system SHALL determine which snapshots already exist on the target (via `list()`). For incremental backups (`target.incremental == True`) the system SHALL execute `qemu-img rebase -u -b <new_backing_path>` to rebuild the backing file path on the target.

When `rate_limit` is set to a value other than `"no"`, the system SHALL use `rsync --bwlimit=<limit_kib> --partial <source> <target>`. When `rate_limit` is `"no"`, the system SHALL use `rsync --partial <source> <target>`. The system SHALL NOT use `cp` under any circumstances. If `rsync` is not available in PATH, the transfer SHALL fail with a `BackupResult(success=False, error="rsync not found")`.

When `target.copy_base` is `False` (default), the system SHALL NOT copy `base.qcow2` to the target. The first backup to a target SHALL be a FULL backup via `create_full_backup()`. When `target.copy_base` is `True`, the system MAY copy `base.qcow2` to the target (legacy behavior).

#### Scenario: New snapshot copied to empty target via rsync
- **WHEN** target is empty (list() returns [])
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

#### Scenario: Incremental backup — rebase backing path
- **WHEN** `target.incremental == True`
- **AND** the copied snapshot has a backing file
- **THEN** after copying, `qemu-img rebase -u -b <new_relative_path> <target_file>` is executed
- **AND** `<new_relative_path>` is the backing file name (without path) in the same target directory

#### Scenario: Rebase to FULL anchor when present
- **WHEN** target directory contains a FULL anchor file `vm.FULL.*.qcow2`
- **THEN** newly transferred incrementals are rebased to `./vm.FULL.YYYYMMDD.qcow2` instead of the source backing filename
- **AND** the dependency is recorded via `IStateManager.record_incremental_dependency()`

#### Scenario: No FULL anchor preserves existing behavior
- **WHEN** target directory has no `vm.FULL.*.qcow2` files
- **THEN** incremental rebase uses the source backing filename as before

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

#### Scenario: copy_base=false prevents base.qcow2 duplication
- **WHEN** `target.copy_base` is `False` (default)
- **THEN** `base.qcow2` is never copied to the target
- **AND** the first backup to the target is a FULL via `qemu-img convert`

#### Scenario: copy_base=true allows legacy base copy
- **WHEN** `target.copy_base` is `True`
- **THEN** `base.qcow2` MAY be copied to the target (legacy behavior)

### Requirement: FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target

`FileCopyBackupProvider.create_full_backup(source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, bucket_level: str = "monthly") -> BackupResult` SHALL run `qemu-img convert [-c] -f qcow2 -O qcow2 <source> <target_path>/vm.FULL.YYYYMMDD.qcow2`. When `compress=True`, the `-c` flag SHALL be added. The `bucket_level` parameter SHALL be passed to `IStateManager.record_full_backup()`. The result SHALL report success/failure and the created file path. The operation SHALL be atomic: convert to a `.tmp` path, then rename to the final name on success.

#### Scenario: Uncompressed full backup succeeds
- **WHEN** `create_full_backup(snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** `qemu-img convert` is invoked WITHOUT `-c` and `BackupResult(success=True)` is returned
- **AND** the FULL is recorded in state with `bucket_level="monthly"`

#### Scenario: Compressed full backup succeeds
- **WHEN** `create_full_backup(snapshot, target, compress=True, bucket_level="yearly")` is called
- **THEN** `qemu-img convert -c` is invoked
- **AND** the FULL is recorded in state with `bucket_level="yearly"`

## REMOVED Requirements

### Requirement: Fallback to cp when rsync unavailable with rate_limit set

**Reason**: `cp` fallback is removed. `rsync` is now the sole transfer mechanism and a hard requirement.
**Migration**: Install `rsync` on the system. The `_validate_environment()` check will abort the pipeline if `rsync` is not found.
