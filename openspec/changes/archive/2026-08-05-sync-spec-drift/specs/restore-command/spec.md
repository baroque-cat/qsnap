## MODIFIED Requirements

### Requirement: Snapshot resolution exposes shared primitives for fork
`Core` SHALL provide a `_resolve_snapshot(snapshot_name: str, vm_filter: str | None = None) -> tuple[SnapshotInfo, VMConfig]` method that locates a snapshot by name across all sources (IStateManager and backup providers) and returns both the `SnapshotInfo` and the `VMConfig`. The `SnapshotInfo` carries a `disk` field identifying which disk it belongs to. This method SHALL be used internally by both `restore()` and `fork()`.

Two-layer failure contract: `_resolve_snapshot()` is the low-level primitive and SHALL raise `FileNotFoundError("Snapshot not found: {name}")` when the snapshot exists in neither source (or the `vm_filter` excludes every owner). The public commands `restore()` and `fork()` SHALL catch that exception and return `RestoreResult(success=False, error="Snapshot not found: {name}")` — they never raise for expected failures (Result-object convention). Both spec statements ("raises" for the primitive, "returns failed result" for the commands) describe different layers of the same contract.

#### Scenario: _resolve_snapshot finds snapshot in state
- **WHEN** `_resolve_snapshot("myvm.20260701T1200")` is called and the snapshot exists in IStateManager
- **THEN** returns `(SnapshotInfo(name="myvm.20260701T1200", disk="vda", ...), VMConfig(name="myvm", ...))`

#### Scenario: _resolve_snapshot finds snapshot in backup
- **WHEN** `_resolve_snapshot("vm.FULL.20260701T000000_a1b2c3")` is called and the snapshot exists on a backup target
- **THEN** returns `(SnapshotInfo(name="vm.FULL.20260701T000000_a1b2c3", disk="vda", ...), VMConfig(...))`

#### Scenario: _resolve_snapshot raises on not found
- **WHEN** `_resolve_snapshot("nonexistent")` is called
- **THEN** raises `FileNotFoundError` with message `"Snapshot not found: nonexistent"`

#### Scenario: restore and fork convert the raised error into a failed result
- **WHEN** `restore("nonexistent")` or `fork("nonexistent", out)` is called and `_resolve_snapshot` raises `FileNotFoundError`
- **THEN** the command catches it and returns `RestoreResult(success=False, error="Snapshot not found: nonexistent")`
- **AND** no exception propagates to the CLI layer
