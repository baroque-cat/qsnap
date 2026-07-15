## MODIFIED Requirements

### Requirement: GlobalConfig dataclass
The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options, including timestamp format, preserve day of week, state directory, lockfile path, snapshot/target preserve policies, rate limit, deferred monitoring thresholds, and fault-tolerance safety controls.

#### Scenario: GlobalConfig is immutable
- **WHEN** a GlobalConfig instance is created with `timestamp_format="long"` and `preserve_day_of_week="monday"`
- **THEN** attempting to mutate any field raises FrozenInstanceError

#### Scenario: GlobalConfig default values
- **WHEN** a GlobalConfig is created with only required fields
- **THEN** optional fields have documented defaults (`state_dir="/var/lib/qsnap/state"`, `lockfile=None`, `rate_limit="no"`, `deferred_warn_count="5"`, `deferred_crit_count="10"`, `deferred_warn_age="7d"`, `deferred_crit_age="14d"`, `auto_cleanup=true`, `state_backup_count=2`, `chain_verify_before_commit=true`, `chain_verify_after_commit=true`, `deep_check_schedule="off"`)

### Requirement: VMConfig dataclass
The system SHALL provide an immutable `VMConfig` dataclass representing a single VM's configuration, including its name, base image path, snapshot directory, snapshot creation mode, retention policy, optional targets, and fault-tolerance deep verification controls.

#### Scenario: VMConfig with required fields
- **WHEN** a VMConfig is created with `name="myvm"`, `base_image=Path(...)`, `snapshot_dir=Path(...)`
- **THEN** the instance has all required fields populated and `snapshot_create` defaults to `"always"`, `blockcommit_deep_verify` defaults to `False`, `snapshot_deep_verify` defaults to `False`

### Requirement: TargetConfig dataclass
The system SHALL provide an immutable `TargetConfig` dataclass representing a backup target: its path, whether incremental backup is enabled, its retention policy, its rate limit setting, verification mode, and retry controls.

#### Scenario: TargetConfig with incremental enabled
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and `incremental=True`
- **THEN** both fields are accessible, `rate_limit` defaults to `"no"`, `backup_retry_max` defaults to `3`, `backup_retry_base` defaults to `"2s"`, and the instance is frozen

## ADDED Requirements

### Requirement: GlobalConfig auto_cleanup field
`GlobalConfig` SHALL include an `auto_cleanup: bool` field with default `True`. See `specs/pre-flight-cleanup/spec.md` for full semantics.

#### Scenario: Default auto_cleanup is true
- **WHEN** `GlobalConfig` is constructed without `auto_cleanup`
- **THEN** `auto_cleanup` is `True`

### Requirement: GlobalConfig state_backup_count field
`GlobalConfig` SHALL include a `state_backup_count: int` field with default `2`. See `specs/state-recovery/spec.md` for full semantics.

#### Scenario: Default state_backup_count
- **WHEN** `GlobalConfig` is constructed without `state_backup_count`
- **THEN** `state_backup_count` is `2`

### Requirement: GlobalConfig chain verification fields
`GlobalConfig` SHALL include `chain_verify_before_commit: bool` (default `True`) and `chain_verify_after_commit: bool` (default `True`). See `specs/chain-integrity-verification/spec.md` for full semantics.

#### Scenario: Chain verification enabled by default
- **WHEN** `GlobalConfig` is constructed without chain verify fields
- **THEN** both are `True`

### Requirement: GlobalConfig deep_check_schedule field
`GlobalConfig` SHALL include a `deep_check_schedule: str` field with default `"off"`. See `specs/deep-verification-circuit/spec.md` for full semantics.

#### Scenario: deep_check_schedule defaults to off
- **WHEN** `GlobalConfig` is constructed without `deep_check_schedule`
- **THEN** `deep_check_schedule` is `"off"`

### Requirement: VMConfig blockcommit_deep_verify and snapshot_deep_verify fields
`VMConfig` SHALL include `blockcommit_deep_verify: bool` (default `False`) and `snapshot_deep_verify: bool` (default `False`). See `specs/deep-verification-circuit/spec.md` for full semantics.

#### Scenario: Deep verify defaults to off
- **WHEN** `VMConfig` is constructed without deep verify fields
- **THEN** both `blockcommit_deep_verify` and `snapshot_deep_verify` are `False`

### Requirement: TargetConfig backup_retry_max and backup_retry_base fields
`TargetConfig` SHALL include `backup_retry_max: int` (default `3`) and `backup_retry_base: str` (default `"2s"`). See `specs/backup-retry/spec.md` for full semantics.

#### Scenario: Default retry values
- **WHEN** `TargetConfig` is constructed without retry fields
- **THEN** `backup_retry_max` is `3`, `backup_retry_base` is `"2s"`
