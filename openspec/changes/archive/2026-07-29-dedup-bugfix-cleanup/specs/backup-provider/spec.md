# backup-provider — Delta Spec

## MODIFIED Requirements

### Requirement: BitmapBackupProvider.create_full_backup implemented via configurable engine

`BitmapBackupProvider` SHALL override `create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, compression_type: str = "zstd", stall_timeout: int = 1800, full_transfer_engine: str = "qemu-img-convert", convert_parallel: int = 4, convert_out_of_order: bool = True) -> BackupResult` to create a standalone FULL backup via the selected transfer engine. The `bucket_level` parameter is REMOVED from the method signature — it was a legacy parameter from the time-bucket retention system that is no longer used. When `full_transfer_engine == "qemu-img-convert"`, the method SHALL use `qemu-img convert` via `_qemu-img_convert_transfer()`. When `full_transfer_engine == "libnbd"`, the method SHALL use the libnbd pread/pwrite engine via `_full_transfer_via_libnbd()`. The method SHALL NOT raise `NotImplementedError`. The result SHALL be a standalone qcow2 file on the target. The method SHALL pass a `checkpoint_name` to `_full_pull_lifecycle()` so that a baseline checkpoint is created **atomically** with the FULL's `backup-begin` (named `qsnap-{target_hash}-{yyyymmddTHHMMSS}`); a bitmap-mode FULL therefore always leaves a checkpoint baseline anchored at the FULL's freeze point.

The method SHALL NOT call `self._state.record_full_backup()` — state recording is Core's responsibility after post-create verification passes.

#### Scenario: Bitmap FULL with zstd compression via qemu-img convert

- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", full_transfer_engine="qemu-img-convert")` is called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p <source> <target>.tmp` is executed via `run_with_stall_detection()`
- **AND** the resulting FULL is compressed with zstd

#### Scenario: Bitmap FULL with zstd compression via libnbd

- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", full_transfer_engine="libnbd")` is called
- **THEN** `qemu-img create -f qcow2 -o compression_type=zstd <tmp_file> <virtual_size>` is executed
- **AND** `_start_write_server(..., compress=True)` is called
- **AND** `_transfer(..., zero_skip=True, compress=True)` is called
- **AND** the resulting FULL is compressed with zstd

#### Scenario: Bitmap FULL with custom convert_parallel

- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", full_transfer_engine="qemu-img-convert", convert_parallel=2)` is called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 2 -W -p <source> <target>.tmp` is executed

#### Scenario: Bitmap FULL creates atomically with checkpoint

- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully
- **THEN** a backup-begin/freeze checkpoint named `qsnap-{target_hash}-{yyyymmddTHHMMSS}` exists
- **AND** its baseline equals the FULL export's freeze point
- **AND** no standalone `virsh checkpoint-create-as` call is made by the provider

#### Scenario: Bitmap FULL does not self-record in state

- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully
- **THEN** `self._state.record_full_backup()` is NOT called by the provider
- **AND** state recording is deferred to Core's `_backup_target()` after post-create verification

#### Scenario: Bitmap FULL with dotted VM name

- **WHEN** `BitmapBackupProvider.create_full_backup("3.Projects_opencode", snapshot, target, compress=False)` is called
- **THEN** the FULL backup file is named `3.Projects_opencode.FULL.YYYYMMDD.qcow2`

### Requirement: transfer_missing safety net when prior is None

`BitmapBackupProvider.transfer_missing()` SHALL create a FULL export via `_full_pull_lifecycle()` when `prior is None` (no prior snapshot or checkpoint exists on the target). This is a **safety net** for the edge case where `Core._backup_target()` bypasses FULL creation (e.g., during `--preserve` mode or after a configuration change). The primary path (FULL created by `Core._backup_target()` before `transfer_missing()`) ensures `prior` is always set in normal operation. When the safety net is triggered, the provider SHALL internally call `verify_full_backup()` on the exported file before returning `BackupResult`. Core SHALL then record an incremental dependency for the export (since it came through `transfer_missing()`, not `create_full_backup()`).

#### Scenario: Normal path — prior is always set

- **WHEN** `Core._backup_target()` creates a FULL via `create_full_backup()` then calls `transfer_missing()`
- **THEN** `prior` is the most recent snapshot on the source
- **AND** `transfer_missing()` performs incremental transfer via `_copy_dirty_blocks()`

#### Scenario: Safety net — prior is None triggers full export

- **WHEN** `transfer_missing()` is called with `prior = None`
- **THEN** a full NBD export is performed via `_full_pull_lifecycle()` using `qemu-img convert`
- **AND** `verify_full_backup()` is called on the exported file
- **AND** a `BackupResult` is returned with the full export result
