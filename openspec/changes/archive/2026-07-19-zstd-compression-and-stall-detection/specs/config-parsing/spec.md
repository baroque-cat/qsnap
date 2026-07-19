## ADDED Requirements

### Requirement: Parse compression_type from TOML

`ConfigFacade` SHALL parse the `compression_type` field from the TOML config file at both the global and target levels. The field SHALL be validated against the set `{"zstd", "zlib"}`. If the value is not in this set, a `ConfigError` SHALL be raised with a message indicating valid values. If the field is absent from the TOML, the default `"zstd"` SHALL be used. At the target level, if the field is absent, it SHALL inherit from the global level.

#### Scenario: Global compression_type parsed from TOML
- **WHEN** the TOML config contains `[global] compression_type = "zlib"`
- **THEN** `GlobalConfig.compression_type == "zlib"`

#### Scenario: Target compression_type overrides global
- **WHEN** the TOML config contains `[global] compression_type = "zstd"` and `[[targets]] compression_type = "zlib"`
- **THEN** the target's `compression_type == "zlib"` (target overrides global)

#### Scenario: Target compression_type inherits global default
- **WHEN** the TOML config contains `[global] compression_type = "zstd"` and the target section does NOT contain `compression_type`
- **THEN** the target's `compression_type == "zstd"` (inherited from global)

#### Scenario: Invalid compression_type raises ConfigError
- **WHEN** the TOML config contains `compression_type = "lz4"`
- **THEN** a `ConfigError` is raised with message: "Invalid compression_type='lz4'. Must be one of: zstd, zlib."

#### Scenario: compression_type absent defaults to zstd
- **WHEN** the TOML config does NOT contain `compression_type` at global or target level
- **THEN** `GlobalConfig.compression_type == "zstd"` (default)
- **AND** `TargetConfig.compression_type == "zstd"` (inherited from global default)

### Requirement: Parse backup_stall_timeout from TOML

`ConfigFacade` SHALL parse the `backup_stall_timeout` field from the TOML config file at both the global and target levels. The value SHALL be a duration string (e.g., `"30m"`, `"1h"`, `"0s"`) and SHALL be validated via the existing `parse_duration()` utility. If the value is not a valid duration string, a `ConfigError` SHALL be raised. If the field is absent from the TOML, the default `"30m"` SHALL be used. At the target level, if the field is absent, it SHALL inherit from the global level. A value of `"0s"` disables stall detection (falls back to fixed timeout behavior).

#### Scenario: Global backup_stall_timeout parsed from TOML
- **WHEN** the TOML config contains `[global] backup_stall_timeout = "1h"`
- **THEN** `GlobalConfig.backup_stall_timeout == "1h"`

#### Scenario: Target backup_stall_timeout overrides global
- **WHEN** the TOML config contains `[global] backup_stall_timeout = "30m"` and `[[targets]] backup_stall_timeout = "15m"`
- **THEN** the target's `backup_stall_timeout == "15m"` (target overrides global)

#### Scenario: Target backup_stall_timeout inherits global default
- **WHEN** the TOML config contains `[global] backup_stall_timeout = "1h"` and the target section does NOT contain `backup_stall_timeout`
- **THEN** the target's `backup_stall_timeout == "1h"` (inherited from global)

#### Scenario: Invalid backup_stall_timeout raises ConfigError
- **WHEN** the TOML config contains `backup_stall_timeout = "abc"`
- **THEN** a `ConfigError` is raised with message indicating invalid duration format

#### Scenario: backup_stall_timeout absent defaults to 30m
- **WHEN** the TOML config does NOT contain `backup_stall_timeout` at global or target level
- **THEN** `GlobalConfig.backup_stall_timeout == "30m"` (default)
- **AND** `TargetConfig.backup_stall_timeout == "30m"` (inherited from global default)

#### Scenario: backup_stall_timeout zero disables stall detection
- **WHEN** the TOML config contains `backup_stall_timeout = "0s"`
- **THEN** stall detection is disabled
- **AND** the system falls back to fixed timeout behavior (`IShell.run()` with timeout=3600)
