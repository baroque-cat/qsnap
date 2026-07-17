## MODIFIED Requirements

### Requirement: VM running-state detection for FULL backup method selection

`FileCopyBackupProvider.create_full_backup()` and `BitmapBackupProvider.create_full_backup()` SHALL receive `vm_name: str` as an explicit method parameter (the first positional argument, passed from Core's `vm_config.name`). The method SHALL NOT extract the VM name from the snapshot filename. The method SHALL detect whether the source VM is running by calling `virsh dominfo --domain <vm_name>` with the full, untruncated VM name and parsing the `State:` line. If the VM state is `running`, the provider SHALL use the NBD pull-model to export a frozen point-in-time view of the disk. If the VM state is `shut off` (or any non-running state), the provider SHALL use direct `qemu-img convert` on the snapshot file (existing behavior, no lock conflict).

#### Scenario: Running VM triggers NBD-based FULL backup
- **WHEN** `virsh dominfo --domain myvm` returns `State: running`
- **AND** `create_full_backup("myvm", ...)` is called
- **THEN** the provider uses `virsh backup-begin` + `qemu-img convert -n nbd:unix:<socket>` to create the FULL
- **AND** no direct `qemu-img convert` on the snapshot file is attempted

#### Scenario: Stopped VM triggers direct convert FULL backup
- **WHEN** `virsh dominfo --domain myvm` returns `State: shut off`
- **AND** `create_full_backup("myvm", ...)` is called
- **THEN** the provider uses `qemu-img convert [-c] -f qcow2 -O qcow2 <source> <target>` directly
- **AND** no NBD export is started

#### Scenario: VM state detection failure falls back to direct convert with warning
- **WHEN** `virsh dominfo` fails (non-zero exit code)
- **THEN** the provider logs a WARNING and attempts direct `qemu-img convert`
- **AND** if direct convert fails with a lock error, `BackupResult(success=False, error="...lock...")` is returned

#### Scenario: Dotted VM name passed untruncated to is_vm_running
- **WHEN** `create_full_backup("3.Projects_opencode", ...)` is called
- **THEN** `is_vm_running(shell, "3.Projects_opencode")` is called with the full VM name
- **AND** `virsh dominfo --domain 3.Projects_opencode` is executed (not `--domain 3`)
- **AND** if the VM is running, `nbd_full_export(shell, "3.Projects_opencode", ...)` is called with the full VM name

#### Scenario: Core passes vm_config.name to create_full_backup
- **WHEN** `Core._backup_target(vm_config, target, snapshots)` is called with `vm_config.name = "3.Projects_opencode"`
- **AND** `_should_create_bucket_full()` returns `(True, bucket_level)`
- **THEN** `provider.create_full_backup(vm_config.name, most_recent, target, ...)` is called
- **AND** the full VM name `3.Projects_opencode` is passed as the `vm_name` parameter
