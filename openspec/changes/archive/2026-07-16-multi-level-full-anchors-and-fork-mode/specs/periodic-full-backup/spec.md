# periodic-full-backup — Delta Spec

## MODIFIED Requirements

### Requirement: Bucket-driven FULL creation logic
`Core._should_create_bucket_full(target, policy, all_fulls, snapshot_ts) -> tuple[bool, str]` SHALL accept a list of ALL full backups for the target (instead of a single `last_full` record). It SHALL determine which buckets to check as follows:
1. If any `anchor_*` field on the policy is `True`, check only F-marked buckets in descending order (yearly → monthly → weekly → daily → hourly).
2. Otherwise, check ALL buckets where `policy.{bucket} > 0` in descending order.

For each checked bucket, it SHALL find the most recent FULL with matching `bucket_level` from `all_fulls`. It SHALL return `(True, bucket_level)` when: (a) no previous FULL exists for that bucket, or (b) the snapshot's timestamp falls in a new period of that bucket compared to the matching FULL's timestamp. It SHALL short-circuit on the first match — at most one FULL is created per snapshot. It SHALL return `(False, "")` if no checked bucket triggers a new FULL.

#### Scenario: Highest bucket is yearly with all-buckets mode
- **WHEN** policy has `yearly=1, monthly=12, weekly=4, daily=7, hourly=24` and no F-anchors
- **THEN** all buckets are checked: yearly, monthly, weekly, daily, hourly
- **THEN** FULLs are created at each bucket's boundary

#### Scenario: F-anchor overrides to daily-only
- **WHEN** policy has `yearly=1, monthly=12, weekly=4, daily=7, hourly=24, anchor_daily=True`
- **THEN** only daily bucket is checked
- **THEN** FULLs are created only at day boundaries

#### Scenario: No active buckets
- **WHEN** policy has all counts 0 and `preserve_min = "all"` and no F-anchors
- **THEN** `_should_create_bucket_full()` returns `(False, "")` and no FULL is created

#### Scenario: First backup to target creates FULL
- **WHEN** a target has no existing backups (`all_fulls` is empty) and a snapshot is available
- **THEN** `_should_create_bucket_full()` returns `(True, "yearly")` (first checked bucket that is active or F-marked)

### Requirement: Core triggers full backup before incremental transfer
`Core._backup_target()` SHALL retrieve ALL full backups via `state.get_full_backups(target.path)` and pass the complete list to `_should_create_bucket_full()`. The first backup to a target SHALL always be a FULL.

#### Scenario: First backup to target creates FULL
- **WHEN** `get_full_backups(target.path)` returns an empty list and a snapshot is available
- **THEN** a FULL backup is created immediately via `qemu-img convert`

#### Scenario: New weekly period triggers FULL (all-buckets mode)
- **WHEN** the policy has `weekly=4` active and no F-anchors, and the current snapshot's ISO week differs from the last weekly FULL's week
- **THEN** a FULL backup is created with `bucket_level="weekly"`

#### Scenario: F-anchor on weekly only triggers FULL at week boundaries
- **WHEN** the policy has `weekly=4, anchor_weekly=True, daily=7, anchor_daily=False`
- **AND** the current snapshot's day differs from the last daily FULL's day
- **THEN** no FULL is created (daily is not an F-anchor)
- **AND** if the current snapshot's week differs from the last weekly FULL's week, a FULL IS created

## REMOVED Requirements

### Requirement: Core._backup_target triggers full backup when due
**Reason**: Replaced by bucket-driven FULL creation with all-bucket checking and F-anchor support. The old requirement referenced `target.full_every` which no longer exists.
**Migration**: No action required. The new behavior is automatic. Users who relied on single-highest-bucket behavior must either reduce their bucket counts or use F-syntax to select the desired level.
