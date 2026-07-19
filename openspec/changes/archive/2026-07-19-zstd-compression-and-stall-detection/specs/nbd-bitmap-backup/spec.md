## MODIFIED Requirements

### Requirement: Transfer missing snapshots via dirty bitmap extraction

The system SHALL determine which snapshots are missing on the target and for each SHALL use `virsh backup-begin` with NBD export to transfer data. On first backup (no prior checkpoint), a full export is performed. On subsequent backups, only dirty blocks since the last checkpoint are exported. The `qemu-img convert` command SHALL include `-c -o compression_type=<type>` when `target.compress=True` and `compression_type` is passed from Core. The `qemu-img convert` command SHALL be executed via `IShell.run_with_stall_detection()` with `output_file` set to the target file path and `stall_timeout` from `target.backup_stall_timeout`.

#### Scenario: First backup — full NBD export (no prior checkpoint)
- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `BitmapBackupProvider` performs a full NBD export
- **THEN** the backup is a standalone qcow2 file on the target containing the complete virtual disk

#### Scenario: Incremental backup — dirty blocks only
- **WHEN** a prior qsnap checkpoint exists for this VM+target
- **AND** the VM has written data since that checkpoint
- **THEN** `virsh backup-begin` exports only changed blocks via NBD
- **THEN** the resulting backup file size is proportional to the changed data, not the full disk

#### Scenario: Incremental NBD transfer with zstd compression
- **WHEN** `transfer_missing()` is called with `target.compress=True`, `compression_type="zstd"`
- **THEN** `qemu-img convert -O qcow2 -c -o compression_type=zstd nbd:unix:<socket> <target>` is executed

#### Scenario: Incremental NBD transfer with zlib compression
- **WHEN** `transfer_missing()` is called with `target.compress=True`, `compression_type="zlib"`
- **THEN** `qemu-img convert -O qcow2 -c nbd:unix:<socket> <target>` is executed (default zlib)

#### Scenario: Incremental NBD transfer uses stall detection
- **WHEN** `transfer_missing()` is called with `target.backup_stall_timeout = "30m"`
- **THEN** the `qemu-img convert` command is executed via `shell.run_with_stall_detection(cmd, output_file=target_file, stall_timeout=1800)`
- **AND** if the output file stops growing for 30 minutes, the convert is killed

#### Scenario: Checkpoint cleanup after successful transfer
- **WHEN** `qemu-img convert` completes successfully
- **THEN** the prior checkpoint is deleted via `virsh checkpoint-delete --metadata`
- **THEN** a new checkpoint is created for the next incremental run

#### Scenario: Transfer failure preserves checkpoint
- **WHEN** `qemu-img convert` from NBD fails (including stall detection kill)
- **THEN** the checkpoint is NOT deleted
- **THEN** the module returns `BackupResult(success=False, error=<stderr>)`
- **THEN** the NBD socket is cleaned up via `rm -f` in the `finally` block
