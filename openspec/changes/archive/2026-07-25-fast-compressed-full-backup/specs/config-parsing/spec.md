## ADDED Requirements

### Requirement: [global] TOML section support

`ConfigFacade._parse()` SHALL accept a `[global]` section in the TOML config file. When a `[global]` section is present, its keys SHALL be unwrapped to the top level before resolving global options. This means `compress = false` under `[global]` SHALL be treated identically to `compress = false` at the top level. The unwrapping SHALL happen after `tomllib.load()` and before any key lookups. If both a `[global]` section and top-level keys exist, the top-level keys SHALL take precedence (explicit top-level keys override `[global]` section keys).

#### Scenario: [global] section keys parsed correctly

- **WHEN** the TOML config contains `[global]` with `compress = false` and `lockfile = "/run/qsnap.lock"`
- **THEN** `GlobalConfig.compress` is `False`
- **AND** `GlobalConfig.lockfile` is `"/run/qsnap.lock"`

#### Scenario: [global] section with target-level inheritance

- **WHEN** the TOML config contains `[global] compress = false` and a `[[vm.target]]` without `compress`
- **THEN** the target's `TargetConfig.compress` is `False` (inherited from global)

#### Scenario: Top-level keys override [global] section

- **WHEN** the TOML config contains both `compress = true` at the top level and `[global] compress = false`
- **THEN** `GlobalConfig.compress` is `True` (top-level takes precedence)

#### Scenario: No [global] section — backward compatible

- **WHEN** the TOML config uses top-level keys only (no `[global]` section)
- **THEN** parsing works exactly as before (backward compatible)
