## MODIFIED Requirements

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
- **AND** if it fails due to lock conflict, `BackupResult(success=False, error="verification failed: lock conflict — use verify=metadata for live sources")` is returned

#### Scenario: No verification when verify=off

- **WHEN** `target.verify == "off"`
- **THEN** no qemu-img commands are executed after the file copy
- **THEN** backup is marked success based on copy exit code alone
