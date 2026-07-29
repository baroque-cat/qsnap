# Live VM Full Backup via NBD

## Purpose

Full backup creation for live (running) and stopped VMs. Running VMs use the NBD pull-model (`virsh backup-begin` without `--incremental` + `qemu-img convert nbd:unix:<socket>`). Stopped VMs use direct `qemu-img convert <source_path> <target>`. The Python `pread`/`pwrite` loop is no longer used for FULLs — `qemu-img convert` (C code, parallel coroutines) provides ~30-60x faster compression. Atomic `.tmp` → final rename matches the project-wide pattern. Backup timestamp is recorded as the snapshot's timestamp for retention bucket alignment.

## Requirements

### Requirement: FULL backup requires a running VM or stopped-VM fallback

`BitmapBackupProvider.create_full_backup()` SHALL receive `vm_name: str` as an explicit method parameter (the first positional argument, passed from Core's `vm_config.name`). The method SHALL detect VM state via `is_vm_running()`.

When the VM is **running**, the method SHALL use the NBD pull-model (`virsh backup-begin` without `--incremental`) to export a frozen point-in-time view of the disk, then transfer data via `qemu-img convert nbd:unix:<socket> <target>.tmp`.

When the VM is **stopped**, the method SHALL use direct `qemu-img convert <source_path> <target>.tmp` from the source qcow2 file (no `virsh backup-begin`, no NBD socket). The source path SHALL be resolved via `get_first_disk_path()`.

The method SHALL NOT use the Python `pread`/`pwrite` loop for FULL backups. The method SHALL NOT use `nbd_full_export()`.

When `compress=True` and `compression_type="zstd"`, the `qemu-img convert` command SHALL include `-c -O qcow2 -o compression_type=zstd`. When `compress=True` and `compression_type="zlib"`, the command SHALL include `-c -O qcow2 -o compression_type=zlib`. When `compress=False`, neither `-c` nor `-o compression_type=` SHALL be present.

#### Scenario: Running VM triggers NBD-based FULL backup
- **WHEN** `create_full_backup("myvm", ...)` is called and the VM is running
- **THEN** the provider uses `virsh backup-begin` + `qemu-img convert nbd:unix:<socket>` to create the FULL
- **AND** no Python `pread`/`pwrite` loop runs
- **AND** no write-side `qemu-nbd` is started

#### Scenario: Stopped VM uses direct qemu-img convert
- **WHEN** `create_full_backup("myvm", ...)` is called and the VM is shut off
- **THEN** `virsh backup-begin` is NOT called
- **AND** `qemu-img convert` reads directly from the source qcow2 file
- **AND** `BackupResult(success=True, ...)` is returned on success

#### Scenario: Dotted VM name passed untruncated
- **WHEN** `create_full_backup("3.Projects_opencode", ...)` is called
- **THEN** `virsh backup-begin --domain 3.Projects_opencode` is executed (not `--domain 3`)

#### Scenario: Core passes vm_config.name to create_full_backup
- **WHEN** `Core._backup_target(vm_config, target, snapshots)` is called with `vm_config.name = "3.Projects_opencode"`
- **AND** the inline FULL-creation logic in `_backup_target()` determines a FULL is needed (when `target.target_chain_length` is not `None` and `incremental_count > target.target_chain_length`)
- **THEN** `provider.create_full_backup(vm_config.name, most_recent, target, ...)` is called
- **AND** the full VM name `3.Projects_opencode` is passed as the `vm_name` parameter

### Requirement: Atomic FULL file creation via qemu-img convert

When using `qemu-img convert` for FULL backup, the target file SHALL be created at a `.tmp` path first, then atomically renamed to the final `vm.FULL.YYYYMMDD.qcow2` name on success. This matches the project-wide atomic-creation pattern for backup outputs.

#### Scenario: qemu-img convert FULL creates tmp then renames
- **WHEN** `qemu-img convert` succeeds
- **THEN** the data is written to `<target_path>/vm.FULL.YYYYMMDD.qcow2.tmp`
- **THEN** the file is renamed to `<target_path>/vm.FULL.YYYYMMDD.qcow2`
- **AND** `BackupResult(success=True, path=<final_path>)` is returned

#### Scenario: qemu-img convert FULL failure leaves no final file
- **WHEN** `qemu-img convert` fails
- **THEN** the `.tmp` file is removed
- **AND** no `vm.FULL.*.qcow2` file is created
- **AND** `BackupResult(success=False, error=<message>)` is returned

### Requirement: NBD FULL exports current disk state

The NBD full-export mechanism exports the disk state at the moment of `virsh backup-begin`, which MAY be slightly newer than the last snapshot (writes between snapshot creation and FULL backup creation). The FULL backup timestamp SHALL be recorded as the snapshot's timestamp (for retention bucket alignment), NOT the NBD export time.

#### Scenario: FULL timestamp matches snapshot, not export time
- **WHEN** a FULL backup is created via NBD at time T_export
- **AND** the source snapshot was created at time T_snapshot
- **THEN** the FULL is recorded in state with `timestamp = T_snapshot`
- **AND** retention bucket alignment uses T_snapshot
