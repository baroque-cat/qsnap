# FULL Backup Verification Pipeline

## Purpose

Defines mandatory and configurable verification of FULL backup files at three lifecycle points: post-creation (before state recording), pre-rebase (before linking incrementals), and pre-deletion (before cascade-deletion). Verification uses three tiers: M1 (metadata — format + corrupt-bit), M2 (structural — qemu-img check), and M3 (content comparison — qemu-img compare).

## Requirements

### Requirement: M1 metadata verification of FULL after creation

When `Core._backup_target()` creates a FULL backup, it SHALL call `verify_full_backup()` on the FULL file BEFORE calling `record_full_backup()`. On failure, the FULL file SHALL be deleted and NOT recorded in state.

### Requirement: M1 metadata verification of FULL before rebase (REMOVED)

This requirement has been removed. The `full_verify_before_rebase` config field was never wired into Core — it was parsed, validated, and stored in `GlobalConfig`, but zero code paths consumed it. The rebase step it was intended to protect died with `FileCopyBackupProvider`. The `BitmapBackupProvider` does not rebase incrementals to FULL anchors — it creates backing-chained COW deltas via `qemu-img create -b`. The `full_verify_before_rebase` field has been removed from `GlobalConfig`. If the field appears in a TOML config, it is silently ignored as an unknown key.

### Requirement: M1 metadata verification of FULL before cascade-deletion (NON-CONFIGURABLE)

Before `Core._cleanup_backups()` deletes a FULL backup, Core SHALL run M1 verification. This check is always enforced — it cannot be disabled by configuration. On failure, deletion of the FULL and all dependent incrementals is blocked with a CRITICAL log.

### Requirement: M2 structural verification of FULL (qemu-img check)

When configured (`GlobalConfig.full_verify_after_create = "check"` or `"compare"`, or `full_verify_before_delete = "check"`), Core SHALL additionally run `qemu-img check --output=json` and verify that ALL of `errors`, `leaks`, AND `corruptions` are zero. Any non-zero value among the three fields SHALL fail verification.

#### Scenario: M2 passes when all fields are zero

- **WHEN** `qemu-img check --output=json` returns `{"errors": 0, "leaks": 0, "corruptions": 0}`
- **THEN** M2 verification passes

#### Scenario: M2 fails on non-zero corruptions

- **WHEN** `qemu-img check --output=json` returns `{"errors": 0, "leaks": 0, "corruptions": 3}`
- **THEN** M2 verification fails with an error message naming the corruption count

#### Scenario: M2 fails on non-zero errors

- **WHEN** `qemu-img check --output=json` returns `{"errors": 2, "leaks": 0, "corruptions": 0}`
- **THEN** M2 verification fails with an error message naming the error count

#### Scenario: M2 fails on non-zero leaks

- **WHEN** `qemu-img check --output=json` returns `{"errors": 0, "leaks": 5, "corruptions": 0}`
- **THEN** M2 verification fails with an error message naming the leak count

### Requirement: M3 — Content comparison tier

The M3 tier runs `qemu-img compare -q --force-share <source> <target>` — a chain-traversing byte-level content comparison. M3 is triggered by `verify_mode = "compare"` (was `"hash"`). The `"hash"` and `"full"` values are deprecated and treated as `"compare"`. M3 is available at the post-create lifecycle point (controlled by `GlobalConfig.full_verify_after_create`) and in `TargetConfig.verify` for post-transfer verification. A WARNING is logged when comparing a live source (the guest may write during the comparison).

#### Scenario: M3 triggered by compare mode

- **WHEN** `verify_mode = "compare"` and M1+M2 pass
- **THEN** `qemu-img compare -q --force-share <source> <target>` is executed
- **AND** the comparison traverses both backing chains

#### Scenario: Deprecated hash triggers compare

- **WHEN** `verify_mode = "hash"` (deprecated)
- **THEN** a WARNING is logged
- **AND** M3 is triggered (same as `"compare"`)
