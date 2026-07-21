# Delta: backup-provider

## MODIFIED Requirements

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
