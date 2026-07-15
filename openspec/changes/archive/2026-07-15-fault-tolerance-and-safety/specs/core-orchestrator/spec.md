## MODIFIED Requirements

### Requirement: Pipeline step order
`Core._execute_pipeline(vm_config)` SHALL execute steps in this order:
1. Pre-flight environment validation (including stale file cleanup per `auto_cleanup`)
2. Deferred blockcommit check (if VM is shut off)
3. Change detection — if `snapshot_create` mode requires it
4. Snapshot creation — if detector says we should, or if mode is "always"
5. Snapshot retention evaluation — which snapshots to keep/remove
6. Snapshots to merge: pre-commit backing chain integrity verification (per `chain_verify_before_commit`)
7. Snapshot lifecycle — blockcommit removed snapshots with MAC denial deferral
8. Post-commit chain length verification (per `chain_verify_after_commit`)
9. For each target: backup transfer (with retry per `backup_retry_max`) → backup verification → backup retention → cleanup

After all VMs are processed, `_check_deferred_thresholds()` SHALL be called.

## ADDED Requirements

### Requirement: Pre-commit chain verification before blockcommit
When `chain_verify_before_commit = true` and there are snapshots to merge, Core SHALL call `_verify_backing_chain(vm_config)` before `lifecycle.blockcommit()`. If verification fails, blockcommit SHALL be skipped. See `specs/chain-integrity-verification/spec.md`.

#### Scenario: Chain verification blocks broken chain
- **WHEN** `_verify_backing_chain()` detects a missing file in the backing chain
- **THEN** blockcommit is skipped for this VM
- **AND** a CRITICAL log is emitted
- **AND** remaining VMs are processed normally

### Requirement: Post-commit chain verification after blockcommit
When `chain_verify_after_commit = true` and blockcommit succeeded, Core SHALL verify the chain length decreased. See `specs/chain-integrity-verification/spec.md`.

#### Scenario: Post-commit chain check passes
- **WHEN** chain length decreased after blockcommit
- **THEN** verification passes silently

### Requirement: Retry wrapper for backup transfers
Core's `_backup_target()` method SHALL wrap provider transfer calls in a retry loop when `target.backup_retry_max > 0`. See `specs/backup-retry/spec.md`.

#### Scenario: Backup retried on transient error
- **WHEN** a transfer fails with "Connection refused" and `backup_retry_max = 3`
- **THEN** the transfer is retried with exponential backoff

### Requirement: Deferred blockcommit with deep verify
When executing deferred blockcommit operations on a shut-off VM and `vm_config.blockcommit_deep_verify = true`, Core SHALL pass `deep_verify=True` to `BlockCommitManager.blockcommit()`. See `specs/deep-verification-circuit/spec.md`.

#### Scenario: Deep verify passed to deferred blockcommit
- **WHEN** VM is shut off, `blockcommit_deep_verify = true`, and deferred commits execute
- **THEN** `manager.blockcommit(vm_config, to_merge, deep_verify=True)` is called
