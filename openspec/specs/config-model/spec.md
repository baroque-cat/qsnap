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
