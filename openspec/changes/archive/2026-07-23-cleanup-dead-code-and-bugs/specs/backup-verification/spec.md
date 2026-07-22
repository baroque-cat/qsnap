## MODIFIED Requirements

### Requirement: TargetConfig verify field

`TargetConfig.verify` controls post-transfer verification. Valid values: `"off"` (no verification), `"metadata"` (structural checks: format, virtual-size, corrupt-bit, backing-filename, dirty-size barrier), `"check"` (metadata + `qemu-img check` structural verification: errors, leaks, corruptions), `"compare"` (metadata + check + `qemu-img compare` chain-traversing content comparison). The `"hash"` and `"full"` values are deprecated — both ran `qemu-img compare`; they are now unified to `"compare"`. Existing configs with `"hash"` or `"full"` SHALL log a deprecation WARNING and be treated as `"compare"`. Default is `"metadata"`.

#### Scenario: Default verification is metadata

- **WHEN** `verify` is not set in the TOML
- **THEN** `TargetConfig.verify` defaults to `"metadata"`

#### Scenario: Explicit compare verification

- **WHEN** `verify = "compare"` is set in the TOML
- **THEN** `TargetConfig.verify` is `"compare"`

#### Scenario: Explicit check verification

- **WHEN** `verify = "check"` is set in the TOML
- **THEN** `TargetConfig.verify` is `"check"`
- **AND** `verify_bitmap_incremental()` runs `qemu-img check` in addition to metadata checks

#### Scenario: Deprecated hash treated as compare

- **WHEN** `verify = "hash"` is set in the TOML
- **THEN** a WARNING is logged naming the deprecated value
- **AND** `TargetConfig.verify` is treated as `"compare"`

#### Scenario: Deprecated full treated as compare

- **WHEN** `verify = "full"` is set in the TOML
- **THEN** a WARNING is logged naming the deprecated value
- **AND** `TargetConfig.verify` is treated as `"compare"`

#### Scenario: Invalid verify value raises ConfigError

- **WHEN** `verify = "invalid"` is set in the TOML
- **THEN** `ConfigError` is raised
