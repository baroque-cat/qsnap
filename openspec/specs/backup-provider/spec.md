# Backup Provider

## Purpose

Backup transfer to external storage via `rsync` with backing chain rebase (`qemu-img rebase -u`).
Copies missing snapshots to a target directory on a separate filesystem (e.g. XFS), maintaining incremental backup semantics.
Supports optional bandwidth control via `rate_limit` config field using `rsync --bwlimit`.

## Requirements

### Requirement: Transfer missing snapshots to backup target

The system SHALL copy snapshots missing from the target storage into the `target.path` directory using `rsync` exclusively. Before copying, the system SHALL determine which snapshots already exist on the target (via `list()`). For incremental backups (`target.incremental == True`) the system SHALL execute `qemu-img rebase -u -b <new_backing_path> -B qcow2 <target_file>` to rebuild the backing file path on the target. The `-B` flag (backing-format) SHALL be used instead of the deprecated `-F` flag (renamed in QEMU 11.0).

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
- **THEN** after copying, `qemu-img rebase -u -b <new_relative_path> -B qcow2 <target_file>` is executed
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
The system SHALL provide a `BitmapBackupProvider` class in `qsnap/modules/backup/bitmap.py` that implements `IBackupProvider`. It SHALL accept `IShell` and an optional `state: IStateManager | None = None` as constructor parameters. It SHALL use the `virsh backup-begin` NBD pull-model API.

#### Scenario: Constructor accepts IShell
- **WHEN** `BitmapBackupProvider(shell=mock_shell)` is instantiated
- **THEN** `isinstance(provider, IBackupProvider)` is True
- **THEN** the provider is ready for transfer operations

### Requirement: Transfer missing snapshots via dirty bitmap extraction
The system SHALL determine which snapshots are missing on the target and for each SHALL use `virsh backup-begin` with NBD export to transfer data. On first backup (no prior checkpoint), a full export is performed. On subsequent backups, only dirty blocks since the last checkpoint are exported. Every `backup-begin` SHALL receive a checkpoint XML as its third positional argument so the successor checkpoint is created atomically at the export's freeze point (see the `nbd-bitmap-backup` capability). The `qemu-img convert` command SHALL include `-c -o compression_type=<type>` when `target.compress=True` and `compression_type` is passed from Core. The `qemu-img convert` command SHALL be executed via `IShell.run_with_stall_detection()` with `output_file` set to the target file path and `stall_timeout` from `target.backup_stall_timeout`.

#### Scenario: First backup — full NBD export (no prior checkpoint)
- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `BitmapBackupProvider` performs a full NBD export with an atomic successor checkpoint
- **THEN** the backup is a standalone qcow2 file on the target containing the complete virtual disk

#### Scenario: Incremental backup — dirty blocks only
- **WHEN** a prior qsnap checkpoint exists for this VM+target
- **AND** the VM has written data since that checkpoint
- **THEN** `virsh backup-begin` exports only changed blocks via NBD
- **THEN** the resulting backup file size is proportional to the changed data, not the full disk

#### Scenario: Checkpoint rotation after successful transfer
- **WHEN** `qemu-img convert` completes successfully and verification passes
- **THEN** the successor checkpoint created atomically with this export exists
- **THEN** all superseded (older) qsnap checkpoints are deleted via `virsh checkpoint-delete --metadata`
- **AND** exactly one qsnap checkpoint remains for this VM+target

#### Scenario: Transfer failure preserves prior checkpoint
- **WHEN** `qemu-img convert` from NBD fails
- **THEN** the prior checkpoint is NOT deleted
- **THEN** the successor checkpoint created by the failed run is deleted best-effort
- **THEN** the module returns `BackupResult(success=False, error=<stderr>)`
- **THEN** the NBD socket is cleaned up via `rm -f`

### Requirement: Rebase error handling in FileCopyBackupProvider
`FileCopyBackupProvider.transfer_missing()` SHALL return `BackupResult(success=False, error=<message>)` when `qemu-img rebase -u` fails. Before returning the failure result, the method SHALL emit a `logger.warning` with the snapshot name and the rebase error message. The system SHALL NOT silently swallow the error.

