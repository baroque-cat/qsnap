# Backup Provider

## Purpose

Backup transfer to external storage via `rsync` with backing chain rebase (`qemu-img rebase -u`).
Copies missing snapshots to a target directory on a separate filesystem (e.g. XFS), maintaining incremental backup semantics.
Supports optional bandwidth control via `rate_limit` config field using `rsync --bwlimit`.

## Requirements

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

### Requirement: List existing backups on target

The system SHALL scan the `target.path` directory for `.qcow2` files. For each file the system SHALL obtain metadata via `qemu-img info --output=json` and produce a `SnapshotInfo` with name, path, timestamp (from filename or mtime), and allocation.

#### Scenario: Target directory exists with backups

- **WHEN** `target.path` contains files `vm.20250101T000000.qcow2` and `vm.20250102T000000.qcow2`
- **THEN** `list()` returns a list of 2 `SnapshotInfo`, sorted by timestamp

#### Scenario: Target directory does not exist

- **WHEN** `target.path` does not exist
- **THEN** `list()` returns an empty list
- **AND** no shell commands are executed

#### Scenario: Target directory exists but is empty

- **WHEN** `target.path` exists but contains no `.qcow2` files
- **THEN** `list()` returns an empty list

### Requirement: Delete backup from target

The system SHALL delete a backup file via `rm -f`. The method accepts a `SnapshotInfo` and returns a `ShellResult`.

#### Scenario: Successful backup deletion

- **WHEN** `rm -f <backup.path>` completes successfully
- **THEN** the module returns `ShellResult(success=True)`

#### Scenario: Backup file does not exist

- **WHEN** the backup file does not exist
- **THEN** `rm -f` returns success
- **AND** the module returns `ShellResult(success=True)`

### Requirement: BitmapBackupProvider implements IBackupProvider
The system SHALL provide a `BitmapBackupProvider` class in `qsnap/modules/backup/bitmap.py` that implements `IBackupProvider`. It SHALL accept `IShell` as its sole constructor dependency. It SHALL use the `virsh backup-begin` NBD pull-model API.

#### Scenario: Constructor accepts IShell
- **WHEN** `BitmapBackupProvider(shell=mock_shell)` is instantiated
- **THEN** `isinstance(provider, IBackupProvider)` is True
- **THEN** the provider is ready for transfer operations

### Requirement: Transfer missing snapshots via dirty bitmap extraction
The system SHALL determine which snapshots are missing on the target and for each SHALL use `virsh backup-begin` with NBD export to transfer data. On first backup (no prior checkpoint), a full export is performed. On subsequent backups, only dirty blocks since the last checkpoint are exported.

#### Scenario: First backup — full NBD export (no prior checkpoint)
- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `BitmapBackupProvider` performs a full NBD export
- **THEN** the backup is a standalone qcow2 file on the target containing the complete virtual disk

#### Scenario: Incremental backup — dirty blocks only
- **WHEN** a prior qsnap checkpoint exists for this VM+target
- **AND** the VM has written data since that checkpoint
- **THEN** `virsh backup-begin` exports only changed blocks via NBD
- **THEN** the resulting backup file size is proportional to the changed data, not the full disk

#### Scenario: Checkpoint cleanup after successful transfer
- **WHEN** `qemu-img convert` completes successfully
- **THEN** the prior checkpoint is deleted via `virsh checkpoint-delete --metadata`
- **THEN** a new checkpoint is created for the next incremental run

#### Scenario: Transfer failure preserves checkpoint
- **WHEN** `qemu-img convert` from NBD fails
- **THEN** the checkpoint is NOT deleted
- **THEN** the module returns `BackupResult(success=False, error=<stderr>)`
- **THEN** the NBD socket is cleaned up via `rm -f`

### Requirement: Rebase error handling in FileCopyBackupProvider
`FileCopyBackupProvider.transfer_missing()` SHALL return `BackupResult(success=False, error=<message>)` when `qemu-img rebase -u` fails. It SHALL NOT silently swallow the error.

#### Scenario: Rebase fails due to invalid backing path
- **WHEN** `qemu-img rebase -u -b /nonexistent/base.qcow2 /target/snap.qcow2` returns non-zero
- **THEN** the backup for that snapshot is marked `success=False` with the rebase error message

### Requirement: List checkpoints for target
`BitmapBackupProvider` SHALL provide a method `list_checkpoints(vm_name: str) -> list[str]` that discovers existing qsnap-owned checkpoints via `virsh checkpoint-list --name`. Only checkpoints with the `qsnap-` prefix SHALL be returned.

#### Scenario: Existing qsnap checkpoints found
- **WHEN** `virsh checkpoint-list --name VM` returns `["qsnap-target1-20250101", "manual-checkpoint", "qsnap-target1-20250102"]`
- **THEN** `list_checkpoints("VM")` returns `["qsnap-target1-20250101", "qsnap-target1-20250102"]`

