## MODIFIED Requirements

### Requirement: Metadata verification after transfer

After `cp` or `qemu-img convert` completes, `FileCopyBackupProvider` and `BitmapBackupProvider` SHALL run `qemu-img info --force-share --output=json` on the target file when `target.verify != "off"`. The `--force-share` flag is used on the source-side `qemu-img info` when the source may be the active layer. The following assertions SHALL be made: (a) `format` is `"qcow2"`, (b) `virtual-size` matches the source file, (c) `actual-size` is within reasonable tolerance (±10% for metadata overhead). Failure SHALL produce `BackupResult(success=False, error="verification failed: ...")`.

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

#### Scenario: Source-side info uses --force-share on active layer
- **WHEN** `verify_backup()` is called and the source path may be the active layer
- **THEN** the source-side `qemu-img info` command includes `--force-share`
- **AND** the command succeeds despite the VM holding a write lock

### Requirement: Full verification via qemu-img compare

When `target.verify == "full"`, after metadata verification passes, the provider SHALL additionally execute `qemu-img compare -q <source> <target>`. Non-zero exit code SHALL produce `BackupResult(success=False, error="verification failed: data comparison mismatch")`. Timeout SHALL be 7200 seconds (2 hours).

`--force-share` SHALL NOT be added to `qemu-img compare`. `qemu-img compare` is a data-copying operation that reads ALL clusters — using `--force-share` on a live source produces false mismatches or false matches due to race conditions. When the source is the active layer of a running VM, the `full` verification tier SHALL log a WARNING recommending `metadata` verification instead. The `metadata` tier is the recommended verification level for live sources.

#### Scenario: Full verification passes (stopped VM or frozen snapshot)
- **WHEN** `qemu-img compare -q source.qcow2 target.qcow2` returns exit code 0
- **AND** the source is a frozen snapshot (not the active layer) or the VM is stopped
- **THEN** backup is marked success after both metadata and full verification

#### Scenario: Full verification detects corruption
- **WHEN** `qemu-img compare` returns exit code 1 (content mismatch)
- **THEN** `BackupResult(success=False, error="verification failed: data comparison mismatch")` is returned

#### Scenario: Full verification on live source logs warning
- **WHEN** `target.verify == "full"` and the source is the active layer of a running VM
- **THEN** a WARNING is logged: "verify=full on running VM active layer — results may be unreliable, consider verify=metadata"
- **AND** `qemu-img compare` is still executed (without `--force-share`)
- **AND** if it fails due to lock conflict, `BackupResult(success=False, error="verification failed: lock conflict — use verify=metadata for live sources")` is returned

#### Scenario: No verification when verify=off
- **WHEN** `target.verify == "off"`
- **THEN** no qemu-img commands are executed after the file copy
- **THEN** backup is marked success based on copy exit code alone
