# Result Types — delta

## MODIFIED Requirements

### Requirement: CommitResult dataclass
The system SHALL provide an immutable `CommitResult` dataclass representing the outcome of a commit operation (`virsh blockcommit` or `qemu-img commit`), with `success: bool`, `committed_snapshot: str`, `error: str | None`, and `outcome: str`. `outcome` SHALL be one of `"success"`, `"failure"`, or `"unknown"`, defaulting to `"failure"` so existing constructor calls remain valid. `"unknown"` denotes an indeterminate outcome (command timed out or was killed; the real state of the chain is unknown and MUST be reconciled). `success=True` SHALL imply `outcome="success"`.

#### Scenario: Successful blockcommit
- **WHEN** a `CommitResult` is created with `success=True`, `committed_snapshot="myvm.20250101T1200"`, `error=None`
- **THEN** the result indicates the named snapshot was merged into its backing file
- **AND** `outcome` is `"success"` when set explicitly by the producer

#### Scenario: Unknown outcome from timeout
- **WHEN** a `CommitResult` is created with `success=False`, `outcome="unknown"`, `error="Command timed out after 1800s"`
- **THEN** `result.outcome == "unknown"` and callers MUST NOT treat it as a definitive failure

#### Scenario: Default outcome preserves legacy constructors
- **WHEN** a `CommitResult` is created without the `outcome` argument
- **THEN** `result.outcome == "failure"` (the default) and all existing fields behave unchanged
