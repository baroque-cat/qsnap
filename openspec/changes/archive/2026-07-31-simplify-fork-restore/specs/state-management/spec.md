## ADDED Requirements

### Requirement: IStateManager reset_vm_state method
`IStateManager` SHALL provide a `reset_vm_state(vm_name: str) -> None` method that atomically clears all per-VM state: snapshots list, `last_allocation` baseline, and `deferred_operations` queue. This method is used by `Core.restore()` to reset VM state after disk replacement.

#### Scenario: reset_vm_state clears all per-VM state
- **WHEN** `reset_vm_state("myvm")` is called and the VM has 5 snapshots, allocation baseline, and 2 deferred operations
- **THEN** `get_snapshots("myvm")` returns an empty list
- **AND** `get_last_allocation("myvm")` returns None
- **AND** `get_deferred_operations("myvm")` returns an empty list

#### Scenario: reset_vm_state is atomic
- **WHEN** `reset_vm_state("myvm")` is called
- **THEN** the state file is written atomically (`.tmp` + `os.replace`)
- **AND** no partial state is ever visible on crash

#### Scenario: reset_vm_state for nonexistent VM
- **WHEN** `reset_vm_state("nonexistent")` is called
- **THEN** no error is raised
- **AND** no state file is created

#### Scenario: JsonStateManager implements reset_vm_state
- **WHEN** `JsonStateManager.reset_vm_state("myvm")` is called
- **THEN** the VM's JSON file is loaded, `snapshots`, `last_allocation`, `deferred_operations` keys are cleared, and the file is saved atomically

#### Scenario: InMemoryStateManager implements reset_vm_state
- **WHEN** `InMemoryStateManager.reset_vm_state("myvm")` is called
- **THEN** the in-memory dict for `myvm` is cleared of snapshots, allocation, and deferred operations

### Requirement: IStateManager reset_target_state method
`IStateManager` SHALL provide a `reset_target_state(target_path: str) -> None` method that atomically clears all per-target state: full backup records, incremental dependencies, and `last_backup_allocation` baseline. This method is used by `Core.restore()` to reset target state after VM disk replacement.

#### Scenario: reset_target_state clears all per-target state
- **WHEN** `reset_target_state("/mnt/backup/myvm")` is called and the target has 2 FULLs, 5 dependencies, and a backup allocation baseline
- **THEN** `get_full_backups("/mnt/backup/myvm")` returns an empty list
- **AND** `get_incremental_dependencies("/mnt/backup/myvm", any_full)` returns an empty list
- **AND** `get_last_backup_allocation("/mnt/backup/myvm")` returns None

#### Scenario: reset_target_state is atomic
- **WHEN** `reset_target_state("/mnt/backup/myvm")` is called
- **THEN** all three state files (`_full_backups.json`, `_dependencies.json`, `_target_state.json`) are updated atomically

#### Scenario: reset_target_state for nonexistent target
- **WHEN** `reset_target_state("/nonexistent")` is called
- **THEN** no error is raised
- **AND** no state files are modified

#### Scenario: JsonStateManager implements reset_target_state
- **WHEN** `JsonStateManager.reset_target_state("/mnt/backup/myvm")` is called
- **THEN** the target's entry is removed from `_full_backups.json`
- **AND** the target's entry is removed from `_dependencies.json`
- **AND** the target's entry is removed from `_target_state.json`
- **AND** all three files are saved atomically

#### Scenario: InMemoryStateManager implements reset_target_state
- **WHEN** `InMemoryStateManager.reset_target_state("/mnt/backup/myvm")` is called
- **THEN** the in-memory dicts for full backups, dependencies, and target state are cleared for the target path

### Requirement: IStateManager implementations must implement reset methods
All concrete implementations of `IStateManager` (JsonStateManager, InMemoryStateManager) SHALL implement `reset_vm_state` and `reset_target_state`. Contract tests SHALL verify these methods exist and return correct types.

#### Scenario: JsonStateManager implements reset_vm_state
- **WHEN** `JsonStateManager.reset_vm_state(vm_name)` is called
- **THEN** the method SHALL clear the VM's state atomically

#### Scenario: InMemoryStateManager implements reset_vm_state
- **WHEN** `InMemoryStateManager.reset_vm_state(vm_name)` is called
- **THEN** the method SHALL clear the in-memory state for the VM
