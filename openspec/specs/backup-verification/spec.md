# Backup Verification

## Purpose

Post-transfer verification of backup integrity — ensures copied qcow2 files are structurally valid and, optionally, byte-for-byte identical to the source. Supports metadata-only checks (fast) and full `qemu-img compare` (thorough) to detect corruption introduced during transfer.

## Requirements

### Requirement: TargetConfig verify field

`TargetConfig` SHALL gain a `verify: str` field with default value `"metadata"`. Accepted values SHALL be `"off"` (no verification), `"metadata"` (qemu-img info consistency check), and `"full"` (qemu-img compare byte-level verification).

#### Scenario: Default verification is metadata

- **WHEN** a TargetConfig is created without explicit `verify`
- **THEN** `target.verify` is `"metadata"`

### Requirement: Metadata verification after transfer

After `cp` or `qemu-img convert` completes, `FileCopyBackupProvider` and `BitmapBackupProvider` SHALL run `qemu-img info --output=json` on the target file when `target.verify != "off"`. The following assertions SHALL be made: (a) `format` is `"qcow2"`, (b) `virtual-size` matches the source file, (c) `actual-size` is within reasonable tolerance (±10% for metadata overhead). Failure SHALL produce `BackupResult(success=False, error="verification failed: ...")`.

#### Scenario: Metadata verification passes

- **WHEN** `qemu-img info` on target returns format="qcow2", virtual-size=10737418240, actual-size=10738466816
- **AND** source file has virtual-size=10737418240
- **THEN** verification passes and backup is marked success

#### Scenario: Metadata verification fails — wrong format

- **WHEN** `qemu-img info` on target returns format="raw" instead of "qcow2"
- **THEN** `BackupResult(success=False, error="verification failed: expected format qcow2, got raw")` is returned

#### Scenario: Metadata verification fails — size mismatch

- **WHEN** target virtual-size differs from source virtual-size by more than 0 bytes
- **THEN** `BackupResult(success=False, error="verification failed: virtual-size mismatch")` is returned

### Requirement: Full verification via qemu-img compare

When `target.verify == "full"`, after metadata verification passes, the provider SHALL additionally execute `qemu-img compare -q <source> <target>`. Non-zero exit code SHALL produce `BackupResult(success=False, error="verification failed: data comparison mismatch")`. Timeout SHALL be 7200 seconds (2 hours).

#### Scenario: Full verification passes

- **WHEN** `qemu-img compare -q source.qcow2 target.qcow2` returns exit code 0
- **THEN** backup is marked success after both metadata and full verification

#### Scenario: Full verification detects corruption

- **WHEN** `qemu-img compare` returns exit code 1 (content mismatch)
- **THEN** `BackupResult(success=False, error="verification failed: data comparison mismatch")` is returned

#### Scenario: No verification when verify=off

- **WHEN** `target.verify == "off"`
- **THEN** no qemu-img commands are executed after the file copy
- **THEN** backup is marked success based on copy exit code alone
