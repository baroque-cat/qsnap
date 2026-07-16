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
`ConfigFacade` SHALL parse `auto_cleanup`, `state_backup_count`, `chain_verify_before_commit`, `chain_verify_after_commit`, and `deep_check_schedule` from the global section. It SHALL parse `blockcommit_deep_verify` and `snapshot_deep_verify` from each `[[vm]]` section. It SHALL parse `backup_retry_max`, `backup_retry_base`, `compress`, and `copy_base` from each `[[vm.target]]` section. All fields SHALL use their documented defaults when absent. `ConfigFacade` SHALL NOT parse `full_every` (removed). If `full_every` is present in TOML, a deprecation WARNING SHALL be logged. If `full_compress` is present and `compress` is not, `full_compress` SHALL be mapped to `compress` with a deprecation WARNING.

#### Scenario: Global safety fields parsed
- **WHEN** config TOML contains `auto_cleanup = true`, `state_backup_count = 2`, `chain_verify_before_commit = true`
- **THEN** `GlobalConfig.auto_cleanup` is `True`, `state_backup_count` is `2`, `chain_verify_before_commit` is `True`

#### Scenario: Target compress and copy_base parsed
- **WHEN** a `[[vm.target]]` section contains `compress = true` and `copy_base = false`
- **THEN** that target's `TargetConfig.compress` is `True` and `copy_base` is `False`

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
The shipped `qsnap.toml.example` SHALL document all fault-tolerance fields plus all existing-but-not-shown fields: `snapshot_preserve_min`, `target_preserve_min`, `rate_limit`, `compress`, `copy_base`, `incremental_mode`, `change_detection_mode`, `disks`, `deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age`. It SHALL NOT document `full_every` or `full_compress` (removed).

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