#### Scenario: Rebase fails due to invalid backing path
- **WHEN** `qemu-img rebase -u -b /nonexistent/base.qcow2 /target/snap.qcow2` returns non-zero
- **THEN** the backup for that snapshot is marked `success=False` with the rebase error message
- **AND** a WARNING is logged: `"rebase to FULL failed for <snapshot>: <error>"`

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
`BitmapBackupProvider` v2 SHALL use the libvirt pull-model backup API: (1) create backup XML with NBD Unix socket at `/tmp/qsnap-backup-{pid}.sock` and a checkpoint XML naming the successor checkpoint, (2) `virsh backup-begin --domain VM backup.xml checkpoint.xml` to start the NBD export and atomically create the successor checkpoint at the export's freeze point, (3) `qemu-img convert -n nbd:unix:<socket> <target>` to pull dirty blocks, (4) remove socket. Checkpoints SHALL persist for subsequent incremental runs. This replaces the previous `qemu-img convert --bitmap` direct-access approach.

#### Scenario: First backup — full pull via NBD
- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `virsh backup-begin` starts a full NBD export and creates the successor checkpoint atomically
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2 file

#### Scenario: Incremental backup — dirty blocks via NBD
- **WHEN** a prior checkpoint exists and VM has written data since that checkpoint
- **THEN** `virsh backup-begin` exports only blocks changed since the checkpoint
- **THEN** the resulting backup file is smaller than a full copy
- **AND** a new successor checkpoint is created atomically at this export's freeze point

### Requirement: Libvirt version check in BitmapBackupProvider

`DefaultFactory.create_backup_provider()` SHALL call `is_libvirt_new_enough()` from `qsnap.utils.nbd` before constructing `BitmapBackupProvider`. `is_libvirt_new_enough()` SHALL return `True` only for libvirt version 7.2 or newer (the incremental backup API, including the checkpoint XML argument of `backup-begin`, is complete since 7.2 per the libvirt knowledge base). If the version is insufficient, the factory SHALL log a WARNING and return `FileCopyBackupProvider`. `BitmapBackupProvider.__init__()` SHALL NOT raise `RuntimeError` for any expected operational condition — it SHALL accept `IShell` and an optional `IStateManager` and trust that the factory only constructs it when appropriate.

#### Scenario: Libvirt too old — factory fallback
- **WHEN** libvirt version is 7.1 (or older) and `target.incremental_mode == "bitmap"`
- **THEN** `DefaultFactory` calls `is_libvirt_new_enough(shell)` which returns `False`
- **THEN** `DefaultFactory` logs a WARNING and returns `FileCopyBackupProvider(shell)`
- **AND** no `RuntimeError` is raised

#### Scenario: Libvirt sufficient — BitmapBackupProvider constructed
- **WHEN** libvirt version is 9.0 and `target.incremental_mode == "bitmap"`
- **THEN** `DefaultFactory` calls `is_libvirt_new_enough(shell)` which returns `True`
- **THEN** `DefaultFactory` constructs and returns `BitmapBackupProvider(shell, state)`

#### Scenario: BitmapBackupProvider constructor does not check version
- **WHEN** `BitmapBackupProvider(shell)` is constructed
- **THEN** no `virsh --version` call is made in the constructor
- **AND** no version-related `raise RuntimeError` exists in the constructor

### Requirement: Backup verification step
`FileCopyBackupProvider.transfer_missing()` and `BitmapBackupProvider.transfer_missing()` SHALL perform post-transfer verification according to `target.verify`. `"metadata"`: verify `qemu-img info` output (format, virtual-size, actual-size tolerance). `"full"`: additionally run `qemu-img compare`. Both failure cases SHALL produce `BackupResult(success=False, error="verification failed: ...")`.

#### Scenario: Metadata verification passes
- **WHEN** target.verify is "metadata" and qemu-img info shows matching format and size
- **THEN** backup is marked success

#### Scenario: Verification failure produces error
- **WHEN** verification detects wrong format or size mismatch
- **THEN** `BackupResult(success=False, error="verification failed: ...")` is returned

### Requirement: FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target

`FileCopyBackupProvider.create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, compression_type: str = "zstd", bucket_level: str = "monthly") -> BackupResult` SHALL create a standalone qcow2 on the target. The `vm_name` parameter SHALL be the full, untruncated VM name (e.g. `3.Projects_opencode`), passed from Core's `vm_config.name` — the method SHALL NOT extract the VM name from the snapshot filename. The method SHALL detect VM running state via `virsh dominfo --domain <vm_name>`. When the VM is running, the method SHALL use the NBD pull-model (`virsh backup-begin` without `--incremental` + `qemu-img convert -n nbd:unix:<socket>`) to avoid lock conflicts on the active layer. When the VM is stopped, the method SHALL use direct `qemu-img convert [-c] [-o compression_type=<type>] -f qcow2 -O qcow2 <source> <target_path>/<vm_name>.FULL.YYYYMMDD.qcow2`. When `compress=True` and `compression_type="zstd"`, the `-c -o compression_type=zstd` flags SHALL be added to BOTH the NBD path and the direct convert path. When `compress=True` and `compression_type="zlib"`, only `-c` SHALL be added (default zlib). The `bucket_level` parameter SHALL be passed to `IStateManager.record_full_backup()`. The operation SHALL be atomic: convert to a `.tmp` path, then rename to the final name on success. The `qemu-img convert` command SHALL use `IShell.run_with_stall_detection()` with `output_file` set to the `.tmp` file path and `stall_timeout` from `target.backup_stall_timeout`.

