## MODIFIED Requirements

### Requirement: Core prevents deletion of FULLs with active dependents

`Core._cleanup_backups()` SHALL check `IStateManager.get_incremental_dependencies()` before deleting any FULL backup. Before any deletion decision, Core SHALL perform M1 verification (`qemu-img info --output=json`) on the FULL to verify it is a valid qcow2 with no corrupt bit. This check SHALL execute unconditionally — it is NOT configurable. If M1 fails, the FULL SHALL NOT be deleted and ALL dependent incrementals SHALL be preserved regardless of retention status. A CRITICAL log SHALL be emitted. If M1 passes and the FULL is in the retention remove-set but an incremental referencing it is in the keep-set, the FULL SHALL NOT be deleted (ghost retention). The FULL SHALL only be deleted when it falls out of ALL retention buckets AND no dependent incremental is in the keep-set AND M1 verification passes.

#### Scenario: FULL kept due to corrupt FULL — cascade blocked
- **WHEN** a FULL is in the retention remove-set with no dependents in keep-set
- **AND** M1 verification of the FULL fails (corrupt bit set)
- **THEN** the FULL is NOT deleted
- **AND** all dependent incrementals are NOT deleted
- **AND** a CRITICAL log is emitted: "FULL backup <name> is corrupt — blocking deletion to prevent data loss. Run: qsnap check --deep <target>"

#### Scenario: FULL kept due to active dependent
- **WHEN** a FULL is in the retention remove-set but an incremental referencing it is in the keep-set
- **AND** M1 verification passes
- **THEN** the FULL is NOT deleted (ghost retention)
- **AND** a log message explains the ghost retention

#### Scenario: FULL deleted when no active dependents and M1 passes
- **WHEN** a FULL is in the retention remove-set and no dependent incremental is in the keep-set
- **AND** M1 verification of the FULL passes
- **THEN** the FULL is deleted

### Requirement: Cascade deletion of orphaned incrementals

When a FULL is deleted (after passing M1 verification), all incrementals that referenced it AND are not in the retention keep-set SHALL be cascade-deleted. Incrementals in the keep-set that referenced the deleted FULL SHALL be rebased to the next available FULL anchor (or flagged as orphaned if none exists).

#### Scenario: Orphaned incrementals cascade-deleted
- **WHEN** a FULL is deleted and 3 incrementals referenced it, none of which are in the keep-set
- **AND** M1 verification of the FULL passed
- **THEN** all 3 orphaned incrementals are deleted

#### Scenario: Kept incremental rebased to new anchor
- **WHEN** a FULL is deleted and 1 incremental referencing it IS in the keep-set
- **AND** M1 verification of the FULL passed
- **THEN** that incremental is rebased to the next available FULL anchor in the target directory
- **AND** if no other FULL anchor exists, a WARNING is logged

## ADDED Requirements

### Requirement: State cleanup when FULL backup is deleted

When `Core._cleanup_backups()` deletes a FULL backup (after passing M1 verification), Core SHALL call `IStateManager.remove_full_backup(target_path, full_name)` to remove the corresponding `FullBackupInfo` entry from persistent state. This prevents phantom FULL entries from accumulating in state, which would block future FULL creation via `_should_create_bucket_full()` (the function sees a FULL already exists for a bucket period even though the file was deleted).

#### Scenario: FULL deleted — FullBackupInfo removed from state
- **WHEN** `_cleanup_backups()` deletes a FULL file
- **AND** M1 verification passed
- **THEN** `IStateManager.remove_full_backup(str(target.path), full_name)` is called
- **AND** subsequent `get_full_backups()` calls no longer include this FULL
- **AND** the next `_should_create_bucket_full()` can trigger a new FULL for a previously-blocked bucket period

#### Scenario: FULL deletion blocked by M1 — FullBackupInfo preserved in state
- **WHEN** `_cleanup_backups()` blocks deletion due to M1 failure
- **THEN** `IStateManager.remove_full_backup()` is NOT called
- **AND** the FULL remains in state (it's corrupt but the file is still present — operator must remediate)

### Requirement: State cleanup when incremental backup is deleted

When `Core._cleanup_backups()` cascade-deletes an orphaned incremental, Core SHALL also call `IStateManager.remove_incremental_dependency(target_path, incremental_name, full_name)` to clean the dependency record. This prevents phantom dependency entries from causing ghost retention on already-deleted incrementals.

#### Scenario: Orphaned incremental cascade-deleted — dependency removed from state
- **WHEN** a FULL is deleted and its orphaned incrementals are cascade-deleted
- **THEN** `IStateManager.remove_incremental_dependency()` is called for each deleted incremental
- **AND** subsequent `get_incremental_dependencies()` calls no longer include deleted incrementals
