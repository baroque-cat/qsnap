# core-orchestrator — Delta Spec

## MODIFIED Requirements

### Requirement: Core._backup_target triggers full backup when due
`Core._backup_target(vm_config, target, snapshots)` SHALL, before the incremental transfer loop, call `state.get_full_backups(target.path)` to retrieve ALL full backups for the target. It SHALL pass the complete list to `_should_create_bucket_full(target, policy, all_fulls, snapshot_ts)` along with the most recent snapshot's timestamp. The signature of `_should_create_bucket_full` SHALL accept a list of `FullBackupInfo` objects instead of a single `last_full` record.

#### Scenario: Full backup list passed to bucket check
- **WHEN** `_backup_target()` is called and the target has 2 existing FULL records
- **THEN** `state.get_full_backups(target.path)` returns a list of 2 `FullBackupInfo` objects
- **THEN** the list is passed to `_should_create_bucket_full(target, policy, all_fulls, snapshot_ts)`

#### Scenario: First run creates full backup
- **WHEN** `get_full_backups(target.path)` returns an empty list (no previous FULLs)
- **THEN** `_should_create_bucket_full` returns `(True, bucket_level)` for the first active/F-marked bucket
- **THEN** a FULL is created

### Requirement: Core._should_create_bucket_full signature change
`Core._should_create_bucket_full` SHALL accept `all_fulls: list[FullBackupInfo]` instead of `last_full: FullBackupInfo | None`. All callers SHALL be updated to pass the full list.

#### Scenario: Updated signature
- **WHEN** `_should_create_bucket_full(target, policy, all_fulls, snapshot_ts)` is called
- **THEN** `all_fulls` is a list that may be empty
- **THEN** the method returns `(bool, str)` as before

## ADDED Requirements

### Requirement: Core.fork method
`Core` SHALL provide a `fork(snapshot_name: str, new_vm_name: str, storage_dir: Path, add_to_config: bool = False, vm_filter: str | None = None) -> RestoreResult` method. It SHALL:
1. Resolve the snapshot via `IStateManager` and backup providers (reuse restore resolution).
2. Determine the snapshot's full chain via `qemu-img info --backing-chain --output=json`.
3. Estimate and log total chain size.
4. Execute `qemu-img convert -O qcow2 <snapshot-path> <storage_dir>/<new_vm_name>/<new_vm_name>.qcow2`.
5. Obtain source VM XML via `virsh dumpxml <source-vm>`.
6. Modify XML: new name, new UUID (uuidgen), new disk source paths.
7. Execute `virsh define <modified-xml-path>`.
8. Optionally append `[[vm]]` block to qsnap config file.

#### Scenario: fork succeeds
- **WHEN** `core.fork("myvm.20260701T1200", "myvm-clone", Path("/var/lib/libvirt/images"), add_to_config=False)` is called
- **THEN** returns `RestoreResult(success=True, restored_path=Path("/var/lib/libvirt/images/myvm-clone/myvm-clone.qcow2"))`

### Requirement: Core.deploy method
`Core` SHALL provide a `deploy(backup_name: str, new_vm_name: str, storage_dir: Path, add_to_config: bool = False, vm_filter: str | None = None) -> RestoreResult` method. It SHALL delegate to `Core.fork()` with the same parameters.

#### Scenario: deploy delegates to fork
- **WHEN** `core.deploy("vm.FULL.20260701.monthly", "recovered-vm", Path("/var/lib/libvirt/images"))` is called
- **THEN** `core.fork("vm.FULL.20260701.monthly", "recovered-vm", Path("/var/lib/libvirt/images"))` is called internally
- **THEN** returns the same `RestoreResult`