#### Scenario: Uncompressed full backup succeeds (stopped VM)
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=False, compression_type="zstd", bucket_level="monthly")` is called
- **AND** `virsh dominfo --domain myvm` returns `State: shut off`
- **THEN** `qemu-img convert` is invoked WITHOUT `-c` and `BackupResult(success=True)` is returned
- **AND** the FULL is recorded in state with `bucket_level="monthly"`

#### Scenario: zstd compressed full backup succeeds (stopped VM)
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", bucket_level="yearly")` is called
- **AND** `virsh dominfo --domain myvm` returns `State: shut off`
- **THEN** `qemu-img convert -c -o compression_type=zstd` is invoked
- **AND** the FULL is recorded in state with `bucket_level="yearly"`

#### Scenario: zlib compressed full backup succeeds (stopped VM)
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zlib", bucket_level="yearly")` is called
- **AND** `virsh dominfo --domain myvm` returns `State: shut off`
- **THEN** `qemu-img convert -c` is invoked (default zlib compression)
- **AND** the FULL is recorded in state with `bucket_level="yearly"`

#### Scenario: NBD full backup with zstd compression (running VM)
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", bucket_level="daily")` is called
- **AND** the VM is running (NBD path selected)
- **THEN** `qemu-img convert -c -o compression_type=zstd nbd:unix:<socket> <target>` is called
- **AND** the resulting FULL is compressed with zstd

#### Scenario: NBD full backup with zlib compression (running VM)
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zlib", bucket_level="daily")` is called
- **AND** the VM is running (NBD path selected)
- **THEN** `qemu-img convert -c nbd:unix:<socket> <target>` is called (default zlib compression)
- **AND** the resulting FULL is compressed with zlib

#### Scenario: Full backup uses stall detection
- **WHEN** `create_full_backup(...)` is called with `target.backup_stall_timeout = "30m"`
- **THEN** the `qemu-img convert` command is executed via `shell.run_with_stall_detection(cmd, output_file=tmp_file, stall_timeout=1800)`
- **AND** if the output file stops growing for 30 minutes, the convert is killed

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

`BitmapBackupProvider` SHALL override `create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, compression_type: str = "zstd", bucket_level: str = "monthly") -> BackupResult` to create a standalone FULL backup via the NBD full-export path. The `compression_type` parameter SHALL be passed through to `nbd_full_export()`. The method SHALL NOT raise `NotImplementedError`. The result SHALL be a standalone qcow2 file on the target. The method SHALL pass a `checkpoint_name` to `nbd_full_export()` so that a baseline checkpoint is created **atomically** with the FULL's `backup-begin` (named `qsnap-{target_hash}-{yyyymmddTHHMMSS}`); a bitmap-mode FULL therefore always leaves a checkpoint baseline anchored at the FULL's freeze point.

The method SHALL NOT call `self._state.record_full_backup()` — state recording is Core's responsibility after post-create verification passes. This matches `FileCopyBackupProvider.create_full_backup()` behavior, which also does not self-record.

#### Scenario: Bitmap FULL with zstd compression
- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", bucket_level="monthly")` is called
- **THEN** `nbd_full_export(shell, "myvm", target_file, compress=True, compression_type="zstd", checkpoint_name=<generated>)` is called
- **AND** the resulting FULL is compressed with zstd

#### Scenario: Bitmap FULL with zlib compression
- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zlib", bucket_level="monthly")` is called
- **THEN** `nbd_full_export(shell, "myvm", target_file, compress=True, compression_type="zlib", checkpoint_name=<generated>)` is called
- **AND** the resulting FULL is compressed with zlib

#### Scenario: Bitmap FULL no longer raises NotImplementedError
- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** the method does NOT raise `NotImplementedError`
- **AND** `virsh backup-begin` is called without any `--incremental` CLI flag
- **AND** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2

