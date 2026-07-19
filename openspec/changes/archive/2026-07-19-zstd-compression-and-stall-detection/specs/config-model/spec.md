## ADDED Requirements

### Requirement: compression_type field in GlobalConfig

`GlobalConfig` SHALL include a `compression_type: str = "zstd"` field. Valid values are `"zstd"` (default) and `"zlib"`. This field selects the compression algorithm used by `qemu-img convert -c` (via `-o compression_type=<type>`) and `rsync --compress` (via `--compress-choice=<type>`) when `compress=True`. The field is immutable (frozen dataclass).

#### Scenario: GlobalConfig default compression_type is zstd
- **WHEN** a GlobalConfig is created with only required fields
- **THEN** `compression_type` defaults to `"zstd"`

#### Scenario: GlobalConfig compression_type is immutable
- **WHEN** a GlobalConfig is created with `compression_type="zlib"`
- **THEN** attempting to mutate `compression_type` raises `FrozenInstanceError`

#### Scenario: GlobalConfig compression_type set to zlib
- **WHEN** a GlobalConfig is created with `compression_type="zlib"`
- **THEN** `config.compression_type == "zlib"`

### Requirement: backup_stall_timeout field in GlobalConfig

`GlobalConfig` SHALL include a `backup_stall_timeout: str = "30m"` field. The value is a duration string (e.g., `"30m"`, `"1h"`, `"0s"`) parsed to seconds via the existing `parse_duration()` utility. When set to `"0s"`, stall detection is disabled and the system falls back to fixed timeout behavior. This field is the global default for all VMs and targets, overridable per-target.

#### Scenario: GlobalConfig default stall timeout is 30m
- **WHEN** a GlobalConfig is created with only required fields
- **THEN** `backup_stall_timeout` defaults to `"30m"`

#### Scenario: GlobalConfig stall timeout is immutable
- **WHEN** a GlobalConfig is created with `backup_stall_timeout="1h"`
- **THEN** attempting to mutate `backup_stall_timeout` raises `FrozenInstanceError`

### Requirement: compression_type field in TargetConfig

`TargetConfig` SHALL include a `compression_type: str = "zstd"` field. This field is resolved via option inheritance: if not set in the target's TOML section, it inherits from `GlobalConfig.compression_type`. Valid values are `"zstd"` and `"zlib"`.

#### Scenario: TargetConfig compression_type inherits from global
- **WHEN** a TargetConfig is created without explicit `compression_type`
- **AND** the GlobalConfig has `compression_type="zstd"`
- **THEN** `target.compression_type == "zstd"`

#### Scenario: TargetConfig compression_type overrides global
- **WHEN** a TargetConfig is created with `compression_type="zlib"`
- **AND** the GlobalConfig has `compression_type="zstd"`
- **THEN** `target.compression_type == "zlib"` (target overrides global)

### Requirement: backup_stall_timeout field in TargetConfig

`TargetConfig` SHALL include a `backup_stall_timeout: str = "30m"` field. This field is resolved via option inheritance: if not set in the target's TOML section, it inherits from `GlobalConfig.backup_stall_timeout`. The value is a duration string parsed to seconds.

#### Scenario: TargetConfig stall timeout inherits from global
- **WHEN** a TargetConfig is created without explicit `backup_stall_timeout`
- **AND** the GlobalConfig has `backup_stall_timeout="1h"`
- **THEN** `target.backup_stall_timeout == "1h"`

#### Scenario: TargetConfig stall timeout overrides global
- **WHEN** a TargetConfig is created with `backup_stall_timeout="15m"`
- **AND** the GlobalConfig has `backup_stall_timeout="30m"`
- **THEN** `target.backup_stall_timeout == "15m"` (target overrides global)

## MODIFIED Requirements

### Requirement: GlobalConfig dataclass
The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options, including timestamp format, preserve day of week, state directory, lockfile path, snapshot/target preserve policies, rate limit, deferred monitoring thresholds, fault-tolerance safety controls, compression default, compression type, and backup stall timeout.

#### Scenario: GlobalConfig is immutable
- **WHEN** a GlobalConfig instance is created with `timestamp_format="long"` and `preserve_day_of_week="monday"`
- **THEN** attempting to mutate any field raises FrozenInstanceError

#### Scenario: GlobalConfig default values
- **WHEN** a GlobalConfig is created with only required fields
- **THEN** optional fields have documented defaults (`state_dir="/var/lib/qsnap/state"`, `lockfile=None`, `rate_limit="no"`, `compress=True`, `compression_type="zstd"`, `backup_stall_timeout="30m"`, `deferred_warn_count="5"`, `deferred_crit_count="10"`, `deferred_warn_age="7d"`, `deferred_crit_age="14d"`, `auto_cleanup=true`, `state_backup_count=2`, `chain_verify_before_commit=true`, `chain_verify_after_commit=true`, `deep_check_schedule="off"`)

### Requirement: TargetConfig dataclass
The system SHALL provide an immutable `TargetConfig` dataclass representing a backup target: its path, whether incremental backup is enabled, its retention policy, its rate limit setting, verification mode, retry controls, compression setting, compression type, backup stall timeout, and base-copy behavior. The `verify` field SHALL default to `"metadata"` at the dataclass level. `ConfigFacade._build_target()` SHALL resolve the effective default based on `incremental_mode`. The `incremental_mode` field SHALL default to `"bitmap"`. The `compression_type` field SHALL default to `"zstd"` and inherit from `GlobalConfig.compression_type` when not explicitly set. The `backup_stall_timeout` field SHALL default to `"30m"` and inherit from `GlobalConfig.backup_stall_timeout` when not explicitly set.

#### Scenario: TargetConfig with incremental enabled
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and `incremental=True`
- **THEN** both fields are accessible, `rate_limit` defaults to `"no"`, `backup_retry_max` defaults to `3`, `backup_retry_base` defaults to `"2s"`, `compress` defaults to `True`, `compression_type` defaults to `"zstd"`, `backup_stall_timeout` defaults to `"30m"`, `copy_base` defaults to `False`, and the instance is frozen
