## MODIFIED Requirements

### Requirement: Transfer missing snapshots via dirty bitmap extraction

The system SHALL determine which snapshots are missing on the target and for each SHALL use `virsh backup-begin` with NBD export to transfer data. On first backup (no prior checkpoint), a full export is performed via the engine selected by `full_transfer_engine`: when `"qemu-img-convert"` (default), `qemu-img convert` is used; when `"libnbd"`, the unified NBD transfer engine is used with `meta_contexts=["base:allocation"]` and `zero_skip=True`. On subsequent backups, only dirty blocks since the last checkpoint are exported via the unified engine with `meta_contexts=["base:allocation", "qemu:dirty-bitmap:backup-<disk>"]` and `zero_skip=False`. Every `backup-begin` SHALL receive a checkpoint XML as its third positional argument so the successor checkpoint is created atomically at the export's freeze point. The `full_transfer_engine` setting SHALL NOT affect incremental transfers — incrementals always use the `pread`/`pwrite` engine. The `full_verify_before_rebase` parameter is REMOVED from the `transfer_missing()` signature — it was dead plumbing (rebase died with file-copy).

#### Scenario: First backup — full NBD export via qemu-img convert (default engine)

- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **AND** `full_transfer_engine == "qemu-img-convert"` (default)
- **THEN** `qemu-img convert` reads from `nbd:unix:<socket>` and writes to the target qcow2
- **THEN** the backup is a standalone qcow2 file on the target containing the complete virtual disk
- **AND** no Python `pread`/`pwrite` loop runs

#### Scenario: First backup — full NBD export via libnbd engine

- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **AND** `full_transfer_engine == "libnbd"`
- **THEN** the unified engine performs a full export with `meta_contexts=["base:allocation"]`, `zero_skip=True`
- **THEN** the backup is a standalone qcow2 file on the target containing the complete virtual disk
- **AND** no `qemu-img convert` is executed

#### Scenario: Incremental backup — dirty blocks only (unaffected by engine selection)

- **WHEN** a prior qsnap checkpoint exists for this VM+target
- **AND** the VM has written data since that checkpoint
- **THEN** the unified engine transfers dirty∩allocated extents with `zero_skip=False`
- **THEN** the resulting backup file size is proportional to the changed data, not the full disk
- **AND** the `full_transfer_engine` setting does NOT affect this incremental transfer

#### Scenario: Checkpoint rotation after successful transfer

- **WHEN** the transfer completes successfully and verification passes
- **THEN** the successor checkpoint created atomically with this export exists
- **THEN** all superseded (older) qsnap checkpoints are deleted via `virsh checkpoint-delete --metadata`
- **AND** exactly one qsnap checkpoint remains for this VM+target

#### Scenario: Transfer failure preserves prior checkpoint

- **WHEN** the transfer fails (NBD error or stall)
- **THEN** the prior checkpoint is NOT deleted
- **THEN** the successor checkpoint created by the failed run is deleted best-effort
- **THEN** the module returns `BackupResult(success=False, error=<message>)`
- **THEN** the NBD socket and qemu-nbd process are cleaned up

#### Scenario: Scaffolding dedup — both FULL paths use shared helper with engine branch

- **WHEN** `transfer_missing()` full-pull or `create_full_backup()` executes a FULL backup
- **THEN** both SHALL call the private `_full_pull_lifecycle()` helper
- **AND** the helper branches on `full_transfer_engine`: `"qemu-img-convert"` → `_qemu_img_convert_transfer()`, `"libnbd"` → `_full_transfer_via_libnbd()`
- **AND** the helper handles: transfer, mv .tmp → final, finally cleanup

### Requirement: BitmapBackupProvider.create_full_backup implemented via configurable engine

