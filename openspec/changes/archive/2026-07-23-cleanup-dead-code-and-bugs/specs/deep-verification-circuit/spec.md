## MODIFIED Requirements

### Requirement: VMConfig blockcommit_deep_verify and snapshot_deep_verify fields
`VMConfig` SHALL include `blockcommit_deep_verify: bool` (default `False`). The `snapshot_deep_verify` field is REMOVED — it was parsed and stored but never consumed by any code path. Only `blockcommit_deep_verify` is wired into the lifecycle manager via the `deep_verify` keyword argument on `ILifecycleManager.blockcommit()`.

#### Scenario: Deep verify defaults to off

- **WHEN** `VMConfig` is constructed without deep verify fields
- **THEN** `blockcommit_deep_verify` is `False`
- **AND** `snapshot_deep_verify` does not exist on the dataclass

#### Scenario: Deep verify enabled for critical VM

- **WHEN** `blockcommit_deep_verify = true`
- **AND** deferred blockcommit executes while VM is shut off
- **THEN** `BlockCommitManager.blockcommit()` is called with `deep_verify=True`

### Requirement: BlockCommitManager deep_verify flag
`BlockCommitManager.blockcommit()` SHALL accept an optional `deep_verify: bool = False` keyword argument. When `True`, after a successful blockcommit the manager SHALL run `qemu-img check --output=json` on the base image. If ANY of `corruptions`, `errors`, OR `leaks` is non-zero, the `CommitResult` SHALL be `success=False` with the count in the error message.

#### Scenario: deep_verify passes after deferred commit

- **WHEN** `blockcommit(deep_verify=True)` succeeds and `qemu-img check` reports 0 for all fields
- **THEN** `CommitResult(success=True)` is returned

#### Scenario: deep_verify fails on corruptions

- **WHEN** `blockcommit(deep_verify=True)` succeeds but `qemu-img check` reports `corruptions: 5`
- **THEN** `CommitResult(success=False, error="deep verify: 5 corruptions in base image")` is returned

#### Scenario: deep_verify fails on errors

- **WHEN** `blockcommit(deep_verify=True)` succeeds but `qemu-img check` reports `errors: 2`
- **THEN** `CommitResult(success=False, error="deep verify: 2 errors in base image")` is returned

#### Scenario: deep_verify fails on leaks

- **WHEN** `blockcommit(deep_verify=True)` succeeds but `qemu-img check` reports `leaks: 3`
- **THEN** `CommitResult(success=False, error="deep verify: 3 leaks in base image")` is returned
