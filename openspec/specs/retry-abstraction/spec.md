# Retry Abstraction

## Purpose

Extracts the duplicated retry-loop pattern from `_transfer_with_retry()` and the inline FULL backup creation loop in `Core._backup_target()` into a single generic `_execute_with_retry()` method. Fixes the `backup_retry_max = 0` bug where the FULL creation path produces an empty retry loop.

## ADDED Requirements

### Requirement: Generic retry wrapper _execute_with_retry

Core SHALL provide a private method `_execute_with_retry(operation: Callable[[], Any], target: TargetConfig, *, is_retryable_fn: Callable[[str], bool] = is_retryable) -> Any` that executes `operation()` with exponential backoff retry. When `target.backup_retry_max <= 0`, it SHALL execute `operation()` exactly once (no retry loop). When `target.backup_retry_max > 0`, it SHALL loop up to `target.backup_retry_max` times. On each attempt:

1. Call `operation()` and check if the result has a `success` attribute.
2. If `result.success` is `True`, return `result` immediately.
3. If `result.success` is `False`, check if the error is retryable via `is_retryable_fn(result.error or "")`.
4. If not retryable, return `result` immediately (no further retries).
5. If retryable and more attempts remain, compute backoff via `compute_backoff(base_seconds, attempt)` and `time.sleep(backoff)`.
6. If all attempts exhausted, return the last `result`.

The `base_seconds` SHALL be derived from `target.backup_retry_base` via `parse_retry_duration()`.

The method SHALL replace both the retry loop in `_transfer_with_retry()` and the inline retry loop in the FULL backup creation path.

#### Scenario: max_retries <= 0 — single attempt

- **WHEN** `_execute_with_retry(operation, target)` is called
- **AND** `target.backup_retry_max = 0`
- **THEN** `operation()` is called exactly once
- **AND** the result is returned as-is (success or failure)
- **AND** no `compute_backoff()` or `time.sleep()` is called

#### Scenario: Transient error retried successfully

- **WHEN** `_execute_with_retry(operation, target)` is called
- **AND** `target.backup_retry_max = 3`, `target.backup_retry_base = "2s"`
- **AND** attempt 1 fails with "Connection refused"
- **THEN** `is_retryable_fn("Connection refused")` returns `True`
- **AND** `compute_backoff(2, 1) = 2` seconds waited
- **AND** attempt 2 is executed
- **AND** if attempt 2 succeeds, the result is returned

#### Scenario: Non-retryable error fails immediately

- **WHEN** `_execute_with_retry(operation, target)` is called
- **AND** `target.backup_retry_max = 3`
- **AND** attempt 1 fails with "No space left on device"
- **THEN** `is_retryable_fn("No space left on device")` returns `False`
- **AND** the result is returned immediately without further attempts

#### Scenario: All retries exhausted

- **WHEN** `_execute_with_retry(operation, target)` is called
- **AND** `target.backup_retry_max = 3`
- **AND** all 3 attempts fail with retryable errors
- **THEN** the last result is returned
- **AND** an INFO log indicates recovery after successful retry, or a WARNING log if all exhausted

### Requirement: FULL backup creation uses _execute_with_retry

The inline retry loop in `Core._backup_target()` that wraps `provider.create_full_backup()` SHALL be replaced with a call to `self._execute_with_retry(operation, target)`. The FULL backup creation SHALL apply `is_retryable()` filtering so that non-transient errors (e.g., "No space left on device") are not retried, consistent with the incremental path.

#### Scenario: FULL creation retried on transient error

- **WHEN** `create_full_backup()` fails with "Connection refused"
- **AND** `target.backup_retry_max = 3`
- **THEN** the FULL creation is retried with exponential backoff
- **AND** up to 3 attempts are made

#### Scenario: FULL creation not retried on non-transient error

- **WHEN** `create_full_backup()` fails with "No space left on device"
- **AND** `target.backup_retry_max = 3`
- **THEN** no retry is attempted
- **AND** the failure result is returned immediately

### Requirement: Incremental transfer uses _execute_with_retry

`Core._transfer_with_retry()` SHALL delegate its retry loop to `self._execute_with_retry()`. The method retains its current signature but its implementation SHALL use the shared retry wrapper.

#### Scenario: Incremental transfer retry via shared wrapper

- **WHEN** `_transfer_with_retry(provider, vm_config, target, snapshots)` is called
- **AND** `target.backup_retry_max = 3`
- **THEN** the retry loop is executed via `_execute_with_retry()`
- **AND** behavior is unchanged from the current implementation
