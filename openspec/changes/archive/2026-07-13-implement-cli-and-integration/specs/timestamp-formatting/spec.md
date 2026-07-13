## ADDED Requirements

### Requirement: Timestamp format from config
`Core._generate_snapshot_name()` SHALL use the `timestamp_format` from `GlobalConfig` to format snapshot names. Supported formats: `short` (`YYYYMMDD`), `long` (`YYYYMMDDThhmm`), `long-iso` (`YYYYMMDDThhmmss±hhmm`). Default SHALL be `"long"`.

#### Scenario: Short format
- **WHEN** `timestamp_format = "short"` and a snapshot is created at 2025-07-13 15:31
- **THEN** the snapshot name matches pattern `<vmname>.20250713.qcow2`

#### Scenario: Long format (default)
- **WHEN** `timestamp_format = "long"` (or not specified) and a snapshot is created at 2025-07-13 15:31
- **THEN** the snapshot name matches pattern `<vmname>.20250713T1531.qcow2`

#### Scenario: Long-iso format
- **WHEN** `timestamp_format = "long-iso"` and a snapshot is created
- **THEN** the snapshot name includes the UTC offset, matching pattern `<vmname>.20250713T153123+0200.qcow2`

### Requirement: Collision suffix for duplicate timestamps
If a snapshot or backup with the same timestamp already exists, the system SHALL append `_N` (starting at 1) to the timestamp portion of the name.

#### Scenario: Duplicate timestamp resolution
- **WHEN** a snapshot named `vm.20250713T1531.qcow2` already exists and another snapshot is created at the same minute
- **THEN** the new snapshot is named `vm.20250713T1531_1.qcow2`

### Requirement: preserve_day_of_week in retention
`TimeBasedRetention.evaluate()` SHALL accept an optional `preserve_day_of_week` parameter (default `"monday"`). The weekly bucket SHALL group snapshots based on the configured day, not ISO week boundaries.

#### Scenario: Weekly retention with Tuesday boundary
- **WHEN** `preserve_day_of_week = "tuesday"` and `weekly = 4`
- **THEN** at most 4 weekly snapshots are kept, with the "first of week" being the first snapshot on or after Tuesday

#### Scenario: Weekly retention with default Monday
- **WHEN** `preserve_day_of_week` is not specified and `weekly = 2`
- **THEN** at most 2 weekly snapshots are kept, with Monday as the week boundary
