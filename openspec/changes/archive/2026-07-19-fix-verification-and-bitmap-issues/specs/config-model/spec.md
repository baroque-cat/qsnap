## MODIFIED Requirements

### Requirement: TargetConfig dataclass

The system SHALL provide an immutable `TargetConfig` dataclass representing a backup target: its path, whether incremental backup is enabled, its retention policy, its rate limit setting, verification mode, retry controls, compression setting, and base-copy behavior. The `verify` field SHALL default to `"metadata"` at the dataclass level. `ConfigFacade._build_target()` SHALL resolve the effective default based on `incremental_mode`: `"hash"` when `incremental_mode == "file-copy"`, `"metadata"` when `incremental_mode == "bitmap"`. When the user explicitly sets `verify` in the TOML config, the explicit value takes precedence over the mode-dependent default.

#### Scenario: TargetConfig with incremental enabled

- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and `incremental=True`
- **THEN** both fields are accessible, `rate_limit` defaults to `"no"`, `backup_retry_max` defaults to `3`, `backup_retry_base` defaults to `"2s"`, `compress` defaults to `True`, `copy_base` defaults to `False`, `verify` defaults to `"metadata"` (dataclass-level), and the instance is frozen

#### Scenario: ConfigFacade resolves hash default for file-copy mode

- **WHEN** `ConfigFacade._build_target()` processes a target with `incremental_mode="file-copy"` and no explicit `verify` field
- **THEN** the resulting `TargetConfig.verify` SHALL be `"hash"`

#### Scenario: ConfigFacade resolves metadata default for bitmap mode

- **WHEN** `ConfigFacade._build_target()` processes a target with `incremental_mode="bitmap"` and no explicit `verify` field
- **THEN** the resulting `TargetConfig.verify` SHALL be `"metadata"`

#### Scenario: Explicit verify overrides mode-dependent default

- **WHEN** `ConfigFacade._build_target()` processes a target with `incremental_mode="file-copy"` and `verify="metadata"` explicitly set in TOML
- **THEN** the resulting `TargetConfig.verify` SHALL be `"metadata"` (explicit value takes precedence)

#### Scenario: Explicit verify="full" works for both modes

- **WHEN** `ConfigFacade._build_target()` processes a target with `verify="full"` explicitly set
- **THEN** the resulting `TargetConfig.verify` SHALL be `"full"` regardless of `incremental_mode`

#### Scenario: Bitmap mode with verify="hash" triggers warning and downgrade

- **WHEN** `ConfigFacade._build_target()` processes a target with `incremental_mode="bitmap"` and `verify="hash"` explicitly set
- **THEN** a WARNING SHALL be logged: "verify='hash' is not supported in bitmap mode — downgrading to verify='metadata'. Use verify='full' for content-level verification."
- **AND** the resulting `TargetConfig.verify` SHALL be `"metadata"` (auto-downgraded)

#### Scenario: Default incremental_mode is bitmap

- **WHEN** a TargetConfig is created without explicit `incremental_mode`
- **THEN** `incremental_mode` SHALL default to `"bitmap"` at the dataclass level

#### Scenario: Explicit incremental_mode="file-copy" overrides default

- **WHEN** `ConfigFacade._build_target()` processes a target with `incremental_mode="file-copy"` explicitly set
- **THEN** the resulting `TargetConfig.incremental_mode` SHALL be `"file-copy"`

#### Scenario: Factory falls back to file-copy when libvirt too old

- **WHEN** `incremental_mode="bitmap"` is configured (or default)
- **AND** `is_libvirt_new_enough(shell)` returns `False`
- **THEN** `DefaultFactory.create_backup_provider()` SHALL log a WARNING and return `FileCopyBackupProvider`
- **AND** the `TargetConfig.incremental_mode` remains `"bitmap"` (config is not mutated)
