## ADDED Requirements

### Requirement: backup_create field in TargetConfig

`TargetConfig` SHALL include a `backup_create: str = "always"` field. Valid values are `"always"` (default — always transfer backups to this target) and `"onchange"` (skip backup transfer when the VM disk has not changed since the last backup to this target). The field SHALL be immutable (`frozen=True`). It SHALL inherit from `GlobalConfig.backup_create` when not explicitly set on the target.

#### Scenario: Default backup_create is always

- **WHEN** a TargetConfig is created without `backup_create`
- **THEN** `target.backup_create` is `"always"`

#### Scenario: Explicit onchange mode

- **WHEN** a TargetConfig is created with `backup_create="onchange"`
- **THEN** `target.backup_create` is `"onchange"`

#### Scenario: Target inherits backup_create from global

- **WHEN** global config sets `backup_create = "onchange"` and a target does not specify `backup_create`
- **THEN** `TargetConfig.backup_create` resolves to `"onchange"`

#### Scenario: Target overrides global backup_create

- **WHEN** global config sets `backup_create = "onchange"` and a target sets `backup_create = "always"`
- **THEN** `TargetConfig.backup_create` resolves to `"always"` (target overrides global)

### Requirement: backup_create field in GlobalConfig

`GlobalConfig` SHALL include a `backup_create: str = "always"` field. This serves as the global default for `TargetConfig.backup_create` when the target does not explicitly set it.

#### Scenario: Global backup_create default

- **WHEN** `GlobalConfig` is constructed without `backup_create`
- **THEN** `backup_create` is `"always"`

#### Scenario: Global backup_create set to onchange

- **WHEN** `GlobalConfig` is constructed with `backup_create="onchange"`
- **THEN** `backup_create` is `"onchange"`
