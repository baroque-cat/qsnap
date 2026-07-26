## ADDED Requirements

### Requirement: full_transfer_engine field in GlobalConfig

`GlobalConfig` SHALL include a `full_transfer_engine: str = "qemu-img-convert"` field. Valid values are `"qemu-img-convert"` (default — C code, parallel coroutines, ~850 MB/s zstd) and `"libnbd"` (Python pread/pwrite loop via `INbdClient`, finer-grained control, slower). This field selects the FULL backup transfer engine. The field is immutable (frozen dataclass). It serves as the global default for `TargetConfig.full_transfer_engine` when the target does not explicitly set it.

#### Scenario: GlobalConfig default full_transfer_engine is qemu-img-convert

- **WHEN** a GlobalConfig is created with only required fields
- **THEN** `full_transfer_engine` defaults to `"qemu-img-convert"`

#### Scenario: GlobalConfig full_transfer_engine is immutable

- **WHEN** a GlobalConfig is created with `full_transfer_engine="libnbd"`
- **THEN** attempting to mutate `full_transfer_engine` raises `FrozenInstanceError`

#### Scenario: GlobalConfig full_transfer_engine set to libnbd

- **WHEN** a GlobalConfig is created with `full_transfer_engine="libnbd"`
- **THEN** `config.full_transfer_engine == "libnbd"`

### Requirement: full_transfer_engine field in TargetConfig

`TargetConfig` SHALL include a `full_transfer_engine: str = "qemu-img-convert"` field. This field is resolved via option inheritance: if not set in the target's TOML section, it inherits from `GlobalConfig.full_transfer_engine`. Valid values are `"qemu-img-convert"` and `"libnbd"`.

#### Scenario: TargetConfig full_transfer_engine inherits from global

- **WHEN** a TargetConfig is created without explicit `full_transfer_engine`
- **AND** the GlobalConfig has `full_transfer_engine="libnbd"`
- **THEN** `target.full_transfer_engine == "libnbd"`

#### Scenario: TargetConfig full_transfer_engine overrides global

- **WHEN** a TargetConfig is created with `full_transfer_engine="libnbd"`
- **AND** the GlobalConfig has `full_transfer_engine="qemu-img-convert"`
- **THEN** `target.full_transfer_engine == "libnbd"` (target overrides global)

#### Scenario: TargetConfig default full_transfer_engine is qemu-img-convert

- **WHEN** a TargetConfig is created without explicit `full_transfer_engine`
- **AND** the GlobalConfig has default `full_transfer_engine="qemu-img-convert"`
- **THEN** `target.full_transfer_engine == "qemu-img-convert"`

### Requirement: full_transfer_engine validation

`ConfigFacade` SHALL validate that `full_transfer_engine` is one of `"qemu-img-convert"` or `"libnbd"`. Invalid values SHALL raise `ConfigError` with a message listing the valid values.

#### Scenario: Valid full_transfer_engine value

- **WHEN** the config has `full_transfer_engine = "libnbd"`
- **THEN** ConfigFacade accepts it and stores `"libnbd"` in the config dataclass

#### Scenario: Invalid full_transfer_engine raises ConfigError

- **WHEN** the config has `full_transfer_engine = "rsync"`
- **THEN** ConfigFacade raises `ConfigError` with a message listing valid values: `"qemu-img-convert"`, `"libnbd"`

### Requirement: convert_parallel field in GlobalConfig

`GlobalConfig` SHALL include a `convert_parallel: int = 4` field. This field maps to the `qemu-img convert -m` flag (number of parallel coroutines). Valid range is 1-8. This field is only consumed when `full_transfer_engine == "qemu-img-convert"`. The field is immutable (frozen dataclass). It serves as the global default for `TargetConfig.convert_parallel`.

#### Scenario: GlobalConfig default convert_parallel is 4

- **WHEN** a GlobalConfig is created with only required fields
- **THEN** `convert_parallel` defaults to `4`

#### Scenario: GlobalConfig convert_parallel is immutable

- **WHEN** a GlobalConfig is created with `convert_parallel=2`
- **THEN** attempting to mutate `convert_parallel` raises `FrozenInstanceError`

### Requirement: convert_parallel field in TargetConfig

`TargetConfig` SHALL include a `convert_parallel: int = 4` field. This field is resolved via option inheritance: if not set in the target's TOML section, it inherits from `GlobalConfig.convert_parallel`. Valid range is 1-8.

#### Scenario: TargetConfig convert_parallel inherits from global

- **WHEN** a TargetConfig is created without explicit `convert_parallel`
- **AND** the GlobalConfig has `convert_parallel=2`
- **THEN** `target.convert_parallel == 2`

#### Scenario: TargetConfig convert_parallel overrides global

- **WHEN** a TargetConfig is created with `convert_parallel=8`
- **AND** the GlobalConfig has `convert_parallel=4`
- **THEN** `target.convert_parallel == 8` (target overrides global)

### Requirement: convert_parallel validation

`ConfigFacade` SHALL validate that `convert_parallel` is an integer in the range 1-8. Values outside this range SHALL raise `ConfigError` with a message indicating the valid range.

#### Scenario: Valid convert_parallel value

- **WHEN** the config has `convert_parallel = 2`
- **THEN** ConfigFacade accepts it and stores `2` in the config dataclass

#### Scenario: convert_parallel below range raises ConfigError

- **WHEN** the config has `convert_parallel = 0`
- **THEN** ConfigFacade raises `ConfigError` with a message indicating the valid range is 1-8

#### Scenario: convert_parallel above range raises ConfigError

- **WHEN** the config has `convert_parallel = 9`
- **THEN** ConfigFacade raises `ConfigError` with a message indicating the valid range is 1-8

### Requirement: convert_out_of_order field in GlobalConfig

`GlobalConfig` SHALL include a `convert_out_of_order: bool = True` field. This field maps to the `qemu-img convert -W` flag (out-of-order writes). When `True`, `qemu-img convert` writes data in out-of-order fashion for optimal throughput on HDDs. When `False`, writes are in-order (may be preferred on some SSDs). This field is only consumed when `full_transfer_engine == "qemu-img-convert"`. The field is immutable (frozen dataclass). It serves as the global default for `TargetConfig.convert_out_of_order`.

#### Scenario: GlobalConfig default convert_out_of_order is true

- **WHEN** a GlobalConfig is created with only required fields
- **THEN** `convert_out_of_order` defaults to `True`

#### Scenario: GlobalConfig convert_out_of_order is immutable

- **WHEN** a GlobalConfig is created with `convert_out_of_order=False`
- **THEN** attempting to mutate `convert_out_of_order` raises `FrozenInstanceError`

### Requirement: convert_out_of_order field in TargetConfig

`TargetConfig` SHALL include a `convert_out_of_order: bool = True` field. This field is resolved via option inheritance: if not set in the target's TOML section, it inherits from `GlobalConfig.convert_out_of_order`.

#### Scenario: TargetConfig convert_out_of_order inherits from global

- **WHEN** a TargetConfig is created without explicit `convert_out_of_order`
- **AND** the GlobalConfig has `convert_out_of_order=False`
- **THEN** `target.convert_out_of_order == False`

#### Scenario: TargetConfig convert_out_of_order overrides global

- **WHEN** a TargetConfig is created with `convert_out_of_order=False`
- **AND** the GlobalConfig has `convert_out_of_order=True`
- **THEN** `target.convert_out_of_order == False` (target overrides global)
