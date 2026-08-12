# Config Model — delta

## ADDED Requirements

### Requirement: blockcommit_timeout field in GlobalConfig

`GlobalConfig` SHALL include a `blockcommit_timeout: int = 1800` field (seconds). It is the
wall-clock ceiling for a single commit command (`virsh blockcommit --wait` or `qemu-img
commit`) and for the reconciliation-free waiting around it. Valid values are positive
integers; config parsing SHALL reject zero, negative, and non-integer values with a clear
error. The field is immutable (frozen dataclass). Core SHALL pass it to lifecycle managers
via the `timeout` keyword argument of `blockcommit()`; no module SHALL read it from a stored
config reference.

#### Scenario: Default blockcommit timeout is 1800

- **WHEN** a `GlobalConfig` is created without `blockcommit_timeout`
- **THEN** `global_config.blockcommit_timeout == 1800`

#### Scenario: TOML override is parsed

- **WHEN** the config file sets `[global] blockcommit_timeout = 900`
- **THEN** `ConfigFacade` produces `GlobalConfig` with `blockcommit_timeout == 900`

#### Scenario: Invalid values rejected

- **WHEN** the config file sets `blockcommit_timeout = 0`, a negative number, or a non-integer
- **THEN** config parsing fails with a clear validation error naming the option

#### Scenario: Field is immutable

- **WHEN** code attempts to assign `global_config.blockcommit_timeout = 60`
- **THEN** a frozen-dataclass error is raised
