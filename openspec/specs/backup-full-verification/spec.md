# FULL Backup Verification Pipeline

## Purpose

Defines mandatory and configurable verification of FULL backup files at three lifecycle points: post-creation (before state recording), pre-deletion (before cascade-deletion), and post-transfer (via `transfer_missing` safety net). Verification uses three tiers: M1 (metadata — format + corrupt-bit), M2 (structural — `qemu-img check`), and M3 (content comparison — `qemu-img compare`).

## Requirements

### Requirement: Post-create FULL verification via full_verify_after_create

When `Core._backup_target()` creates a FULL backup, it SHALL call `verify_full_backup()` on the FULL file BEFORE calling `record_full_backup()`. The verification mode is controlled by `GlobalConfig.full_verify_after_create` (default `"check"`). Valid values: `"off"`, `"metadata"`, `"check"`, `"compare"`. The legacy value `"hash"` SHALL be treated as `"compare"` with a deprecation WARNING. On failure, the FULL file SHALL be deleted and NOT recorded in state, and any checkpoints created during the failed FULL attempt SHALL be cleaned up.

#### Scenario: Full verify after create passes

- **WHEN** `full_verify_after_create = "check"` and FULL creation succeeds
- **THEN** `verify_full_backup(shell, full_path, "check", source_path=snapshot.path)` is called
- **AND** on success, `record_full_backup()` is called
- **AND** the FULL is available for subsequent incrementals

#### Scenario: Full verify after create fails

- **WHEN** `full_verify_after_create = "check"` and verification returns an error
- **THEN** the FULL file is deleted via `rm -f`
- **AND** checkpoints created during the failed FULL attempt are cleaned up
- **AND** `record_full_backup()` is NOT called
- **AND** `BackupResult(success=False)` is returned
- **AND** old generations are preserved (verify-before-delete gate)


### Requirement: Pre-delete FULL verification (NON-CONFIGURABLE M1, configurable M2)

Before `Core._cleanup_backups()` deletes a FULL backup, Core SHALL run M1 metadata verification (`verify_full_backup(shell, full_path, "metadata")`). This check is always enforced — it cannot be disabled by configuration. On failure, deletion of the FULL and all dependent incrementals is blocked with a CRITICAL log.

When `GlobalConfig.full_verify_before_delete = "check"` (default), Core SHALL additionally run M2 structural verification (`verify_full_backup(shell, full_path, "check")`) before deletion. M2 failure also blocks deletion with a CRITICAL log.

#### Scenario: M1 passes — FULL deletion proceeds

- **WHEN** M1 verification passes
- **AND** `full_verify_before_delete != "check"`
- **THEN** the FULL backup is deleted

#### Scenario: M1 fails — deletion blocked

- **WHEN** M1 verification returns an error (corrupt format or corrupt bit set)
- **THEN** a CRITICAL log is emitted: "FULL backup <name> is corrupt — blocking deletion. Run: qsnap check --deep <target>"
- **AND** the FULL is NOT deleted
- **AND** all dependent incrementals are NOT deleted


### Requirement: M2 structural verification of FULL (qemu-img check)

When triggered by `verify_mode = "check"` or `"compare"`, Core or the verification function SHALL run `qemu-img check --output=json` and verify that ALL of `errors`, `leaks`, AND `corruptions` are zero. Any non-zero value among the three fields SHALL fail verification.

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


### Requirement: M3 — Content comparison tier via qemu-img compare

The M3 tier SHALL run `qemu-img compare -q --force-share <source> <target>` — a chain-traversing byte-level content comparison. M3 is triggered by `verify_mode = "compare"`. The `"hash"` value is deprecated and SHALL be treated as `"compare"` with a deprecation WARNING. M3 is available at the post-create lifecycle point (controlled by `GlobalConfig.full_verify_after_create`) and in post-transfer verification.

#### Scenario: M3 triggered by compare mode

- **WHEN** `verify_mode = "compare"` and M1+M2 pass
- **THEN** `qemu-img compare -q --force-share <source> <target>` is executed
- **AND** the comparison traverses both backing chains

#### Scenario: Deprecated hash triggers compare

- **WHEN** `verify_mode = "hash"` (deprecated)
- **THEN** a WARNING is logged
- **AND** M3 is triggered (same as `"compare"`)


### Requirement: verify_full_backup function signature

`verify_full_backup(shell, target_path, verify_mode, source_path=None, expected_virtual_size=None) -> str | None` SHALL verify a standalone FULL backup file. Supported modes: `"off"` (skip), `"metadata"` (M1: format is qcow2, no corrupt bit), `"check"` (M1+M2: M1 + `qemu-img check` returns zero errors/leaks/corruptions), `"compare"` (M1+M2+M3: additionally `qemu-img compare`). The `"hash"` value SHALL be treated as `"compare"` with a deprecation WARNING. Returns `None` on success or an error string on failure.

#### Scenario: verify_full_backup with off mode

- **WHEN** `verify_mode = "off"`
- **THEN** the function returns `None` immediately without running any commands

#### Scenario: verify_full_backup with metadata mode catches corrupt bit

- **WHEN** `verify_mode = "metadata"` and the FULL has the corrupt bit set
- **THEN** the function returns an error string: "verification failed: FULL backup has corrupt bit set — file is damaged"

#### Scenario: verify_full_backup with check mode catches qemu-img check errors

- **WHEN** `verify_mode = "check"` and `qemu-img check` reports non-zero errors
- **THEN** the function returns an error string: "verification failed: qemu-img check found N errors"


### Requirement: Verify-before-delete gate for FULL backups

When FULL backup creation fails (post-create verification, all retries exhausted), Core SHALL set `full_verification_failed = True` which prevents `_cleanup_backups()` from running. This ensures old generations are not deleted when the new FULL is unverified — implementing the verify-before-delete gate.

#### Scenario: Failed FULL preserves old generations

- **WHEN** FULL creation fails after all retries
- **THEN** `full_verification_failed` is set to `True`
- **AND** `_cleanup_backups()` is not called
- **AND** old FULLs and their dependent incrementals are preserved
