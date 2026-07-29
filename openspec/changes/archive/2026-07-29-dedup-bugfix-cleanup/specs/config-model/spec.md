# config-model — Delta Spec

## REMOVED Requirements

### Requirement: GlobalConfig rate_limit field

**Reason**: The `rate_limit` field on `GlobalConfig` was removed when `FileCopyBackupProvider` was deleted. NBD bitmap-based backups do not support rate limiting.
**Migration**: `ConfigFacade` already logs a deprecation WARNING if `rate_limit` appears in TOML. Users should remove the key from their configuration.

### Requirement: TargetConfig rate_limit field

**Reason**: Same as GlobalConfig — removed with `FileCopyBackupProvider`.
**Migration**: `ConfigFacade` already logs a deprecation WARNING. Users should remove the key from their configuration.

### Requirement: TargetConfig incremental_mode field

**Reason**: `incremental_mode` was removed from `TargetConfig` when `FileCopyBackupProvider` was deleted. Only `BitmapBackupProvider` remains, making the field meaningless — all backups are now bitmap-based.
**Migration**: `ConfigFacade` already logs a deprecation WARNING. Users should remove the key from their configuration.

### Requirement: GlobalConfig deep_check_targets field

**Reason**: `deep_check_targets` was parsed and stored but never consumed by any code path. The `qsnap check --deep` command always checks both snapshots AND backups regardless of this flag. The field provides no functionality and creates false expectations.
**Migration**: The field is silently removed from `GlobalConfig`. Users with `deep_check_targets = true` in config will not see an error — the field is simply ignored. No behavior changes because the field was never consumed.

## MODIFIED Requirements

### Requirement: TargetConfig dataclass

The system SHALL provide an immutable `TargetConfig` dataclass representing a backup target: its path, count-based retention overrides (`target_chain_length`, `target_keep_generations`), verification mode, retry controls, compression setting, compression type, backup stall timeout, and backup_create mode. The fields `target_preserve`, `target_preserve_min`, `incremental`, and `incremental_mode` SHALL NOT exist on `TargetConfig`. The `verify` field SHALL default to `"metadata"` at the dataclass level. When the user explicitly sets `verify` in the TOML config, the explicit value takes precedence. The `compression_type` field SHALL default to `"zstd"` and inherit from `GlobalConfig.compression_type` when not explicitly set. The `backup_stall_timeout` field SHALL default to `"30m"` and inherit from `GlobalConfig.backup_stall_timeout` when not explicitly set. If the deprecated `incremental` key appears in TOML, `ConfigFacade` SHALL log a WARNING: `"incremental is deprecated and ignored — all backups are now bitmap-based"` and silently ignore the value.

#### Scenario: TargetConfig with path only — all defaults

- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")`
- **THEN** `backup_retry_max` defaults to `3`, `backup_retry_base` defaults to `"2s"`, `compress` defaults to `True`, `compression_type` defaults to `"zstd"`, `backup_stall_timeout` defaults to `"30m"`, `target_chain_length` defaults to `None`, `target_keep_generations` defaults to `None`, and the instance is frozen
- **AND** there is no `incremental` field on the dataclass

#### Scenario: TOML with incremental key logs deprecation WARNING

- **WHEN** a TOML config contains `incremental = false` under `[[vm.target]]`
- **THEN** `ConfigFacade` logs a WARNING: "incremental is deprecated and ignored — all backups are now bitmap-based"
- **AND** `TargetConfig` is created without the `incremental` field
- **AND** no `ConfigError` is raised

## ADDED Requirements

### Requirement: parse_duration and parse_stall_timeout in utils

The functions `parse_duration()` and `parse_stall_timeout()` SHALL be moved from `qsnap/retention/time_based.py` to `qsnap/utils/time.py`. Both `qsnap/core/__init__.py` and `qsnap/retention/time_based.py` SHALL import them from their new location. The functions' behavior SHALL remain identical — this is a pure relocation to reflect that these are general-purpose time-parsing utilities, not retention-specific logic.
