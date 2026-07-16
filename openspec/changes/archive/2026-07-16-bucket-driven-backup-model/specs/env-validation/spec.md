## MODIFIED Requirements

### Requirement: Pre-flight rsync availability check

The pre-flight environment validation SHALL check that the `rsync` binary is available in PATH on every pipeline run. If `rsync` is not found, validation SHALL fail with an error and the pipeline SHALL NOT proceed. This is a hard requirement — `rsync` is the sole file transfer mechanism and no fallback exists.

#### Scenario: Rsync available — validation passes
- **WHEN** `which rsync` succeeds
- **THEN** validation passes and pipeline execution continues

#### Scenario: Rsync unavailable — pipeline aborts
- **WHEN** `which rsync` returns non-zero
- **THEN** validation fails with an error: "rsync is required but not found in PATH"
- **AND** the pipeline does NOT proceed to change detection or snapshot creation

#### Scenario: Rsync check always runs
- **WHEN** `_validate_environment()` is called
- **THEN** `which rsync` is always called, regardless of `rate_limit` configuration
