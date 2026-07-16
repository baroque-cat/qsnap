# Full Anchor Syntax (F-Syntax)

## Purpose

Manual per-bucket FULL anchor control via `F` prefix in the `target_preserve` and `snapshot_preserve` strings. When one or more `F`-marked buckets are present, automatic multi-level FULL logic is disabled — FULLs are created ONLY at F-marked levels.

## ADDED Requirements

### Requirement: F-prefix syntax for FULL anchor specification
The system SHALL accept `F` as an optional prefix on the count in a retention policy token: `7Fd` means "retain 7 daily snapshots AND create FULL backups at daily boundaries." The format SHALL be `{count}{F?}{bucket}` where `bucket` is one of `h, d, w, m, y` and `F` is the literal character `F`.

#### Scenario: F-anchor on a single bucket
- **WHEN** `target_preserve = "24h 7Fd 4w"` is parsed
- **THEN** daily bucket has `count=7` and `anchor_daily=True`
- **THEN** hourly and weekly buckets are NOT anchors

#### Scenario: F-anchor on all buckets
- **WHEN** `target_preserve = "24Fh 7Fd 4Fw 12Fm 1Fy"` is parsed
- **THEN** ALL buckets are anchors
- **THEN** FULLs are created at every boundary

#### Scenario: F-anchor requires count > 0
- **WHEN** `target_preserve = "0Fh 7d"` is parsed
- **THEN** ConfigFacade raises ConfigError with message: "F-anchor on bucket 'h' requires count > 0"

### Requirement: F-syntax disables automatic multi-level behavior
When ANY bucket in the policy has `anchor_* = True`, `Core._should_create_bucket_full()` SHALL only check F-marked buckets. Non-F buckets SHALL be ignored for FULL creation (they still participate in retention).

#### Scenario: F-anchor present — only F-marked buckets checked
- **WHEN** policy has `daily=7, anchor_daily=True` and `weekly=4, anchor_weekly=False`
- **THEN** `_should_create_bucket_full()` checks only daily bucket boundaries
- **THEN** weekly bucket is ignored for FULL creation

#### Scenario: Multiple F-anchors — all checked
- **WHEN** policy has `anchor_daily=True` and `anchor_monthly=True`
- **AND** both daily and monthly periods have changed
- **THEN** `_should_create_bucket_full()` returns `(True, "monthly")` (highest first)

### Requirement: F-syntax is valid in both snapshot_preserve and target_preserve
The `F` prefix SHALL be valid in both `snapshot_preserve` and `target_preserve` configuration fields. For snapshot policies, F-anchors SHALL have no effect on snapshot behavior (snapshots are never FULLs — the `F` is silently ignored at the snapshot level) but SHALL NOT produce a parse error.

#### Scenario: F-anchor in snapshot_preserve parses without error
- **WHEN** `snapshot_preserve = "24Fh 7Fd"` is configured
- **THEN** ConfigFacade accepts it without error
- **THEN** the F-markers are stored in RetentionPolicy but have no effect on snapshot logic

### Requirement: RetentionPolicy gains anchor boolean fields
`RetentionPolicy` SHALL gain five frozen boolean fields: `anchor_hourly: bool = False`, `anchor_daily: bool = False`, `anchor_weekly: bool = False`, `anchor_monthly: bool = False`, `anchor_yearly: bool = False`. These SHALL be set to `True` when the corresponding `F` prefix is present in the parsed policy string.

#### Scenario: Anchor fields default to False
- **WHEN** `RetentionPolicy()` is created with `hourly=24, daily=7`
- **THEN** all `anchor_*` fields are `False`

#### Scenario: Anchor fields set from parsed F-syntax
- **WHEN** `_parse_preserve("24h 7Fd 4Fw")` is called
- **THEN** returns `RetentionPolicy(hourly=24, daily=7, weekly=4, anchor_daily=True, anchor_weekly=True)`
