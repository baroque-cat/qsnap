## MODIFIED Requirements

### Requirement: Option inheritance from global to per-VM to per-target
`ConfigFacade` SHALL resolve option inheritance: global-level options are defaults, VM-level options override globals, and target-level options override both. The count-based retention fields (`snapshot_chain_length`, `target_chain_length`, `target_keep_generations`) and the backup engine options (`compress`, `compression_type`, `convert_parallel`, `convert_out_of_order`, `backup_stall_timeout`) SHALL follow the same global → VM → target inheritance chain. The `verify` option SHALL follow VM → target inheritance (it has no global-level key; the default is `"metadata"`).

#### Scenario: VM overrides global chain length
- **WHEN** global config sets `snapshot_chain_length = 168` and a VM sets `snapshot_chain_length = 24`
- **THEN** that VM's `VMConfig` has `snapshot_chain_length = 24`

#### Scenario: Target inherits VM chain length when not overridden
- **WHEN** VM has `target_chain_length = 168` and a target does not specify its own `target_chain_length`
- **THEN** the target's `TargetConfig` has `target_chain_length = 168`

#### Scenario: Target overrides VM chain length
- **WHEN** VM has `target_chain_length = 168` and a target specifies `target_chain_length = 336`
- **THEN** the target's `TargetConfig` has `target_chain_length = 336`

#### Scenario: VM overrides global engine option
- **WHEN** global config sets `compression_type = "zstd"` and a VM sets `compression_type = "zlib"`
- **THEN** that VM's `VMConfig` has `compression_type = "zlib"`

#### Scenario: Target inherits VM engine option when not overridden
- **WHEN** a VM sets `convert_parallel = 8` and its target does not specify `convert_parallel`
- **THEN** the target's `TargetConfig` has `convert_parallel = 8`

#### Scenario: Target overrides VM engine option
- **WHEN** a VM sets `compression_type = "zlib"` and its target sets `compression_type = "zstd"`
- **THEN** the target's `TargetConfig` has `compression_type = "zstd"`

#### Scenario: Target inherits VM verify when not overridden
- **WHEN** a VM sets `verify = "compare"` and its target does not specify `verify`
- **THEN** the target's `TargetConfig` has `verify = "compare"`

### Requirement: Parse compression_type from TOML
`ConfigFacade` SHALL parse the `compression_type` field from the TOML config file at the global, VM, and target levels. The field SHALL be validated against the set `{"zstd", "zlib"}` at every level. If the value is not in this set, a `ConfigError` SHALL be raised naming the offending level (and VM, when applicable). If absent from TOML at all levels, the default `"zstd"` SHALL be used.

#### Scenario: Global compression_type parsed from TOML
- **WHEN** the TOML config contains `[global] compression_type = "zlib"`
- **THEN** `GlobalConfig.compression_type == "zlib"`

#### Scenario: VM-level compression_type parsed from TOML
- **WHEN** the TOML config contains `compression_type = "zlib"` inside a `[[vm]]` section
- **THEN** that VM's `VMConfig.compression_type == "zlib"`

#### Scenario: Target compression_type overrides VM
- **WHEN** the TOML config contains `[[vm]] compression_type = "zlib"` and `[[vm.target]] compression_type = "zstd"`
- **THEN** the target's `compression_type == "zstd"`

#### Scenario: Target compression_type overrides global
- **WHEN** the TOML config contains `[global] compression_type = "zstd"` and `[[vm.target]] compression_type = "zlib"`
- **THEN** the target's `compression_type == "zlib"`

#### Scenario: Invalid compression_type raises ConfigError
- **WHEN** the TOML config contains `compression_type = "lz4"`
- **THEN** a `ConfigError` is raised with message indicating valid values

### Requirement: Parse backup_stall_timeout from TOML
`ConfigFacade` SHALL parse the `backup_stall_timeout` field from the TOML config at the global, VM, and target levels. The value SHALL be a duration string validated via `parse_stall_timeout()` at every level. If invalid, a `ConfigError` SHALL be raised. If absent at all levels, the default `"30m"` SHALL be used.

#### Scenario: Global backup_stall_timeout parsed from TOML
- **WHEN** the TOML config contains `[global] backup_stall_timeout = "1h"`
- **THEN** `GlobalConfig.backup_stall_timeout == "1h"`

