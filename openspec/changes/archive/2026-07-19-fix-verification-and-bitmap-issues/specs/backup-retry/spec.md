## MODIFIED Requirements

### Requirement: Retry wrapper for backup transfers on transient errors

Core SHALL wrap backup transfer calls in a retry loop when `backup_retry_max > 0`. The retry SHALL use exponential backoff: attempt 1 waits `backup_retry_base`, attempt 2 waits `backup_retry_base * 2`, attempt 3 waits `backup_retry_base * 4`. A transfer is retryable if the `BackupResult.error` or `ShellResult.error` contains any of: `"Connection refused"`, `"No route to host"`, `"timed out"` (case-insensitive match), `"broken pipe"`, `"EOF"`, `"verification failed: hash mismatch"`. Errors matching `"No space left on device"`, `"Permission denied"`, or qcow2-format errors SHALL NOT be retried. Verification failures other than hash mismatch (e.g., format errors, virtual-size mismatch) SHALL NOT be retried — they indicate deterministic corruption, not transient failures.

#### Scenario: Transient error retried successfully

- **WHEN** a transfer fails with "Connection refused" on attempt 1
- **AND** `backup_retry_max = 3`, `backup_retry_base = "2s"`
- **THEN** the transfer is retried after 2 seconds
- **AND** if attempt 2 succeeds, `BackupResult(success=True)` is returned
- **AND** an INFO log indicates retry succeeded on attempt 2

#### Scenario: Hash mismatch retried

- **WHEN** a transfer fails with "verification failed: hash mismatch" on attempt 1
- **AND** `backup_retry_max = 3`
- **THEN** the transfer is retried (the mismatch may indicate a transient transfer corruption)
- **AND** if attempt 2 succeeds, `BackupResult(success=True)` is returned

#### Scenario: All retries exhausted

- **WHEN** a transfer fails on all 3 attempts
- **THEN** `BackupResult(success=False)` is returned with the error from the last attempt
- **AND** a WARNING log is emitted: "Backup transfer for <snap> failed after 3 retries"

#### Scenario: Non-retryable error fails immediately

- **WHEN** a transfer fails with "No space left on device"
- **THEN** no retry is attempted
- **AND** `BackupResult(success=False)` is returned immediately

#### Scenario: Format verification error not retried

- **WHEN** a transfer fails with "verification failed: expected format qcow2, got raw"
- **THEN** no retry is attempted (deterministic corruption, not transient)
- **AND** `BackupResult(success=False)` is returned immediately

#### Scenario: Retry disabled when backup_retry_max = 0

- **WHEN** `backup_retry_max = 0`
- **THEN** no retry loop is entered regardless of error type
- **AND** the transfer result is returned as-is
