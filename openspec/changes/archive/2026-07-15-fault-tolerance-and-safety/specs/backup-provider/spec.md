## ADDED Requirements

### Requirement: Backup providers remain retry-unaware
Backup providers (`FileCopyBackupProvider`, `BitmapBackupProvider`) SHALL NOT implement any retry logic internally. They SHALL continue to return `BackupResult(success=False, error=...)` for any failure. The retry logic SHALL be handled by Core's `_backup_target()` method, which wraps the provider's `transfer_missing()` call.

#### Scenario: Provider returns error, Core handles retry
- **WHEN** `FileCopyBackupProvider.transfer_missing()` returns `BackupResult(success=False, error="Connection refused")`
- **THEN** the provider itself does not retry
- **AND** Core's retry wrapper inspects the error and decides whether to retry

#### Scenario: BackupResult error is structured for retry detection
- **WHEN** a backup transfer fails
- **THEN** the `BackupResult.error` string contains the underlying error from `ShellResult.error`
- **AND** Core's retry logic can pattern-match against it to determine retryability