#### Scenario: Bitmap FULL creates checkpoint atomically
- **WHEN** `BitmapBackupProvider.create_full_backup()` is called for a running VM
- **THEN** `virsh backup-begin` receives a checkpoint XML as the third positional argument
- **AND** on success a checkpoint named `qsnap-{target_hash}-{yyyymmddTHHMMSS}` exists
- **AND** its baseline equals the FULL export's freeze point
- **AND** no standalone `virsh checkpoint-create-as` call is made by the provider

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

### Requirement: transfer_missing SHALL NOT create FULL backups

`FileCopyBackupProvider.transfer_missing()` SHALL NOT call `create_full_backup()` under any circumstances. FULL backup creation is the sole responsibility of `Core._backup_target()` via the bucket-driven mechanism, ensuring every FULL passes through Core's verification pipeline before state recording.

### Requirement: Snapshot file existence guard before rsync

Before calling `rsync`, `transfer_missing()` SHALL verify the source snapshot file exists on disk. If missing (stale — blockcommitted but not cleaned from state), the entry SHALL be removed from state and the snapshot SHALL be skipped.

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

`BitmapBackupProvider.__init__()` SHALL accept an optional `state: IStateManager | None = None` parameter, mirroring `FileCopyBackupProvider.__init__()`. The parameter is retained for constructor parity with the factory and possible future use; checkpoint selection and transfer decisions SHALL NOT consult `IStateManager` (checkpoint discovery is newest-wins via `virsh checkpoint-list`). The `create_full_backup()` method SHALL NOT call `self._state.record_full_backup()` — state recording is Core's responsibility after post-create verification passes, matching `FileCopyBackupProvider.create_full_backup()` behavior.

#### Scenario: Constructor accepts IStateManager
- **WHEN** `BitmapBackupProvider(shell=mock_shell, state=mock_state)` is instantiated
- **THEN** `isinstance(provider, IBackupProvider)` is True
- **AND** the provider stores the state reference

#### Scenario: Constructor works without IStateManager
- **WHEN** `BitmapBackupProvider(shell=mock_shell)` is instantiated (no state argument)
- **THEN** `isinstance(provider, IBackupProvider)` is True
- **AND** `self._state` is `None`

#### Scenario: create_full_backup does not self-record in state
- **WHEN** `BitmapBackupProvider.create_full_backup(...)` succeeds and `self._state` is not `None`
- **THEN** `self._state.record_full_backup()` is NOT called by the provider
- **AND** state recording is deferred to Core's `_backup_target()` after post-create verification

#### Scenario: create_full_backup skips state recording when state is None
- **WHEN** `BitmapBackupProvider.create_full_backup(...)` succeeds and `self._state` is `None`
- **THEN** no error is raised
- **AND** the method returns `BackupResult(success=True)` without recording in state

### Requirement: Factory passes IStateManager to BitmapBackupProvider

`DefaultFactory.create_backup_provider(vm_config, target)` SHALL pass `self._state` as the `state` parameter when constructing `BitmapBackupProvider`, identical to how it is passed to `FileCopyBackupProvider`.

#### Scenario: Factory constructs BitmapBackupProvider with state
- **WHEN** `target.incremental_mode == "bitmap"` and factory has `self._state`
- **THEN** `BitmapBackupProvider(shell=self._shell, state=self._state)` is returned

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

