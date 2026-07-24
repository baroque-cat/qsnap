## Requirements

### Requirement: IConfigFacade ABC
The system SHALL provide an `IConfigFacade` ABC with methods `get_global() → GlobalConfig`, `get_vms() → list[VMConfig]`, and `get_vm(name: str) → VMConfig`.

#### Scenario: ConfigFacade implements IConfigFacade
- **WHEN** ConfigFacade is instantiated
- **THEN** `isinstance(facade, IConfigFacade)` is True

### Requirement: TOML file parsing
ConfigFacade SHALL parse a TOML configuration file and produce immutable GlobalConfig and VMConfig dataclasses. Global options defined at the top level apply as defaults to all VMs.

#### Scenario: Minimal valid config
- **WHEN** ConfigFacade parses a TOML file containing a single `[[vm]]` with required fields (`name`, `base_image`, `snapshot_dir`)
- **THEN** `get_vms()` returns a list with one VMConfig, and `get_global()` returns GlobalConfig with defaults

#### Scenario: Missing required VM field
- **WHEN** ConfigFacade parses a TOML file where `[[vm]]` lacks `name`
- **THEN** an error is raised indicating which field is missing

#### Scenario: Invalid TOML syntax
- **WHEN** ConfigFacade attempts to parse a file with malformed TOML
- **THEN** an error is raised with the parse error details

### Requirement: Option inheritance from global to per-VM to per-target
ConfigFacade SHALL resolve option inheritance: global-level options are defaults, VM-level options override globals, and target-level options override both.

#### Scenario: VM overrides global retention policy
- **WHEN** global config sets `snapshot_preserve = "24h 2d"` and a VM sets `snapshot_preserve = "48h 4d"`
- **THEN** that VM's VMConfig has `snapshot_preserve = "48h 4d"`

#### Scenario: Target inherits VM retention when not overridden
- **WHEN** VM has `target_preserve = "20d 10w"` and a target does not specify its own `target_preserve`
- **THEN** the target's TargetConfig has the VM-level retention policy

#### Scenario: Target overrides VM retention
- **WHEN** VM has `target_preserve = "20d 10w"` and a target specifies `target_preserve = "10d 5w"`
- **THEN** the target's TargetConfig has `target_preserve = "10d 5w"`

### Requirement: Multiple VMs from a single config
ConfigFacade SHALL support multiple `[[vm]]` sections in one TOML file, each producing a separate VMConfig.

#### Scenario: Config with two VMs
- **WHEN** a TOML file contains `[[vm]]` with `name="vm1"` and another `[[vm]]` with `name="vm2"`
- **THEN** `get_vms()` returns a list of two VMConfigs with names "vm1" and "vm2"

### Requirement: VM lookup by name
ConfigFacade SHALL provide `get_vm(name)` that returns the VMConfig for a specific VM, or raises an error if not found.

#### Scenario: Lookup existing VM
- **WHEN** `get_vm("vm1")` is called and VM "vm1" exists in config
- **THEN** the returned VMConfig has `name == "vm1"`

#### Scenario: Lookup non-existent VM
- **WHEN** `get_vm("nonexistent")` is called
- **THEN** a KeyError or ConfigError is raised

### Requirement: ConfigFacade parses new fault-tolerance fields
`ConfigFacade` SHALL parse `auto_cleanup`, `state_backup_count`, `chain_verify_before_commit`, `chain_verify_after_commit`, and `deep_check_schedule` from the global section. It SHALL parse `blockcommit_deep_verify` and `snapshot_deep_verify` from each `[[vm]]` section. It SHALL parse `backup_retry_max`, `backup_retry_base`, and `compress` from each `[[vm.target]]` section. All fields SHALL use their documented defaults when absent. `ConfigFacade` SHALL NOT parse `full_every` (removed). If `full_every` is present in TOML, a deprecation WARNING SHALL be logged. If `full_compress` is present and `compress` is not, `full_compress` SHALL be mapped to `compress` with a deprecation WARNING. If any of `incremental_mode`, `rate_limit`, or `copy_base` are present in TOML (target-level), a deprecation WARNING SHALL be logged naming the field — the fields are ignored.

