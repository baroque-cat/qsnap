## MODIFIED Requirements

### Requirement: FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target

`FileCopyBackupProvider.create_full_backup(source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, bucket_level: str = "monthly") -> BackupResult` SHALL create a standalone qcow2 on the target. The method SHALL detect VM running state via `virsh dominfo`. When the VM is running, the method SHALL use the NBD pull-model (`virsh backup-begin` without `--incremental` + `qemu-img convert -n nbd:unix:<socket>`) to avoid lock conflicts on the active layer. When the VM is stopped, the method SHALL use direct `qemu-img convert [-c] -f qcow2 -O qcow2 <source> <target_path>/vm.FULL.YYYYMMDD.qcow2`. When `compress=True`, the `-c` flag SHALL be added to direct convert (NBD path does not support compression — the result is uncompressed). The `bucket_level` parameter SHALL be passed to `IStateManager.record_full_backup()`. The operation SHALL be atomic: convert to a `.tmp` path, then rename to the final name on success.

#### Scenario: Uncompressed full backup succeeds (stopped VM)
- **WHEN** `create_full_backup(snapshot, target, compress=False, bucket_level="monthly")` is called
- **AND** `virsh dominfo` returns `State: shut off`
- **THEN** `qemu-img convert` is invoked WITHOUT `-c` and `BackupResult(success=True)` is returned
- **AND** the FULL is recorded in state with `bucket_level="monthly"`

#### Scenario: Compressed full backup succeeds (stopped VM)
- **WHEN** `create_full_backup(snapshot, target, compress=True, bucket_level="yearly")` is called
- **AND** `virsh dominfo` returns `State: shut off`
- **THEN** `qemu-img convert -c` is invoked
- **AND** the FULL is recorded in state with `bucket_level="yearly"`

#### Scenario: NBD full backup succeeds (running VM)
- **WHEN** `create_full_backup(snapshot, target, compress=False, bucket_level="weekly")` is called
- **AND** `virsh dominfo` returns `State: running`
- **THEN** `virsh backup-begin` is called without `--incremental`
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` is called
- **AND** the FULL is recorded in state with `bucket_level="weekly"`
- **AND** no `--force-share` is used on any data-copying operation

#### Scenario: NBD full backup ignores compress flag
- **WHEN** `create_full_backup(snapshot, target, compress=True, bucket_level="daily")` is called
- **AND** the VM is running (NBD path selected)
- **THEN** the resulting FULL is uncompressed (NBD path does not support `-c`)
- **AND** a WARNING is logged: "compress=True ignored for NBD-based FULL backup"

## ADDED Requirements

### Requirement: BitmapBackupProvider.create_full_backup implemented via NBD

`BitmapBackupProvider` SHALL override `create_full_backup()` to create a standalone FULL backup via the NBD full-export path (no `--incremental` flag). The method SHALL NOT raise `NotImplementedError`. The result SHALL be a standalone qcow2 file on the target. No checkpoint SHALL be created for this FULL — the checkpoint lifecycle remains in `transfer_missing()` for incremental runs.

#### Scenario: Bitmap FULL no longer raises NotImplementedError
- **WHEN** `BitmapBackupProvider.create_full_backup(snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** the method does NOT raise `NotImplementedError`
- **AND** `virsh backup-begin` is called without `--incremental`
- **AND** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2

#### Scenario: Bitmap FULL does not create checkpoint
- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully
- **THEN** no `virsh checkpoint-create-as` is called
- **AND** no `virsh checkpoint-delete` is called
- **AND** the FULL is recorded in state via `IStateManager.record_full_backup()`

#### Scenario: Bucket-driven FULL works for bitmap targets
- **WHEN** `Core._backup_target()` calls `_should_create_bucket_full()` for a bitmap-mode target
- **AND** it returns `(True, "weekly")`
- **THEN** `BitmapBackupProvider.create_full_backup()` is called
- **AND** it succeeds (no crash)
- **AND** the FULL is recorded in state with `bucket_level="weekly"`
