## REMOVED Requirements

### Requirement: FileCopyBackupProvider creates full backups via qemu-img convert
**Reason**: `FileCopyBackupProvider` is deleted. FULL backups are created exclusively by `BitmapBackupProvider.create_full_backup()` via the NBD full-export path (see the `backup-provider` and `live-vm-full-backup` capabilities).
**Migration**: FULL creation requires a running VM and libvirt >= 7.2. State recording via `IStateManager.record_full_backup()` remains Core's responsibility and is unchanged.

### Requirement: Incremental backups rebase to the FULL anchor
**Reason**: Post-copy `qemu-img rebase -u` was file-copy mechanics. Bitmap incrementals are created as backing-chained qcow2 deltas whose `backing-filename` already points at the previous backup on the target — no rebase step exists.
**Migration**: Native backing chains (see `nbd-dirty-block-transfer`) plus Core's `record_incremental_dependency()` after verified transfers (see `nbd-bitmap-backup`).

## MODIFIED Requirements

### Requirement: Core triggers full backup before incremental transfer

`Core._backup_target()` SHALL retrieve ALL full backups via `state.get_full_backups(target.path)` and pass the complete list to `_should_create_bucket_full()`. The first backup to a target SHALL always be a FULL. When `_should_create_bucket_full()` returns `(True, bucket_level)`, Core SHALL call `provider.create_full_backup(most_recent, target, compress=target.compress, bucket_level=bucket_level)`. The provider uses the NBD pull-model and requires a running VM. Core SHALL NOT pass VM running state to the provider. After FULL creation, Core SHALL record it via `IStateManager.record_full_backup()`.

#### Scenario: First backup to target creates FULL
- **WHEN** `get_full_backups(target.path)` returns an empty list and a snapshot is available
- **THEN** a FULL backup is created via `provider.create_full_backup()`
- **AND** the provider uses the NBD pull-model (running VM required)

#### Scenario: New weekly period triggers FULL (all-buckets mode)
- **WHEN** the policy has `weekly=4` active and no F-anchors, and the current snapshot's ISO week differs from the last weekly FULL's week
- **THEN** a FULL backup is created with `bucket_level="weekly"`

#### Scenario: F-anchor on weekly only triggers FULL at week boundaries
- **WHEN** the policy has `weekly=4, anchor_weekly=True, daily=7, anchor_daily=False`
- **AND** the current snapshot's day differs from the last daily FULL's day
- **THEN** no FULL is created (daily is not an F-anchor)
- **AND** if the current snapshot's week differs from the last weekly FULL's week, a FULL IS created

#### Scenario: FULL creation works for backup targets
- **WHEN** `_should_create_bucket_full()` returns `(True, "monthly")` for a target
- **THEN** `BitmapBackupProvider.create_full_backup()` is called
- **AND** the FULL is created via the NBD full-export path
- **AND** the FULL is recorded in state with `bucket_level="monthly"`

#### Scenario: Dry-run logs FULL-would-be-created without executing
- **WHEN** `Core._backup_target()` is called in dry-run mode
- **AND** `_should_create_bucket_full()` returns `(True, "weekly")`
- **THEN** an INFO log is emitted: "[dry-run] Would create FULL backup (bucket=weekly)"
- **AND** the log includes the transfer method: "via NBD"
- **AND** `provider.create_full_backup()` is NOT called
- **AND** no `virsh backup-begin` or `qemu-img convert` is executed
