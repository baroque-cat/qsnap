# Backup Summary

## Purpose

Provides a btrbk-style summary table on stdout after each `qsnap run` (or subcommand) completion. The table uses symbolic prefixes (`+++`/`---`/`>>>`/`***`/`!!!`) to indicate action types, grouped by VM. In dry-run mode, the header includes `Dryrun: YES` and a footer disclaimer is printed. The formatter is a pure function that accepts only `PipelineResult` and returns a formatted string.

## Requirements

### Requirement: Summary table output after run command

After `qsnap run` completes, the CLI SHALL print a summary table to stdout showing all actions performed, grouped by VM. The table SHALL use symbolic prefixes mirroring btrbk's legend.

#### Scenario: Summary table with created and deleted snapshots
- **WHEN** `qsnap run` creates 2 snapshots and blockcommits 1 snapshot for VM "testvm"
- **THEN** stdout contains a table with header `qsnap Backup Summary`
- **AND** the "testvm" block shows `+++` for each created snapshot
- **AND** the "testvm" block shows `---` for each deleted snapshot

#### Scenario: Summary table with backup transfers
- **WHEN** `qsnap run` transfers 2 incremental backups and creates 1 FULL backup for VM "testvm"
- **THEN** the "testvm" block shows `>>>` for each incremental transfer
- **AND** the "testvm" block shows `***` for the FULL backup creation

#### Scenario: Summary table with errors
- **WHEN** `qsnap run` encounters a backup transfer failure for snapshot "testvm.20260701T1200_vda"
- **THEN** the "testvm" block shows `!!!` for the failed transfer
- **AND** the error message is printed on the same line

#### Scenario: Summary table legend
- **WHEN** `qsnap run` produces a summary table
- **THEN** stdout includes a Legend section explaining each symbol: `+++` created snapshot, `---` deleted snapshot (blockcommitted), `>>>` transferred incremental backup, `***` created FULL backup, `!!!` ERROR

### Requirement: Dry-run summary table

In dry-run mode (`qsnap -n run`), the summary table SHALL show predicted actions (what WOULD happen) using the same format. The header SHALL include `Dryrun: YES`. The footer SHALL include `NOTE: Dryrun was active, none of the operations above were actually executed!`.

#### Scenario: Dry-run summary header
- **WHEN** `qsnap -n run` completes
- **THEN** the summary table header contains `Dryrun: YES`

#### Scenario: Dry-run summary footer
- **WHEN** `qsnap -n run` completes
- **THEN** the summary table footer contains the dry-run disclaimer note

#### Scenario: Dry-run shows predicted actions from retention evaluation
- **WHEN** `qsnap -n run` evaluates retention policy for VM "testvm" with 3 snapshots to keep and 2 to remove
- **THEN** the "testvm" block shows `---` for the 2 snapshots that WOULD be removed
- **AND** no actual blockcommit or file deletion was executed

### Requirement: Summary formatter as pure function

The summary formatter SHALL be implemented as a pure function `format_summary(result: PipelineResult) -> str` in `qsnap/cli/summary.py`. It SHALL accept only `PipelineResult` and return a formatted string. It SHALL NOT access the filesystem, state manager, or config facade.

#### Scenario: Formatter has no side effects
- **WHEN** `format_summary(result)` is called twice with the same `PipelineResult`
- **THEN** identical strings are returned
- **AND** no files are read or written

#### Scenario: Formatter reads from PipelineResult.actions only
- **WHEN** `format_summary(result)` is called
- **THEN** all data used for formatting comes from `result.actions` and `result.results`

### Requirement: Summary table respects --quiet mode

When `--quiet` / `-q` is passed, the summary table SHALL still be printed. The `--quiet` flag suppresses stderr logging, not stdout summary output. This mirrors btrbk behavior.

#### Scenario: Quiet mode still prints summary
- **WHEN** `qsnap -q run` completes
- **THEN** no stderr INFO/WARNING messages are printed
- **AND** the summary table is printed to stdout

### Requirement: Summary table groups actions by VM

The summary table SHALL group `ActionRecord` entries by `vm_name`. VMs with no actions SHALL be omitted. Within each VM block, actions SHALL be sorted in pipeline execution order.

#### Scenario: VM with no actions is omitted
- **WHEN** a VM had `onchange` mode and no changes were detected
- **THEN** that VM does not appear in the summary table

#### Scenario: Actions sorted by pipeline order
- **WHEN** VM "testvm" had snapshot_create, then snapshot_delete, then backup_transfer
- **THEN** the "testvm" block lists actions in that exact order
