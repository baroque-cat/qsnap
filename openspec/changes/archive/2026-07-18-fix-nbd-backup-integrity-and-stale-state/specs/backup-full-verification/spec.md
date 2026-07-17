# Backup Full Verification

## Purpose

Mandatory and optional integrity verification of FULL backup files at three lifecycle points: post-creation (before state recording), pre-rebase (before linking incrementals to the FULL), and pre-deletion (before cascade-deletion). Provides M1 (qemu-img info header check with corrupt-bit detection), M2 (qemu-img check structural scan), and M3 (SHA-256 content hash) verification tiers. M1 at pre-deletion is NON-CONFIGURABLE — it is always enforced to prevent data loss.

## ADDED Requirements

### Requirement: M1 metadata verification of FULL after creation

After `create_full_backup()` writes the FULL file (via atomic `.tmp` → rename), Core SHALL perform M1 verification before calling `IStateManager.record_full_backup()`. M1 SHALL run `qemu-img info --output=json` on the FULL and verify: (a) `format` is `"qcow2"`, (b) no corrupt bit is set (`incompatible_features` does not contain bit 1 — the `"corrupt"` feature). On failure, the FULL file SHALL be deleted and `BackupResult(success=False)` SHALL be returned. The FULL SHALL NOT be recorded in state.

#### Scenario: M1 passes — FULL valid after creation
- **WHEN** `create_full_backup()` completes and atomic rename succeeds
- **AND** `qemu-img info --output=json` returns format="qcow2" with no corrupt incompatible feature
- **THEN** `record_full_backup()` is called
- **AND** `BackupResult(success=True)` is returned

#### Scenario: M1 fails — corrupt FULL detected after creation
- **WHEN** `qemu-img info --output=json` on the FULL returns format="qcow2" with `incompatible-features: [{name: "corrupt"}]`
- **THEN** the FULL file is deleted via `rm -f`
- **AND** `record_full_backup()` is NOT called
- **AND** `BackupResult(success=False, error="verification failed: FULL backup has corrupt bit set")` is returned

#### Scenario: M1 fails — FULL not a valid qcow2
- **WHEN** `qemu-img info --output=json` on the FULL fails with a non-zero exit code
- **THEN** the FULL file is deleted via `rm -f`
- **AND** `record_full_backup()` is NOT called
- **AND** `BackupResult(success=False, error="verification failed: qemu-img info returned <stderr>")` is returned

### Requirement: M1 metadata verification of FULL before rebase

Before `FileCopyBackupProvider.transfer_missing()` calls `qemu-img rebase -u` to link an incremental to a FULL anchor, Core SHALL perform M1 verification on the FULL anchor file. On failure, the system SHALL NOT rebase to that FULL. It SHALL search for an alternative FULL anchor (previous by timestamp). If no valid FULL anchor exists, the incremental SHALL retain its source backing reference (no rebase) and a WARNING SHALL be logged.

#### Scenario: M1 passes — rebase proceeds
- **WHEN** `transfer_missing()` is about to rebase an incremental to `vm.FULL.20260701.qcow2`
- **AND** M1 on the FULL passes (format qcow2, no corrupt bit)
- **THEN** `qemu-img rebase -u -b ./FULL.qcow2 -F qcow2 <incremental>` is called
- **AND** `record_incremental_dependency()` is called

#### Scenario: M1 fails — rebase uses alternative FULL anchor
- **WHEN** `transfer_missing()` is about to rebase to `vm.FULL.20260708.qcow2`
- **AND** M1 on that FULL fails (corrupt bit set)
- **AND** an older FULL `vm.FULL.20260701.qcow2` exists and M1 passes
- **THEN** the incremental is rebased to `vm.FULL.20260701.qcow2` instead
- **AND** a WARNING is logged about the skipped corrupt FULL

#### Scenario: M1 fails — no alternative FULL anchor exists
- **WHEN** `transfer_missing()` is about to rebase to a FULL
- **AND** M1 on that FULL fails
- **AND** no other FULL anchor exists in the target directory
- **THEN** no rebase is performed (incremental retains source backing reference)
- **AND** a WARNING is logged: "Skipping rebase — FULL anchor <name> failed M1 verification and no alternative exists"

### Requirement: M1 metadata verification of FULL before cascade-deletion (NON-CONFIGURABLE)

Before `Core._cleanup_backups()` deletes a FULL backup AND its cascade-deleted dependent incrementals, Core SHALL perform M1 verification on the FULL. This check SHALL execute regardless of any configuration setting — it is always enforced. On failure, the FULL SHALL NOT be deleted. All dependent incrementals SHALL NOT be cascade-deleted. A CRITICAL message SHALL be logged with the FULL name, error details, and remediation guidance.