`BitmapBackupProvider` SHALL override `create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, bucket_level: str = "monthly", compression_type: str = "zstd", stall_timeout: int = 1800, full_transfer_engine: str = "qemu-img-convert", convert_parallel: int = 4, convert_out_of_order: bool = True) -> BackupResult` to create a standalone FULL backup via the selected transfer engine. When `full_transfer_engine == "qemu-img-convert"`, the method SHALL use `qemu-img convert` via `_qemu_img_convert_transfer()`. When `full_transfer_engine == "libnbd"`, the method SHALL use the libnbd pread/pwrite engine via `_full_transfer_via_libnbd()`. The method SHALL NOT raise `NotImplementedError`. The result SHALL be a standalone qcow2 file on the target. The method SHALL pass a `checkpoint_name` to `_full_pull_lifecycle()` so that a baseline checkpoint is created **atomically** with the FULL's `backup-begin` (named `qsnap-{target_hash}-{yyyymmddTHHMMSS}`); a bitmap-mode FULL therefore always leaves a checkpoint baseline anchored at the FULL's freeze point.

The method SHALL NOT call `self._state.record_full_backup()` — state recording is Core's responsibility after post-create verification passes.

#### Scenario: Bitmap FULL with zstd compression via qemu-img convert

- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", bucket_level="monthly", full_transfer_engine="qemu-img-convert")` is called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p <source> <target>.tmp` is executed via `run_with_stall_detection()`
- **AND** the resulting FULL is compressed with zstd

#### Scenario: Bitmap FULL with zstd compression via libnbd

- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", bucket_level="monthly", full_transfer_engine="libnbd")` is called
- **THEN** `qemu-img create -f qcow2 -o compression_type=zstd <tmp_file> <virtual_size>` is executed
- **AND** `_start_write_server(..., compress=True)` is called
- **AND** `_transfer(..., zero_skip=True, compress=True)` is called
- **AND** the resulting FULL is compressed with zstd

#### Scenario: Bitmap FULL with custom convert_parallel

- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", full_transfer_engine="qemu-img-convert", convert_parallel=2)` is called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 2 -W -p <source> <target>.tmp` is executed
- **AND** the `-m` flag has value `2`

#### Scenario: Bitmap FULL with convert_out_of_order disabled

- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=False, full_transfer_engine="qemu-img-convert", convert_out_of_order=False)` is called
- **THEN** `qemu-img convert -O qcow2 -m 4 -p <source> <target>.tmp` is executed
- **AND** the `-W` flag is NOT present

#### Scenario: Bitmap FULL no longer raises NotImplementedError

- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** the method does NOT raise `NotImplementedError`
- **AND** `virsh backup-begin` is called without any `--incremental` CLI flag
- **AND** the default `full_transfer_engine="qemu-img-convert"` is used

#### Scenario: Bitmap FULL creates checkpoint atomically

- **WHEN** `create_full_backup()` is called for a running VM
- **THEN** `virsh backup-begin` receives a checkpoint XML as the third positional argument
- **AND** on success a checkpoint named `qsnap-{target_hash}-{yyyymmddTHHMMSS}` exists
- **AND** its baseline equals the FULL export's freeze point
- **AND** no standalone `virsh checkpoint-create-as` call is made by the provider

#### Scenario: Bitmap FULL does not self-record in state

- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully
- **THEN** `self._state.record_full_backup()` is NOT called by the provider
- **AND** state recording is deferred to Core's `_backup_target()` after post-create verification

#### Scenario: Bitmap FULL with dotted VM name

- **WHEN** `BitmapBackupProvider.create_full_backup("3.Projects_opencode", snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** the FULL backup file is named `3.Projects_opencode.FULL.YYYYMMDD.qcow2`

### Requirement: Compression type parameter for backup providers

`IBackupProvider.create_full_backup()` SHALL accept a `compression_type: str = "zstd"` parameter. `IBackupProvider.transfer_missing()` SHALL accept a `compression_type: str = "zstd"` parameter. The `compression_type` parameter SHALL be passed from Core's `target.compression_type` config field. Valid values are `"zstd"` (default) and `"zlib"`. When `compress=False`, the `compression_type` parameter SHALL be ignored (no compression regardless of type).

When `full_transfer_engine == "libnbd"` and `compress=True`, the `compression_type` SHALL be used to create the target qcow2 with `qemu-img create -o compression_type=<compression_type>`. The compress driver on the write-side `qemu-nbd` auto-detects the algorithm from the qcow2 header.

#### Scenario: create_full_backup with zstd compression via qemu-img convert

- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", full_transfer_engine="qemu-img-convert")` is called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p <source> <target>.tmp` is executed
- **AND** the resulting FULL backup is compressed with zstd

#### Scenario: create_full_backup with zstd compression via libnbd

- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", full_transfer_engine="libnbd")` is called
- **THEN** `qemu-img create -f qcow2 -o compression_type=zstd <tmp_file> <virtual_size>` is executed
- **AND** the write-side `qemu-nbd` uses `--image-opts driver=compress,...`
- **AND** the resulting FULL backup is compressed with zstd

#### Scenario: create_full_backup with compression disabled

- **WHEN** `create_full_backup("myvm", snapshot, target, compress=False, compression_type="zstd", full_transfer_engine="qemu-img-convert")` is called
- **THEN** `qemu-img convert -O qcow2 -m 4 -W -p <source> <target>.tmp` is executed (no `-c` flag)
- **AND** the `compression_type` parameter is ignored

### Requirement: Full transfer engine parameters for backup providers

`IBackupProvider.create_full_backup()` SHALL accept `full_transfer_engine: str = "qemu-img-convert"`, `convert_parallel: int = 4`, and `convert_out_of_order: bool = True` as keyword parameters. `IBackupProvider.transfer_missing()` SHALL accept the same three keyword parameters. These parameters SHALL be passed from Core's `target.full_transfer_engine`, `target.convert_parallel`, and `target.convert_out_of_order` config fields. The `full_transfer_engine` parameter selects the FULL backup transfer engine. The `convert_parallel` and `convert_out_of_order` parameters are only consumed when `full_transfer_engine == "qemu-img-convert"`; they are ignored when `full_transfer_engine == "libnbd"`.

#### Scenario: create_full_backup with default engine

- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True)` is called without explicit `full_transfer_engine`
- **THEN** `full_transfer_engine` defaults to `"qemu-img-convert"`
- **AND** `qemu-img convert` is used for the FULL transfer

#### Scenario: create_full_backup with libnbd engine

- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, full_transfer_engine="libnbd")` is called
- **THEN** the libnbd pread/pwrite engine is used for the FULL transfer
- **AND** `convert_parallel` and `convert_out_of_order` are ignored

