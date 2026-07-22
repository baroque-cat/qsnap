## MODIFIED Requirements

### Requirement: GlobalConfig full_verify_before_rebase field

The `full_verify_before_rebase` field is REMOVED from `GlobalConfig`. It was parsed, validated, and stored, but never consumed by any code path. The rebase step it was intended to protect died with `FileCopyBackupProvider` (removed in `2026-07-22-remove-rsync-filecopy`). `ConfigFacade` SHALL NOT parse or validate this field. If the field appears in a TOML config, it SHALL be silently ignored as an unknown key.

#### Scenario: full_verify_before_rebase not in GlobalConfig

- **WHEN** `GlobalConfig` is constructed
- **THEN** the dataclass does not have a `full_verify_before_rebase` field
- **AND** attempting to access `config.full_verify_before_rebase` raises `AttributeError`

#### Scenario: TOML with full_verify_before_rebase is silently ignored

- **WHEN** a TOML config contains `full_verify_before_rebase = "metadata"`
- **THEN** `ConfigFacade` does not raise an error
- **AND** the value is not stored in any config dataclass
- **AND** an INFO log may note the unknown key

### Requirement: VMConfig snapshot_create validation

`ConfigFacade._build_vm()` SHALL validate that `snapshot_create` is one of `{"always", "onchange", "ondemand"}`. Invalid values SHALL raise `ConfigError` with a message listing the valid values. The default value when not specified in TOML SHALL be `"always"`.

#### Scenario: Valid snapshot_create value

- **WHEN** the config has `snapshot_create = "onchange"`
- **THEN** `ConfigFacade` accepts it and stores `"onchange"` in `VMConfig.snapshot_create`

#### Scenario: Invalid snapshot_create value raises ConfigError

- **WHEN** the config has `snapshot_create = "on-changed"`
- **THEN** `ConfigFacade` raises `ConfigError` with a message listing valid values: `"always"`, `"onchange"`, `"ondemand"`

#### Scenario: Default snapshot_create is always

- **WHEN** the config does not specify `snapshot_create`
- **THEN** `VMConfig.snapshot_create` defaults to `"always"`

### Requirement: VMConfig blockcommit_deep_verify and snapshot_deep_verify fields

`VMConfig` SHALL include `blockcommit_deep_verify: bool` (default `False`). The `snapshot_deep_verify` field is REMOVED from `VMConfig` — it was parsed and stored but never consumed by any code path. `ConfigFacade` SHALL NOT parse or validate `snapshot_deep_verify`. If the field appears in a TOML config, it SHALL be silently ignored as an unknown key.

#### Scenario: VMConfig has blockcommit_deep_verify only

- **WHEN** `VMConfig` is constructed
- **THEN** `blockcommit_deep_verify` exists with default `False`
- **AND** `snapshot_deep_verify` does not exist on the dataclass

#### Scenario: TOML with snapshot_deep_verify is silently ignored

- **WHEN** a TOML config contains `snapshot_deep_verify = true`
- **THEN** `ConfigFacade` does not raise an error
- **AND** the value is not stored in `VMConfig`
