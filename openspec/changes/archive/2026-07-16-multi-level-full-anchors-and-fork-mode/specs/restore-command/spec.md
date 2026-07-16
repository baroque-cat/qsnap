# restore-command — Delta Spec

## MODIFIED Requirements

### Requirement: Core.restore method
`Core` SHALL provide a `restore(snapshot_name: str, target_dir: Path, vm_filter: str | None = None) -> RestoreResult` method. It SHALL search snapshots and backups across all configured VMs for the named snapshot. The snapshot resolution logic SHALL be extractable (as `_resolve_snapshot`) for reuse by `Core.fork()`.

#### Scenario: Restore from snapshot
- **WHEN** `core.restore("vm.20250101T1200", Path("/restore"))` is called and the snapshot exists in `IStateManager` records
- **THEN** the snapshot and its backing chain are restored to `/restore/`

#### Scenario: Restore from backup
- **WHEN** `core.restore("vm.20250101T1200", Path("/restore"))` is called and the snapshot is found on a backup target
- **THEN** the backup and its chain are restored to `/restore/`

## ADDED Requirements

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