### Requirement: Factory selects BitmapBackupProvider for bitmap mode
`DefaultFactory.create_backup_provider(vm_config, target)` SHALL return `BitmapBackupProvider` when `target.incremental_mode == "bitmap"`. It SHALL return `FileCopyBackupProvider` when `target.incremental_mode == "file-copy"` (default). On `BitmapBackupProvider` construction failure (QEMU < 5.1), it SHALL log a warning and fall back to `FileCopyBackupProvider`.

#### Scenario: Bitmap mode selected via TargetConfig
- **WHEN** a target has `incremental_mode = "bitmap"`
- **THEN** `factory.create_backup_provider(vm_config, target)` returns a `BitmapBackupProvider` instance

#### Scenario: File-copy mode is the default
- **WHEN** a target has `incremental_mode` unset or set to `"file-copy"`
- **THEN** `factory.create_backup_provider(vm_config, target)` returns a `FileCopyBackupProvider` instance

### Requirement: NBD pull-model backup via virsh backup-begin
`BitmapBackupProvider` v2 SHALL use the libvirt pull-model backup API: (1) create backup XML with NBD Unix socket at `/tmp/qsnap-backup-{pid}.sock`, (2) `virsh backup-begin --domain VM backup.xml` to start NBD export, (3) `qemu-img convert -n nbd:unix:<socket> <target>` to pull dirty blocks, (4) remove socket. Checkpoints SHALL persist for subsequent incremental runs. This replaces the previous `qemu-img convert --bitmap` direct-access approach.

#### Scenario: First backup — full pull via NBD
- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `virsh backup-begin` starts a full NBD export
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2 file

#### Scenario: Incremental backup — dirty blocks via NBD
- **WHEN** a prior checkpoint exists and VM has written data since that checkpoint
- **THEN** `virsh backup-begin` exports only blocks changed since the checkpoint
- **THEN** the resulting backup file is smaller than a full copy

### Requirement: Libvirt version check in BitmapBackupProvider
`BitmapBackupProvider.__init__()` SHALL verify libvirt >= 6.0 (required for `backup-begin`). If version is older, SHALL raise `RuntimeError`. `DefaultFactory.create_backup_provider()` SHALL catch `RuntimeError` and fall back to `FileCopyBackupProvider`.

#### Scenario: Libvirt too old — factory fallback
- **WHEN** libvirt version is 5.0 and `target.incremental_mode == "bitmap"`
- **THEN** `BitmapBackupProvider()` raises `RuntimeError`
- **THEN** `DefaultFactory` catches it, logs a warning, and returns `FileCopyBackupProvider`

### Requirement: Backup verification step
`FileCopyBackupProvider.transfer_missing()` and `BitmapBackupProvider.transfer_missing()` SHALL perform post-transfer verification according to `target.verify`. `"metadata"`: verify `qemu-img info` output (format, virtual-size, actual-size tolerance). `"full"`: additionally run `qemu-img compare`. Both failure cases SHALL produce `BackupResult(success=False, error="verification failed: ...")`.

#### Scenario: Metadata verification passes
- **WHEN** target.verify is "metadata" and qemu-img info shows matching format and size
- **THEN** backup is marked success

#### Scenario: Verification failure produces error
- **WHEN** verification detects wrong format or size mismatch
- **THEN** `BackupResult(success=False, error="verification failed: ...")` is returned

### Requirement: FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target

`FileCopyBackupProvider.create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, bucket_level: str = "monthly") -> BackupResult` SHALL create a standalone qcow2 on the target. The `vm_name` parameter SHALL be the full, untruncated VM name (e.g. `3.Projects_opencode`), passed from Core's `vm_config.name` — the method SHALL NOT extract the VM name from the snapshot filename. The method SHALL detect VM running state via `virsh dominfo --domain <vm_name>`. When the VM is running, the method SHALL use the NBD pull-model (`virsh backup-begin` without `--incremental` + `qemu-img convert -n nbd:unix:<socket>`) to avoid lock conflicts on the active layer. When the VM is stopped, the method SHALL use direct `qemu-img convert [-c] -f qcow2 -O qcow2 <source> <target_path>/<vm_name>.FULL.YYYYMMDD.qcow2`. When `compress=True`, the `-c` flag SHALL be added to BOTH the NBD path and the direct convert path. The `bucket_level` parameter SHALL be passed to `IStateManager.record_full_backup()`. The operation SHALL be atomic: convert to a `.tmp` path, then rename to the final name on success.

#### Scenario: Uncompressed full backup succeeds (stopped VM)
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=False, bucket_level="monthly")` is called
- **AND** `virsh dominfo --domain myvm` returns `State: shut off`
- **THEN** `qemu-img convert` is invoked WITHOUT `-c` and `BackupResult(success=True)` is returned
- **AND** the FULL is recorded in state with `bucket_level="monthly"`

#### Scenario: Compressed full backup succeeds (stopped VM)
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, bucket_level="yearly")` is called
- **AND** `virsh dominfo --domain myvm` returns `State: shut off`
- **THEN** `qemu-img convert -c` is invoked
- **AND** the FULL is recorded in state with `bucket_level="yearly"`

