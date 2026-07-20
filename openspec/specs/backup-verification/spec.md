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

After `rsync` or `qemu-img convert` completes, `FileCopyBackupProvider` and `BitmapBackupProvider` SHALL run `qemu-img info --force-share --output=json` on the target file when `target.verify != "off"`. The `--force-share` flag is used on the source-side `qemu-img info` when the source may be the active layer. The following assertions SHALL be made: (a) `format` is `"qcow2"`, (b) `virtual-size` matches the source file exactly. The `actual-size` tolerance check SHALL NOT be performed — `actual-size` is unreliable for live sources because the running VM writes data to the active snapshot layer between transfer completion and verification, causing the source's `actual-size` to grow beyond any reasonable tolerance. Failure SHALL produce `BackupResult(success=False, error="verification failed: ...")`.

#### Scenario: Metadata verification passes

- **WHEN** `qemu-img info` on target returns format="qcow2", virtual-size=10737418240
- **AND** source file has virtual-size=10737418240
- **THEN** verification passes and backup is marked success

#### Scenario: Metadata verification fails — wrong format

- **WHEN** `qemu-img info` on target returns format="raw" instead of "qcow2"
- **THEN** `BackupResult(success=False, error="verification failed: expected format qcow2, got raw")` is returned

#### Scenario: Metadata verification fails — virtual-size mismatch

- **WHEN** target virtual-size differs from source virtual-size by more than 0 bytes
- **THEN** `BackupResult(success=False, error="verification failed: virtual-size mismatch")` is returned

#### Scenario: Metadata verification passes despite actual-size difference

- **WHEN** source actual-size=2031616 and target actual-size=1572864
- **AND** source virtual-size matches target virtual-size exactly
- **AND** target format is "qcow2"
- **THEN** verification passes (actual-size is not checked)

#### Scenario: Source-side info uses --force-share on active layer
- **WHEN** `verify_backup()` is called and the source path may be the active layer
- **THEN** the source-side `qemu-img info` command includes `--force-share`
- **AND** the command succeeds despite the VM holding a write lock

### Requirement: Full verification via qemu-img compare

When `target.verify == "full"`, after metadata verification passes, the provider SHALL additionally execute `qemu-img compare -q --force-share <source> <target>`. The `--force-share` flag SHALL be added to avoid lock errors when the source is the active layer of a running VM. Non-zero exit code SHALL produce `BackupResult(success=False, error="verification failed: data comparison mismatch")`. Timeout SHALL be 7200 seconds (2 hours).

When the source is the active layer of a running VM, the `full` verification tier SHALL log a WARNING recommending `metadata` or `hash` verification instead, because `--force-share` opens the image in shared mode and the comparison may produce false mismatches if the VM writes during the comparison. The comparison is still executed — a potential false mismatch is better than no verification (hard lock error without `--force-share`).

#### Scenario: Full verification passes (stopped VM or frozen snapshot)

- **WHEN** `qemu-img compare -q --force-share source.qcow2 target.qcow2` returns exit code 0
- **AND** the source is a frozen snapshot (not the active layer) or the VM is stopped
- **THEN** backup is marked success after both metadata and full verification

#### Scenario: Full verification detects corruption

- **WHEN** `qemu-img compare` returns exit code 1 (content mismatch)
- **THEN** `BackupResult(success=False, error="verification failed: data comparison mismatch")` is returned

#### Scenario: Full verification on live source logs warning
- **WHEN** `target.verify == "full"` and the source is the active layer of a running VM
- **THEN** a WARNING is logged: "verify=full on running VM active layer — results may be unreliable, consider verify=metadata or verify=hash"
- **AND** `qemu-img compare -q --force-share` is executed
- **AND** if it fails due to lock conflict, `BackupResult(success=False, error="verification failed: lock conflict — use verify=metadata or verify=hash for live sources")` is returned

#### Scenario: No verification when verify=off

- **WHEN** `target.verify == "off"`
- **THEN** no qemu-img commands are executed after the file copy
- **THEN** backup is marked success based on copy exit code alone

### Requirement: Hash verification tier (verify="hash")

`verify_backup` SHALL support `verify_mode="hash"` — see `specs/backup-hash-verification/spec.md` for the authoritative spec. Hash verification is the recommended default for file-copy (rsync) mode (race-condition-immune, hash computed at snapshot creation time). Hash verification is NOT supported in bitmap (NBD) mode because NBD-converted qcow2 files have different internal structure. When bitmap mode is configured with `verify="hash"`, ConfigFacade SHALL log a WARNING and auto-downgrade to `"metadata"`.

Full verification (`verify="full"`) is also NOT supported in bitmap (NBD) mode for incremental transfers. An incremental NBD export produces a standalone qcow2 containing only dirty blocks (non-dirty blocks read as zeros), while the source snapshot (with backing chain) resolves to full data. `qemu-img compare` between these will always mismatch. When bitmap mode is configured with `verify="full"`, ConfigFacade SHALL log a WARNING and auto-downgrade to `"metadata"`.

#### Scenario: Bitmap mode with verify=hash auto-downgrades
- **WHEN** `incremental_mode == "bitmap"` and `verify == "hash"` is explicitly configured
- **THEN** ConfigFacade logs a WARNING: "verify='hash' is not supported in bitmap mode (NBD-converted qcow2 has different internal structure). Downgrading to verify='metadata'. Use verify='full' for content-level verification."
- **AND** the effective `verify` value is `"metadata"`

#### Scenario: Bitmap mode with verify=full auto-downgrades
- **WHEN** `incremental_mode == "bitmap"` and `verify == "full"` is explicitly configured
- **THEN** ConfigFacade logs a WARNING: "verify='full' is not supported in bitmap mode (incremental NBD exports contain only dirty blocks; qemu-img compare will always mismatch against source with backing chain). Downgrading to verify='metadata'."
- **AND** the effective `verify` value is `"metadata"`

#### Scenario: Bitmap mode with verify=metadata (default) works correctly
- **WHEN** `incremental_mode == "bitmap"` and `verify` is unset or set to `"metadata"`
- **THEN** no WARNING is logged
- **AND** the effective `verify` value is `"metadata"`

#### Scenario: File-copy mode retains verify=full
- **WHEN** `incremental_mode == "file-copy"` and `verify == "full"` is explicitly configured
- **THEN** no downgrade occurs
- **AND** `qemu-img compare` is used for post-transfer verification (rsync produces byte-identical copies)

#### Scenario: File-copy mode retains verify=hash
- **WHEN** `incremental_mode == "file-copy"` and `verify == "hash"` is explicitly configured
- **THEN** no downgrade occurs
- **AND** SHA-256 hash verification is used (rsync produces byte-identical copies)

### Requirement: verify_full_backup function for standalone FULL verification

`verify_full_backup(shell, target_path, verify_mode, source_path=None, expected_virtual_size=None) -> str | None` verifies a standalone FULL backup file without source comparison. Unlike `verify_backup()` which compares source and target, this function only checks the target file's structural integrity.

Supported modes:
- `"metadata"` (M1): `qemu-img info` checks format is `"qcow2"` and no `"corrupt"` feature bit
- `"check"` (M2): M1 + `qemu-img check` verifies zero errors and leaks
- `"hash"` (M3): M1 + M2 + `qemu-img compare -q --force-share <source_path> <target_path>` for byte-level content comparison. Note: M3 for `verify_full_backup` uses `qemu-img compare` (not SHA-256) to correctly compare virtual-disk content across backing chains.
- `"off"`: Skip all checks

Returns `None` on success or an error string on failure.
