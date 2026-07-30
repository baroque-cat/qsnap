## MODIFIED Requirements

### Requirement: GlobalConfig default values

The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options, including timestamp format, state directory, lockfile path, count-based retention defaults (`snapshot_chain_length=24`, `target_chain_length=168`, `target_keep_generations=2`), `snapshot_preserve_min=0` (snapshot preservation floor, 0 = inactive), deferred monitoring thresholds, fault-tolerance safety controls, compression default, compression type, and backup stall timeout. The fields `preserve_day_of_week`, `snapshot_preserve`, `target_preserve`, and `target_preserve_min` SHALL NOT exist on `GlobalConfig`.

#### Scenario: GlobalConfig default values

- **WHEN** a GlobalConfig is created with only required fields
- **THEN** optional fields have documented defaults (`state_dir="/var/lib/qsnap/state"`, `lockfile=None`, `compress=True`, `compression_type="zstd"`, `backup_stall_timeout="30m"`, `snapshot_chain_length=24`, `target_chain_length=168`, `target_keep_generations=2`, `snapshot_preserve_min=0`, `auto_cleanup=true`, `state_backup_count=2`, `chain_verify_before_commit=true`, `chain_verify_after_commit=true`, `deep_check_schedule="off"`)

### Requirement: VMConfig dataclass

The system SHALL provide an immutable `VMConfig` dataclass representing a single VM's configuration, including its name, base image path, snapshot directory, snapshot creation mode, count-based retention overrides (`snapshot_chain_length`, `target_chain_length`, `target_keep_generations`), `snapshot_preserve_min` (snapshot preservation floor, inherits from global, 0 = inactive), optional targets, and fault-tolerance deep verification controls. The fields `snapshot_preserve`, `target_preserve`, and `target_preserve_min` SHALL NOT exist on `VMConfig`.

#### Scenario: VMConfig with required fields

- **WHEN** a VMConfig is created with `name="myvm"`, `base_image=Path(...)`, `snapshot_dir=Path(...)`
- **THEN** the instance has all required fields populated and `snapshot_create` defaults to `"always"`, `blockcommit_deep_verify` defaults to `False`, `snapshot_chain_length` defaults to `None`, `snapshot_preserve_min` defaults to `None`

#### Scenario: VMConfig with targets

- **WHEN** a VMConfig is created with a list of TargetConfig objects
- **THEN** `vm.targets` contains those targets in order

## ADDED Requirements

### Requirement: GlobalConfig snapshot_preserve_min field

`GlobalConfig` SHALL include a `snapshot_preserve_min: int = 0` field. This field is the global default for the snapshot preservation floor. When set to 0 (default), the preservation floor is inactive. When set to a positive integer N, the newest N snapshots SHALL always be preserved (never blockcommitted) regardless of `snapshot_chain_length`. The field is immutable (frozen dataclass). It SHALL be inherited by `VMConfig.snapshot_preserve_min` via option inheritance when the VM does not explicitly set it.

#### Scenario: GlobalConfig default snapshot_preserve_min is 0

- **WHEN** a GlobalConfig is created with only required fields
- **THEN** `snapshot_preserve_min` defaults to `0` (inactive)

#### Scenario: GlobalConfig snapshot_preserve_min is immutable

- **WHEN** a GlobalConfig is created with `snapshot_preserve_min=24`
- **THEN** attempting to mutate `snapshot_preserve_min` raises `FrozenInstanceError`

### Requirement: VMConfig snapshot_preserve_min field

`VMConfig` SHALL include a `snapshot_preserve_min: int | None = None` field. This field overrides `GlobalConfig.snapshot_preserve_min` when set. When `None`, the value is resolved via option inheritance from `GlobalConfig.snapshot_preserve_min`. The resolved value SHALL be a non-negative integer (0 = inactive).

#### Scenario: VM inherits snapshot_preserve_min from global

- **WHEN** global sets `snapshot_preserve_min = 24` and VM omits it
- **THEN** `VMConfig.snapshot_preserve_min` resolves to `24`

#### Scenario: VM overrides global snapshot_preserve_min

- **WHEN** global sets `snapshot_preserve_min = 24` and VM sets `snapshot_preserve_min = 48`
- **THEN** `VMConfig.snapshot_preserve_min` resolves to `48` (VM overrides global)

#### Scenario: VM sets snapshot_preserve_min to 0 (explicitly inactive)

- **WHEN** global sets `snapshot_preserve_min = 24` and VM sets `snapshot_preserve_min = 0`
- **THEN** `VMConfig.snapshot_preserve_min` resolves to `0` (VM disables the floor)

### Requirement: snapshot_preserve_min validation

`ConfigFacade._build_vm()` SHALL validate that `snapshot_preserve_min` is a non-negative integer. Negative values SHALL raise `ConfigError` with a message indicating the valid range. The default value when not specified in TOML SHALL be `None` (inherits from global).

#### Scenario: Valid snapshot_preserve_min value

- **WHEN** the config has `snapshot_preserve_min = 24`
- **THEN** `ConfigFacade` accepts it and stores `24` in `VMConfig.snapshot_preserve_min`

#### Scenario: Negative snapshot_preserve_min raises ConfigError

- **WHEN** the config has `snapshot_preserve_min = -1`
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating the valid range is >= 0
