## MODIFIED Requirements

### Requirement: Core prevents deletion of FULLs with active dependents

`Core._cleanup_backups()` SHALL check `IStateManager.get_incremental_dependencies()` before deleting any FULL backup. If any dependent incremental is in the retention keep-set, the FULL SHALL NOT be deleted (ghost retention). The FULL SHALL only be deleted when it falls out of ALL retention buckets AND no dependent incremental is in the keep-set.

Additionally, before deleting any non-FULL incremental, `Core._cleanup_backups()` SHALL check a reverse backing-chain dependency map (built via `qemu-img info` on all backups at the target) to determine whether any other backup in the keep-set has the incremental as its backing file. If any dependent is in the keep-set, the incremental SHALL NOT be deleted (ghost retention for incrementals — extending the existing FULL ghost retention pattern).

#### Scenario: FULL kept due to active dependent
- **WHEN** a FULL is in the retention remove-set but an incremental referencing it is in the keep-set
- **THEN** the FULL is NOT deleted and a log message explains the ghost retention

#### Scenario: FULL deleted when no active dependents
- **WHEN** a FULL is in the retention remove-set and no dependent incremental is in the keep-set
- **THEN** the FULL is deleted

#### Scenario: Incremental kept due to active dependent in keep-set
- **WHEN** a non-FULL incremental is in the retention remove-set
- **AND** another backup at the target has this incremental as its `backing-filename` (determined via `qemu-img info`)
- **AND** that dependent backup is in the retention keep-set
- **THEN** the incremental is NOT deleted (ghost-retained)
- **AND** a log message explains the ghost retention with the count of dependents in keep-set

#### Scenario: Incremental deleted when no active dependents
- **WHEN** a non-FULL incremental is in the retention remove-set
- **AND** no other backup at the target has this incremental as its `backing-filename`
- **THEN** the incremental is deleted

### Requirement: Cascade deletion of orphaned incrementals

When a FULL is deleted, all incrementals that referenced it AND are not in the retention keep-set SHALL be cascade-deleted. Incrementals in the keep-set that referenced the deleted FULL SHALL be rebased to the next available FULL anchor (or flagged as orphaned if none exists).

When a non-FULL incremental is deleted (after passing the ghost-retention check), all backups that have the deleted incremental as their `backing-filename` AND are not in the retention keep-set SHALL be cascade-deleted. This prevents orphaned broken-chain files from accumulating on the target.

#### Scenario: Orphaned incrementals cascade-deleted after FULL deletion
- **WHEN** a FULL is deleted and 3 incrementals referenced it, none of which are in the keep-set
- **THEN** all 3 orphaned incrementals are deleted

#### Scenario: Kept incremental rebased to new anchor
- **WHEN** a FULL is deleted and 1 incremental referencing it IS in the keep-set
- **THEN** that incremental is rebased to the next available FULL anchor in the target directory
- **AND** if no other FULL anchor exists, a WARNING is logged

#### Scenario: Orphaned incrementals cascade-deleted after incremental deletion
- **WHEN** a non-FULL incremental is deleted (ghost-retention check passed — no dependents in keep-set)
- **AND** 2 other backups at the target have the deleted incremental as their `backing-filename`
- **AND** neither of those 2 backups is in the keep-set
- **THEN** both orphaned backups are cascade-deleted
- **AND** a log message records each cascade deletion

### Requirement: State cleanup when incremental backup is deleted

When `Core._cleanup_backups()` cascade-deletes an orphaned incremental, Core SHALL call `IStateManager.remove_incremental_dependency(target_path, incremental_name, full_name)` to clean the dependency record.

When `Core._cleanup_backups()` deletes a non-FULL incremental via the else-branch (retention-driven deletion, not cascade), Core SHALL also call `IStateManager.remove_incremental_dependency(target_path, incremental_name, full_name)` to clean the dependency record. The `full_name` SHALL be resolved by walking the backing chain via `_resolve_chain_full_anchor()` before the file is deleted.

#### Scenario: Dependency record cleaned on retention-driven incremental deletion
- **WHEN** a non-FULL incremental is deleted by retention (else-branch of `_cleanup_backups`)
- **THEN** `remove_incremental_dependency` is called with the target path, incremental name, and resolved FULL anchor
- **AND** the dependency record is removed from `_dependencies.json`

## ADDED Requirements

### Requirement: Reverse backing-chain dependency map

`Core._cleanup_backups()` SHALL build a reverse backing-chain dependency map before the deletion loop. The map SHALL be a `dict[str, list[str]]` mapping `{absolute_backing_path → [dependent_backup_name, ...]}`. The map SHALL be built by running `qemu-img info --output=json` on each backup at the target (via `IShell`) and extracting the `backing-filename` field. Relative `backing-filename` values SHALL be resolved to absolute paths against the backup's parent directory. Files where `qemu-img info` fails or JSON parsing fails SHALL be skipped (no entry in the map).

#### Scenario: Reverse dependency map built correctly
- **WHEN** `_cleanup_backups()` is called with backups `[FULL, T0008, T0141]` where T0141.backing=T0008 and T0008.backing=FULL
- **THEN** the reverse dependency map contains `{FULL.path: [T0008], T0008.path: [T0141]}`
- **AND** FULL has no entry (it has no backing file)

#### Scenario: Broken qemu-img info skipped
- **WHEN** `qemu-img info` fails for a backup file
- **THEN** that backup is not included in the reverse dependency map
- **AND** the deletion loop continues without error
