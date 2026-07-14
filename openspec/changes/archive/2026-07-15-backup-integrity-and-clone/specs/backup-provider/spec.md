## ADDED Requirements

### Requirement: FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target

`FileCopyBackupProvider.create_full_backup(source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False) -> BackupResult` SHALL run `qemu-img convert -f qcow2 -O qcow2 <source> <target_path>/vm.FULL.YYYYMMDD.qcow2`. When `compress=True`, the `-c` flag SHALL be added. The result SHALL report success/failure and the created file path.

#### Scenario: Uncompressed full backup succeeds
- **WHEN** `create_full_backup(snapshot, target, compress=False)` is called
- **THEN** `qemu-img convert` is invoked WITHOUT `-c` and `BackupResult(success=True)` is returned

#### Scenario: Compressed full backup succeeds
- **WHEN** `create_full_backup(snapshot, target, compress=True)` is called
- **THEN** `qemu-img convert -c` is invoked

## MODIFIED Requirements

### Requirement: transfer_missing rebases incrementals to FULL anchor when present

`FileCopyBackupProvider.transfer_missing(snapshots, target)` SHALL, after copying each missing snapshot file, check for an existing FULL anchor (most recent `vm.FULL.*.qcow2` in the target directory). When a FULL anchor exists, the copied incremental SHALL be rebased via `qemu-img rebase -u -b ./vm.FULL.YYYYMMDD.qcow2 <target_incremental>`. When no FULL anchor exists, existing behavior (rebase to source backing filename) SHALL be preserved.

#### Scenario: Rebase to FULL anchor
- **WHEN** target directory contains `vm.FULL.20250714.qcow2` and `vm.20250715.qcow2` is transferred
- **THEN** `qemu-img rebase -u -b ./vm.FULL.20250714.qcow2 vm.20250715.qcow2` is called

#### Scenario: No FULL anchor preserves existing behavior
- **WHEN** target directory has no `vm.FULL.*.qcow2` files
- **THEN** rebase uses the source backing filename (unchanged behavior)
