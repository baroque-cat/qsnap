## MODIFIED Requirements

### Requirement: Option inheritance from global to per-VM to per-target
ConfigFacade SHALL resolve option inheritance: global-level options are defaults, VM-level options override globals, and target-level options override both. The count-based retention fields (`snapshot_chain_length`, `target_chain_length`, `target_keep_generations`) SHALL follow the same inheritance chain.

#### Scenario: VM overrides global chain length
- **WHEN** global config sets `snapshot_chain_length = 168` and a VM sets `snapshot_chain_length = 24`
- **THEN** that VM's VMConfig has `snapshot_chain_length = 24`

#### Scenario: Target inherits VM chain length when not overridden
- **WHEN** VM has `target_chain_length = 168` and a target does not specify its own `target_chain_length`
- **THEN** the target's TargetConfig has `target_chain_length = 168`

#### Scenario: Target overrides VM chain length
- **WHEN** VM has `target_chain_length = 168` and a target specifies `target_chain_length = 336`
- **THEN** the target's TargetConfig has `target_chain_length = 336`

### Requirement: ConfigFacade parses new fault-tolerance fields
`ConfigFacade` SHALL parse `auto_cleanup`, `state_backup_count`, `chain_verify_before_commit`, `chain_verify_after_commit`, and `deep_check_schedule` from the global section. It SHALL parse `blockcommit_deep_verify` from each `[[vm]]` section. It SHALL parse `backup_retry_max`, `backup_retry_base`, and `compress` from each `[[vm.target]]` section. All fields SHALL use their documented defaults when absent. `ConfigFacade` SHALL NOT parse `full_every` (removed). If `full_every` is present in TOML, a deprecation WARNING SHALL be logged. If `full_compress` is present and `compress` is not, `full_compress` SHALL be mapped to `compress` with a deprecation WARNING. If any of `incremental_mode`, `rate_limit`, or `copy_base` are present in TOML (target-level), a deprecation WARNING SHALL be logged naming the field — the fields are ignored.

#### Scenario: Global safety fields parsed
- **WHEN** config TOML contains `auto_cleanup = true`, `state_backup_count = 2`, `chain_verify_before_commit = true`
- **THEN** `GlobalConfig.auto_cleanup` is `True`, `state_backup_count` is `2`, `chain_verify_before_commit` is `True`

#### Scenario: Target compress parsed
- **WHEN** a `[[vm.target]]` section contains `compress = true`
- **THEN** that target's `TargetConfig.compress` is `True`

#### Scenario: full_every in config triggers deprecation warning
- **WHEN** a `[[vm.target]]` section contains `full_every = "7d"`
- **THEN** a WARNING is logged: "full_every is deprecated, FULLs are now count-driven"
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
The shipped `qsnap.toml.example` SHALL document all fault-tolerance fields plus count-based retention fields: `snapshot_chain_length`, `target_chain_length`, `target_keep_generations`. It SHALL NOT document `snapshot_preserve`, `target_preserve`, `snapshot_preserve_min`, `target_preserve_min`, `preserve_day_of_week`, `full_every`, `full_compress`, `rate_limit`, `incremental_mode`, or `copy_base` (removed). Removed fields present in existing user TOMLs SHALL trigger a deprecation WARNING naming the field and be otherwise ignored.

#### Scenario: Example config is parseable with all fields documented
- **WHEN** `qsnap -c qsnap.toml.example list config` is executed
- **THEN** the config is parsed successfully and all documented fields are visible in the output

## REMOVED Requirements

### Requirement: Config validation forbids preserve_min without buckets
**Reason**: No bucket system. Replaced by `chain_length >= 1` validation.
**Migration**: Set `target_chain_length` to a positive integer.

### Requirement: F-syntax parsing in _parse_preserve
**Reason**: `_parse_preserve()` is deleted. No F-anchor syntax. Config values are plain integers.
**Migration**: Use `target_chain_length = 168` instead of `target_preserve = "24h 7Fd 4w"`.

## ADDED Requirements

### Requirement: ConfigFacade parses count-based retention fields

`ConfigFacade` SHALL parse `snapshot_chain_length` (int), `target_chain_length` (int), and `target_keep_generations` (int) from the global, VM, and target TOML sections. These fields SHALL use option inheritance (global → VM → target). When absent at all levels, they SHALL default to `None`.

#### Scenario: Global chain_length parsed
- **WHEN** config TOML contains `snapshot_chain_length = 168` at the global level
- **THEN** `GlobalConfig.snapshot_chain_length` is `168`

#### Scenario: VM-level chain_length overrides global
- **WHEN** global sets `snapshot_chain_length = 168` and a VM sets `snapshot_chain_length = 24`
- **THEN** `VMConfig.snapshot_chain_length` is `24`

#### Scenario: Target-level chain_length overrides VM
- **WHEN** VM sets `target_chain_length = 168` and a target sets `target_chain_length = 336`
- **THEN** `TargetConfig.target_chain_length` is `336`

### Requirement: Count-based retention validation

`ConfigFacade` SHALL validate that `snapshot_chain_length` (when set) is an integer >= 1. `ConfigFacade` SHALL validate that `target_chain_length` (when set) is an integer >= 1. `ConfigFacade` SHALL validate that `target_keep_generations` (when set) is an integer >= 1. Values of 0 or negative SHALL raise `ConfigError`.

#### Scenario: Valid chain_length
- **WHEN** config has `snapshot_chain_length = 168`
- **THEN** ConfigFacade accepts it

#### Scenario: Zero chain_length rejected
- **WHEN** config has `snapshot_chain_length = 0`
- **THEN** ConfigFacade raises `ConfigError` with message: "snapshot_chain_length must be >= 1"

#### Scenario: Negative keep_generations rejected
- **WHEN** config has `target_keep_generations = -1`
- **THEN** ConfigFacade raises `ConfigError` with message: "target_keep_generations must be >= 1"
