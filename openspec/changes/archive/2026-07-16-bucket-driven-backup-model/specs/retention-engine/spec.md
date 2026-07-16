## ADDED Requirements

### Requirement: Dependency-aware deletion is handled by Core, not retention engine

`TimeBasedRetention.evaluate()` SHALL remain a pure function that returns keep/remove lists based solely on timestamps and policy. Dependency-aware cascade deletion (preventing deletion of FULLs with active dependents) SHALL be handled by `Core._cleanup_backups()` after the retention engine produces its result. The retention engine SHALL NOT access `IStateManager` or perform any I/O.

#### Scenario: Retention engine returns pure keep/remove
- **WHEN** `evaluate()` is called with items and policy
- **THEN** the result contains only keep/remove lists based on timestamps
- **AND** no dependency checking is performed by the retention engine

#### Scenario: Core post-processes retention result for dependencies
- **WHEN** the retention engine marks a FULL for removal
- **AND** `Core._cleanup_backups()` finds that an incremental in the keep-set references that FULL
- **THEN** Core removes the FULL from the deletion list (ghost retention)
