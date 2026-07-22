## REMOVED Requirements

### Requirement: NBD full-export helper for FULL backups

**Reason**: The `nbd_full_export()` function in `qsnap/utils/nbd.py` used `qemu-img convert` to pull the full disk. After unification, FULL backups use the same `pread`/`pwrite` engine as incrementals — `qemu-img convert` is no longer in the data path. The helper is deleted entirely.

**Migration**: `BitmapBackupProvider.create_full_backup()` now uses the unified NBD transfer engine directly (see the `nbd-bitmap-backup` capability). The helper functions `write_backup_xml()`, `write_checkpoint_xml()`, `get_first_disk_target()` survive — they are used by `bitmap.py` directly.

## MODIFIED Requirements

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
