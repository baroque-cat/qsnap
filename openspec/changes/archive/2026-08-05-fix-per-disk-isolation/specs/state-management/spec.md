## MODIFIED Requirements

### Requirement: IStateManager reset_vm_state method
`IStateManager` SHALL provide a `reset_vm_state(vm_name: str) -> None` method that atomically clears all per-VM state: snapshots list cleared to `[]`, `last_allocation` per-disk dict cleared to `{}`, and `deferred_operations` queue cleared to `[]`. This method clears state for ALL disks of the VM. `Core.restore()` SHALL NOT call this method — restore uses the per-disk `reset_vm_disk_state()` instead, so that disks not being restored keep their state.

#### Scenario: reset_vm_state clears all per-VM state
- **WHEN** `reset_vm_state("myvm")` is called and the VM has 5 snapshots, per-disk allocation baselines, and 2 deferred operations
- **THEN** `get_snapshots("myvm")` returns an empty list
- **AND** `get_last_allocation("myvm", "vda")` returns None
- **AND** `get_last_allocation("myvm", "vdb")` returns None
- **AND** `get_deferred_operations("myvm")` returns an empty list

#### Scenario: reset_vm_state is atomic
- **WHEN** `reset_vm_state("myvm")` is called
- **THEN** the state file is written atomically (`.tmp` + `os.replace`)
- **AND** no partial state is ever visible on crash

#### Scenario: reset_vm_state for nonexistent VM
- **WHEN** `reset_vm_state("nonexistent")` is called
- **THEN** no error is raised
- **AND** no state file is created

### Requirement: IStateManager reset_target_state method
`IStateManager` SHALL provide a `reset_target_state(target_path: str) -> None` method that atomically clears all per-target state by removing the target's entry from `_full_backups.json`, `_dependencies.json`, and `_target_state.json`. This method clears records of ALL VMs and ALL disks sharing the target. `Core.restore()` SHALL NOT call this method — restore uses the per-disk `reset_target_disk_state()` instead, so that other disks and other VMs sharing the target keep their records.

#### Scenario: reset_target_state clears all per-target state
- **WHEN** `reset_target_state("/mnt/backup/myvm")` is called and the target has 2 FULLs, 5 dependencies, and per-disk backup allocation baselines
- **THEN** `get_full_backups("/mnt/backup/myvm")` returns an empty list
- **AND** `get_incremental_dependencies("/mnt/backup/myvm", any_full)` returns an empty list
- **AND** `get_last_backup_allocation("/mnt/backup/myvm", "vda")` returns None

#### Scenario: reset_target_state is atomic
- **WHEN** `reset_target_state("/mnt/backup/myvm")` is called
- **THEN** all three state files (`_full_backups.json`, `_dependencies.json`, `_target_state.json`) are updated atomically

#### Scenario: reset_target_state for nonexistent target
- **WHEN** `reset_target_state("/nonexistent")` is called
- **THEN** no error is raised
- **AND** no state files are modified

## ADDED Requirements

### Requirement: IStateManager reset_vm_disk_state method
`IStateManager` SHALL provide a `reset_vm_disk_state(vm_name: str, disk: str) -> None` method that atomically clears ONLY the given disk's per-VM state: all snapshot records with `SnapshotInfo.disk == disk` are removed (other disks' snapshots remain), the `disk` key is removed from the `last_allocation` dict (a legacy bare-integer `last_allocation` value SHALL be treated as absent, so `get_last_allocation` returns `None` afterwards), and all deferred operations with `DeferredBlockcommit.disk == disk` are removed. State of all other disks of the VM SHALL NOT be modified. The write SHALL be atomic (`.tmp` + `os.replace`). This method is used by `Core.restore()` after replacing one disk's base image.

#### Scenario: reset_vm_disk_state clears only the given disk
- **WHEN** `reset_vm_disk_state("myvm", "vda")` is called and the VM has snapshots, allocation baselines, and deferred operations for both `vda` and `vdb`
- **THEN** `get_snapshots("myvm")` returns only the `vdb` snapshots
- **AND** `get_last_allocation("myvm", "vda")` returns None
- **AND** `get_last_allocation("myvm", "vdb")` still returns its prior value
- **AND** `get_deferred_operations("myvm")` returns only the `vdb` deferred operations

#### Scenario: reset_vm_disk_state handles legacy bare-integer allocation
- **WHEN** the VM state file contains a legacy bare-integer `last_allocation` and `reset_vm_disk_state("myvm", "vda")` is called
- **THEN** no error is raised
- **AND** `get_last_allocation("myvm", "vda")` returns None afterwards

#### Scenario: reset_vm_disk_state for unknown VM or disk
- **WHEN** `reset_vm_disk_state("nonexistent", "vda")` or `reset_vm_disk_state("myvm", "vdz")` is called with no matching state
- **THEN** no error is raised
- **AND** no state file is created

#### Scenario: reset_vm_disk_state is atomic
- **WHEN** `reset_vm_disk_state("myvm", "vda")` is called
- **THEN** the state file is written atomically (`.tmp` + `os.replace`)

### Requirement: IStateManager reset_target_disk_state method
`IStateManager` SHALL provide a `reset_target_disk_state(target_path: str, vm_name: str, disk: str) -> None` method that atomically clears ONLY the given VM+disk's per-target state:

- `_full_backups.json`: FULL entries whose name starts with `{vm_name}.` AND whose `disk` equals `disk` are removed. Entries of other VMs sharing the target and entries of other disks of the same VM SHALL NOT be touched.
- `_dependencies.json`: dependency keys whose FULL backup belongs to `(vm_name, disk)` are removed. The disk SHALL be extracted from the FULL name via `parse_disk_from_snapshot_name()`; keys whose disk cannot be determined or does not match SHALL NOT be touched.
- `_target_state.json`: the `last_backup_allocation[disk]` entry for the target is removed; other disks' baselines remain.

All writes SHALL be atomic. This method is used by `Core.restore()` for each configured target after replacing one disk's base image.

#### Scenario: reset_target_disk_state clears only the given VM and disk
- **WHEN** `reset_target_disk_state("/mnt/backup/shared", "myvm", "vda")` is called and the target holds FULLs for `myvm`/`vda`, `myvm`/`vdb`, and `othervm`/`vda`
- **THEN** `get_full_backups("/mnt/backup/shared")` no longer contains the `myvm` `vda` FULLs
- **AND** the `myvm` `vdb` FULLs and the `othervm` `vda` FULLs remain
- **AND** `get_last_backup_allocation("/mnt/backup/shared", "vda")` returns None
- **AND** `get_last_backup_allocation("/mnt/backup/shared", "vdb")` still returns its prior value

#### Scenario: reset_target_disk_state removes only the disk's dependencies
- **WHEN** `_dependencies.json` holds FULL keys for `myvm` `vda` and `myvm` `vdb` and `reset_target_disk_state(target, "myvm", "vda")` is called
- **THEN** only the `vda` FULL keys (disk parsed from the FULL name) are removed
- **AND** the `vdb` FULL keys remain intact

#### Scenario: reset_target_disk_state for unknown target
- **WHEN** `reset_target_disk_state("/nonexistent", "myvm", "vda")` is called
- **THEN** no error is raised
- **AND** no state files are modified

#### Scenario: reset_target_disk_state is atomic
- **WHEN** `reset_target_disk_state(target, "myvm", "vda")` is called
- **THEN** each modified state file is written atomically (`.tmp` + `os.replace`)