#### Scenario: VM-level backup_stall_timeout parsed from TOML
- **WHEN** the TOML config contains `backup_stall_timeout = "45m"` inside a `[[vm]]` section
- **THEN** that VM's `VMConfig.backup_stall_timeout == "45m"`

#### Scenario: Target backup_stall_timeout overrides global
- **WHEN** the TOML config contains `[global] backup_stall_timeout = "30m"` and `[[vm.target]] backup_stall_timeout = "15m"`
- **THEN** the target's `backup_stall_timeout == "15m"`

#### Scenario: Target backup_stall_timeout inherits VM value
- **WHEN** the TOML config contains `[[vm]] backup_stall_timeout = "2h"` and the target does not set `backup_stall_timeout`
- **THEN** the target's `backup_stall_timeout == "2h"`

#### Scenario: Invalid backup_stall_timeout raises ConfigError
- **WHEN** the TOML config contains `backup_stall_timeout = "abc"`
- **THEN** a `ConfigError` is raised with message indicating invalid duration format

### Requirement: convert_parallel validation
`ConfigFacade` SHALL validate that `convert_parallel` is an integer in the range 1-8 at every level where it is parsed (global, VM, and target). Values outside this range SHALL raise `ConfigError`.

#### Scenario: Valid convert_parallel value
- **WHEN** the config has `convert_parallel = 2`
- **THEN** `ConfigFacade` accepts it

#### Scenario: VM-level convert_parallel accepted
- **WHEN** a `[[vm]]` section sets `convert_parallel = 8`
- **THEN** `ConfigFacade` accepts it and `VMConfig.convert_parallel == 8`

#### Scenario: convert_parallel below range raises ConfigError
- **WHEN** the config has `convert_parallel = 0`
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating the valid range is 1-8

#### Scenario: VM-level convert_parallel above range raises ConfigError
- **WHEN** a `[[vm]]` section sets `convert_parallel = 9`
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating the valid range is 1-8 and naming the VM

### Requirement: TargetConfig verify parsing
`ConfigFacade` SHALL parse the `verify` field on each `[[vm.target]]`. Valid explicit values are `"off"`, `"metadata"`, `"check"`, `"compare"`. If absent on the target, the value SHALL be inherited from the VM-level resolved `verify` (which itself defaults to `"metadata"` when absent on the VM). The deprecated values `"hash"` and `"full"` SHALL be treated as `"compare"` with a WARNING at whichever level they appear.

#### Scenario: Explicit verify value
- **WHEN** config has `verify = "compare"`
- **THEN** `TargetConfig.verify` is `"compare"`

#### Scenario: Deprecated hash treated as compare
- **WHEN** config has `verify = "hash"`
- **THEN** a WARNING is logged and `TargetConfig.verify` resolves to `"compare"`

#### Scenario: Verify absent defaults to metadata
- **WHEN** neither the VM nor the target specifies `verify`
- **THEN** `TargetConfig.verify` is `"metadata"`

#### Scenario: Target verify inherits VM value
- **WHEN** a `[[vm]]` section sets `verify = "check"` and its target does not set `verify`
- **THEN** `TargetConfig.verify` is `"check"`

#### Scenario: Target verify overrides VM value
- **WHEN** a `[[vm]]` section sets `verify = "check"` and its target sets `verify = "off"`
- **THEN** `TargetConfig.verify` is `"off"`

## ADDED Requirements

### Requirement: VM-level backup engine option parsing
`ConfigFacade` SHALL parse the backup engine options `compress` (bool), `compression_type` (str), `convert_parallel` (int), `convert_out_of_order` (bool), `backup_stall_timeout` (duration string), and `verify` (str) from each `[[vm]]` section. Each parsed value SHALL be stored on the corresponding `VMConfig` field. When a key is absent from the `[[vm]]` section, the VM-level value SHALL be inherited from `GlobalConfig` (`verify` inherits the default `"metadata"`, having no global key). VM-level values SHALL undergo the same validation as the corresponding global/target fields (`compression_type` in `{"zstd", "zlib"}`, `convert_parallel` in 1-8, `verify` in `{"off", "metadata", "check", "compare"}` plus deprecated-value mapping, `backup_stall_timeout` via `parse_stall_timeout()`). Validation failures SHALL raise `ConfigError` with a message naming the VM.

