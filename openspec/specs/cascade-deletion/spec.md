# Cascade Deletion

## Purpose

State cleanup when FULL and incremental backups are deleted by per-chain retention. Per-chain retention groups backups by chain and evaluates keep/remove at the chain level — the entire chain is either kept or removed atomically. Ghost-retention and cascade-deletion are no longer used (see the `per-chain-retention` capability spec).

## Requirements

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

The `full_name` parameter in `get_incremental_dependencies`, `remove_incremental_dependency`, and `remove_all_incremental_dependencies` SHALL accept both stem form (`vm.FULL.20260727`) and extended form (`vm.FULL.20260727.qcow2`). When the extended form is passed, the implementation SHALL normalize it to stem form before lookup, because the storage format uses stem (as produced by `_resolve_chain_full_anchor`).

#### Scenario: Dependency recorded after rebase
- **WHEN** an incremental is rebased to FULL `vm.FULL.20260701.qcow2`
- **THEN** `get_incremental_dependencies(target_path, "vm.FULL.20260701.qcow2")` includes the incremental's name
- **AND** `get_incremental_dependencies(target_path, "vm.FULL.20260701")` also includes the incremental's name (stem form works too)

#### Scenario: Multiple incrementals depend on same FULL
- **WHEN** three incrementals are rebased to the same FULL
- **THEN** `get_incremental_dependencies()` returns a list of 3 names

#### Scenario: Lookup with stem key finds dependencies stored with stem
- **WHEN** `record_incremental_dependency(target, "incr-001", "vm.FULL.20260727")` is called (stem form)
- **AND** `get_incremental_dependencies(target, "vm.FULL.20260727.qcow2")` is called (extended form)
- **THEN** the method returns `["incr-001"]` (normalization makes both forms equivalent)

#### Scenario: Lookup with extended key finds dependencies stored with stem
- **WHEN** `record_incremental_dependency(target, "incr-001", "vm.FULL.20260727")` is called (stem form)
- **AND** `get_incremental_dependencies(target, "vm.FULL.20260727")` is called (stem form)
- **THEN** the method returns `["incr-001"]`

### Requirement: _full_backups.json format migration

`JsonStateManager` SHALL auto-migrate the `_full_backups.json` file from the old format (dict keyed by target_path -> single dict) to the new format (dict keyed by target_path -> list of dicts) on load. If the value for a key is a dict (not a list), it SHALL be wrapped in a single-element list.

#### Scenario: Old format auto-migrated
- **WHEN** `_full_backups.json` contains `{"target_path": {"name": "...", "timestamp": "..."}}` (dict value)
- **THEN** on load, it is migrated to `{"target_path": [{"name": "...", "timestamp": "..."}]}` (list value)

#### Scenario: New format loaded as-is
- **WHEN** `_full_backups.json` contains `{"target_path": [{"name": "...", ...}]}` (list value)
- **THEN** it is loaded as-is without migration

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