#### Scenario: M1 passes — cascade-deletion proceeds
- **WHEN** `_cleanup_backups()` is about to delete a FULL with 3 dependent incrementals
- **AND** M1 on the FULL passes (format qcow2, no corrupt bit)
- **THEN** the FULL is deleted
- **AND** the 3 dependent incrementals are cascade-deleted

#### Scenario: M1 fails — cascade-deletion completely blocked
- **WHEN** `_cleanup_backups()` is about to delete a FULL with 3 dependent incrementals
- **AND** M1 on the FULL fails (corrupt bit set or qemu-img info fails)
- **THEN** the FULL is NOT deleted
- **AND** the 3 dependent incrementals are NOT deleted
- **AND** a CRITICAL log is emitted: "FULL backup <name> is corrupt — blocking deletion of FULL and N dependent incrementals to prevent data loss"
- **AND** the message includes the FULL path and recommends `qsnap check --deep <target>`

#### Scenario: M1 fails on FULL with no dependents — deletion still blocked
- **WHEN** `_cleanup_backups()` is about to delete a FULL with no dependent incrementals
- **AND** M1 on the FULL fails
- **THEN** the FULL is NOT deleted
- **AND** a CRITICAL message is logged detailing the corrupt file

### Requirement: M2 structural verification of FULL (qemu-img check)

When configured, Core SHALL perform M2 verification using `qemu-img check --output=json` on the FULL file. This runs after M1 passes. M2 SHALL verify that `qemu-img check` reports no errors and no leaks. M2 is configurable per lifecycle point via `GlobalConfig.full_verify_after_create` and `GlobalConfig.full_verify_before_delete`.

#### Scenario: M2 passes — no errors or leaks
- **WHEN** `qemu-img check --output=json` returns `{errors: 0, leaks: 0}`
- **THEN** M2 verification passes
- **AND** the FULL is considered structurally valid

#### Scenario: M2 fails — errors detected
- **WHEN** `qemu-img check --output=json` returns `{errors: 5}`
- **THEN** M2 verification fails
- **AND** the FULL is treated as corrupt (deleted at post-create, cascade blocked at pre-deletion)

#### Scenario: M2 skipped when configured to "metadata" only
- **WHEN** `full_verify_after_create = "metadata"`
- **THEN** M2 (`qemu-img check`) is NOT executed
- **AND** only M1 (`qemu-img info`) is performed

### Requirement: M3 content comparison of FULL (qemu-img compare)

When `GlobalConfig.full_verify_after_create = "hash"`, Core SHALL additionally run `qemu-img compare -q --force-share <source_path> <target_path>` to compare the source snapshot content against the FULL backup content at the virtual-disk level. This replaces the previous SHA-256 hash comparison (which was incorrect — SHA-256 of a delta snapshot file with backing chain never matches SHA-256 of a standalone NBD-converted FULL). `qemu-img compare` traverses the backing chain automatically, comparing the actual disk content visible to the guest OS. A mismatch SHALL be treated as verification failure.

#### Scenario: M3 content comparison matches
- **WHEN** `full_verify_after_create = "hash"`
- **AND** `qemu-img compare -q --force-share <snap_path> <full_path>` returns exit code 0
- **THEN** M3 verification passes

#### Scenario: M3 content comparison mismatch
- **WHEN** `full_verify_after_create = "hash"`
- **AND** `qemu-img compare -q --force-share <snap_path> <full_path>` returns non-zero
- **THEN** the FULL is deleted
- **AND** `BackupResult(success=False, error="verification failed: content comparison mismatch")` is returned

### Requirement: verify_full_backup function in verification.py

`qsnap/modules/backup/verification.py` SHALL provide a `verify_full_backup(shell: IShell, target_path: Path, verify_mode: str, source_path: Path | None = None, expected_virtual_size: int | None = None) -> str | None` function. It SHALL return `None` on success or an error string on failure. Supported `verify_mode` values: `"metadata"`, `"check"`, `"hash"`, `"off"`.

#### Scenario: verify_full_backup metadata mode succeeds
- **WHEN** `verify_full_backup(shell, target_path, "metadata")` is called
- **AND** the file is a valid qcow2 with no corrupt bit
- **THEN** the function returns `None`

#### Scenario: verify_full_backup check mode runs M1 then M2
- **WHEN** `verify_full_backup(shell, target_path, "check")` is called
- **AND** M1 passes
- **THEN** `qemu-img check --output=json` is executed
- **AND** the function returns `None` if M2 also passes

#### Scenario: verify_full_backup off mode skips everything
- **WHEN** `verify_full_backup(shell, target_path, "off")` is called
- **THEN** no qemu-img commands are executed
- **AND** the function returns `None`