#### Scenario: All six engine options parsed at VM level
- **WHEN** a `[[vm]]` section sets `compress = false`, `compression_type = "zlib"`, `convert_parallel = 8`, `convert_out_of_order = false`, `backup_stall_timeout = "1h"`, and `verify = "compare"`
- **THEN** the resulting `VMConfig` carries exactly those six values

#### Scenario: Absent VM-level options inherit global values
- **WHEN** the global section sets `compress = false`, `compression_type = "zlib"`, `convert_parallel = 2`, `convert_out_of_order = false`, `backup_stall_timeout = "1h"` and a `[[vm]]` section sets none of them
- **THEN** the resulting `VMConfig` carries `compress=False`, `compression_type="zlib"`, `convert_parallel=2`, `convert_out_of_order=False`, `backup_stall_timeout="1h"`, and `verify="metadata"`

#### Scenario: VM-level options feed target resolution
- **WHEN** a `[[vm]]` section sets `compression_type = "zlib"` and `convert_parallel = 8` and its `[[vm.target]]` sets neither
- **THEN** the resulting `TargetConfig` has `compression_type == "zlib"` and `convert_parallel == 8`

#### Scenario: Invalid VM-level compression_type raises ConfigError naming the VM
- **WHEN** a `[[vm]]` section with `name = "web01"` sets `compression_type = "lz4"`
- **THEN** `ConfigFacade` raises `ConfigError` with a message containing the VM name and the valid values

### Requirement: Unknown config key rejection
`ConfigFacade` SHALL reject any key that is not recognized at its table level. Recognition is defined by per-level whitelists covering: the top-level/`[global]` table, each `[[vm]]` table, each `[[vm.disk]]` table, and each `[[vm.target]]` table. When an unknown key is found, `ConfigFacade` SHALL raise `ConfigError` with a message naming the table (including the VM name and target path where applicable) and the offending key. When the unknown key is a recognized key at a different level, the message SHALL include a hint pointing at the correct level. Deprecated-but-tolerated keys (keys that today trigger a deprecation WARNING and are ignored or mapped) SHALL remain accepted and SHALL NOT raise. Structural keys (`vm` at top level; `disk` and `target` inside `[[vm]]`) SHALL be accepted. `[global]` section keys are unwrapped to the top level before validation.

#### Scenario: Unknown key at VM level raises ConfigError
- **WHEN** a `[[vm]]` section with `name = "web01"` contains `compresion_type = "zlib"` (typo)
- **THEN** `ConfigFacade` raises `ConfigError` with a message naming the `[[vm]]` table, the VM name, and the key `compresion_type`

#### Scenario: Unknown key at target level raises ConfigError
- **WHEN** a `[[vm.target]]` section contains `parallel = 8`
- **THEN** `ConfigFacade` raises `ConfigError` with a message naming the key `parallel`

#### Scenario: Unknown key at global level raises ConfigError
- **WHEN** the top-level config contains `compresss = true`
- **THEN** `ConfigFacade` raises `ConfigError` with a message naming the key `compresss`

#### Scenario: Unknown key at disk level raises ConfigError
- **WHEN** a `[[vm.disk]]` section contains `base = "/images/vm.qcow2"`
- **THEN** `ConfigFacade` raises `ConfigError` with a message naming the key `base`

#### Scenario: Hint when key belongs to another level
- **WHEN** a `[[vm]]` section contains a key that is recognized only at the target level (e.g. `backup_retry_max`)
- **THEN** the `ConfigError` message includes a hint that the key belongs in `[[vm.target]]`

#### Scenario: Deprecated keys remain tolerated
- **WHEN** the global section contains `rate_limit = "10M"` or any deprecated retention key (`snapshot_preserve`, `target_preserve`, `target_preserve_min`, `preserve_day_of_week`)
- **THEN** no `ConfigError` is raised and the existing deprecation WARNING behavior is preserved

#### Scenario: All fixture configs still parse
- **WHEN** `ConfigFacade` parses every TOML fixture under `tests/fixtures/configs/`
- **THEN** no `ConfigError` about unknown keys is raised
