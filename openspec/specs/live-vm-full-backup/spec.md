# Live VM Full Backup via NBD

## Purpose

Full backup creation for live (running) VMs via the NBD pull-model (`virsh backup-begin` without `--incremental` + `qemu-img convert -n nbd:unix:<socket>`). Stopped-VM FULL backups return `BackupResult(success=False)` — there is no direct `qemu-img convert` fallback. Avoids lock conflicts on the active layer of running VMs without using `--force-share` on data-copying operations.

## Requirements

### Requirement: FULL backup requires a running VM

`BitmapBackupProvider.create_full_backup()` SHALL receive `vm_name: str` as an explicit method parameter (the first positional argument, passed from Core's `vm_config.name`). The method SHALL NOT extract the VM name from the snapshot filename. The method SHALL use the NBD pull-model (`virsh backup-begin` without `--incremental`) to export a frozen point-in-time view of the disk. A running VM is required: when the VM is shut off, `virsh backup-begin` fails and the method SHALL return `BackupResult(success=False, error=<virsh error>)`. No direct `qemu-img convert` fallback SHALL be attempted.

#### Scenario: Running VM triggers NBD-based FULL backup
- **WHEN** `create_full_backup("myvm", ...)` is called and the VM is running
- **THEN** the provider uses `virsh backup-begin` + `qemu-img convert -n nbd:unix:<socket>` to create the FULL
- **AND** no direct `qemu-img convert` on the snapshot file is attempted

#### Scenario: Stopped VM fails with a BackupResult error
- **WHEN** `create_full_backup("myvm", ...)` is called and the VM is shut off
- **THEN** `virsh backup-begin` fails (domain not running)
- **AND** `BackupResult(success=False, error=...)` is returned
- **AND** no direct `qemu-img convert` fallback is attempted

#### Scenario: Dotted VM name passed untruncated to is_vm_running
- **WHEN** `create_full_backup("3.Projects_opencode", ...)` is called
- **THEN** `nbd_full_export(shell, "3.Projects_opencode", ...)` is called with the full VM name
- **AND** `virsh backup-begin --domain 3.Projects_opencode` is executed (not `--domain 3`)

#### Scenario: Core passes vm_config.name to create_full_backup
- **WHEN** `Core._backup_target(vm_config, target, snapshots)` is called with `vm_config.name = "3.Projects_opencode"`
- **AND** `_should_create_bucket_full()` returns `(True, bucket_level)`
- **THEN** `provider.create_full_backup(vm_config.name, most_recent, target, ...)` is called
- **AND** the full VM name `3.Projects_opencode` is passed as the `vm_name` parameter

### Requirement: NBD full-export helper for FULL backups

The system SHALL provide a shared NBD full-export mechanism used by `BitmapBackupProvider.create_full_backup()` and by Core's restore/fork path. The mechanism SHALL: (1) remove any stale socket at `/tmp/qsnap-backup-{pid}.sock`, (2) write a backup XML, (3) run `virsh backup-begin --domain <vm> <xml>` WITHOUT `--incremental` (full export, no checkpoint), (4) run `qemu-img convert -O qcow2 [-c -o compression_type=<type>] nbd:unix:<socket> <target_file>` to pull the full disk, (5) clean up the socket via `rm -f` in a `finally` block. The function signature SHALL be `nbd_full_export(shell: IShell, vm_name: str, target_file: str | Path, compress: bool = False, compression_type: str = "zstd") -> ShellResult`. When `compress=True` and `compression_type="zstd"`, the convert command SHALL include `-c -o compression_type=zstd`. When `compress=True` and `compression_type="zlib"`, the convert command SHALL include only `-c` (default zlib). The `qemu-img convert` command SHALL be executed via `IShell.run_with_stall_detection()` with `output_file` set to `target_file` and `stall_timeout` from the target config.

#### Scenario: NBD full export with zstd compression
- **WHEN** `nbd_full_export(shell, "myvm", "/target/full.qcow2", compress=True, compression_type="zstd")` is called
- **THEN** `virsh backup-begin` is called without `--incremental`
- **THEN** `qemu-img convert -O qcow2 -c -o compression_type=zstd nbd:unix:<socket> <target>` is executed
- **THEN** the resulting file has no backing file

#### Scenario: NBD full export with zlib compression
- **WHEN** `nbd_full_export(shell, "myvm", "/target/full.qcow2", compress=True, compression_type="zlib")` is called
- **THEN** `qemu-img convert -O qcow2 -c nbd:unix:<socket> <target>` is executed (default zlib)

#### Scenario: NBD full export without compression
- **WHEN** `nbd_full_export(shell, "myvm", "/target/full.qcow2", compress=False, compression_type="zstd")` is called
- **THEN** `qemu-img convert -O qcow2 nbd:unix:<socket> <target>` is executed (no `-c` flag)
- **AND** the `compression_type` parameter is ignored

#### Scenario: NBD full export uses stall detection
- **WHEN** `nbd_full_export(shell, "myvm", "/target/full.qcow2.tmp", compress=True, compression_type="zstd")` is called
- **AND** the calling context provides `stall_timeout` from target config
- **THEN** the `qemu-img convert` command is executed via `shell.run_with_stall_detection(cmd, output_file=Path(target_file), stall_timeout=...)`
- **AND** if the output file stops growing, the convert is killed

#### Scenario: NBD full export produces standalone qcow2
- **WHEN** the NBD full-export helper is called for a running VM
- **THEN** `virsh backup-begin` is called without `--incremental`
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2 file
- **THEN** the resulting file has no backing file (`qemu-img info` shows `backing file: <none>`)

#### Scenario: NBD socket cleaned up on success
- **WHEN** the NBD full export completes successfully
- **THEN** the Unix socket file is removed via `rm -f`

#### Scenario: NBD socket cleaned up on failure
- **WHEN** `qemu-img convert` from NBD fails
- **THEN** the Unix socket file is still removed via `rm -f` in the `finally` block
- **AND** `BackupResult(success=False, error=<stderr>)` is returned

### Requirement: NBD FULL exports current disk state

The NBD full-export mechanism exports the disk state at the moment of `virsh backup-begin`, which MAY be slightly newer than the last snapshot (writes between snapshot creation and FULL backup creation). The FULL backup timestamp SHALL be recorded as the snapshot's timestamp (for retention bucket alignment), NOT the NBD export time.

#### Scenario: FULL timestamp matches snapshot, not export time
- **WHEN** a FULL backup is created via NBD at time T_export
- **AND** the source snapshot was created at time T_snapshot
- **THEN** the FULL is recorded in state with `timestamp = T_snapshot`
- **AND** retention bucket alignment uses T_snapshot

### Requirement: Atomic FULL file creation via NBD

When using NBD for FULL backup, the target file SHALL be created at a `.tmp` path first, then atomically renamed to the final `vm.FULL.YYYYMMDD.qcow2` name on success. This matches the project-wide atomic-creation pattern for backup outputs.

#### Scenario: NBD FULL creates tmp then renames
- **WHEN** NBD full export succeeds
- **THEN** the data is written to `<target_path>/vm.FULL.YYYYMMDD.qcow2.tmp`
- **THEN** the file is renamed to `<target_path>/vm.FULL.YYYYMMDD.qcow2`
- **AND** `BackupResult(success=True, path=<final_path>)` is returned

#### Scenario: NBD FULL failure leaves no final file
- **WHEN** NBD full export fails
- **THEN** the `.tmp` file is removed
- **AND** no `vm.FULL.*.qcow2` file is created
- **AND** `BackupResult(success=False, error=<message>)` is returned
