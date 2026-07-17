## MODIFIED Requirements

### Requirement: Core triggers full backup before incremental transfer

`Core._backup_target()` SHALL retrieve ALL full backups via `state.get_full_backups(target.path)` and pass the complete list to `_should_create_bucket_full()`. The first backup to a target SHALL always be a FULL. When `_should_create_bucket_full()` returns `(True, bucket_level)`, Core SHALL call `provider.create_full_backup(most_recent, target, compress=target.compress, bucket_level=bucket_level)`. The provider SHALL internally decide whether to use NBD (running VM) or direct convert (stopped VM). Core SHALL NOT pass VM running state to the provider — the provider detects it itself via `virsh dominfo`. After FULL creation, Core SHALL record it via `IStateManager.record_full_backup()`.

#### Scenario: First backup to target creates FULL
- **WHEN** `get_full_backups(target.path)` returns an empty list and a snapshot is available
- **THEN** a FULL backup is created via `provider.create_full_backup()`
- **AND** the provider selects NBD or direct convert based on VM running state

#### Scenario: New weekly period triggers FULL (all-buckets mode)
- **WHEN** the policy has `weekly=4` active and no F-anchors, and the current snapshot's ISO week differs from the last weekly FULL's week
- **THEN** a FULL backup is created with `bucket_level="weekly"`
- **AND** the provider uses NBD if the VM is running, direct convert if stopped

#### Scenario: F-anchor on weekly only triggers FULL at week boundaries
- **WHEN** the policy has `weekly=4, anchor_weekly=True, daily=7, anchor_daily=False`
- **AND** the current snapshot's day differs from the last daily FULL's day
- **THEN** no FULL is created (daily is not an F-anchor)
- **AND** if the current snapshot's week differs from the last weekly FULL's week, a FULL IS created

#### Scenario: FULL creation works for both file-copy and bitmap targets
- **WHEN** `_should_create_bucket_full()` returns `(True, "monthly")` for a bitmap-mode target
- **THEN** `BitmapBackupProvider.create_full_backup()` is called (no longer raises `NotImplementedError`)
- **AND** the FULL is created via NBD full export
- **AND** the FULL is recorded in state with `bucket_level="monthly"`

#### Scenario: Dry-run logs FULL-would-be-created without executing
- **WHEN** `Core._backup_target()` is called in dry-run mode
- **AND** `_should_create_bucket_full()` returns `(True, "weekly")`
- **THEN** an INFO log is emitted: "[dry-run] Would create FULL backup (bucket=weekly)"
- **AND** the log includes the transfer method: "via NBD (VM running)" or "via direct convert (VM stopped)"
- **AND** `provider.create_full_backup()` is NOT called
- **AND** no `virsh backup-begin` or `qemu-img convert` is executed
