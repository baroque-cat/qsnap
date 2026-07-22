## REMOVED Requirements

### Requirement: Transfer missing snapshots to backup target
**Reason**: This requirement specified the rsync whole-file transfer plus post-copy `qemu-img rebase -u` and `copy_base` handling of the deleted `FileCopyBackupProvider`. rsync is eliminated as a transfer mechanism.
**Migration**: Incremental transfer is the NBD/libnbd dirty-block copy loop producing backing-chained deltas — see the `nbd-bitmap-backup` and `nbd-dirty-block-transfer` capabilities. No post-copy rebase exists; chains are native at creation.

### Requirement: Rebase error handling in FileCopyBackupProvider
**Reason**: `FileCopyBackupProvider` is deleted; there is no post-copy rebase step to fail.
**Migration**: None — bitmap deltas are created with the correct `backing-filename` at `qemu-img create` time and verified by `verify_bitmap_incremental()`.

### Requirement: FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target
**Reason**: `FileCopyBackupProvider` is deleted, including its stopped-VM direct-convert path.
**Migration**: FULL backups are created by `BitmapBackupProvider.create_full_backup()` via the NBD full-export path and require a running VM (see `live-vm-full-backup`).

### Requirement: Snapshot file existence guard before rsync
**Reason**: rsync-specific guard. The NBD transfer pulls blocks from the running VM via `virsh backup-begin`; snapshot files on disk are not the transfer source.
**Migration**: None. Stale state entries are handled by Core's stale-state self-healing before the backup step.

### Requirement: Compression for rsync incremental transfers
**Reason**: rsync is eliminated. `--compress`/`--compress-choice` were rsync transfer-stream flags with no on-disk effect.
**Migration**: Compression applies to FULL backups via `qemu-img convert -c -o compression_type=<type>`. Bitmap incremental transfers are uncompressed by design (see `nbd-dirty-block-transfer`).

### Requirement: FileCopyBackupProvider rsync failure logging
**Reason**: `FileCopyBackupProvider` and its rsync failure path are deleted.
**Migration**: None — the NBD transfer's failure paths log and return `BackupResult(success=False, error=...)` per existing requirements.

### Requirement: FileCopyBackupProvider verify_backup failure logging
**Reason**: `FileCopyBackupProvider` and the `verify_backup()` helper are deleted.
**Migration**: Verification failures of the remaining provider are covered by the `nbd-bitmap-backup` capability and the modified deletion requirement below.

### Requirement: Libvirt version check in BitmapBackupProvider
**Reason**: The WARNING-plus-fallback behavior depended on `FileCopyBackupProvider`, which is deleted. Version gating with hard errors is now specified in the `module-factory` capability.
**Migration**: Hosts must run libvirt >= 7.2; the factory raises `RuntimeError` otherwise. `BitmapBackupProvider.__init__()` still performs no version check itself.

### Requirement: Factory selects BitmapBackupProvider for bitmap mode
**Reason**: There is no mode selection — `incremental_mode` and the file-copy branch are deleted. Single-provider construction with hard dependency gates is specified in the `module-factory` capability.
**Migration**: None — `DefaultFactory.create_backup_provider()` always returns `BitmapBackupProvider`.

## MODIFIED Requirements

### Requirement: Backup verification step
`BitmapBackupProvider.transfer_missing()` SHALL perform post-transfer verification according to `target.verify` via `verify_bitmap_incremental()` (see the `nbd-bitmap-backup` capability). `"off"` skips verification. Every verification failure SHALL produce `BackupResult(success=False, error="verification failed: ...")`.

#### Scenario: Metadata verification passes
- **WHEN** target.verify is "metadata" and the delta passes the structural checks (format, virtual-size, backing-filename, dirty-size barrier)
- **THEN** backup is marked success

#### Scenario: Verification failure produces error
- **WHEN** verification detects a structural or content mismatch
- **THEN** `BackupResult(success=False, error="verification failed: ...")` is returned

### Requirement: transfer_missing SHALL NOT create FULL backups

`BitmapBackupProvider.transfer_missing()` SHALL NOT call `create_full_backup()` under any circumstances. FULL backup creation is the sole responsibility of `Core._backup_target()` via the bucket-driven mechanism, ensuring every FULL passes through Core's verification pipeline before state recording.

#### Scenario: transfer_missing never creates a FULL
- **WHEN** `transfer_missing()` runs for any snapshot set, including an empty target
- **THEN** the provider never invokes `create_full_backup()`

### Requirement: Immediate deletion of failed backup files after verification failure

When verification returns a non-None error string in `BitmapBackupProvider.transfer_missing()`, the provider SHALL delete the partially-transferred target file via `self._shell.run(["rm", "-f", str(target_file)], timeout=10)` immediately after logging the WARNING, before appending `BackupResult(success=False)` and before `continue`. The same `rm -f` cleanup SHALL apply when the transfer itself fails (NBD copy loop or FULL export), removing any partial or `.tmp` output. This prevents the failed file from being discovered by retention cleanup (which uses `glob("*.qcow2")` and would delete it with a misleading `[delete] removed backup` log message).

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