#### Scenario: Global safety fields parsed
- **WHEN** config TOML contains `auto_cleanup = true`, `state_backup_count = 2`, `chain_verify_before_commit = true`
- **THEN** `GlobalConfig.auto_cleanup` is `True`, `state_backup_count` is `2`, `chain_verify_before_commit` is `True`

#### Scenario: Target compress parsed
- **WHEN** a `[[vm.target]]` section contains `compress = true`
- **THEN** that target's `TargetConfig.compress` is `True`

#### Scenario: full_every in config triggers deprecation warning
- **WHEN** a `[[vm.target]]` section contains `full_every = "7d"`
- **THEN** a WARNING is logged: "full_every is deprecated, FULLs are now bucket-driven"
- **AND** the value is ignored

#### Scenario: full_compress mapped to compress
- **WHEN** a `[[vm.target]]` section contains `full_compress = true` but no `compress`
- **THEN** `TargetConfig.compress` is `True`
- **AND** a WARNING is logged: "full_compress is deprecated, use compress instead"

#### Scenario: VM deep verify fields parsed
- **WHEN** a `[[vm]]` section contains `blockcommit_deep_verify = true`
- **THEN** that VM's `VMConfig.blockcommit_deep_verify` is `True`

#### Scenario: Target retry fields parsed
- **WHEN** a `[[vm.target]]` section contains `backup_retry_max = 5` and `backup_retry_base = "10s"`
- **THEN** that target's `TargetConfig.backup_retry_max` is `5` and `backup_retry_base` is `"10s"`

### Requirement: ConfigFacade updates example config
The shipped `qsnap.toml.example` SHALL document all fault-tolerance fields plus all existing-but-not-shown fields: `snapshot_preserve_min`, `target_preserve_min`, `compress`, `change_detection_mode`, `disks`, `deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age`. It SHALL NOT document `full_every`, `full_compress`, `rate_limit`, `incremental_mode`, or `copy_base` (removed). Removed fields (`incremental_mode`, `rate_limit`, `copy_base`) present in existing user TOMLs SHALL trigger a deprecation WARNING naming the field and be otherwise ignored.

#### Scenario: Example config is parseable with all fields documented
- **WHEN** `qsnap -c qsnap.toml.example list config` is executed
- **THEN** the config is parsed successfully and all documented fields are visible in the output

### Requirement: Config validation forbids preserve_min without buckets
`ConfigFacade` SHALL validate that if `target_preserve` is not `None` and not `"latest"` and all parsed bucket counts (hourly through yearly) are 0 AND no F-anchors are present, then `target_preserve_min` SHALL be `"all"`. If `target_preserve_min` is not `"all"` and all bucket counts are 0 and no F-anchors are present, `ConfigFacade` SHALL raise `ConfigError` with a message explaining that `preserve_min` without buckets requires a FULL anchor which cannot be created without at least one active bucket.

Additionally, `ConfigFacade` SHALL validate that any bucket with a non-zero count MAY have an `F` prefix in the parsed policy string. If an F-anchor is present on a bucket with `count = 0`, `ConfigFacade` SHALL raise `ConfigError` with message: "F-anchor on bucket '<bucket>' requires count > 0".

#### Scenario: preserve_min without buckets rejected
- **WHEN** a target has `target_preserve = "0h 0d 0w 0m 0y"` and `target_preserve_min = "48h"` and no F-anchors
- **THEN** `ConfigError` is raised with message: "preserve_min without active buckets is not allowed — at least one bucket must have count > 0"

#### Scenario: preserve_min=all without buckets allowed
- **WHEN** a target has `target_preserve = "0h 0d 0w 0m 0y"` and `target_preserve_min = "all"` and no F-anchors
- **THEN** no error is raised (chain grows indefinitely, nothing is deleted)

#### Scenario: preserve_min with buckets allowed
- **WHEN** a target has `target_preserve = "24h 7d"` and `target_preserve_min = "6h"`
- **THEN** no error is raised (buckets are active, FULLs will be created)

#### Scenario: F-anchor with count=0 rejected
- **WHEN** a target has `target_preserve = "0Fh 7d"` and ConfigFacade parses the policy
- **THEN** `ConfigError` is raised with message: "F-anchor on bucket 'h' requires count > 0"

