# Restore Command

## Purpose

File-level restore of a snapshot or backup to a target directory. Copies the backup file and its entire backing chain, then rebuilds backing paths using `qemu-img rebase -u` with relative `./` prefixes.

## Requirements

### Requirement: Restore command copies backup chain to target directory
The `qsnap restore` command SHALL identify a backup by its snapshot name, copy the backup file and its entire backing chain to a specified target directory, and rebuild backing paths using `qemu-img rebase -u` with relative `./` prefixes. The chain SHALL be resolved starting from the backup file, following backing file references through FULL anchors and incremental layers until a standalone file (no backing) is reached.

The chain file path extraction from `qemu-img info --backing-chain --output=json` SHALL accept both `"image"` (legacy QEMU) and `"filename"` (QEMU 11.0+) as the key for the disk image file path in each chain entry.

#### Scenario: Restore a backup chain with FULL anchor
- **WHEN** `qsnap restore debiantest.20250101T1200 /restore/path` is executed
- **AND** the backup chain includes a FULL anchor `vm.FULL.20250101.qcow2`
- **THEN** the FULL anchor and all incremental files in the chain are copied to `/restore/path/`
- **THEN** `qemu-img rebase -u -b ./vm.FULL.20250101.qcow2` is run on each incremental
- **THEN** the command outputs the path to the active (top) image in the restored chain

#### Scenario: Restore chain with new QEMU format
- **WHEN** `qsnap restore debiantest.20250101T1200 /restore/path` is executed
- **AND** `qemu-img info` output uses `"filename"` keys (QEMU 11.0+ format)
- **THEN** the chain is correctly parsed and all files are copied to `/restore/path/`

#### Scenario: Restore a nonexistent backup
- **WHEN** `qsnap restore nonexistent-snap /restore/path` is executed
- **THEN** the command exits with code 1 and an error message

#### Scenario: Target directory does not exist
- **WHEN** `qsnap restore snap.20250101 /nonexistent/path` is executed
- **THEN** the command exits with code 1 and an error message indicating the directory must exist

### Requirement: Core.restore method
`Core` SHALL provide a `restore(snapshot_name: str, target_dir: Path, vm_filter: str | None = None) -> RestoreResult` method. It SHALL search snapshots and backups across all configured VMs for the named snapshot. The snapshot resolution logic SHALL be extractable (as `_resolve_snapshot`) for reuse by `Core.fork()`.

#### Scenario: Restore from snapshot
- **WHEN** `core.restore("vm.20250101T1200", Path("/restore"))` is called and the snapshot exists in `IStateManager` records
- **THEN** the snapshot and its backing chain are restored to `/restore/`

#### Scenario: Restore from backup
- **WHEN** `core.restore("vm.20250101T1200", Path("/restore"))` is called and the snapshot is found on a backup target
- **THEN** the backup and its chain are restored to `/restore/`

### Requirement: Snapshot resolution exposes shared primitives for fork
`Core` SHALL provide a `_resolve_snapshot(snapshot_name: str, vm_filter: str | None = None) -> tuple[SnapshotInfo, VMConfig]` method that locates a snapshot by name across all sources (IStateManager and backup providers) and returns both the `SnapshotInfo` and the `VMConfig` it belongs to. This method SHALL be used internally by both `restore()` and `fork()`.

#### Scenario: _resolve_snapshot finds snapshot in state
- **WHEN** `_resolve_snapshot("myvm.20260701T1200")` is called and the snapshot exists in IStateManager
- **THEN** returns `(SnapshotInfo(name="myvm.20260701T1200", ...), VMConfig(name="myvm", ...))`

#### Scenario: _resolve_snapshot finds snapshot in backup
- **WHEN** `_resolve_snapshot("vm.FULL.20260701.monthly")` is called and the snapshot exists on a backup target
- **THEN** returns `(SnapshotInfo(name="vm.FULL.20260701.monthly", ...), VMConfig(...))`

#### Scenario: _resolve_snapshot raises on not found
- **WHEN** `_resolve_snapshot("nonexistent")` is called
- **THEN** raises `FileNotFoundError` with message "Snapshot not found: nonexistent"

### Requirement: RestoreResult type
The system SHALL provide a `RestoreResult` frozen dataclass with fields: `success: bool`, `snapshot_name: str`, `restored_path: Path`, `chain_files: list[Path]`, `error: str | None`.

#### Scenario: Successful restore result
- **WHEN** restore completes successfully
- **THEN** `RestoreResult(success=True, chain_files=[...])` is returned with all copied file paths
