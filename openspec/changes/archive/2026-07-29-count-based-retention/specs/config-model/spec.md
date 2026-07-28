## MODIFIED Requirements

### Requirement: GlobalConfig dataclass
The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options, including timestamp format, state directory, lockfile path, count-based retention defaults (`snapshot_chain_length`, `target_chain_length`, `target_keep_generations`), deferred monitoring thresholds, fault-tolerance safety controls, compression default, compression type, and backup stall timeout. The fields `preserve_day_of_week`, `snapshot_preserve`, `target_preserve`, `snapshot_preserve_min`, and `target_preserve_min` SHALL NOT exist on `GlobalConfig`.

#### Scenario: GlobalConfig is immutable
- **WHEN** a GlobalConfig instance is created with `timestamp_format="long"` and `snapshot_chain_length=168`
- **THEN** attempting to mutate any field raises FrozenInstanceError

#### Scenario: GlobalConfig default values
- **WHEN** a GlobalConfig is created with only required fields
- **THEN** optional fields have documented defaults (`state_dir="/var/lib/qsnap/state"`, `lockfile=None`, `compress=True`, `compression_type="zstd"`, `backup_stall_timeout="30m"`, `snapshot_chain_length=None`, `target_chain_length=None`, `target_keep_generations=None`, `auto_cleanup=true`, `state_backup_count=2`, `chain_verify_before_commit=true`, `chain_verify_after_commit=true`, `deep_check_schedule="off"`)

### Requirement: VMConfig dataclass
The system SHALL provide an immutable `VMConfig` dataclass representing a single VM's configuration, including its name, base image path, snapshot directory, snapshot creation mode, count-based retention overrides (`snapshot_chain_length`, `target_chain_length`, `target_keep_generations`), optional targets, and fault-tolerance deep verification controls. The fields `snapshot_preserve`, `target_preserve`, `snapshot_preserve_min`, and `target_preserve_min` SHALL NOT exist on `VMConfig`.

#### Scenario: VMConfig with required fields
- **WHEN** a VMConfig is created with `name="myvm"`, `base_image=Path(...)`, `snapshot_dir=Path(...)`
- **THEN** the instance has all required fields populated and `snapshot_create` defaults to `"always"`, `blockcommit_deep_verify` defaults to `False`, `snapshot_chain_length` defaults to `None`

#### Scenario: VMConfig with targets
- **WHEN** a VMConfig is created with a list of TargetConfig objects
- **THEN** `vm.targets` contains those targets in order

### Requirement: TargetConfig dataclass
The system SHALL provide an immutable `TargetConfig` dataclass representing a backup target: its path, whether incremental backup is enabled, count-based retention overrides (`target_chain_length`, `target_keep_generations`), verification mode, retry controls, compression setting, compression type, and backup stall timeout. The fields `target_preserve` and `target_preserve_min` SHALL NOT exist on `TargetConfig`. The `verify` field SHALL default to `"metadata"` at the dataclass level. When the user explicitly sets `verify` in the TOML config, the explicit value takes precedence. The `compression_type` field SHALL default to `"zstd"` and inherit from `GlobalConfig.compression_type` when not explicitly set. The `backup_stall_timeout` field SHALL default to `"30m"` and inherit from `GlobalConfig.backup_stall_timeout` when not explicitly set.

#### Scenario: TargetConfig with incremental enabled
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and `incremental=True`
- **THEN** both fields are accessible, `backup_retry_max` defaults to `3`, `backup_retry_base` defaults to `"2s"`, `compress` defaults to `True`, `compression_type` defaults to `"zstd"`, `backup_stall_timeout` defaults to `"30m"`, `target_chain_length` defaults to `None`, `target_keep_generations` defaults to `None`, and the instance is frozen

## REMOVED Requirements

### Requirement: RetentionPolicy dataclass
**Reason**: Replaced by count-based `RetentionPolicy(chain_length: int, keep_generations: int)` defined in the `count-based-retention` capability spec.
**Migration**: Use `RetentionPolicy(chain_length=N, keep_generations=M)` instead of `RetentionPolicy(hourly=24, daily=7, ...)`.

### Requirement: GlobalConfig preserve_day_of_week field is consumed
**Reason**: No weekly bucket boundaries in count-based retention.
**Migration**: Remove `preserve_day_of_week` from config. Use `snapshot_chain_length` to control snapshot count.

### Requirement: GlobalConfig preserve_day_of_week validation
**Reason**: No weekly bucket boundaries in count-based retention.
**Migration**: Remove `preserve_day_of_week` from config.

### Requirement: GlobalConfig contains snapshot_preserve_min and target_preserve_min
**Reason**: No `preserve_min` concept in count-based retention. `chain_length` IS the minimum.
**Migration**: Use `snapshot_chain_length` and `target_chain_length` instead.

### Requirement: VMConfig contains snapshot_preserve_min and target_preserve_min
**Reason**: No `preserve_min` concept in count-based retention.
**Migration**: Use `snapshot_chain_length` and `target_chain_length` instead.

### Requirement: Active retention buckets required when targets configured
**Reason**: No bucket system. Validation replaced by `chain_length >= 1` check.
**Migration**: Set `target_chain_length` to a positive integer.

## ADDED Requirements

### Requirement: GlobalConfig count-based retention fields

`GlobalConfig` SHALL include `snapshot_chain_length: int | None = None`, `target_chain_length: int | None = None`, and `target_keep_generations: int | None = None`. These serve as global defaults for VM-level and target-level overrides.

#### Scenario: Defaults are None
- **WHEN** `GlobalConfig` is constructed without chain_length keys
- **THEN** `snapshot_chain_length`, `target_chain_length`, and `target_keep_generations` are all `None`

### Requirement: VMConfig count-based retention fields

`VMConfig` SHALL include `snapshot_chain_length: int | None = None`, `target_chain_length: int | None = None`, and `target_keep_generations: int | None = None`. These override global defaults when set.

#### Scenario: VM inherits from global
- **WHEN** global sets `snapshot_chain_length = 168` and VM omits it
- **THEN** `VMConfig.snapshot_chain_length` resolves to `168`

### Requirement: TargetConfig count-based retention fields

`TargetConfig` SHALL include `target_chain_length: int | None = None` and `target_keep_generations: int | None = None`. These override VM-level and global defaults when set.

#### Scenario: Target inherits from VM
- **WHEN** VM sets `target_chain_length = 168` and target omits it
- **THEN** `TargetConfig.target_chain_length` resolves to `168`

#### Scenario: Target overrides VM
- **WHEN** VM sets `target_chain_length = 168` and target sets `target_chain_length = 336`
- **THEN** `TargetConfig.target_chain_length` resolves to `336`