#### Scenario: NBD full backup succeeds (running VM)
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=False, bucket_level="weekly")` is called
- **AND** `virsh dominfo --domain myvm` returns `State: running`
- **THEN** `virsh backup-begin` is called without `--incremental`
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` is called
- **AND** the FULL is recorded in state with `bucket_level="weekly"`
- **AND** no `--force-share` is used on any data-copying operation

#### Scenario: NBD full backup supports compression
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, bucket_level="daily")` is called
- **AND** the VM is running (NBD path selected)
- **THEN** `qemu-img convert -c nbd:unix:<socket> <target>` is called with the `-c` flag
- **AND** the resulting FULL is compressed (NBD path supports `-c`, experimentally verified with qemu-img 11.0.2)

#### Scenario: Dotted VM name is passed untruncated to virsh dominfo
- **WHEN** `create_full_backup("3.Projects_opencode", snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** `virsh dominfo --domain 3.Projects_opencode` is called with the full VM name
- **AND** the FULL backup file is named `3.Projects_opencode.FULL.YYYYMMDD.qcow2`
- **AND** the VM name is NOT extracted from the snapshot filename via `split(".")`

#### Scenario: transfer_missing passes vm_config.name to create_full_backup
- **WHEN** `transfer_missing(vm_config, target, snapshots)` is called with `vm_config.name = "3.Projects_opencode"`
- **AND** `target.copy_base` is `False` and the target is empty
- **THEN** `self.create_full_backup(vm_config.name, ...)` is called with the full VM name
- **AND** the FULL backup file is named `3.Projects_opencode.FULL.YYYYMMDD.qcow2`

### Requirement: Backup providers remain retry-unaware
Backup providers (`FileCopyBackupProvider`, `BitmapBackupProvider`) SHALL NOT implement any retry logic internally. They SHALL continue to return `BackupResult(success=False, error=...)` for any failure. The retry logic SHALL be handled by Core's `_backup_target()` method, which wraps the provider's `transfer_missing()` call.

#### Scenario: Provider returns error, Core handles retry
- **WHEN** `FileCopyBackupProvider.transfer_missing()` returns `BackupResult(success=False, error="Connection refused")`
- **THEN** the provider itself does not retry
- **AND** Core's retry wrapper inspects the error and decides whether to retry

#### Scenario: BackupResult error is structured for retry detection
- **WHEN** a backup transfer fails
- **THEN** the `BackupResult.error` string contains the underlying error from `ShellResult.error`
- **AND** Core's retry logic can pattern-match against it to determine retryability

### Requirement: BitmapBackupProvider.create_full_backup implemented via NBD

`BitmapBackupProvider` SHALL override `create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, bucket_level: str = "monthly") -> BackupResult` to create a standalone FULL backup via the NBD full-export path (no `--incremental` flag). The `vm_name` parameter SHALL be the full, untruncated VM name, passed from Core's `vm_config.name` — the method SHALL NOT extract the VM name from the snapshot filename. The method SHALL NOT raise `NotImplementedError`. The result SHALL be a standalone qcow2 file on the target. No checkpoint SHALL be created for this FULL — the checkpoint lifecycle remains in `transfer_missing()` for incremental runs.

#### Scenario: Bitmap FULL no longer raises NotImplementedError
- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** the method does NOT raise `NotImplementedError`
- **AND** `virsh backup-begin` is called without `--incremental`
- **AND** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2

#### Scenario: Bitmap FULL does not create checkpoint
- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully
- **THEN** no `virsh checkpoint-create-as` is called
- **AND** no `virsh checkpoint-delete` is called
- **AND** the FULL is recorded in state via `IStateManager.record_full_backup()`

#### Scenario: Bucket-driven FULL works for bitmap targets
- **WHEN** `Core._backup_target()` calls `_should_create_bucket_full()` for a bitmap-mode target
- **AND** it returns `(True, "weekly")`
- **THEN** `BitmapBackupProvider.create_full_backup(vm_config.name, ...)` is called with the full VM name
- **AND** it succeeds (no crash)
- **AND** the FULL is recorded in state with `bucket_level="weekly"`

#### Scenario: Bitmap FULL with dotted VM name
- **WHEN** `BitmapBackupProvider.create_full_backup("3.Projects_opencode", snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** `nbd_full_export(shell, "3.Projects_opencode", ...)` is called with the full VM name
- **AND** the FULL backup file is named `3.Projects_opencode.FULL.YYYYMMDD.qcow2`

### Requirement: transfer_missing SHALL NOT create FULL backups

`FileCopyBackupProvider.transfer_missing()` SHALL NOT call `create_full_backup()` under any circumstances. FULL backup creation is the sole responsibility of `Core._backup_target()` via the bucket-driven mechanism, ensuring every FULL passes through Core's verification pipeline before state recording.

### Requirement: Snapshot file existence guard before rsync

Before calling `rsync`, `transfer_missing()` SHALL verify the source snapshot file exists on disk. If missing (stale — blockcommitted but not cleaned from state), the entry SHALL be removed from state and the snapshot SHALL be skipped.
