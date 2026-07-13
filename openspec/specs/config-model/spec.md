## Requirements

### Requirement: GlobalConfig dataclass
The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options.

#### Scenario: GlobalConfig is immutable
- **WHEN** a GlobalConfig instance is created with `timestamp_format="long"` and `preserve_day_of_week="monday"`
- **THEN** attempting to mutate any field raises FrozenInstanceError

#### Scenario: GlobalConfig default values
- **WHEN** a GlobalConfig is created with only required fields
- **THEN** optional fields have documented defaults (`state_dir="/var/lib/qsnap/state"`, `lockfile=None`)

### Requirement: VMConfig dataclass
The system SHALL provide an immutable `VMConfig` dataclass representing a single VM's configuration, including its name, base image path, snapshot directory, snapshot creation mode, retention policy, and optional targets.

#### Scenario: VMConfig with required fields
- **WHEN** a VMConfig is created with `name="myvm"`, `base_image=Path(...)`, `snapshot_dir=Path(...)`
- **THEN** the instance has all required fields populated and `snapshot_create` defaults to `"always"`

#### Scenario: VMConfig with targets
- **WHEN** a VMConfig is created with a list of TargetConfig objects
- **THEN** `vm.targets` contains those targets in order

### Requirement: TargetConfig dataclass
The system SHALL provide an immutable `TargetConfig` dataclass representing a backup target: its path, whether incremental backup is enabled, and its retention policy.

#### Scenario: TargetConfig with incremental enabled
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and `incremental=True`
- **THEN** both fields are accessible and the instance is frozen

### Requirement: RetentionPolicy dataclass
The system SHALL provide an immutable `RetentionPolicy` dataclass with fields for hourly, daily, weekly, monthly, and yearly retention counts, plus a `preserve_min` duration string.

#### Scenario: RetentionPolicy with hourly and daily limits
- **WHEN** a RetentionPolicy is created with `hourly=24`, `daily=2`, `weekly=0`, `monthly=0`, `yearly=0`, `preserve_min="6h"`
- **THEN** all fields are accessible and match the provided values

#### Scenario: RetentionPolicy defaults
- **WHEN** a RetentionPolicy is created with no arguments
- **THEN** all retention counts default to 0 and `preserve_min` defaults to `"all"`

### Requirement: GlobalConfig lockfile field is consumed
The `lockfile` field on `GlobalConfig` (already defined, default `None`) SHALL be consumed by the locking mechanism. If `lockfile` is not `None`, the process SHALL acquire a lock on this path before pipeline execution.

#### Scenario: Lockfile from config is used
- **WHEN** the config has `lockfile = "/var/lock/qsnap.lock"` and no `--lockfile` CLI flag is passed
- **THEN** a lock is acquired on `/var/lock/qsnap.lock`

### Requirement: GlobalConfig timestamp_format field is consumed
The `timestamp_format` field on `GlobalConfig` (default `"long"`) SHALL be consumed by `Core._generate_snapshot_name()` to select the timestamp format string.

#### Scenario: timestamp_format controls snapshot naming
- **WHEN** `timestamp_format = "short"` in the config and a snapshot is created
- **THEN** the snapshot name uses `YYYYMMDD` format

### Requirement: GlobalConfig preserve_day_of_week field is consumed
The `preserve_day_of_week` field on `GlobalConfig` (default `"monday"`) SHALL be passed to `TimeBasedRetention.evaluate()` and used to determine weekly bucket boundaries.

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

### Requirement: TargetConfig incremental_mode field
`TargetConfig` SHALL gain an `incremental_mode: str` field with default value `"file-copy"`. Accepted values SHALL be `"file-copy"` (whole-file copy) and `"bitmap"` (dirty-block extraction via checkpoint). The field SHALL be immutable (`frozen=True`).

#### Scenario: Default incremental_mode is file-copy
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and no `incremental_mode`
- **THEN** `target.incremental_mode` is `"file-copy"`

#### Scenario: Explicit bitmap mode
- **WHEN** a TargetConfig is created with `incremental_mode="bitmap"`
- **THEN** `target.incremental_mode` is `"bitmap"`

### Requirement: VMConfig disks field
`VMConfig` SHALL gain an optional `disks: list[str] | None` field (default `None`). When `None`, `Core` SHALL auto-discover all disks via `virsh domblklist`. When a list is provided, only those disks are snapshotted.

#### Scenario: Disks list is None — auto-discovery
- **WHEN** a VMConfig is created without `disks`
- **THEN** `vm_config.disks` is `None`
- **THEN** Core discovers disks dynamically at runtime

#### Scenario: Explicit disk list
- **WHEN** a VMConfig is created with `disks=["vda", "vdb"]`
- **THEN** only `vda` and `vdb` are snapshotted
