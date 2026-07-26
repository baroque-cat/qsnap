## ADDED Requirements

### Requirement: Reconcile CLI subcommand

The CLI SHALL provide a `reconcile` subcommand that dispatches to `Core.reconcile()`. The subcommand SHALL accept an optional VM name filter positional argument, a `--dry-run` flag, and a `--format` flag (table/long/raw).

#### Scenario: Reconcile command dispatches to Core

- **WHEN** `qsnap reconcile` is invoked
- **THEN** the CLI handler SHALL call `core.reconcile(vm_filter)` and format the results as a table

#### Scenario: Reconcile with dry-run

- **WHEN** `qsnap reconcile --dry-run` is invoked
- **THEN** the CLI handler SHALL set `core.dry_run = True` before calling `core.reconcile()`
- **AND** the output SHALL indicate what would be fixed without making changes

#### Scenario: Reconcile with VM filter

- **WHEN** `qsnap reconcile myvm` is invoked
- **THEN** the CLI handler SHALL pass `myvm` as the VM filter to `core.reconcile()`

#### Scenario: Reconcile exit code

- **WHEN** `qsnap reconcile` completes
- **THEN** the exit code SHALL be 0 if no errors occurred, or 1 if any VM had errors in its `ReconcileResult`

#### Scenario: Reconcile in dispatch map

- **WHEN** the CLI dispatch map is constructed
- **THEN** `"reconcile"` SHALL map to `commands.handle_reconcile`
