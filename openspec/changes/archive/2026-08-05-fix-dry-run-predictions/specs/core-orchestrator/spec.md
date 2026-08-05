# Core Orchestrator — Delta Spec

## MODIFIED Requirements

### Requirement: Dry-run mode
Core SHALL support dry-run mode where all pipeline steps are evaluated but no mutation occurs (no snapshot creation, no blockcommit, no file copy, no file deletion, no state writes, no XML changes, no transaction log). Dry-run mode SHALL be activated via the `dry_run` boolean property on the Core instance, settable by the CLI `--dry-run` / `-n` flag. The dry-run SHALL NOT accumulate `ActionRecord` entries in `PipelineResult.actions` — since no mutations occur, no actions are recorded. Instead, dry-run SHALL accumulate prediction records in `PipelineResult.predictions` (see capability `dry-run-prediction`). The `PipelineResult.dry_run` flag SHALL be set to `True` to indicate the run was a dry-run.

In dry-run mode, Core SHALL simulate the snapshots that would be created and thread them through snapshot retention, backup steps, the per-disk FULL decision, and the incremental transfer prediction, so that all predictions reflect the post-run world (capability `dry-run-prediction`). `Core._check_deferred_operations()` SHALL be guarded: no blockcommit execution and no state writes occur in dry-run.

#### Scenario: Dry-run logs planned actions
- **WHEN** `core.run()` is called in dry-run mode
- **THEN** each planned action is logged at INFO level with VM and disk context, but no IShell mutating commands are executed
- **AND** `PipelineResult.dry_run` is `True`
- **AND** `PipelineResult.actions` is empty (no mutations occurred, so no `ActionRecord` entries are accumulated)
- **AND** `PipelineResult.predictions` contains one record per predicted mutation

#### Scenario: Dry-run activated from CLI
- **WHEN** `qsnap -n run` is executed
- **THEN** `Core.dry_run` is set to `True` before `core.run()` is called

#### Scenario: Dry-run predictions reflect post-run state
- **WHEN** `core.run()` is called in dry-run mode for a VM whose snapshot count would exceed `snapshot_chain_length` after the would-be-created snapshots
- **THEN** the retention prediction includes the would-be-created snapshots in its input
- **AND** the predicted remove set matches what a real run would remove

#### Scenario: Dry-run does not drain the deferred queue
- **WHEN** `core.run()` is called in dry-run mode with queued deferred blockcommits
- **THEN** no lifecycle manager blockcommit is executed
- **AND** the deferred queue and snapshot state are unchanged
- **AND** a per-disk prediction of the would-be drain is logged
