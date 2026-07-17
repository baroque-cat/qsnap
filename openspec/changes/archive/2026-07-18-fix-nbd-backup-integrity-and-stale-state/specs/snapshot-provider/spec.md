## ADDED Requirements

### Requirement: Snapshot creation retry on lock conflict

`ExternalSnapshotProvider.create()` SHALL retry `virsh snapshot-create-as` when the error indicates a state change lock conflict. The retry loop SHALL attempt up to 3 total attempts (1 initial + 2 retries) with exponential backoff: 2 seconds, 4 seconds. On the third failure, the error SHALL be returned as-is. The retry is scoped to lock-conflict errors only — other failures (disk full, permission denied, etc.) SHALL NOT be retried.

#### Scenario: Lock conflict resolves on first retry
- **WHEN** `virsh snapshot-create-as` fails with "cannot acquire state change lock"
- **AND** the second attempt succeeds
- **THEN** `SnapshotResult(success=True)` is returned
- **AND** total wait time was 2 seconds

#### Scenario: Lock conflict persists through all retries
- **WHEN** `virsh snapshot-create-as` fails with "cannot acquire state change lock" on all 3 attempts
- **THEN** `SnapshotResult(success=False, error=<last error>)` is returned
- **AND** total wait time was 6 seconds (2s + 4s)

#### Scenario: Non-lock error is NOT retried
- **WHEN** `virsh snapshot-create-as` fails with "No space left on device"
- **THEN** `SnapshotResult(success=False, error=<error>)` is returned immediately
- **AND** no retry is attempted

#### Scenario: Timeout on lock conflict triggers retry
- **WHEN** `virsh snapshot-create-as` times out with error containing "cannot acquire state change lock"
- **THEN** the retry mechanism treats this as a lock conflict and retries
- **AND** subsequent attempts use the same timeout value
