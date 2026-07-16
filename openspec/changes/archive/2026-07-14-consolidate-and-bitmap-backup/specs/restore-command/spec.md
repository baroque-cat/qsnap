## ADDED Requirements

### Requirement: Restore command copies backup chain to target directory
The `qsnap restore` command SHALL identify a backup by its snapshot name, copy the backup file and its entire backing chain to a specified target directory, and rebuild backing paths using `qemu-img rebase -u` with relative `./` prefixes.

#### Scenario: Restore a file-copy backup chain
- **WHEN** `qsnap restore debiantest.20250101T1200 /restore/path` is executed
- **THEN** the backup file and all its backing chain files are copied to `/restore/path/`
- **THEN** `qemu-img rebase -u -b ./base.qcow2` is run on each copied file to use relative backing paths
- **THEN** the command outputs the path to the active (top) image in the restored chain

#### Scenario: Restore a nonexistent backup
- **WHEN** `qsnap restore nonexistent-snap /restore/path` is executed
- **THEN** the command exits with code 1 and an error message

#### Scenario: Target directory does not exist
- **WHEN** `qsnap restore snap.20250101 /nonexistent/path` is executed
- **THEN** the command exits with code 1 and an error message indicating the directory must exist

### Requirement: Core.restore method
`Core` SHALL provide a `restore(snapshot_name: str, target_dir: Path, vm_filter: str | None = None) -> RestoreResult` method. It SHALL search snapshots and backups across all configured VMs for the named snapshot.

#### Scenario: Restore from snapshot
- **WHEN** `core.restore("vm.20250101T1200", Path("/restore"))` is called and the snapshot exists in `IStateManager` records
- **THEN** the snapshot and its backing chain are restored to `/restore/`

#### Scenario: Restore from backup
- **WHEN** `core.restore("vm.20250101T1200", Path("/restore"))` is called and the snapshot is found on a backup target
- **THEN** the backup and its chain are restored to `/restore/`

### Requirement: RestoreResult type
The system SHALL provide a `RestoreResult` frozen dataclass with fields: `success: bool`, `snapshot_name: str`, `restored_path: Path`, `chain_files: list[Path]`, `error: str | None`.

#### Scenario: Successful restore result
- **WHEN** restore completes successfully
- **THEN** `RestoreResult(success=True, chain_files=[...])` is returned with all copied file paths
