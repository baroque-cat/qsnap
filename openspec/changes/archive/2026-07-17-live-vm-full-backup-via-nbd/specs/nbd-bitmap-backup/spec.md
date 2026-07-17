## ADDED Requirements

### Requirement: BitmapBackupProvider.create_full_backup via NBD full export

`BitmapBackupProvider` SHALL implement `create_full_backup()` using the NBD full-export path (no `--incremental` flag). This produces a standalone qcow2 on the target. The method SHALL NOT raise `NotImplementedError`. No checkpoint SHALL be created or deleted for this FULL — the checkpoint lifecycle remains exclusively in `transfer_missing()` for incremental runs.

#### Scenario: Bitmap FULL via NBD succeeds
- **WHEN** `BitmapBackupProvider.create_full_backup(snapshot, target, compress=False, bucket_level="monthly")` is called
- **THEN** `virsh backup-begin` is called without `--incremental`
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2
- **AND** no `virsh checkpoint-create-as` is called
- **AND** no `virsh checkpoint-delete` is called

#### Scenario: Bitmap FULL socket cleanup
- **WHEN** the NBD full export completes (success or failure)
- **THEN** the Unix socket is removed via `rm -f` in a `finally` block

#### Scenario: Bucket-driven FULL no longer crashes bitmap targets
- **WHEN** `Core._backup_target()` triggers `_should_create_bucket_full()` for a bitmap-mode target
- **AND** it returns `(True, bucket_level)`
- **THEN** `BitmapBackupProvider.create_full_backup()` is called and succeeds
- **AND** the FULL is recorded in state with the given `bucket_level`
