## MODIFIED Requirements

### Requirement: Core._evaluate_snapshot_retention uses count-based policy

`Core._evaluate_snapshot_retention(vm_config, snapshots)` SHALL construct a `RetentionPolicy(chain_length=vm_config.snapshot_chain_length or 0, keep_generations=1)` and pass it to `IRetentionEngine.evaluate()`. The method SHALL NOT call `_parse_preserve()`. The oldest-prefix post-processing SHALL remain as a safety net for blockcommit.

#### Scenario: Snapshot retention with chain_length
- **WHEN** VM has `snapshot_chain_length = 168` and 200 snapshots exist
- **THEN** the retention engine keeps the newest 168 snapshots and marks the oldest 32 for removal

#### Scenario: Snapshot retention with no chain_length
- **WHEN** VM has `snapshot_chain_length = None` (unset)
- **THEN** the retention engine uses `chain_length=0` and marks all snapshots for removal

### Requirement: Core._evaluate_backup_retention uses count-based policy

`Core._evaluate_backup_retention(vm_config, target, backups)` SHALL group backups by chain via `_group_backups_by_chain()` (unchanged), construct a `RetentionPolicy(chain_length=0, keep_generations=target.keep_generations or 1)`, and pass chain-level items to `IRetentionEngine.evaluate()`. The method SHALL NOT call `_parse_preserve()`.

#### Scenario: Backup retention with keep_generations
- **WHEN** target has `target_keep_generations = 2` and 3 chains exist
- **THEN** the retention engine keeps the 2 newest chains and marks the oldest for removal

### Requirement: Core._backup_target triggers full backup when due

`Core._backup_target(vm_config, target, snapshots)` SHALL, before the incremental transfer loop, count the incrementals in the newest chain by calling `state.get_full_backups(target.path)` and `state.get_incremental_dependencies(target_path, newest_full.name)`. When `incremental_count > target.chain_length` (or no FULLs exist), Core SHALL create a new FULL backup via `provider.create_full_backup()`. Core SHALL NOT obtain an `IBucketFullStrategy` from the factory. Core SHALL NOT contain private methods `_should_create_bucket_full`, `_active_buckets`, `_f_anchor_buckets`, `_period_key`, or `_parse_preserve`.

After FULL creation, Core SHALL verify it (M1/M2 per `full_verify_after_create`). Only after verification succeeds SHALL Core record the FULL in state and evaluate retention + cleanup old generations. If verification fails, Core SHALL rollback (delete FULL file + checkpoint + state records) and retry up to `backup_retry_max` times. If retries are exhausted, Core SHALL log CRITICAL and keep old generations.

#### Scenario: Incremental count exceeds chain length triggers FULL
- **WHEN** the newest chain has 169 incrementals and `target.chain_length = 168`
- **THEN** a FULL backup is created via `provider.create_full_backup()`

#### Scenario: First run creates full backup
- **WHEN** `get_full_backups(target.path)` returns an empty list (no previous FULLs)
- **THEN** a FULL is created

#### Scenario: Verified FULL triggers retention + cleanup
- **WHEN** a FULL is created and passes M1/M2 verification
- **THEN** Core records it via `state.record_full_backup()`
- **AND** evaluates retention (keep newest `keep_generations` chains)
- **AND** deletes old generations via `_cleanup_backups()`

#### Scenario: Failed FULL verification triggers rollback
- **WHEN** a FULL is created but fails M1/M2 verification
- **THEN** Core deletes the broken FULL file from disk via `provider.delete()`
- **AND** deletes the checkpoint via `_cleanup_failed_checkpoint()`
- **AND** removes any state records via `state.remove_full_backup()`
- **AND** retries FULL creation (up to `backup_retry_max`)

#### Scenario: Retries exhausted keeps old generations
- **WHEN** all retry attempts fail verification
- **THEN** Core logs CRITICAL
- **AND** old generations are NOT deleted (verify-before-delete gate)

#### Scenario: No bucket strategy obtained from factory
- **WHEN** `_backup_target()` runs
- **THEN** it does NOT call `self._factory.create_bucket_full_strategy()`
- **AND** no `IBucketFullStrategy` is used

### Requirement: Core.schedule_summary produces count-based summary

`Core.schedule_summary(vm_filter=None) -> str` SHALL display count-based retention information for each VM and each target. The output SHALL show `chain_length`, `keep_generations`, current snapshot/chain counts, and real size projections. The method SHALL NOT generate synthetic timestamps or compute retention windows. The methods `_retention_window()` and `_generate_synthetic_items()` SHALL NOT exist.

#### Scenario: Summary includes all VMs when no filter
- **WHEN** `schedule_summary()` is called with no filter
- **THEN** output includes sections for every configured VM and every target

#### Scenario: Summary filters by VM name
- **WHEN** `schedule_summary(vm_filter="debiantest")` is called
- **THEN** output includes only the "debiantest" VM section

## REMOVED Requirements

### Requirement: Core._parse_preserve accepts optional preserve_min parameter
**Reason**: `_parse_preserve()` is deleted. Config values are plain integers, not preserve strings.
**Migration**: Use `RetentionPolicy(chain_length=N, keep_generations=M)` directly.

### Requirement: Ghost retention INFO
**Reason**: Ghost retention is no longer used. Per-chain retention handles chain-level keep/remove atomically.
**Migration**: No action needed — ghost retention was already dead code.

## ADDED Requirements

### Requirement: Core._cleanup_failed_checkpoint rollback method

Core SHALL provide a private method `_cleanup_failed_checkpoint(vm_config, target, full_result)` that deletes the libvirt checkpoint created during a failed FULL attempt. The method SHALL list checkpoints via `virsh checkpoint-list --name --domain <vm>`, filter for `qsnap-{target_hash}-*` prefix, and delete each via `virsh checkpoint-delete --domain <vm> <checkpoint>`.

#### Scenario: Checkpoint cleaned up after failed FULL
- **WHEN** FULL verification fails and `_cleanup_failed_checkpoint()` is called
- **THEN** the checkpoint created by `virsh backup-begin` is deleted
- **AND** no orphaned checkpoint remains for the next `transfer_missing()` call
