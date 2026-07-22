## MODIFIED Requirements

### Requirement: TargetConfig verify field

`TargetConfig.verify` SHALL accept `"off"`, `"metadata"`, `"compare"`. The `"hash"` and `"full"` values are deprecated and treated as `"compare"`. Default is `"metadata"`. Validation SHALL reject unknown values with `ConfigError`. Deprecated values SHALL log a WARNING naming the value.

#### Scenario: Default verification is metadata

- **WHEN** `verify` is not set in the TOML
- **THEN** `TargetConfig.verify` is `"metadata"`

#### Scenario: Explicit compare verification

- **WHEN** `verify = "compare"` is set
- **THEN** `TargetConfig.verify` is `"compare"`

#### Scenario: Deprecated hash treated as compare

- **WHEN** `verify = "hash"` is set
- **THEN** a WARNING is logged
- **AND** the effective value is `"compare"`

#### Scenario: Invalid verify value raises ConfigError

- **WHEN** `verify = "invalid"` is set
- **THEN** `ConfigError` is raised

### Requirement: GlobalConfig full_verify_after_create

`GlobalConfig.full_verify_after_create` SHALL accept `"off"`, `"metadata"`, `"check"`, `"compare"`. The `"hash"` value is deprecated and treated as `"compare"`. Default is `"check"`.

#### Scenario: Default is check

- **WHEN** `full_verify_after_create` is not set
- **THEN** the value is `"check"`

#### Scenario: Deprecated hash treated as compare

- **WHEN** `full_verify_after_create = "hash"` is set
- **THEN** a WARNING is logged
- **AND** the effective value is `"compare"`
