# Backup Provider

## Purpose

Backup transfer via NBD pull-model dirty-block extraction. Copies missing snapshots to a target directory, maintaining incremental backup semantics with backing-chained qcow2 deltas. The single backup provider is `BitmapBackupProvider`; `FileCopyBackupProvider` (rsync) and `rate_limit` have been removed.

## Requirements

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
The system SHALL determine which snapshots are missing on the target and for each SHALL use `virsh backup-begin` with NBD export to transfer data. On first backup (no prior checkpoint), a full export is performed via the **unified NBD transfer engine** with `meta_contexts=["base:allocation"]` and `zero_skip=True`. On subsequent backups, only dirty blocks since the last checkpoint are exported via the unified engine with `meta_contexts=["base:allocation", "qemu:dirty-bitmap:backup-<disk>"]` and `zero_skip=False`. Every `backup-begin` SHALL receive a checkpoint XML as its third positional argument so the successor checkpoint is created atomically at the export's freeze point. **No `qemu-img convert` SHALL be used in the data path** — the unified engine uses `pread`/`pwrite` through `INbdClient`. The `full_verify_before_rebase` parameter is REMOVED from the `transfer_missing()` signature — it was dead plumbing (rebase died with file-copy).

#### Scenario: First backup — full NBD export (no prior checkpoint)
- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** the unified engine performs a full export with `meta_contexts=["base:allocation"]`, `zero_skip=True`
- **THEN** the backup is a standalone qcow2 file on the target containing the complete virtual disk
- **AND** no `qemu-img convert` is executed

#### Scenario: Incremental backup — dirty blocks only
- **WHEN** a prior qsnap checkpoint exists for this VM+target
- **AND** the VM has written data since that checkpoint
- **THEN** the unified engine transfers dirty∩allocated extents with `zero_skip=False`
- **THEN** the resulting backup file size is proportional to the changed data, not the full disk

#### Scenario: Checkpoint rotation after successful transfer
- **WHEN** the unified engine completes successfully and verification passes
- **THEN** the successor checkpoint created atomically with this export exists
- **THEN** all superseded (older) qsnap checkpoints are deleted via `virsh checkpoint-delete --metadata`
- **AND** exactly one qsnap checkpoint remains for this VM+target

#### Scenario: Transfer failure preserves prior checkpoint
- **WHEN** the transfer fails (NBD error or stall)
- **THEN** the prior checkpoint is NOT deleted
- **THEN** the successor checkpoint created by the failed run is deleted best-effort
- **THEN** the module returns `BackupResult(success=False, error=<message>)`
- **THEN** the NBD socket and qemu-nbd process are cleaned up

#### Scenario: Scaffolding dedup — both FULL paths use shared helper

- **WHEN** `transfer_missing()` full-pull or `create_full_backup()` executes a FULL backup
- **THEN** both SHALL call the private `_full_pull_lifecycle()` helper
- **AND** the helper handles: qemu-img create, _start_write_server, _transfer, _terminate_qemu_nbd, mv .tmp → final, finally cleanup

### Requirement: List checkpoints for target
`BitmapBackupProvider` SHALL provide a method `list_checkpoints(vm_name: str) -> list[str]` that discovers existing qsnap-owned checkpoints via `virsh checkpoint-list --name`. Only checkpoints with the `qsnap-` prefix SHALL be returned.

#### Scenario: Existing qsnap checkpoints found
- **WHEN** `virsh checkpoint-list --name VM` returns `["qsnap-target1-20250101", "manual-checkpoint", "qsnap-target1-20250102"]`
- **THEN** `list_checkpoints("VM")` returns `["qsnap-target1-20250101", "qsnap-target1-20250102"]`

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

### Requirement: Backup verification step
`BitmapBackupProvider.transfer_missing()` SHALL perform post-transfer verification according to `target.verify`. `"off"` skips verification. Every verification failure SHALL produce `BackupResult(success=False, error="verification failed: ...")`. For incrementals, verification SHALL use `verify_bitmap_incremental()`. For full pulls (no prior checkpoint), verification SHALL use `verify_full_backup()`. The verify modes are `"off"`, `"metadata"`, `"compare"` (was `"hash"`/`"full"` — both ran `qemu-img compare`; now unified to `"compare"`). Existing configs with `verify="hash"` or `verify="full"` SHALL log a deprecation WARNING and be treated as `"compare"`.

