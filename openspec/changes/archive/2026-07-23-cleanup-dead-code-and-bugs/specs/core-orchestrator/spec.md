## MODIFIED Requirements

### Requirement: Pipeline step order
`Core._execute_pipeline(vm_config)` SHALL execute steps in this order:
1. Pre-flight environment validation (including stale file cleanup per `auto_cleanup`, compress driver availability check)
2. Deferred blockcommit check — state-adaptive drain per the `deferred-operations` capability
3. Change detection — if `snapshot_create` mode requires it
4. Snapshot creation — if detector says we should, or if mode is "always"
5. Snapshot retention evaluation — which snapshots to keep/remove
6. Snapshots to merge: pre-commit backing chain integrity verification (per `chain_verify_before_commit`)
7. Snapshot lifecycle — **adaptive blockcommit**: Core SHALL determine the VM power state via `virsh domstate` and the active overlay path via `virsh domblklist`, split the remove set into committable and deferrable subsets, execute the committable subset with the mechanism valid for the current state, and defer the rest. MAC denial deferral applies as before.
8. Post-commit chain length verification (per `chain_verify_after_commit`)
9. For each target: backup transfer (with retry per `backup_retry_max`) → backup verification → backup retention → cleanup

Core SHALL NOT directly instantiate `BitmapBackupProvider` or any other domain module. ALL module instantiation SHALL go through `IVMModuleFactory`. This includes orphan checkpoint detection — `Core._detect_orphan_checkpoints()` SHALL obtain the backup provider via `self._factory.create_backup_provider(vm_config, target)`.

Core SHALL NOT hardcode a disk target fallback. When `virsh domblklist` fails or returns no disks in `Core._resolve_disks()`, Core SHALL return an empty list and log a WARNING. The caller SHALL skip snapshot creation when the disk list is empty.

#### Scenario: Orphan checkpoint detection uses factory

- **WHEN** `Core._detect_orphan_checkpoints()` needs a backup provider
- **THEN** it SHALL call `self._factory.create_backup_provider(vm_config, target)`
- **AND** it SHALL NOT directly import or instantiate `BitmapBackupProvider`

#### Scenario: domblklist failure returns empty list

- **WHEN** `virsh domblklist` fails or returns no disk entries
- **THEN** `Core._resolve_disks()` returns an empty list
- **AND** a WARNING is logged
- **AND** snapshot creation is skipped for this VM
- **AND** no hardcoded disk target (e.g., `"vda"`) is used as fallback

#### Scenario: Pipeline with always mode

- **WHEN** a VM has `snapshot_create = "always"` and the pipeline runs
- **THEN** snapshot creation proceeds without change detection
