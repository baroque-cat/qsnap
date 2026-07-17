## MODIFIED Requirements

### Requirement: Core triggers full backup before incremental transfer

`Core._backup_target()` SHALL retrieve ALL full backups via `state.get_full_backups(target.path)` and pass the complete list to `_should_create_bucket_full()`. Before passing, Core SHALL filter out phantom FULLs — entries in state whose file no longer exists on disk (`os.path.exists()` returns False) — logging a WARNING for each. The first backup to a target SHALL always be a FULL. When `_should_create_bucket_full()` returns `(True, bucket_level)`, Core SHALL call `provider.create_full_backup(most_recent, target, compress=target.compress, bucket_level=bucket_level)`. The provider SHALL internally decide whether to use NBD (running VM) or direct convert (stopped VM). Core SHALL NOT pass VM running state to the provider — the provider detects it itself via `virsh dominfo`. After FULL creation, Core SHALL perform verification according to `GlobalConfig.full_verify_after_create` BEFORE calling `IStateManager.record_full_backup()`. When `full_verify_after_create = "hash"`, Core SHALL pass `source_path=most_recent.path` to `verify_full_backup()` for `qemu-img compare` content verification. On verification failure, the FULL file SHALL be deleted and `record_full_backup()` SHALL NOT be called. On verification success, Core SHALL record the FULL via `IStateManager.record_full_backup()`.

#### Scenario: FULL created and verified before state recording
- **WHEN** `provider.create_full_backup()` returns `BackupResult(success=True)`
- **AND** `GlobalConfig.full_verify_after_create = "check"`
- **THEN** `verify_full_backup()` is called on the FULL file with `verify_mode="check"`
- **AND** on success, `record_full_backup()` is called
- **AND** `BackupResult(success=True)` is returned

#### Scenario: FULL verification fails — file deleted, not recorded
- **WHEN** `provider.create_full_backup()` returns `BackupResult(success=True)`
- **AND** `verify_full_backup()` returns an error string
- **THEN** the FULL file is deleted via `rm -f`
- **AND** `record_full_backup()` is NOT called
- **AND** `BackupResult(success=False, error="verification failed: ...")` is returned

#### Scenario: First backup to target creates FULL
- **WHEN** `get_full_backups(target.path)` returns an empty list and a snapshot is available
- **THEN** a FULL backup is created via `provider.create_full_backup()`
- **AND** the provider selects NBD or direct convert based on VM running state
- **AND** verification is performed after creation

#### Scenario: New weekly period triggers FULL (all-buckets mode)
- **WHEN** the policy has `weekly=4` active and no F-anchors, and the current snapshot's ISO week differs from the last weekly FULL's week
- **THEN** a FULL backup is created with `bucket_level="weekly"`
- **AND** the provider uses NBD if the VM is running, direct convert if stopped
- **AND** verification is performed after creation

### Requirement: Cleanup backups with pre-deletion verification

`Core._cleanup_backups()` SHALL perform M1 verification on every FULL backup before cascade-deletion. This check is NON-CONFIGURABLE. If `GlobalConfig.full_verify_before_delete` is `"check"`, M2 (`qemu-img check`) SHALL also be performed. On any verification failure, the FULL and ALL its dependent incrementals SHALL be preserved, and a CRITICAL log SHALL be emitted.

#### Scenario: Cleanup proceeds after M1 passes
- **WHEN** `_cleanup_backups()` is about to cascade-delete a FULL with dependents
- **AND** M1 verification of the FULL passes
- **THEN** cascade-deletion proceeds normally

#### Scenario: Cleanup blocked when M1 fails on FULL
- **WHEN** `_cleanup_backups()` is about to cascade-delete a FULL
- **AND** M1 verification of the FULL fails
- **THEN** the FULL is NOT deleted
- **AND** all dependent incrementals are NOT deleted
- **AND** a CRITICAL log is emitted

### Requirement: File existence guard before blockcommit

Before `Core._blockcommit_snapshots()` passes `to_merge` list to `ILifecycleManager.blockcommit()`, Core SHALL verify each snapshot's file path exists on disk. For snapshots where the file does not exist, Core SHALL call `IStateManager.remove_snapshot()` and remove the entry from `to_merge`. If `to_merge` becomes empty after filtering, the blockcommit step SHALL be skipped.

#### Scenario: Stale entry filtered before blockcommit
- **WHEN** `to_merge` contains a snapshot whose file does not exist
- **AND** that snapshot was already blockcommitted by a prior run (stale state)
- **THEN** `remove_snapshot()` is called for the stale entry
- **AND** the stale entry is removed from `to_merge`
- **AND** remaining snapshots in `to_merge` are blockcommitted normally

#### Scenario: All entries stale — blockcommit skipped
- **WHEN** every entry in `to_merge` references a non-existent file
- **THEN** all entries are removed from state
- **AND** blockcommit is skipped entirely
- **AND** an INFO log is emitted

## ADDED Requirements

### Requirement: Phantom FULL detection in get_full_backups

Before using the list returned by `IStateManager.get_full_backups()` for bucket-level decisions, Core SHALL verify each FULL file exists on disk via `os.path.exists(str(full.path))`. Entries where the file does not exist SHALL be removed from state via `IStateManager.remove_full_backup()` and a WARNING SHALL be logged. This prevents phantom FULLs (deleted externally but still in state) from blocking the creation of new FULLs for their bucket periods.

#### Scenario: Phantom FULL detected and removed from state
- **WHEN** `get_full_backups()` returns a `FullBackupInfo` entry
- **AND** `os.path.exists(full.path)` returns False (file was deleted externally)
- **THEN** `IStateManager.remove_full_backup(target_path, full.name)` is called
- **AND** a WARNING is logged: "Phantom FULL entry: <name> file not found on disk — removed from state"
- **AND** the phantom entry is excluded from the list passed to `_should_create_bucket_full()`

#### Scenario: All FULLs exist — no entries removed
- **WHEN** `get_full_backups()` returns entries and all files exist on disk
- **THEN** no entries are removed from state
- **AND** the full list is passed to `_should_create_bucket_full()`

### Requirement: M3 content comparison receives source_path from Core

When `GlobalConfig.full_verify_after_create = "hash"`, Core SHALL pass `source_path=most_recent.path` to `verify_full_backup()` so that `qemu-img compare` can verify the FULL's disk content matches the source snapshot's disk content. This replaces the previous SHA-256 hash approach (which was incorrect — SHA-256 of a snapshot delta file with backing chain never matches SHA-256 of a standalone NBD-converted FULL).

#### Scenario: Hash mode Full verification passes source_path
- **WHEN** `full_verify_after_create = "hash"`
- **AND** `create_full_backup()` returns success
- **THEN** `verify_full_backup(shell, full_path, "hash", source_path=most_recent.path)` is called
- **AND** `qemu-img compare -q --force-share <most_recent.path> <full_path>` is executed
- **AND** on match, `record_full_backup()` is called
- **AND** on mismatch, the FULL file is deleted and `record_full_backup()` is NOT called
