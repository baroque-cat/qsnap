## ADDED Requirements

### Requirement: Clear last backup allocation

The `IStateManager` ABC SHALL provide a `clear_last_backup_allocation(target_path: str) -> bool` method that removes the `last_backup_allocation` baseline for a target. Returns True if an entry was found and removed, False otherwise.

#### Scenario: Clear existing baseline

- **WHEN** `clear_last_backup_allocation(target_path)` is called and an entry exists for `target_path` in `_target_state.json`
- **THEN** the entry SHALL be removed from the JSON file and True SHALL be returned

#### Scenario: Clear non-existent baseline

- **WHEN** `clear_last_backup_allocation(target_path)` is called and no entry exists for `target_path`
- **THEN** no file SHALL be modified and False SHALL be returned

### Requirement: Remove all incremental dependencies

The `IStateManager` ABC SHALL provide a `remove_all_incremental_dependencies(target_path: str, full_name: str) -> int` method that removes ALL incremental dependency records linked to a given FULL backup. Returns the count of removed entries.

#### Scenario: Remove all dependencies for existing FULL

- **WHEN** `remove_all_incremental_dependencies(target_path, full_name)` is called and dependencies exist for `full_name` in `_dependencies.json`
- **THEN** all dependency entries under `full_name` SHALL be removed and the count of removed entries SHALL be returned

#### Scenario: Remove dependencies for non-existent FULL

- **WHEN** `remove_all_incremental_dependencies(target_path, full_name)` is called and no dependencies exist for `full_name`
- **THEN** no file SHALL be modified and 0 SHALL be returned

### Requirement: IStateManager implementations must implement new methods

All concrete implementations of `IStateManager` (JsonStateManager, InMemoryStateManager) SHALL implement `clear_last_backup_allocation` and `remove_all_incremental_dependencies`. Contract tests SHALL verify these methods exist and return correct types.

#### Scenario: JsonStateManager implements clear_last_backup_allocation

- **WHEN** `JsonStateManager.clear_last_backup_allocation(target_path)` is called
- **THEN** the method SHALL remove the entry from `_target_state.json` atomically (write to `.tmp`, then `os.replace`)

#### Scenario: InMemoryStateManager implements clear_last_backup_allocation

- **WHEN** `InMemoryStateManager.clear_last_backup_allocation(target_path)` is called
- **THEN** the method SHALL remove the key from the in-memory dict and return True if it existed
