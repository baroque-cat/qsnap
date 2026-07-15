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
`ConfigFacade` SHALL parse `auto_cleanup`, `state_backup_count`, `chain_verify_before_commit`, `chain_verify_after_commit`, and `deep_check_schedule` from the global section. It SHALL parse `blockcommit_deep_verify` and `snapshot_deep_verify` from each `[[vm]]` section. It SHALL parse `backup_retry_max` and `backup_retry_base` from each `[[vm.target]]` section. All fields SHALL use their documented defaults when absent.

#### Scenario: Global safety fields parsed
- **WHEN** config TOML contains `auto_cleanup = true`, `state_backup_count = 2`, `chain_verify_before_commit = true`
- **THEN** `GlobalConfig.auto_cleanup` is `True`, `state_backup_count` is `2`, `chain_verify_before_commit` is `True`

#### Scenario: VM deep verify fields parsed
- **WHEN** a `[[vm]]` section contains `blockcommit_deep_verify = true`
- **THEN** that VM's `VMConfig.blockcommit_deep_verify` is `True`

#### Scenario: Target retry fields parsed
- **WHEN** a `[[vm.target]]` section contains `backup_retry_max = 5` and `backup_retry_base = "10s"`
- **THEN** that target's `TargetConfig.backup_retry_max` is `5` and `backup_retry_base` is `"10s"`

### Requirement: ConfigFacade updates example config
The shipped `qsnap.toml.example` SHALL document all fault-tolerance fields plus all existing-but-not-shown fields: `snapshot_preserve_min`, `target_preserve_min`, `rate_limit`, `full_every`, `full_compress`, `incremental_mode`, `change_detection_mode`, `disks`, `deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age`.

#### Scenario: Example config is parseable with all fields documented
- **WHEN** `qsnap -c qsnap.toml.example list config` is executed
- **THEN** the config is parsed successfully and all documented fields are visible in the output