#### Scenario: Metadata verification passes
- **WHEN** `target.verify` is `"metadata"` and the backup passes structural checks
- **THEN** backup is marked success

#### Scenario: Compare verification passes
- **WHEN** `target.verify` is `"compare"` and `qemu-img compare` succeeds
- **THEN** backup is marked success

#### Scenario: Verification failure produces error
- **WHEN** verification detects a structural or content mismatch
- **THEN** `BackupResult(success=False, error="verification failed: ...")` is returned

#### Scenario: Deprecated verify values treated as compare
- **WHEN** `target.verify` is `"hash"` or `"full"` (deprecated)
- **THEN** a WARNING is logged naming the deprecated value
- **AND** `"compare"` behavior is applied (qemu-img compare)

### Requirement: Backup providers remain retry-unaware
`BitmapBackupProvider` SHALL NOT implement any retry logic internally. It SHALL return `BackupResult(success=False, error=...)` for any failure. The retry logic SHALL be handled by Core's `_backup_target()` method, which wraps the provider's `transfer_missing()` call.

#### Scenario: Provider returns error, Core handles retry
- **WHEN** `BitmapBackupProvider.transfer_missing()` returns `BackupResult(success=False, error="Connection refused")`
- **THEN** the provider itself does not retry
- **AND** Core's retry wrapper inspects the error and decides whether to retry

#### Scenario: BackupResult error is structured for retry detection
- **WHEN** a backup transfer fails
- **THEN** the `BackupResult.error` string contains the underlying error from `ShellResult.error`
- **AND** Core's retry logic can pattern-match against it to determine retryability

### Requirement: BitmapBackupProvider.create_full_backup implemented via NBD

`BitmapBackupProvider` SHALL override `create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, compression_type: str = "zstd", bucket_level: str = "monthly") -> BackupResult` to create a standalone FULL backup via the NBD full-export path. The `compression_type` parameter SHALL be passed through to `nbd_full_export()`. The method SHALL NOT raise `NotImplementedError`. The result SHALL be a standalone qcow2 file on the target. The method SHALL pass a `checkpoint_name` to `nbd_full_export()` so that a baseline checkpoint is created **atomically** with the FULL's `backup-begin` (named `qsnap-{target_hash}-{yyyymmddTHHMMSS}`); a bitmap-mode FULL therefore always leaves a checkpoint baseline anchored at the FULL's freeze point. FULL backups require a running VM — on a stopped VM the method returns `BackupResult(success=False, error=<virsh error>)` with no fallback.

The method SHALL NOT call `self._state.record_full_backup()` — state recording is Core's responsibility after post-create verification passes.

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
- **WHEN** `Core._backup_target()` calls `_should_create_bucket_full()` for a target
- **AND** it returns `(True, "weekly")`
- **THEN** `BitmapBackupProvider.create_full_backup(vm_config.name, ...)` is called with the full VM name
- **AND** it succeeds (no crash)
- **AND** the FULL is recorded in state by Core (not by the provider) with `bucket_level="weekly"`

