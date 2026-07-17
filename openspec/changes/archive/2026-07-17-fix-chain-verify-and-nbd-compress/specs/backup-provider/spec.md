## MODIFIED Requirements

### Requirement: FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target

`FileCopyBackupProvider.create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, bucket_level: str = "monthly") -> BackupResult` SHALL create a standalone qcow2 on the target. The `vm_name` parameter SHALL be the full, untruncated VM name (e.g. `3.Projects_opencode`), passed from Core's `vm_config.name` — the method SHALL NOT extract the VM name from the snapshot filename. The method SHALL detect VM running state via `virsh dominfo --domain <vm_name>`. When the VM is running, the method SHALL use the NBD pull-model (`virsh backup-begin` without `--incremental` + `qemu-img convert nbd:unix:<socket>`) to avoid lock conflicts on the active layer. When the VM is stopped, the method SHALL use direct `qemu-img convert [-c] -f qcow2 -O qcow2 <source> <target_path>/<vm_name>.FULL.YYYYMMDD.qcow2`. When `compress=True`, the `-c` flag SHALL be added to BOTH the NBD path and the direct convert path. The `bucket_level` parameter SHALL be passed to `IStateManager.record_full_backup()`. The operation SHALL be atomic: convert to a `.tmp` path, then rename to the final name on success.

#### Scenario: Uncompressed full backup succeeds (stopped VM)
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=False, bucket_level="monthly")` is called
- **AND** `virsh dominfo --domain myvm` returns `State: shut off`
- **THEN** `qemu-img convert` is invoked WITHOUT `-c` and `BackupResult(success=True)` is returned
- **AND** the FULL is recorded in state with `bucket_level="monthly"`

#### Scenario: Compressed full backup succeeds (stopped VM)
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, bucket_level="yearly")` is called
- **AND** `virsh dominfo --domain myvm` returns `State: shut off`
- **THEN** `qemu-img convert -c` is invoked
- **AND** the FULL is recorded in state with `bucket_level="yearly"`

#### Scenario: NBD full backup succeeds (running VM)
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=False, bucket_level="weekly")` is called
- **AND** `virsh dominfo --domain myvm` returns `State: running`
- **THEN** `virsh backup-begin` is called without `--incremental`
- **THEN** `qemu-img convert nbd:unix:<socket> <target>` is called
- **AND** the FULL is recorded in state with `bucket_level="weekly"`
- **AND** no `--force-share` is used on any data-copying operation

#### Scenario: NBD full backup supports compression
- **WHEN** `create_full_backup("myvm", snapshot, target, compress=True, bucket_level="daily")` is called
- **AND** the VM is running (NBD path selected)
- **THEN** `qemu-img convert -c nbd:unix:<socket> <target>` is called with the `-c` flag
- **AND** the resulting FULL is compressed (NBD path supports `-c`, experimentally verified with qemu-img 11.0.2)

#### Scenario: Dotted VM name is passed untruncated to virsh dominfo
- **WHEN** `create_full_backup("3.Projects_opencode", snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** `virsh dominfo --domain 3.Projects_opencode` is called with the full VM name
- **AND** the FULL backup file is named `3.Projects_opencode.FULL.YYYYMMDD.qcow2`
- **AND** the VM name is NOT extracted from the snapshot filename via `split(".")`

#### Scenario: transfer_missing passes vm_config.name to create_full_backup
- **WHEN** `transfer_missing(vm_config, target, snapshots)` is called with `vm_config.name = "3.Projects_opencode"`
- **AND** `target.copy_base` is `False` and the target is empty
- **THEN** `self.create_full_backup(vm_config.name, ...)` is called with the full VM name
- **AND** the FULL backup file is named `3.Projects_opencode.FULL.YYYYMMDD.qcow2`