#### Scenario: Transfer failure does not leave partial files
- **WHEN** the NBD copy loop or the FULL export fails mid-transfer
- **THEN** any partial or `.tmp` output file SHALL be deleted via `rm -f` before appending `BackupResult(success=False)`
- **AND** the NBD socket is cleaned up in the `finally` block (existing behavior)

### Requirement: Stall detection for data transfer commands

`BitmapBackupProvider` SHALL use `IShell.run_with_stall_detection()` for subprocess-based transfers (the `qemu-img convert` FULL export), with `output_file` set to the target file path and `stall_timeout` from `target.backup_stall_timeout` (parsed to seconds). The in-process dirty-block copy loop SHALL use the in-process stall watchdog specified in the `stall-detection` capability. If `backup_stall_timeout` is `"0s"`, stall detection SHALL be disabled: subprocess transfers fall back to `IShell.run()` with a fixed timeout of 3600s, and the in-process watchdog is off.

#### Scenario: NBD convert uses stall detection
- **WHEN** a FULL export runs with `target.backup_stall_timeout = "30m"`
- **THEN** the `qemu-img convert` command is executed via `shell.run_with_stall_detection(cmd, output_file=target_file, stall_timeout=1800)`

#### Scenario: Stall timeout disabled falls back to fixed timeout
- **WHEN** `target.backup_stall_timeout = "0s"`
- **THEN** `shell.run(cmd, timeout=3600)` is used for subprocess transfers (no stall detection)
- **AND** the in-process copy-loop watchdog is disabled

### Requirement: Backup providers remain retry-unaware
`BitmapBackupProvider` SHALL NOT implement any retry logic internally. It SHALL continue to return `BackupResult(success=False, error=...)` for any failure. The retry logic SHALL be handled by Core's `_backup_target()` method, which wraps the provider's `transfer_missing()` call.

#### Scenario: Provider returns error, Core handles retry
- **WHEN** `BitmapBackupProvider.transfer_missing()` returns `BackupResult(success=False, error="Connection refused")`
- **THEN** the provider itself does not retry
- **AND** Core's retry wrapper inspects the error and decides whether to retry

#### Scenario: BackupResult error is structured for retry detection
- **WHEN** a backup transfer fails
- **THEN** the `BackupResult.error` string contains the underlying error from `ShellResult.error`
- **AND** Core's retry logic can pattern-match against it to determine retryability

### Requirement: BitmapBackupProvider accepts IStateManager

`BitmapBackupProvider.__init__()` SHALL accept an optional `state: IStateManager | None = None` parameter. The parameter is retained for constructor parity with the factory and possible future use; checkpoint selection and transfer decisions SHALL NOT consult `IStateManager` (checkpoint discovery is newest-wins via `virsh checkpoint-list`). The `create_full_backup()` method SHALL NOT call `self._state.record_full_backup()` — state recording is Core's responsibility after post-create verification passes.

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

`DefaultFactory.create_backup_provider(vm_config, target)` SHALL pass `self._state` as the `state` parameter when constructing `BitmapBackupProvider`.

#### Scenario: Factory constructs BitmapBackupProvider with state
- **WHEN** `create_backup_provider()` is called and the factory has `self._state`
- **THEN** `BitmapBackupProvider(shell=self._shell, state=self._state)` is returned

### Requirement: Compression type parameter for backup providers

`IBackupProvider.create_full_backup()` SHALL accept a `compression_type: str = "zstd"` parameter. `IBackupProvider.transfer_missing()` SHALL accept a `compression_type: str = "zstd"` parameter. The `compression_type` parameter SHALL be passed from Core's `target.compression_type` config field. Valid values are `"zstd"` (default) and `"zlib"`. When `compress=False`, the `compression_type` parameter SHALL be ignored (no compression regardless of type). Compression applies to FULL backups only; bitmap incremental transfers are uncompressed (see `nbd-dirty-block-transfer`).

#### Scenario: create_full_backup with zstd compression
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", bucket_level="monthly")` is called
- **THEN** `nbd_full_export()` receives `compress=True, compression_type="zstd"`
- **AND** the FULL convert includes `-c -o compression_type=zstd`

#### Scenario: create_full_backup with zlib compression
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zlib", bucket_level="monthly")` is called
- **THEN** the FULL convert includes `-c` only (default zlib)
- **AND** the resulting FULL backup is compressed with zlib

#### Scenario: create_full_backup with compression disabled
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=False, compression_type="zstd", bucket_level="monthly")` is called
- **THEN** no `-c` flag is used
- **AND** the `compression_type` parameter is ignored

### Requirement: BitmapBackupProvider.create_full_backup implemented via NBD

`BitmapBackupProvider` SHALL override `create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, compression_type: str = "zstd", bucket_level: str = "monthly") -> BackupResult` to create a standalone FULL backup via the NBD full-export path. The `compression_type` parameter SHALL be passed through to `nbd_full_export()`. The method SHALL NOT raise `NotImplementedError`. The result SHALL be a standalone qcow2 file on the target. The method SHALL pass a `checkpoint_name` to `nbd_full_export()` so that a baseline checkpoint is created **atomically** with the FULL's `backup-begin` (named `qsnap-{target_hash}-{yyyymmddTHHMMSS}`); a bitmap-mode FULL therefore always leaves a checkpoint baseline anchored at the FULL's freeze point.

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
