# Config Model (DELTA)

## ADDED Requirements

### Requirement: snapshot_retention_mode option

The global config section SHALL support `snapshot_retention_mode` (string, values `"steady"`
or `"hysteresis"`, default `"hysteresis"`), inheritable by VMs via the standard inheritance
chain (explicit VM value overrides the global value). `VMConfig` SHALL expose the resolved
mode as a frozen field. Any other value SHALL raise `ConfigError`. When hysteresis mode is
active for a VM, ConfigFacade SHALL validate the resolved values satisfy
`snapshot_chain_length > snapshot_preserve_min ≥ 1` and raise `ConfigError` naming both
values otherwise.

#### Scenario: Default mode is hysteresis

- **WHEN** the option is absent everywhere
- **THEN** every VM resolves `snapshot_retention_mode = "hysteresis"`

#### Scenario: VM override wins

- **WHEN** global sets `"hysteresis"` and one VM sets `"steady"`
- **THEN** that VM resolves `"steady"` and all other VMs resolve `"hysteresis"`

#### Scenario: Invalid mode value rejected

- **WHEN** the option is set to `"weekly"`
- **THEN** config loading fails with `ConfigError`

#### Scenario: Invalid hysteresis bounds rejected

- **WHEN** a VM resolves hysteresis mode with `snapshot_chain_length = 24` and `snapshot_preserve_min = 48`
- **THEN** config loading fails with `ConfigError` mentioning both resolved values

### Requirement: max_commits_per_run option

The global config section SHALL support `max_commits_per_run` (integer ≥ 0, default 12,
0 = unlimited). It caps per-disk per-run snapshot commits in both retention modes.
Non-integer or negative values SHALL raise `ConfigError`. The option SHALL NOT affect
target/backup retention.

#### Scenario: Default cap

- **WHEN** the option is absent
- **THEN** `max_commits_per_run` resolves to 12

#### Scenario: Negative value rejected

- **WHEN** the option is set to -1
- **THEN** config loading fails with `ConfigError`
