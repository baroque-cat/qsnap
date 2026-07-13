## Requirements

### Requirement: Core initialization with dependency injection
Core SHALL accept `IConfigFacade`, `IVMModuleFactory`, `IStateManager`, and `IShell` via its constructor. No global state, no hidden imports.

#### Scenario: Core receives all dependencies at construction
- **WHEN** Core is instantiated with a mock config, mock factory, mock state, and mock shell
- **THEN** it stores all four and is ready for pipeline execution

### Requirement: Core.run() executes the full pipeline
`Core.run(vm_filter=None)` SHALL iterate over all configured VMs (filtered by optional filter) and execute the pipeline for each.

#### Scenario: run with all VMs
- **WHEN** `core.run()` is called and config has 2 VMs
- **THEN** `_execute_pipeline()` is called twice, once for each VM

#### Scenario: run with filter matching one VM
- **WHEN** `core.run(vm_filter="vm1")` is called
- **THEN** only VM "vm1" is processed

### Requirement: Pipeline step order
`Core._execute_pipeline(vm_config)` SHALL execute steps in this order:
1. Change detection — if `snapshot_create` mode requires it
2. Snapshot creation — if detector says we should, or if mode is "always"
3. Snapshot retention evaluation — which snapshots to keep/remove
4. Snapshot lifecycle — blockcommit removed snapshots
5. For each target: backup transfer → backup retention → cleanup

#### Scenario: Pipeline with always mode
- **WHEN** a VM has `snapshot_create = "always"` and the pipeline runs
- **THEN** a snapshot is created regardless of change detection result

#### Scenario: Pipeline with onchange mode, no changes
- **WHEN** a VM has `snapshot_create = "onchange"` and the change detector reports `has_changed = False`
- **THEN** no snapshot is created, but retention is still evaluated

### Requirement: Error isolation between VMs
An error processing one VM SHALL NOT prevent other VMs from being processed.

#### Scenario: One VM fails, others succeed
- **WHEN** the pipeline for "vm1" raises an error, but "vm2" is also configured
- **THEN** "vm2" is still processed, and the error for "vm1" is logged

### Requirement: Core.snapshot() runs only snapshot steps
`Core.snapshot(vm_filter=None)` SHALL execute only steps 1-4 (change detection, snapshot creation, snapshot retention, lifecycle). No backup steps.

#### Scenario: snapshot command skips backup
- **WHEN** `core.snapshot()` is called
- **THEN** backup methods on the factory are never called

### Requirement: Core.backup() runs only backup steps
`Core.backup(vm_filter=None)` SHALL execute only step 5 (backup transfer, backup retention, cleanup). No snapshot steps.

### Requirement: Core.prune() runs only retention steps
`Core.prune(vm_filter=None)` SHALL execute only retention and lifecycle cleanup for both snapshots and backups.

### Requirement: Dry-run mode
Core SHALL support dry-run mode where all pipeline steps are evaluated but no mutation occurs (no snapshot creation, no blockcommit, no file deletion). Dry-run mode SHALL be activated via the `dry_run` boolean property on the Core instance, settable by the CLI `--dry-run` / `-n` flag.

#### Scenario: Dry-run logs planned actions
- **WHEN** `core.run()` is called in dry-run mode
- **THEN** each planned action is logged at INFO level, but no IShell mutating commands are executed

#### Scenario: Dry-run activated from CLI
- **WHEN** `qsnap -n run` is executed
- **THEN** `Core.dry_run` is set to `True` before `core.run()` is called

### Requirement: Preserve flags on Core
Core SHALL expose `preserve_snapshots: bool` and `preserve_backups: bool` properties, both defaulting to `False`. When `preserve_snapshots` is `True`, `_blockcommit_snapshots()` SHALL be skipped. When `preserve_backups` is `True`, backup deletion in `_backup_target()` and `_execute_prune_steps()` SHALL be skipped. Retention evaluation SHALL still execute for schedule printing purposes.

#### Scenario: Preserve snapshots skips blockcommit
- **WHEN** `core.preserve_snapshots = True` and retention evaluation returns 3 snapshots to remove
- **THEN** `_blockcommit_snapshots()` is not called

#### Scenario: Preserve backups skips backup deletion
- **WHEN** `core.preserve_backups = True` and backup retention evaluation returns 2 backups to remove
- **THEN** `provider.delete()` for those backups is not called

### Requirement: Core.print_schedule() method
Core SHALL provide a `print_schedule(vm_filter=None)` method that evaluates retention policy for all VMs and targets without executing any mutations.

#### Scenario: Schedule shows keep/remove decisions
- **WHEN** `core.print_schedule("vm1")` is called
- **THEN** the result shows which snapshots and backups would be kept/removed by the current retention policy

#### Scenario: Schedule does not mutate filesystem
- **WHEN** `core.print_schedule()` is called
- **THEN** no IShell mutating commands (virsh snapshot-create-as, virsh blockcommit, cp, rm) are executed

### Requirement: Error result collection across pipeline steps
When `--preserve` flags are active, snapshot and backup creation steps that fail SHALL still collect results in `VMRunResult`, but deletion steps SHALL be skipped without error.

#### Scenario: Preserve mode with failed backup
- **WHEN** `qsnap --preserve run` is executed and a backup transfer fails
- **THEN** the error is reported in the result, but no backup deletion is attempted
