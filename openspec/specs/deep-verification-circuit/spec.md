## Purpose

Provides a deep verification circuit that periodically checks the integrity of all snapshot and backup images using `qemu-img check`, and optionally performs post-blockcommit deep verification of base images to detect corruption.

## Requirements

### Requirement: Separate systemd timer for deep verification
The system SHALL ship a `qsnap-check.timer` systemd timer unit that triggers `qsnap-check.service` on a configurable schedule (default weekly, Sunday at 03:00). The timer SHALL use `Persistent=True` and `RandomizedDelaySec=1800`. The service SHALL execute `qsnap check --deep`.

#### Scenario: Weekly deep check service
- **WHEN** `systemctl start qsnap-check.timer` is enabled
- **THEN** `qsnap-check.service` runs `qsnap check --deep` every Sunday at 03:00

#### Scenario: Persistent timer catches up
- **WHEN** the system was off during the scheduled deep check
- **THEN** `Persistent=True` causes the service to run immediately on next boot

### Requirement: qsnap check --deep enhanced with per-image verification

The `qsnap check --deep` command SHALL, for each VM: (a) run `qemu-img check --output=json` on every snapshot in `IStateManager.get_snapshots()`, (b) run `qemu-img check --output=json` on every backup file on each target, (c) compare snapshot files on disk vs state records (orphan detection), (d) report `corruptions`, `errors`, AND `leaks` count per image, (e) aggregate per-VM status: OK (0 corruptions, 0 errors, 0 leaks), WARNING (>0 in any field but recoverable), CRITICAL (images missing/unreadable). The `qemu-img check` command SHALL use a timeout of 7200 seconds (2 hours) to accommodate large disks.

#### Scenario: All images pass deep check

- **WHEN** every snapshot and backup passes `qemu-img check` with 0 corruptions, 0 errors, and 0 leaks
- **THEN** each VM reports "OK" and exit code is 0

#### Scenario: Corruption detected in one image

- **WHEN** `qemu-img check` reports `corruptions: 3` for one snapshot
- **THEN** that VM reports "WARNING: 3 corruptions in snap1.qcow2"
- **AND** the overall exit code is 0 (warnings are non-fatal)

#### Scenario: Errors detected in one image

- **WHEN** `qemu-img check` reports `errors: 2` for one snapshot
- **THEN** that VM reports "WARNING: 2 errors in snap1.qcow2"
- **AND** the overall exit code is 0 (warnings are non-fatal)

#### Scenario: Leaks detected in one image

- **WHEN** `qemu-img check` reports `leaks: 5` for one snapshot
- **THEN** that VM reports "WARNING: 5 leaks in snap1.qcow2"
- **AND** the overall exit code is 0 (warnings are non-fatal)

#### Scenario: Image unreadable

- **WHEN** a snapshot file exists but `qemu-img check` fails with "Could not open"
- **THEN** that VM reports "CRITICAL: unreadable image /path/to/snap.qcow2"

#### Scenario: Deep check timeout accommodates large disks

- **WHEN** `qemu-img check` is run on a multi-GB disk
- **THEN** the timeout is 7200 seconds (2 hours)
- **AND** the command is not prematurely killed for large disks

### Requirement: deep_check_schedule config field
`GlobalConfig` SHALL include a `deep_check_schedule: str` field with default `"off"`. Accepted values: `"off"`, `"weekly"`, `"monthly"`. This field SHALL NOT control the systemd timer schedule (that is in the timer unit file). It SHALL be used by `qsnap check` to report whether the current deep check is on schedule.

#### Scenario: deep_check_schedule defaults to off
- **WHEN** `GlobalConfig` is constructed without `deep_check_schedule`
- **THEN** the field is `"off"`

#### Scenario: deep_check_schedule displayed in check output
- **WHEN** `qsnap check` runs and `deep_check_schedule = "weekly"`
- **AND** the last deep check was 8 days ago
- **THEN** the output includes: "Last deep check: 8 days ago (expected: weekly) — OVERDUE"

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

### Requirement: VMConfig deep verification fields

`VMConfig` SHALL include `blockcommit_deep_verify: bool` (default `False`). The `snapshot_deep_verify` field is REMOVED — it was parsed and stored but never consumed by any code path. Only `blockcommit_deep_verify` is wired into the lifecycle manager via the `deep_verify` keyword argument on `ILifecycleManager.blockcommit()`.

#### Scenario: Deep verify defaults to off

- **WHEN** `VMConfig` is constructed without deep verify fields
- **THEN** `blockcommit_deep_verify` is `False`
- **AND** `snapshot_deep_verify` does not exist on the dataclass

#### Scenario: Deep verify enabled for critical VM

- **WHEN** `blockcommit_deep_verify = true`
- **AND** deferred blockcommit executes while VM is shut off
- **THEN** `BlockCommitManager.blockcommit()` is called with `deep_verify=True`
