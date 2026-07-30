# Periodic Full Backup

## Purpose

Periodic creation of standalone (anchor) full backups via `qemu-img convert` on backup targets. Full backups provide a self-contained restore point independent of the incremental chain.

## Requirements

### Requirement: Core triggers full backup before incremental transfer

`Core._backup_target()` SHALL count the incrementals in the newest chain by calling `state.get_incremental_dependencies(target_path, newest_full.name)`. When `target.target_chain_length` is NOT `None` AND `incremental_count > target.target_chain_length`, Core SHALL create a new FULL backup via `provider.create_full_backup()`. When `target.target_chain_length` is `None` (unconfigured), the incremental count SHALL NOT trigger a FULL backup — FULL creation is triggered only by explicit `target_chain_length` configuration. The first backup to a target (no existing FULLs) SHALL always be a FULL. After FULL creation, Core SHALL verify it (M1/M2 per `full_verify_after_create`). Only after verification succeeds SHALL Core record the FULL in state and evaluate retention + cleanup old generations. If verification fails, Core SHALL rollback (delete FULL file + checkpoint + state records) and retry up to `backup_retry_max` times. When `backup_retry_max <= 0`, Core SHALL execute one FULL creation attempt (no retry loop, but one attempt). Core SHALL NOT delegate FULL creation to `IBucketFullStrategy` — the decision is a simple count check.

#### Scenario: First backup to target creates FULL

- **WHEN** `get_full_backups(target.path)` returns an empty list and a snapshot is available
- **THEN** a FULL backup is created via `provider.create_full_backup()`

#### Scenario: Incremental count exceeds chain length triggers FULL

- **WHEN** the newest chain has 169 incrementals and `target.target_chain_length = 168`
- **THEN** a FULL backup is created

#### Scenario: target_chain_length is None — no FULL triggered by count

- **WHEN** the newest chain has 10 incrementals and `target.target_chain_length` is `None` (unconfigured)
- **THEN** no FULL is created — incremental transfer proceeds
- **AND** the behavior is as if `target_chain_length` were infinite (never triggers FULL based on count)

#### Scenario: Incremental count within chain length skips FULL

- **WHEN** the newest chain has 100 incrementals and `target.target_chain_length = 168`
- **THEN** no FULL is created, incremental transfer proceeds

#### Scenario: backup_retry_max = 0 — single attempt

- **WHEN** a FULL backup is to be created and `target.backup_retry_max = 0`
- **THEN** exactly one `create_full_backup()` call is made (no empty loop)
- **AND** if the attempt fails, `full_verification_failed` is set to `True`
- **AND** old generations are preserved (verify-before-delete gate)

#### Scenario: Verified FULL triggers retention + cleanup

- **WHEN** a FULL is created and passes M1/M2 verification
- **THEN** Core records it via `state.record_full_backup()`
- **AND** evaluates retention (keep newest `keep_generations` chains)
- **AND** deletes old generations via `_cleanup_backups()`

#### Scenario: Failed FULL verification triggers rollback

- **WHEN** a FULL is created but fails M1/M2 verification
- **THEN** Core deletes the broken FULL file from disk
- **AND** deletes the checkpoint via `virsh checkpoint-delete`
- **AND** removes any state records
- **AND** retries FULL creation (up to `backup_retry_max`)

#### Scenario: Retries exhausted keeps old generations

- **WHEN** all retry attempts fail verification
- **THEN** Core logs CRITICAL
- **AND** old generations are NOT deleted (verify-before-delete gate)

#### Scenario: Dry-run logs FULL-would-be-created without executing

- **WHEN** `Core._backup_target()` is called in dry-run mode and `target.target_chain_length is not None` and `incremental_count > target.target_chain_length`
- **THEN** an INFO log is emitted: "[dry-run] Would create FULL backup (chain_length=N, method=NBD, VM=running)"
- **AND** `provider.create_full_backup()` is NOT called

### Requirement: IStateManager tracks full backups per target

`IStateManager` SHALL provide `get_full_backups(target_path) -> list[FullBackupInfo]` returning all FULLs for a target, and `record_full_backup(target_path, name, timestamp)` to append a new FULL. The `bucket_level` parameter is REMOVED. `JsonStateManager` SHALL persist this as a list per target path in `_full_backups.json`. Old JSON entries containing `bucket_level` SHALL be read-tolerantly (field silently ignored).

#### Scenario: Full backup recorded and retrieved
- **WHEN** `record_full_backup("/mnt/backup/vm", "vm.FULL.20260701T000000_a1b2c3", ts)` is called then `get_full_backups("/mnt/backup/vm")` is called
- **THEN** the returned list contains a `FullBackupInfo` with `name="vm.FULL.20260701T000000_a1b2c3"`, `timestamp=ts`

#### Scenario: Old JSON with bucket_level is read-tolerant
- **WHEN** `_full_backups.json` contains an entry with `"bucket_level": "monthly"`
- **THEN** the entry is loaded without error
- **AND** the `bucket_level` field is silently ignored


