# Multi-Level Full Anchors

## Purpose

Automatic FULL backup creation at ALL active retention bucket boundaries (not just the highest). Dramatically reduces maximum incremental chain length from months/years to the span of the shortest active bucket level.

## Requirements

### Requirement: FULL backups triggered at all active bucket boundaries
`Core._should_create_bucket_full(target, policy, all_fulls, snapshot_ts)` SHALL iterate over ALL buckets where `policy.{bucket} > 0` (yearly, monthly, weekly, daily, hourly, in descending order) and for each, compare the snapshot's period key against the most recent FULL with matching `bucket_level`. It SHALL return `(True, bucket_level)` for the first bucket where the period has changed or no previous FULL exists for that bucket. If no active bucket needs a new FULL, it SHALL return `(False, "")`.

#### Scenario: All active buckets produce FULLs on period change
- **WHEN** policy has `yearly=1, monthly=12, weekly=8` and the last FULLs are `yearly=2026-01-01, monthly=2026-06-01, weekly=2026-W24`
- **AND** a snapshot arrives on 2026-06-08 (ISO week 25)
- **THEN** `_should_create_bucket_full()` checks yearly ("2026" == "2026" → no), monthly ("202606" == "202606" → no), weekly ("2026-W24" != "2026-W25" → YES)
- **THEN** returns `(True, "weekly")`

#### Scenario: First backup creates FULL at each active bucket
- **WHEN** a target has no previous FULLs and policy has `yearly=1, monthly=12`
- **AND** a snapshot arrives on 2026-07-01
- **THEN** `_should_create_bucket_full()` checks yearly (no previous → YES), returns `(True, "yearly")`
- **THEN** monthly is not checked (short-circuits on first match)

#### Scenario: Same bucket period skips FULL
- **WHEN** policy has `weekly=4` and the last weekly FULL was 2026-W28 and the snapshot is in 2026-W28
- **AND** no higher bucket is active
- **THEN** returns `(False, "")`

#### Scenario: Single active bucket preserves highest-only behavior
- **WHEN** policy has `yearly=0, monthly=0, weekly=4, daily=0, hourly=0` (only weekly active)
- **THEN** behavior is identical to old highest-active-bucket mode (weekly FULLs only)

### Requirement: Core._backup_target passes all FULLs to bucket check
`Core._backup_target()` SHALL call `state.get_full_backups(target.path)` to retrieve ALL full backups for the target, and pass the complete list to `_should_create_bucket_full()` instead of a single `last_full` record.

#### Scenario: get_full_backups used for per-bucket comparison
- **WHEN** `_backup_target()` is called with a target that has 3 FULL records (yearly, monthly, weekly)
- **THEN** `state.get_full_backups(target.path)` is called (not `get_last_full_backup`)
- **THEN** the full list is passed to `_should_create_bucket_full()`

### Requirement: Periodic FULL frequency is limited by policy granularity
A snapshot that triggers a new yearly FULL on January 1 SHALL also be in a new monthly period (January) and potentially new weekly/daily/hourly periods, but `_should_create_bucket_full` SHALL short-circuit on the first matching bucket. Only ONE FULL is created per snapshot regardless of how many bucket periods have changed.

#### Scenario: One FULL created per snapshot despite multiple period changes
- **WHEN** a snapshot on January 1 falls in a new yearly, monthly, weekly, and daily period
- **THEN** `_should_create_bucket_full()` returns `(True, "yearly")` and stops checking
- **THEN** only one FULL is created
