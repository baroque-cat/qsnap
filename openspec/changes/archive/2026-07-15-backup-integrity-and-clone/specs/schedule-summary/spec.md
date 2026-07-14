## ADDED Requirements

### Requirement: Core.schedule_summary produces a human-readable retention preview

`Core.schedule_summary(vm_filter=None)` SHALL simulate the retention engine against a synthetic timestamp distribution and return a formatted string showing expected chain length, bucket breakdown, and storage estimates for each VM and each target.

#### Scenario: Empty state produces meaningful simulation
- **WHEN** `schedule_summary()` is called with zero recorded snapshots
- **THEN** it SHALL generate synthetic timestamps (one per hour for the configured retention window + 50% margin) and evaluate retention against them

#### Scenario: Summary logs at INFO on every timer invocation
- **WHEN** the pipeline runs via systemd timer
- **THEN** `schedule_summary()` output SHALL be logged at INFO level in the pipeline entry log

#### Scenario: Summary shows snapshot and backup breakdown
- **WHEN** `schedule_summary()` runs with `snapshot_preserve="24h 2d"` and `target_preserve="24h 7d 4w"`
- **THEN** output SHALL include separate sections for snapshots and per-target backups, each showing bucket counts (hourly, daily, weekly), expected total kept count, and estimated storage

### Requirement: TimeBasedRetention.explain returns structured bucket metadata

`TimeBasedRetention.explain(items, policy, now, preserve_day_of_week)` SHALL return a dict mapping each bucket name (`"hourly"`, `"daily"`, `"weekly"`, `"monthly"`, `"yearly"`, `"preserve_min"`) to a dict with `"count"` (number of unique items kept by that bucket) and optionally `"range"` (earliest and latest timestamps in the bucket).

#### Scenario: explain returns per-bucket counts
- **WHEN** `explain(items, policy, now)` is called with a policy of `hourly=24, daily=7`
- **THEN** the result SHALL contain `{"hourly": {"count": 24, ...}, "daily": {"count": 7, ...}}`

#### Scenario: explain is a pure function
- **WHEN** `explain()` is called twice with identical arguments
- **THEN** it SHALL return identical results
