## MODIFIED Requirements

### Requirement: Dry-run mode
Core SHALL support dry-run mode where all pipeline steps are evaluated but no mutation occurs (no snapshot creation, no blockcommit, no file deletion). Dry-run mode SHALL be activated via the `dry_run` boolean property on the Core instance, settable by the CLI `--dry-run` / `-n` flag.

#### Scenario: Dry-run logs planned actions
- **WHEN** `core.dry_run = True` and `core.run()` is called
- **THEN** each planned action is logged at INFO level, but no IShell mutating commands are executed

#### Scenario: Dry-run activated from CLI
- **WHEN** `qsnap -n run` is executed
- **THEN** `Core.dry_run` is set to `True` before `core.run()` is called

## ADDED Requirements

### Requirement: Preserve flags on Core
Core SHALL expose `preserve_snapshots: bool` and `preserve_backups: bool` properties, both defaulting to `False`. When `preserve_snapshots` is `True`, `_blockcommit_snapshots()` SHALL be skipped. When `preserve_backups` is `True`, backup deletion in `_backup_target()` and `_execute_prune_steps()` SHALL be skipped. Retention evaluation SHALL still execute for schedule printing purposes.

#### Scenario: Preserve snapshots skips blockcommit
- **WHEN** `core.preserve_snapshots = True` and retention evaluation returns 3 snapshots to remove
- **THEN** `_blockcommit_snapshots()` is not called

#### Scenario: Preserve backups skips backup deletion
- **WHEN** `core.preserve_backups = True` and backup retention evaluation returns 2 backups to remove
- **THEN** `provider.delete()` for those backups is not called

### Requirement: Core.print_schedule() method
Core SHALL provide a `print_schedule(vm_filter=None)` method that evaluates retention policy for all VMs and targets without executing any mutations. The method SHALL return structured schedule data showing keep/remove decisions per VM per target.

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
