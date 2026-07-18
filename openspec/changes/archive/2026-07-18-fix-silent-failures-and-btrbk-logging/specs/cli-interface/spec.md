## ADDED Requirements

### Requirement: Summary output after run command

The `handle_run()` function in `qsnap/cli/commands.py` SHALL, after computing the exit code via `_format_pipeline_result()`, call `format_summary(result)` from `qsnap/cli/summary.py` and print the result to stdout. The summary formatter SHALL be a pure function — no business logic in CLI.

#### Scenario: Summary printed after successful run
- **WHEN** `qsnap run` completes successfully
- **THEN** the return code is computed by `_format_pipeline_result()`
- **AND** `format_summary(result)` is called with the `PipelineResult`
- **AND** the formatted summary is printed to stdout

#### Scenario: Summary printed after run with backup failures
- **WHEN** `qsnap run` completes with exit code 10 (backup abort)
- **THEN** the summary table is still printed to stdout
- **AND** failed transfers are marked with `!!!` in the summary

#### Scenario: Summary printed after dry-run
- **WHEN** `qsnap -n run` completes
- **THEN** the summary table is printed with `Dryrun: YES` header
- **AND** the dry-run disclaimer footer is printed

### Requirement: CLI thin-layer constraint for summary

The CLI layer SHALL NOT parse `PipelineResult.actions` to compute any business logic. The `format_summary()` function SHALL only translate the `PipelineResult` data structure into a formatted string. It SHALL NOT access `IStateManager`, `IConfigFacade`, or any module.

#### Scenario: No business logic in summary formatter
- **WHEN** reviewing `qsnap/cli/summary.py`
- **THEN** it contains no imports from `qsnap.modules`, `qsnap.config`, `qsnap.retention`, or `qsnap.state`
- **AND** it contains only `from qsnap.models.results import PipelineResult, ActionRecord`

## MODIFIED Requirements

### Requirement: CLI is a thin layer

The CLI layer (commands.py) SHALL NOT parse config, create snapshots, evaluate retention, or perform any business logic. It SHALL only translate CLI args into Core method calls and format the returned results. The summary formatting is handled by `qsnap/cli/summary.py` as a pure function, invoked from `_format_pipeline_result()` after exit code computation.

#### Scenario: No business logic in CLI
- **WHEN** reviewing `qsnap/cli/commands.py`
- **THEN** it contains no imports from `qsnap.modules`, `qsnap.config`, `qsnap.retention`, or `qsnap.state`
- **AND** `format_summary()` is called from `qsnap/cli/summary.py` as a pure function
