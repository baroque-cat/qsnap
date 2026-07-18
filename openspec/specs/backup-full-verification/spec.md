# FULL Backup Verification Pipeline

## Purpose

Defines mandatory and configurable verification of FULL backup files at three lifecycle points: post-creation (before state recording), pre-rebase (before linking incrementals), and pre-deletion (before cascade-deletion). Verification uses three tiers: M1 (metadata — format + corrupt-bit), M2 (structural — qemu-img check), and M3 (content comparison — qemu-img compare).

## Requirements

### Requirement: M1 metadata verification of FULL after creation

When `Core._backup_target()` creates a FULL backup, it SHALL call `verify_full_backup()` on the FULL file BEFORE calling `record_full_backup()`. On failure, the FULL file SHALL be deleted and NOT recorded in state.

### Requirement: M1 metadata verification of FULL before rebase

When `FileCopyBackupProvider.transfer_missing()` rebases an incremental to a FULL anchor, it SHALL call M1 verification on the FULL anchor using the verification mode from `GlobalConfig.full_verify_before_rebase`. If the configured mode is `"off"`, verification SHALL be skipped. If M1 fails, an alternative (older) FULL anchor SHALL be tried. If no valid anchor exists, rebase is skipped with a WARNING. The method SHALL NOT hardcode `"metadata"` — the verification mode SHALL be passed as a parameter from the caller.

#### Scenario: Rebase with full_verify_before_rebase = "metadata"
- **WHEN** `GlobalConfig.full_verify_before_rebase` is `"metadata"`
- **AND** `FileCopyBackupProvider.transfer_missing()` rebases to a FULL anchor
- **THEN** M1 verification (qemu-img info format + corrupt-bit check) is performed on the anchor

#### Scenario: Rebase with full_verify_before_rebase = "off"
- **WHEN** `GlobalConfig.full_verify_before_rebase` is `"off"`
- **AND** `FileCopyBackupProvider.transfer_missing()` rebases to a FULL anchor
- **THEN** no verification is performed on the anchor

#### Scenario: Rebase with full_verify_before_rebase = "check"
- **WHEN** `GlobalConfig.full_verify_before_rebase` is `"check"`
- **AND** `FileCopyBackupProvider.transfer_missing()` rebases to a FULL anchor
- **THEN** M1 + M2 (qemu-img check) verification is performed on the anchor

#### Scenario: Verification mode passed as parameter
- **WHEN** `Core._backup_target()` calls `provider.transfer_missing(...)`
- **THEN** the call includes the verification mode read from `self._config.get_global().full_verify_before_rebase`
- **AND** the provider does NOT hardcode a verification mode

### Requirement: M1 metadata verification of FULL before cascade-deletion (NON-CONFIGURABLE)

Before `Core._cleanup_backups()` deletes a FULL backup, Core SHALL run M1 verification. This check is always enforced — it cannot be disabled by configuration. On failure, deletion of the FULL and all dependent incrementals is blocked with a CRITICAL log.

### Requirement: M2 structural verification of FULL (qemu-img check)

When configured (`GlobalConfig.full_verify_after_create = "check"` or `"hash"`, or `full_verify_before_delete = "check"`), Core SHALL additionally run `qemu-img check` to verify zero errors and leaks.

### Requirement: M3 content comparison of FULL (qemu-img compare)

When configured (`GlobalConfig.full_verify_after_create = "hash"`), Core SHALL run `qemu-img compare -q --force-share <source_path> <target_path>` to verify the FULL's virtual-disk content matches the source snapshot. This replaces an earlier SHA-256 approach which was incorrect for NBD-converted FULLs (SHA-256 of a delta file ≠ SHA-256 of a standalone full file).
