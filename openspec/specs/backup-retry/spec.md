## Purpose

Provides retry logic for backup transfers when transient network or I/O errors occur, using exponential backoff with configurable per-target settings. Retry is a Core concern so that backup providers remain unaware of retry semantics.

## Requirements

### Requirement: Retry wrapper for backup transfers on transient errors
Core SHALL wrap backup transfer calls in a retry loop when `backup_retry_max > 0`. The retry SHALL use exponential backoff: attempt 1 waits `backup_retry_base`, attempt 2 waits `backup_retry_base * 2`, attempt 3 waits `backup_retry_base * 4`. A transfer is retryable if the `BackupResult.error` or `ShellResult.error` contains any of: `"Connection refused"`, `"No route to host"`, `"timed out"` (case-insensitive match), `"broken pipe"`, `"EOF"`. Errors matching `"No space left on device"`, `"Permission denied"`, or qcow2-format errors SHALL NOT be retried.

#### Scenario: Transient error retried successfully
- **WHEN** a transfer fails with "Connection refused" on attempt 1
- **AND** `backup_retry_max = 3`, `backup_retry_base = "2s"`
- **THEN** the transfer is retried after 2 seconds
- **AND** if attempt 2 succeeds, `BackupResult(success=True)` is returned
- **AND** an INFO log indicates retry succeeded on attempt 2

#### Scenario: All retries exhausted
- **WHEN** a transfer fails on all 3 attempts
- **THEN** `BackupResult(success=False)` is returned with the error from the last attempt
- **AND** a WARNING log is emitted: "Backup transfer for <snap> failed after 3 retries"

#### Scenario: Non-retryable error fails immediately
- **WHEN** a transfer fails with "No space left on device"
- **THEN** no retry is attempted
- **AND** `BackupResult(success=False)` is returned immediately

#### Scenario: Retry disabled when backup_retry_max = 0
- **WHEN** `backup_retry_max = 0`
- **THEN** no retry loop is entered regardless of error type
- **AND** the transfer result is returned as-is

### Requirement: Target-level retry configuration
`TargetConfig` SHALL include `backup_retry_max: int` (default `3`) and `backup_retry_base: str` (default `"2s"`) fields. The base SHALL be parsed as a duration string (e.g. `"1s"`, `"5s"`, `"10s"`).

#### Scenario: Target defaults for retry
- **WHEN** `TargetConfig` is constructed without retry fields
- **THEN** `backup_retry_max` is `3` and `backup_retry_base` is `"2s"`

#### Scenario: Target overrides retry settings
- **WHEN** a target config specifies `backup_retry_max = 5` and `backup_retry_base = "10s"`
- **THEN** transfers to this target retry up to 5 times with 10s/20s/40s/80s/160s delays

#### Scenario: Invalid retry base string
- **WHEN** `ConfigFacade` parses `backup_retry_base = "abc"`
- **THEN** a `ConfigError` is raised with a message indicating valid format

### Requirement: Retry is a Core concern, not a provider concern
Retry logic SHALL reside in Core (`_backup_target` method), not in individual backup providers. Providers SHALL return structured `BackupResult` objects; Core SHALL inspect the `error` field to determine retryability.

#### Scenario: Providers remain retry-unaware
- **WHEN** `FileCopyBackupProvider.transfer_missing()` fails with a network error
- **THEN** the provider returns `BackupResult(success=False, error="Connection refused")` as before
- **AND** Core's retry wrapper handles the retry logic externally
