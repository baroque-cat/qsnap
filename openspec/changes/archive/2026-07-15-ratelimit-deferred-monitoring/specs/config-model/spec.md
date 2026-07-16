## ADDED Requirements

### Requirement: GlobalConfig rate_limit field

`GlobalConfig` SHALL include an optional `rate_limit` field of type `str` with default `"no"`. See `specs/rate-limit/spec.md` for full semantics.

### Requirement: GlobalConfig deferred threshold fields

`GlobalConfig` SHALL include optional deferred threshold fields: `deferred_warn_count` (default `"5"`), `deferred_crit_count` (default `"10"`), `deferred_warn_age` (default `"7d"`), `deferred_crit_age` (default `"14d"`). All SHALL be of type `str`. See `specs/deferred-monitoring/spec.md` for full semantics.

### Requirement: TargetConfig rate_limit field

`TargetConfig` SHALL include an optional `rate_limit` field of type `str` with default `"no"`, inherited from `GlobalConfig.rate_limit` when unset.

## MODIFIED Requirements

### Requirement: GlobalConfig dataclass

The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options, including timestamp format, preserve day of week, state directory, lockfile path, snapshot/target preserve policies, rate limit, and deferred monitoring thresholds.

#### Scenario: GlobalConfig is immutable

- **WHEN** a GlobalConfig instance is created with `timestamp_format="long"` and `preserve_day_of_week="monday"`
- **THEN** attempting to mutate any field raises FrozenInstanceError

#### Scenario: GlobalConfig default values

- **WHEN** a GlobalConfig is created with only required fields
- **THEN** optional fields have documented defaults (`state_dir="/var/lib/qsnap/state"`, `lockfile=None`, `rate_limit="no"`, `deferred_warn_count="5"`, `deferred_crit_count="10"`, `deferred_warn_age="7d"`, `deferred_crit_age="14d"`)

### Requirement: TargetConfig dataclass

The system SHALL provide an immutable `TargetConfig` dataclass representing a backup target: its path, whether incremental backup is enabled, its retention policy, and its rate limit setting.

#### Scenario: TargetConfig with incremental enabled

- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and `incremental=True`
- **THEN** both fields are accessible, `rate_limit` defaults to `"no"`, and the instance is frozen