`FileCopyBackupProvider.transfer_missing()` SHALL add the `--compress` flag to the rsync command when `target.compress=True` (default). When `compression_type="zstd"`, the `--compress-choice=zstd` flag SHALL also be added. When `compression_type="zlib"`, only `--compress` is added (rsync's default compression is zlib). When `target.compress=False`, no compression flags SHALL be added. The `--compress` flag compresses the transfer stream, not the file on disk — the target file is identical to the source after transfer.

#### Scenario: rsync with zstd compression
- **WHEN** `transfer_missing()` is called with `target.compress=True`, `compression_type="zstd"`, and `rate_limit="no"`
- **THEN** the rsync command SHALL be `rsync --compress --compress-choice=zstd --partial --progress <source> <target>`
- **AND** the target file on disk is a byte-for-byte copy of the source (compression is transfer-only)

#### Scenario: rsync with zlib compression
- **WHEN** `transfer_missing()` is called with `target.compress=True`, `compression_type="zlib"`, and `rate_limit="no"`
- **THEN** the rsync command SHALL be `rsync --compress --partial --progress <source> <target>`
- **AND** rsync uses its default zlib compression for the transfer stream

#### Scenario: rsync with zstd compression and rate limit
- **WHEN** `transfer_missing()` is called with `target.compress=True`, `compression_type="zstd"`, and `rate_limit="100M"`
- **THEN** the rsync command SHALL be `rsync --bwlimit=<kib> --compress --compress-choice=zstd --partial --progress <source> <target>`

#### Scenario: rsync without compression
- **WHEN** `transfer_missing()` is called with `target.compress=False`
- **THEN** the rsync command SHALL NOT include `--compress` or `--compress-choice`
- **AND** the command is `rsync --partial --progress <source> <target>` (existing behavior)

#### Scenario: Compression does not affect hash verification
- **WHEN** rsync transfers with `--compress --compress-choice=zstd` and `verify="hash"`
- **THEN** the target file's SHA-256 matches the source snapshot's `content_hash`
- **AND** hash verification passes (rsync `--compress` is transfer-level, file bytes are identical)

### Requirement: FileCopyBackupProvider rsync failure logging

`FileCopyBackupProvider.transfer_missing()` SHALL emit `logger.warning` before returning `BackupResult(success=False)` in the rsync failure path. The warning SHALL include the snapshot name and the rsync error message.

#### Scenario: Rsync failure logged
- **WHEN** `rsync` returns a non-zero exit code
- **THEN** a WARNING is logged: `"rsync failed for <snapshot>: <error>"`
- **AND** `BackupResult(success=False)` is returned

### Requirement: Compression type parameter for backup providers

`IBackupProvider.create_full_backup()` SHALL accept a `compression_type: str = "zstd"` parameter. `IBackupProvider.transfer_missing()` SHALL accept a `compression_type: str = "zstd"` parameter. The `compression_type` parameter SHALL be passed from Core's `target.compression_type` config field. Valid values are `"zstd"` (default) and `"zlib"`. When `compress=False`, the `compression_type` parameter SHALL be ignored (no compression regardless of type).

#### Scenario: create_full_backup with zstd compression
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", bucket_level="monthly")` is called
- **AND** the VM is stopped (direct convert path)
- **THEN** `qemu-img convert -c -o compression_type=zstd -f qcow2 -O qcow2 <source> <target>` is executed
- **AND** the resulting FULL backup is compressed with zstd

#### Scenario: create_full_backup with zlib compression
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zlib", bucket_level="monthly")` is called
- **AND** the VM is stopped (direct convert path)
- **THEN** `qemu-img convert -c -f qcow2 -O qcow2 <source> <target>` is executed (default zlib compression)
- **AND** the resulting FULL backup is compressed with zlib

#### Scenario: create_full_backup with compression disabled
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=False, compression_type="zstd", bucket_level="monthly")` is called
- **THEN** `qemu-img convert -f qcow2 -O qcow2 <source> <target>` is executed (no `-c` flag)
- **AND** the `compression_type` parameter is ignored

### Requirement: Stall detection for data transfer commands

`FileCopyBackupProvider.transfer_missing()` and `BitmapBackupProvider.transfer_missing()` SHALL use `IShell.run_with_stall_detection()` instead of `IShell.run()` for the rsync transfer and NBD convert steps respectively. The `output_file` parameter SHALL be the target file path (`.partial` for rsync, target file for NBD convert). The `stall_timeout` parameter SHALL be passed from `target.backup_stall_timeout` (parsed to seconds). If `backup_stall_timeout` is `"0s"`, the method SHALL fall back to `IShell.run()` with a fixed timeout of 3600s (backward compatibility).

#### Scenario: rsync transfer uses stall detection
- **WHEN** `transfer_missing()` is called with `target.backup_stall_timeout = "30m"`
- **THEN** the rsync command is executed via `shell.run_with_stall_detection(cmd, output_file=target_file, stall_timeout=1800)`
- **AND** if the output file stops growing for 30 minutes, the transfer is killed

#### Scenario: NBD convert uses stall detection
- **WHEN** `transfer_missing()` in BitmapBackupProvider is called with `target.backup_stall_timeout = "30m"`
- **THEN** the `qemu-img convert` command is executed via `shell.run_with_stall_detection(cmd, output_file=target_file, stall_timeout=1800)`

#### Scenario: Stall timeout disabled falls back to fixed timeout
- **WHEN** `target.backup_stall_timeout = "0s"`
- **THEN** `shell.run(cmd, timeout=3600)` is used (existing behavior, no stall detection)
