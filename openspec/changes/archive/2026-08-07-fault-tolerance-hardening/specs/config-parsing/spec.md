# Config Parsing — Delta

## ADDED Requirements

### Requirement: ConfigFacade parses and validates free-space gate fields
`ConfigFacade` SHALL parse `free_space_check`, `free_space_reserve`, and
`free_space_factor` from the global section (and honor the standard global→VM
inheritance for them). Validation SHALL enforce: `free_space_check` is one of
`"strict"`, `"warn"`, `"off"` — any other value SHALL raise `ConfigError` naming the
valid values; `free_space_reserve` is a non-negative integer — negative values SHALL
raise `ConfigError`; `free_space_factor` is a number `>= 1.0` — smaller values SHALL
raise `ConfigError`. Absent fields SHALL use the documented defaults
(`"strict"`, `0`, `1.0`).

#### Scenario: Valid free-space fields parsed
- **WHEN** the global section contains `free_space_check = "warn"`,
  `free_space_reserve = 1073741824`, `free_space_factor = 1.1`
- **THEN** `GlobalConfig.free_space_check` is `"warn"`, `free_space_reserve` is
  `1073741824`, `free_space_factor` is `1.1`

#### Scenario: Invalid free_space_check raises ConfigError
- **WHEN** the config has `free_space_check = "hard"`
- **THEN** `ConfigFacade` raises `ConfigError` naming `strict`, `warn`, `off` as valid
  values

#### Scenario: Negative free_space_reserve raises ConfigError
- **WHEN** the config has `free_space_reserve = -1`
- **THEN** `ConfigFacade` raises `ConfigError` indicating the value must be >= 0

#### Scenario: free_space_factor below 1.0 raises ConfigError
- **WHEN** the config has `free_space_factor = 0.5`
- **THEN** `ConfigFacade` raises `ConfigError` indicating the value must be >= 1.0

#### Scenario: Absent fields use defaults
- **WHEN** the config omits all three free-space fields
- **THEN** `free_space_check == "strict"`, `free_space_reserve == 0`,
  `free_space_factor == 1.0`

#### Scenario: snapshot_preserve_min default resolves to 48
- **WHEN** the config omits `snapshot_preserve_min` at all levels
- **THEN** `VMConfig.snapshot_preserve_min` resolves to `48` via inheritance from the
  global default

#### Scenario: Explicit zero preserve_min still honored
- **WHEN** the global section sets `snapshot_preserve_min = 0` and the VM omits it
- **THEN** `VMConfig.snapshot_preserve_min` resolves to `0` (floor inactive)