#### Scenario: Bitmap FULL with dotted VM name
- **WHEN** `BitmapBackupProvider.create_full_backup("3.Projects_opencode", snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** `nbd_full_export(shell, "3.Projects_opencode", ...)` is called with the full VM name
- **AND** the FULL backup file is named `3.Projects_opencode.FULL.YYYYMMDD.qcow2`

### Requirement: transfer_missing SHALL NOT create FULL backups

`BitmapBackupProvider.transfer_missing()` SHALL NOT call `create_full_backup()` under any circumstances. FULL backup creation is the sole responsibility of `Core._backup_target()` via the bucket-driven mechanism, ensuring every FULL passes through Core's verification pipeline before state recording.

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

`BitmapBackupProvider.__init__()` SHALL accept an optional `state: IStateManager | None = None` parameter. The parameter is retained for possible future use; checkpoint selection and transfer decisions SHALL NOT consult `IStateManager` (checkpoint discovery is newest-wins via `virsh checkpoint-list`). The `create_full_backup()` method SHALL NOT call `self._state.record_full_backup()` — state recording is Core's responsibility after post-create verification passes.

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

`DefaultFactory.create_backup_provider(vm_config, target)` SHALL pass `self._state` as the `state` parameter when constructing `BitmapBackupProvider`. `BitmapBackupProvider` is the single backup provider — there is no `incremental_mode` branch.

#### Scenario: Factory constructs BitmapBackupProvider with state
- **WHEN** factory has `self._state`
- **THEN** `BitmapBackupProvider(shell=self._shell, state=self._state)` is returned

### Requirement: Immediate deletion of failed backup files after verification failure

When verification returns a non-None error string in `BitmapBackupProvider.transfer_missing()`, the provider SHALL delete the partially-transferred target file via `self._shell.run(["rm", "-f", str(target_file)], timeout=10)` immediately after logging the WARNING, before appending `BackupResult(success=False)` and before `continue`. This prevents the failed file from being discovered by retention cleanup (which uses `glob("*.qcow2")` and would delete it with a misleading `[delete] removed backup` log message).

#### Scenario: Failed backup file deleted immediately after verification failure

- **WHEN** verification returns an error string for a snapshot transfer
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

#### Scenario: Bitmap NBD convert failure does not leave partial file
- **WHEN** `qemu-img convert` from NBD fails in `BitmapBackupProvider.transfer_missing()`
- **THEN** the partial target file SHALL be deleted via `rm -f` before appending `BackupResult(success=False)`
- **AND** the NBD socket is cleaned up in the `finally` block (existing behavior)

### Requirement: Compression type parameter for backup providers

`IBackupProvider.create_full_backup()` SHALL accept a `compression_type: str = "zstd"` parameter. `IBackupProvider.transfer_missing()` SHALL accept a `compression_type: str = "zstd"` parameter. The `compression_type` parameter SHALL be passed from Core's `target.compression_type` config field. Valid values are `"zstd"` (default) and `"zlib"`. When `compress=False`, the `compression_type` parameter SHALL be ignored (no compression regardless of type).

#### Scenario: create_full_backup with zstd compression
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", bucket_level="monthly")` is called
- **THEN** the NBD export uses `qemu-img convert -c -o compression_type=zstd nbd:unix:<socket> <target>`
- **AND** the resulting FULL backup is compressed with zstd

#### Scenario: create_full_backup with zlib compression
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zlib", bucket_level="monthly")` is called
- **THEN** `qemu-img convert -c nbd:unix:<socket> <target>` is executed (default zlib compression)
- **AND** the resulting FULL backup is compressed with zlib

#### Scenario: create_full_backup with compression disabled
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=False, compression_type="zstd", bucket_level="monthly")` is called
- **THEN** `qemu-img convert nbd:unix:<socket> <target>` is executed (no `-c` flag)
- **AND** the `compression_type` parameter is ignored

### Requirement: Stall detection for data transfer commands

`BitmapBackupProvider.transfer_missing()` SHALL use `IShell.run_with_stall_detection()` instead of `IShell.run()` for the NBD convert step. The `output_file` parameter SHALL be the target file path. The `stall_timeout` parameter SHALL be passed from `target.backup_stall_timeout` (parsed to seconds). If `backup_stall_timeout` is `"0s"`, the method SHALL fall back to `IShell.run()` with a fixed timeout of 3600s (backward compatibility).

#### Scenario: NBD convert uses stall detection
- **WHEN** `transfer_missing()` in BitmapBackupProvider is called with `target.backup_stall_timeout = "30m"`
- **THEN** the `qemu-img convert` command is executed via `shell.run_with_stall_detection(cmd, output_file=target_file, stall_timeout=1800)`

#### Scenario: Stall timeout disabled falls back to fixed timeout
- **WHEN** `target.backup_stall_timeout = "0s"`
- **THEN** `shell.run(cmd, timeout=3600)` is used (existing behavior, no stall detection)