#### Scenario: transfer_missing with default engine

- **WHEN** `transfer_missing(vm_config, target, snapshots)` is called without explicit `full_transfer_engine`
- **THEN** `full_transfer_engine` defaults to `"qemu-img-convert"`
- **AND** FULL transfers within `transfer_missing()` use `qemu-img convert`

#### Scenario: transfer_missing with libnbd engine

- **WHEN** `transfer_missing(vm_config, target, snapshots, full_transfer_engine="libnbd")` is called
- **THEN** FULL transfers within `transfer_missing()` use the libnbd pread/pwrite engine
- **AND** incremental transfers are unaffected (always use pread/pwrite)

### Requirement: Stall detection for data transfer commands

`BitmapBackupProvider.transfer_missing()` SHALL use `IShell.run_with_stall_detection()` instead of `IShell.run()` for the NBD convert step (when `full_transfer_engine == "qemu-img-convert"`) and for the libnbd transfer step (when `full_transfer_engine == "libnbd"`). The `output_file` parameter SHALL be the target file path. The `stall_timeout` parameter SHALL be passed from `target.backup_stall_timeout` (parsed to seconds). If `backup_stall_timeout` is `"0s"`, the method SHALL fall back to `IShell.run()` with a fixed timeout of 3600s (backward compatibility).

#### Scenario: qemu-img convert uses stall detection

- **WHEN** `transfer_missing()` in BitmapBackupProvider is called with `target.backup_stall_timeout = "30m"` and `full_transfer_engine = "qemu-img-convert"`
- **THEN** the `qemu-img convert` command is executed via `shell.run_with_stall_detection(cmd, output_file=target_file, stall_timeout=1800)`

#### Scenario: libnbd transfer uses stall detection

- **WHEN** `transfer_missing()` in BitmapBackupProvider is called with `target.backup_stall_timeout = "30m"` and `full_transfer_engine = "libnbd"`
- **THEN** the libnbd transfer is monitored for stall via the `stall_timeout` parameter passed to `_transfer()`

#### Scenario: Stall timeout disabled falls back to fixed timeout

- **WHEN** `target.backup_stall_timeout = "0s"`
- **THEN** `shell.run(cmd, timeout=3600)` is used (existing behavior, no stall detection)
