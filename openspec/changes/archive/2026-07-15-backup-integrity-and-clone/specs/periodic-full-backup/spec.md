## ADDED Requirements

### Requirement: TargetConfig supports full_every and full_compress

`TargetConfig` SHALL have `full_every: str = "0d"` (a duration string, `"0d"` meaning disabled) and `full_compress: bool = False` fields. `ConfigFacade` SHALL parse these from TOML under `[[vm.target]]`.

#### Scenario: full_every disabled by default
- **WHEN** target config has no `full_every` key
- **THEN** `TargetConfig.full_every` is `"0d"` and full backups are disabled

#### Scenario: full_every set to 7 days
- **WHEN** target config sets `full_every = "7d"`
- **THEN** `TargetConfig.full_every` is `"7d"`

### Requirement: FileCopyBackupProvider creates full backups via qemu-img convert

`FileCopyBackupProvider.create_full_backup(source_snapshot, target, compress=False)` SHALL run `qemu-img convert -f qcow2 -O qcow2 <source> <target_path>/vm.FULL.YYYYMMDD.qcow2` (with `-c` when `compress=True`). The method SHALL return a `BackupResult`.

#### Scenario: Uncompressed full backup
- **WHEN** `create_full_backup(snapshot, target, compress=False)` is called
- **THEN** `qemu-img convert` is called WITHOUT `-c` flag and a `BackupResult(success=True)` is returned

#### Scenario: Compressed full backup
- **WHEN** `create_full_backup(snapshot, target, compress=True)` is called
- **THEN** `qemu-img convert -c` is called and a `BackupResult(success=True)` is returned

### Requirement: Core triggers full backup before incremental transfer

`Core._backup_target()` SHALL check `IStateManager.get_last_full_backup(target.path)` before the incremental transfer loop. If the configured interval has elapsed since the last full backup, it SHALL invoke `FileCopyBackupProvider.create_full_backup()` on the most recent snapshot.

#### Scenario: First run creates full backup
- **WHEN** target has `full_every = "7d"` and no previous full backup exists
- **THEN** a full backup is created immediately

#### Scenario: Interval not elapsed skips full backup
- **WHEN** target has `full_every = "7d"` and the last full backup was 3 days ago
- **THEN** no full backup is created

### Requirement: Incremental backups rebase to the FULL anchor

`FileCopyBackupProvider.transfer_missing()` SHALL check for an existing FULL anchor (most recent `vm.FULL.*.qcow2` in the target directory). When an anchor exists, newly transferred incrementals SHALL be rebased via `qemu-img rebase -u -b ./vm.FULL.YYYYMMDD.qcow2` to point at the FULL instead of the source backing filename.

#### Scenario: New incremental rebased to FULL
- **WHEN** target directory contains `vm.FULL.20250714.qcow2` and a new incremental `vm.20250715.qcow2` is transferred
- **THEN** `qemu-img rebase -u -b ./vm.FULL.20250714.qcow2 vm.20250715.qcow2` is called

#### Scenario: No FULL anchor uses source backing
- **WHEN** target directory has no `vm.FULL.*.qcow2` files
- **THEN** incremental rebase uses the source backing filename as before

### Requirement: IStateManager tracks last full backup per target

`IStateManager` SHALL have `get_last_full_backup(target_path) -> FullBackupInfo | None` and `set_last_full_backup(target_path, name, timestamp)` methods. `JsonStateManager` SHALL persist this under the `"target_full_backups"` key in the per-VM state JSON.

#### Scenario: Full backup timestamp saved and restored
- **WHEN** `set_last_full_backup("/mnt/backup/vm", "vm.FULL.20250714", ts)` is called then `get_last_full_backup("/mnt/backup/vm")` is called
- **THEN** the returned `FullBackupInfo` SHALL have `name="vm.FULL.20250714"` and `timestamp=ts`
