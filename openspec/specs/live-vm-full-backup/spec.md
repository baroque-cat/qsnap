# Live VM Full Backup via NBD

## Purpose

Full backup creation for live (running) VMs via the NBD pull-model (`virsh backup-begin` without `--incremental` + `qemu-img convert -n nbd:unix:<socket>`). Stopped-VM FULL backups return `BackupResult(success=False)` — there is no direct `qemu-img convert` fallback. Avoids lock conflicts on the active layer of running VMs without using `--force-share` on data-copying operations.

## Requirements

### Requirement: FULL backup requires a running VM

`BitmapBackupProvider.create_full_backup()` SHALL receive `vm_name: str` as an explicit method parameter (the first positional argument, passed from Core's `vm_config.name`). The method SHALL NOT extract the VM name from the snapshot filename. The method SHALL use the NBD pull-model (`virsh backup-begin` without `--incremental`) to export a frozen point-in-time view of the disk. A running VM is required: when the VM is shut off, `virsh backup-begin` fails and the method SHALL return `BackupResult(success=False, error=<virsh error>)`. No direct `qemu-img convert` fallback SHALL be attempted. The method SHALL use the **unified NBD transfer engine** (not `nbd_full_export()`) to pull the full disk via `pread`/`pwrite` with `zero_skip=True`.

#### Scenario: Running VM triggers NBD-based FULL backup
- **WHEN** `create_full_backup("myvm", ...)` is called and the VM is running
- **THEN** the provider uses `virsh backup-begin` + the unified NBD engine to create the FULL
- **AND** no `qemu-img convert` is executed
- **AND** no direct `qemu-img convert` on the snapshot file is attempted

#### Scenario: Stopped VM fails with a BackupResult error
- **WHEN** `create_full_backup("myvm", ...)` is called and the VM is shut off
- **THEN** `virsh backup-begin` fails (domain not running)
- **AND** `BackupResult(success=False, error=...)` is returned
- **AND** no direct `qemu-img convert` fallback is attempted

#### Scenario: Dotted VM name passed untruncated
- **WHEN** `create_full_backup("3.Projects_opencode", ...)` is called
- **THEN** `virsh backup-begin --domain 3.Projects_opencode` is executed (not `--domain 3`)

#### Scenario: Core passes vm_config.name to create_full_backup
- **WHEN** `Core._backup_target(vm_config, target, snapshots)` is called with `vm_config.name = "3.Projects_opencode"`
- **AND** `_should_create_bucket_full()` returns `(True, bucket_level)`
- **THEN** `provider.create_full_backup(vm_config.name, most_recent, target, ...)` is called
- **AND** the full VM name `3.Projects_opencode` is passed as the `vm_name` parameter



### Requirement: Atomic FULL file creation via NBD

When using the unified NBD engine for FULL backup, the target file SHALL be created at a `.tmp` path first, then atomically renamed to the final `vm.FULL.YYYYMMDD.qcow2` name on success. This matches the project-wide atomic-creation pattern for backup outputs.

#### Scenario: NBD FULL creates tmp then renames
- **WHEN** the unified engine succeeds
- **THEN** the data is written to `<target_path>/vm.FULL.YYYYMMDD.qcow2.tmp`
- **THEN** the file is renamed to `<target_path>/vm.FULL.YYYYMMDD.qcow2`
- **AND** `BackupResult(success=True, path=<final_path>)` is returned

#### Scenario: NBD FULL failure leaves no final file
- **WHEN** the unified engine fails
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
