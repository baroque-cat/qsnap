## ADDED Requirements

### Requirement: IStateManager tracks multiple FULLs per target

`IStateManager` SHALL provide `get_full_backups(target_path: str) -> list[FullBackupInfo]` returning ALL FULL backups for a target (not just the most recent). Each `FullBackupInfo` SHALL include `name`, `path`, `timestamp`, and `bucket_level`. `record_full_backup(target_path, name, timestamp, bucket_level)` SHALL append to the list (not overwrite).

#### Scenario: Multiple FULLs tracked per target
- **WHEN** two FULL backups are created for the same target at different times
- **THEN** `get_full_backups(target_path)` returns a list of 2 `FullBackupInfo` entries

#### Scenario: FULL recorded with bucket level
- **WHEN** a FULL is created at the monthly bucket level
- **THEN** the recorded `FullBackupInfo` has `bucket_level="monthly"`

### Requirement: IStateManager tracks incremental-to-FULL dependencies

`IStateManager` SHALL provide `record_incremental_dependency(target_path: str, incremental_name: str, full_name: str)` and `get_incremental_dependencies(target_path: str, full_name: str) -> list[str]`. Dependencies SHALL be persisted across runs.

#### Scenario: Dependency recorded after rebase
- **WHEN** an incremental is rebased to FULL `vm.FULL.20260701.qcow2`
- **THEN** `get_incremental_dependencies(target_path, "vm.FULL.20260701.qcow2")` includes the incremental's name

#### Scenario: Multiple incrementals depend on same FULL
- **WHEN** three incrementals are rebased to the same FULL
- **THEN** `get_incremental_dependencies()` returns a list of 3 names

### Requirement: Core prevents deletion of FULLs with active dependents

`Core._cleanup_backups()` SHALL check `IStateManager.get_incremental_dependencies()` before deleting any FULL backup. If any dependent incremental is in the retention keep-set, the FULL SHALL NOT be deleted (ghost retention). The FULL SHALL only be deleted when it falls out of ALL retention buckets AND no dependent incremental is in the keep-set.

#### Scenario: FULL kept due to active dependent
- **WHEN** a FULL is in the retention remove-set but an incremental referencing it is in the keep-set
- **THEN** the FULL is NOT deleted and a log message explains the ghost retention

#### Scenario: FULL deleted when no active dependents
- **WHEN** a FULL is in the retention remove-set and no dependent incremental is in the keep-set
- **THEN** the FULL is deleted

### Requirement: Cascade deletion of orphaned incrementals

When a FULL is deleted, all incrementals that referenced it AND are not in the retention keep-set SHALL be cascade-deleted. Incrementals in the keep-set that referenced the deleted FULL SHALL be rebased to the next available FULL anchor (or flagged as orphaned if none exists).

#### Scenario: Orphaned incrementals cascade-deleted
- **WHEN** a FULL is deleted and 3 incrementals referenced it, none of which are in the keep-set
- **THEN** all 3 orphaned incrementals are deleted

#### Scenario: Kept incremental rebased to new anchor
- **WHEN** a FULL is deleted and 1 incremental referencing it IS in the keep-set
- **THEN** that incremental is rebased to the next available FULL anchor in the target directory
- **AND** if no other FULL anchor exists, a WARNING is logged

### Requirement: _full_backups.json format migration

`JsonStateManager` SHALL auto-migrate the `_full_backups.json` file from the old format (dict keyed by target_path → single dict) to the new format (dict keyed by target_path → list of dicts) on load. If the value for a key is a dict (not a list), it SHALL be wrapped in a single-element list.

#### Scenario: Old format auto-migrated
- **WHEN** `_full_backups.json` contains `{"target_path": {"name": "...", "timestamp": "..."}}` (dict value)
- **THEN** on load, it is migrated to `{"target_path": [{"name": "...", "timestamp": "..."}]}` (list value)

#### Scenario: New format loaded as-is
- **WHEN** `_full_backups.json` contains `{"target_path": [{"name": "...", ...}]}` (list value)
- **THEN** it is loaded as-is without migration
