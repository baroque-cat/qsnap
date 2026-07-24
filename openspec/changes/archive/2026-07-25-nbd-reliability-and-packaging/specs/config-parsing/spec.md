## ADDED Requirements

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
