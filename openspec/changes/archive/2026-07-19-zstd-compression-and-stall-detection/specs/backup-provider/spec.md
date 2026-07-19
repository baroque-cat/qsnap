## ADDED Requirements

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

## MODIFIED Requirements

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

### Requirement: FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target

`FileCopyBackupProvider.create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, compression_type: str = "zstd", bucket_level: str = "monthly") -> BackupResult` SHALL create a standalone qcow2 on the target. The `vm_name` parameter SHALL be the full, untruncated VM name (e.g. `3.Projects_opencode`), passed from Core's `vm_config.name`. The method SHALL detect VM running state via `virsh dominfo --domain <vm_name>`. When the VM is running, the method SHALL use the NBD pull-model to avoid lock conflicts. When the VM is stopped, the method SHALL use direct `qemu-img convert`. When `compress=True` and `compression_type="zstd"`, the `-c -o compression_type=zstd` flags SHALL be added to BOTH the NBD path and the direct convert path. When `compress=True` and `compression_type="zlib"`, only `-c` SHALL be added (default zlib). The `bucket_level` parameter SHALL be passed to `IStateManager.record_full_backup()`. The operation SHALL be atomic: convert to a `.tmp` path, then rename to the final name on success. The `qemu-img convert` command SHALL use `IShell.run_with_stall_detection()` with `output_file` set to the `.tmp` file path and `stall_timeout` from `target.backup_stall_timeout`.

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
- **WHEN** `create_full_backup("3.Projects_opencode", snapshot, target, compress=False, compression_type="zstd", bucket_level="monthly")` is called
- **THEN** `virsh dominfo --domain 3.Projects_opencode` is called with the full VM name
- **AND** the FULL backup file is named `3.Projects_opencode.FULL.YYYYMMDD.qcow2`

### Requirement: BitmapBackupProvider.create_full_backup implemented via NBD

`BitmapBackupProvider` SHALL override `create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, compression_type: str = "zstd", bucket_level: str = "monthly") -> BackupResult` to create a standalone FULL backup via the NBD full-export path. The `compression_type` parameter SHALL be passed through to `nbd_full_export()`. The method SHALL NOT raise `NotImplementedError`. The result SHALL be a standalone qcow2 file on the target. No checkpoint SHALL be created for this FULL.

#### Scenario: Bitmap FULL with zstd compression
- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", bucket_level="monthly")` is called
- **THEN** `nbd_full_export(shell, "myvm", target_file, compress=True, compression_type="zstd")` is called
- **AND** the resulting FULL is compressed with zstd

#### Scenario: Bitmap FULL with zlib compression
- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zlib", bucket_level="monthly")` is called
- **THEN** `nbd_full_export(shell, "myvm", target_file, compress=True, compression_type="zlib")` is called
- **AND** the resulting FULL is compressed with zlib

#### Scenario: Bitmap FULL no longer raises NotImplementedError
- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=False, compression_type="zstd", bucket_level="monthly")` is called
- **THEN** the method does NOT raise `NotImplementedError`
- **AND** `virsh backup-begin` is called without `--incremental`
- **AND** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2