#### Scenario: F-anchor with count=0 and preserve_min allowed
- **WHEN** a target has `target_preserve = "0Fh 7d"` and `target_preserve_min = "48h"`
- **THEN** `ConfigError` is raised with message: "F-anchor on bucket 'h' requires count > 0" (F-anchor validation runs before preserve_min check)

### Requirement: F-syntax parsing in _parse_preserve
`Core._parse_preserve(preserve_str, preserve_min_str=None)` SHALL parse the `F` prefix in bucket tokens. The regex SHALL be extended to `(\d+)(F?)([hdwmy])`. When `F` is present, the corresponding `anchor_*` field on the returned `RetentionPolicy` SHALL be set to `True`.

#### Scenario: F-syntax parsed correctly
- **WHEN** `_parse_preserve("24h 7Fd 4Fw")` is called
- **THEN** returns `RetentionPolicy(hourly=24, daily=7, weekly=4, anchor_daily=True, anchor_weekly=True)`

#### Scenario: No F-prefix — anchors remain False
- **WHEN** `_parse_preserve("24h 7d 4w")` is called
- **THEN** returns `RetentionPolicy(hourly=24, daily=7, weekly=4, anchor_hourly=False, anchor_daily=False, anchor_weekly=False)`

#### Scenario: F-prefix with invalid bucket character
- **WHEN** `_parse_preserve("7Fx")` is called
- **THEN** the token is ignored (does not match regex `[hdwmy]`)

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
- **THEN** a `ConfigError` is raised with message indicating valid values

#### Scenario: compression_type absent defaults to zstd
- **WHEN** the TOML config does NOT contain `compression_type` at global or target level
- **THEN** `GlobalConfig.compression_type == "zstd"` (default)
- **AND** `TargetConfig.compression_type == "zstd"` (inherited from global default)

### Requirement: Parse backup_stall_timeout from TOML

`ConfigFacade` SHALL parse the `backup_stall_timeout` field from the TOML config file at both the global and target levels. The value SHALL be a duration string (e.g., `"30m"`, `"1h"`, `"0s"`) and SHALL be validated via `parse_stall_timeout()`. If the value is not a valid duration string, a `ConfigError` SHALL be raised. If the field is absent from the TOML, the default `"30m"` SHALL be used. At the target level, if the field is absent, it SHALL inherit from the global level. A value of `"0s"` disables stall detection (falls back to fixed timeout behavior).

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

### Requirement: backup_create option resolution

`ConfigFacade` SHALL resolve the `backup_create` option via inheritance: global → VM → target. The VM level SHALL inherit from `GlobalConfig.backup_create` when not set in the VM's TOML section. The target level SHALL inherit from the VM-level resolved value when not set in the target's TOML section. `ConfigFacade` SHALL validate that `backup_create` is one of `{"always", "onchange"}`. Invalid values SHALL raise `ConfigError` with a message listing the valid values. The default value when not specified anywhere SHALL be `"always"`.

#### Scenario: Valid backup_create value
- **WHEN** the config has `backup_create = "onchange"` at the target level
- **THEN** `ConfigFacade` accepts it and stores `"onchange"` in `TargetConfig.backup_create`

#### Scenario: Invalid backup_create raises ConfigError
- **WHEN** the config has `backup_create = "on-change"` at any level
- **THEN** `ConfigFacade` raises `ConfigError` with a message listing valid values: `"always"`, `"onchange"`

#### Scenario: Default backup_create is always
- **WHEN** the config does not specify `backup_create` at any level
- **THEN** `TargetConfig.backup_create` defaults to `"always"`

#### Scenario: Global backup_create inherited by target
- **WHEN** global config sets `backup_create = "onchange"` and neither VM nor target specify it
- **THEN** `TargetConfig.backup_create` resolves to `"onchange"`

#### Scenario: VM-level backup_create overrides global
- **WHEN** global config sets `backup_create = "onchange"` and VM sets `backup_create = "always"`
- **AND** the target does not specify `backup_create`
- **THEN** `TargetConfig.backup_create` resolves to `"always"` (VM overrides global)

#### Scenario: Target-level backup_create overrides VM
- **WHEN** VM sets `backup_create = "onchange"` and target sets `backup_create = "always"`
- **THEN** `TargetConfig.backup_create` resolves to `"always"` (target overrides VM)
