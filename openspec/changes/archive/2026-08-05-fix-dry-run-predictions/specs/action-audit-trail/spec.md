# Action Audit Trail — Delta Spec

## MODIFIED Requirements

### Requirement: PipelineResult carries actions

`PipelineResult` SHALL have a field `actions: list[ActionRecord]`, populated from `self._actions` at the end of `_run_pipeline()`. The field SHALL be present in both `run()` and `snapshot()` pipeline paths.

`PipelineResult` SHALL additionally have a field `predictions: list[ActionRecord]`, populated from the dry-run prediction accumulator at the end of `_run_pipeline()`. In non-dry-run mode `predictions` SHALL be empty. In dry-run mode `actions` SHALL remain empty and `predictions` SHALL carry one record per predicted mutation. Prediction records SHALL use the same `ActionRecord` structure; the action vocabulary is extended with `blockcommit` for predicted overlay merges. Predictions SHALL never be passed to `TransactionWriter`.

#### Scenario: PipelineResult includes actions after successful run
- **WHEN** `core.run()` completes with 2 snapshots created, 1 blockcommitted, 3 backups transferred
- **THEN** `result.actions` contains 6 `ActionRecord` entries in pipeline execution order
- **AND** `result.predictions` is empty

#### Scenario: PipelineResult includes error actions
- **WHEN** `core.run()` completes with 1 backup transfer failure
- **THEN** `result.actions` contains an `ActionRecord(action="error", ...)` for the failed transfer

#### Scenario: PipelineResult carries predictions in dry-run
- **WHEN** `core.run()` completes in dry-run mode with predicted mutations
- **THEN** `result.predictions` contains one `ActionRecord` per predicted mutation, each with `vm_name` and `disk` populated where the action is disk-scoped
- **AND** `result.actions` is empty
- **AND** no prediction is written to the transaction log
