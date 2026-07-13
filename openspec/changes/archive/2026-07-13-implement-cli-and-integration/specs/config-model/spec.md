## ADDED Requirements

### Requirement: GlobalConfig lockfile field is consumed
The `lockfile` field on `GlobalConfig` (already defined, default `None`) SHALL be consumed by the locking mechanism. If `lockfile` is not `None`, the process SHALL acquire a lock on this path before pipeline execution.

#### Scenario: Lockfile from config is used
- **WHEN** the config has `lockfile = "/var/lock/qsnap.lock"` and no `--lockfile` CLI flag is passed
- **THEN** a lock is acquired on `/var/lock/qsnap.lock`

### Requirement: GlobalConfig timestamp_format field is consumed
The `timestamp_format` field on `GlobalConfig` (already defined, default `"short"`) SHALL be consumed by `Core._generate_snapshot_name()` to select the timestamp format string. Default behavior SHALL match `"long"`.

#### Scenario: timestamp_format controls snapshot naming
- **WHEN** `timestamp_format = "short"` in the config and a snapshot is created
- **THEN** the snapshot name uses `YYYYMMDD` format

### Requirement: GlobalConfig preserve_day_of_week field is consumed
The `preserve_day_of_week` field on `GlobalConfig` (already defined, default `"monday"`) SHALL be passed to `TimeBasedRetention.evaluate()` and used to determine weekly bucket boundaries.

#### Scenario: preserve_day_of_week controls weekly grouping
- **WHEN** `preserve_day_of_week = "sunday"` in the config and `weekly = 2`
- **THEN** retention preserves at most 2 weekly snapshots with Sunday as the week boundary

### Requirement: GlobalConfig preserve_day_of_week validation
`ConfigFacade` SHALL validate that `preserve_day_of_week` is one of: monday, tuesday, wednesday, thursday, friday, saturday, sunday (case-insensitive). Invalid values SHALL raise `ConfigError`.

#### Scenario: Valid day of week
- **WHEN** the config has `preserve_day_of_week = "friday"`
- **THEN** ConfigFacade accepts it and stores "friday" in GlobalConfig

#### Scenario: Invalid day of week
- **WHEN** the config has `preserve_day_of_week = "funday"`
- **THEN** ConfigFacade raises ConfigError with a message indicating the valid values
