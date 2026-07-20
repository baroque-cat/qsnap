## MODIFIED Requirements

### Requirement: Transfer missing snapshots to backup target

The system SHALL copy snapshots missing from the target storage into the `target.path` directory using `rsync` exclusively. Before copying, the system SHALL determine which snapshots already exist on the target (via `list()`). For incremental backups (`target.incremental == True`) the system SHALL execute `qemu-img rebase -u -B <new_backing_path> -F qcow2 <target_file>` to rebuild the backing file path on the target. The `-B` flag (backing-format) SHALL be used instead of the deprecated `-F` flag (renamed in QEMU 11.0).

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

#### Scenario: Incremental backup — rebase backing path with -B flag
- **WHEN** `target.incremental == True`
- **AND** the copied snapshot has a backing file
- **THEN** after copying, `qemu-img rebase -u -B <new_relative_path> -F qcow2 <target_file>` is executed
- **AND** `<new_relative_path>` is the backing file name (without path) in the same target directory
- **AND** the `-B` flag is used for backing-format (NOT the deprecated `-F`)

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

### Requirement: BitmapBackupProvider.create_full_backup implemented via NBD

`BitmapBackupProvider` SHALL override `create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, compression_type: str = "zstd", bucket_level: str = "monthly") -> BackupResult` to create a standalone FULL backup via the NBD full-export path. The `compression_type` parameter SHALL be passed through to `nbd_full_export()`. The method SHALL NOT raise `NotImplementedError`. The result SHALL be a standalone qcow2 file on the target. No checkpoint SHALL be created for this FULL — the checkpoint lifecycle remains in `transfer_missing()` for incremental runs.

The method SHALL NOT call `self._state.record_full_backup()` — state recording is Core's responsibility after post-create verification passes. This matches `FileCopyBackupProvider.create_full_backup()` behavior, which also does not self-record.

#### Scenario: Bitmap FULL with zstd compression
- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", bucket_level="monthly")` is called
- **THEN** `nbd_full_export(shell, "myvm", target_file, compress=True, compression_type="zstd")` is called
- **AND** the resulting FULL is compressed with zstd

#### Scenario: Bitmap FULL with zlib compression
- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zlib", bucket_level="monthly")` is called
- **THEN** `nbd_full_export(shell, "myvm", target_file, compress=True, compression_type="zlib")` is called
- **AND** the resulting FULL is compressed with zlib

#### Scenario: Bitmap FULL no longer raises NotImplementedError
- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** the method does NOT raise `NotImplementedError`
- **AND** `virsh backup-begin` is called without any `--incremental` CLI flag
- **AND** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2

#### Scenario: Bitmap FULL does not create checkpoint
- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully
- **THEN** no `virsh checkpoint-create-as` is called
- **AND** no `virsh checkpoint-delete` is called

#### Scenario: Bitmap FULL does not self-record in state
- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully
- **THEN** `self._state.record_full_backup()` is NOT called by the provider
- **AND** state recording is deferred to Core's `_backup_target()` after post-create verification

#### Scenario: Bucket-driven FULL works for bitmap targets
- **WHEN** `Core._backup_target()` calls `_should_create_bucket_full()` for a bitmap-mode target
- **AND** it returns `(True, "weekly")`
- **THEN** `BitmapBackupProvider.create_full_backup(vm_config.name, ...)` is called with the full VM name
- **AND** it succeeds (no crash)
- **AND** the FULL is recorded in state by Core (not by the provider) with `bucket_level="weekly"`

#### Scenario: Bitmap FULL with dotted VM name
- **WHEN** `BitmapBackupProvider.create_full_backup("3.Projects_opencode", snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** `nbd_full_export(shell, "3.Projects_opencode", ...)` is called with the full VM name
- **AND** the FULL backup file is named `3.Projects_opencode.FULL.YYYYMMDD.qcow2`
