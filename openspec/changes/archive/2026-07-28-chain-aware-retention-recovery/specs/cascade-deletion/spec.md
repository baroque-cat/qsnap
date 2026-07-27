## REMOVED Requirements

### Requirement: Core prevents deletion of FULLs with active dependents

**Reason**: Replaced by per-chain retention. Per-chain retention groups backups by chain and evaluates keep/remove at the chain level. It is impossible for a FULL to be in the remove list while its incrementals are in the keep list — the entire chain is either kept or removed. Ghost-retention is no longer needed.

**Migration**: The `_build_backing_refs()` method, ghost-retention checks, and cascade-deletion logic in `_cleanup_backups()` are removed. Per-chain retention (`_group_backups_by_chain()` + chain-level evaluation) replaces them. See the `per-chain-retention` capability spec.

### Requirement: Cascade deletion of orphaned incrementals

**Reason**: Replaced by per-chain retention. When an entire chain is in the remove list, all its members (FULL + all incrementals) are deleted. There are no "orphaned incrementals" to cascade-delete — the chain is atomic.

**Migration**: Cascade-deletion logic in `_cleanup_backups()` is removed. The cleanup loop simply deletes each backup in the remove list. See the `per-chain-retention` capability spec.

### Requirement: Reverse backing-chain dependency map

**Reason**: No longer needed. Per-chain retention does not use a reverse dependency map. Chain grouping is done via `_resolve_chain_full_anchor()` (forward walk), not via reverse maps.

**Migration**: `_build_backing_refs()` is no longer called by `_cleanup_backups()`. The method may be retained for diagnostic purposes but is not used in the retention/cleanup pipeline.

## MODIFIED Requirements

### Requirement: State cleanup when FULL backup is deleted

When `Core._cleanup_backups()` deletes a FULL backup (after passing M1 verification), Core SHALL call `IStateManager.remove_full_backup(target_path, full_name)` to remove the `FullBackupInfo` entry from persistent state. Core SHALL also call `IStateManager.remove_all_incremental_dependencies(target_path, full_name)` to clean all dependency records linked to that FULL. Deletion blocked by M1 failure SHALL NOT call `remove_full_backup()` or `remove_all_incremental_dependencies()`.

#### Scenario: State cleaned after FULL deletion

- **WHEN** a FULL is deleted by per-chain retention (entire chain in remove list)
- **THEN** `remove_full_backup(target_path, full_name)` is called
- **AND** `remove_all_incremental_dependencies(target_path, full_name)` is called
- **AND** the FULL entry is removed from `_full_backups.json`
- **AND** all dependency records for that FULL are removed from `_dependencies.json`

### Requirement: State cleanup when incremental backup is deleted

When `Core._cleanup_backups()` deletes an incremental (as part of a removed chain), Core SHALL call `IStateManager.remove_incremental_dependency(target_path, incremental_name, full_name)` to clean the dependency record. The `full_name` SHALL be resolved by walking the backing chain via `_resolve_chain_full_anchor()` before the file is deleted.

#### Scenario: Dependency record cleaned on chain removal

- **WHEN** an incremental is deleted as part of a removed chain
- **THEN** `remove_incremental_dependency` is called with the target path, incremental name, and resolved FULL anchor
- **AND** the dependency record is removed from `_dependencies.json`
