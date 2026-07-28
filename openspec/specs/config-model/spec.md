## Requirements

### Requirement: GlobalConfig dataclass
The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options, including timestamp format, state directory, lockfile path, count-based retention defaults (`snapshot_chain_length`, `target_chain_length`, `target_keep_generations`), deferred monitoring thresholds, fault-tolerance safety controls, compression default, compression type, and backup stall timeout. The fields `preserve_day_of_week`, `snapshot_preserve`, `target_preserve`, `snapshot_preserve_min`, and `target_preserve_min` SHALL NOT exist on `GlobalConfig`.

#### Scenario: GlobalConfig is immutable
- **WHEN** a GlobalConfig instance is created with `timestamp_format="long"` and `snapshot_chain_length=168`
- **THEN** attempting to mutate any field raises FrozenInstanceError

#### Scenario: GlobalConfig default values
- **WHEN** a GlobalConfig is created with only required fields
- **THEN** optional fields have documented defaults (`state_dir="/var/lib/qsnap/state"`, `lockfile=None`, `compress=True`, `compression_type="zstd"`, `backup_stall_timeout="30m"`, `snapshot_chain_length=None`, `target_chain_length=None`, `target_keep_generations=None`, `auto_cleanup=true`, `state_backup_count=2`, `chain_verify_before_commit=true`, `chain_verify_after_commit=true`, `deep_check_schedule="off"`)

### Requirement: compression_type field in GlobalConfig

`GlobalConfig` SHALL include a `compression_type: str = "zstd"` field. Valid values are `"zstd"` (default) and `"zlib"`. This field selects the compression algorithm used by `qemu-img convert -c` (via `-o compression_type=<type>`) when `compress=True`. The field is immutable (frozen dataclass).

#### Scenario: GlobalConfig default compression_type is zstd
- **WHEN** a GlobalConfig is created with only required fields
- **THEN** `compression_type` defaults to `"zstd"`

#### Scenario: GlobalConfig compression_type is immutable
- **WHEN** a GlobalConfig is created with `compression_type="zlib"`
- **THEN** attempting to mutate `compression_type` raises `FrozenInstanceError`

#### Scenario: GlobalConfig compression_type set to zlib
- **WHEN** a GlobalConfig is created with `compression_type="zlib"`
- **THEN** `config.compression_type == "zlib"`

### Requirement: backup_stall_timeout field in GlobalConfig

`GlobalConfig` SHALL include a `backup_stall_timeout: str = "30m"` field. The value is a duration string (e.g., `"30m"`, `"1h"`, `"0s"`) parsed to seconds via `parse_stall_timeout()`. When set to `"0s"`, stall detection is disabled and the system falls back to fixed timeout behavior. This field is the global default for all VMs and targets, overridable per-target.

#### Scenario: GlobalConfig default stall timeout is 30m
- **WHEN** a GlobalConfig is created with only required fields
- **THEN** `backup_stall_timeout` defaults to `"30m"`

#### Scenario: GlobalConfig stall timeout is immutable
- **WHEN** a GlobalConfig is created with `backup_stall_timeout="1h"`
- **THEN** attempting to mutate `backup_stall_timeout` raises `FrozenInstanceError`

### Requirement: VMConfig dataclass
The system SHALL provide an immutable `VMConfig` dataclass representing a single VM's configuration, including its name, base image path, snapshot directory, snapshot creation mode, count-based retention overrides (`snapshot_chain_length`, `target_chain_length`, `target_keep_generations`), optional targets, and fault-tolerance deep verification controls. The fields `snapshot_preserve`, `target_preserve`, `snapshot_preserve_min`, and `target_preserve_min` SHALL NOT exist on `VMConfig`.

#### Scenario: VMConfig with required fields
- **WHEN** a VMConfig is created with `name="myvm"`, `base_image=Path(...)`, `snapshot_dir=Path(...)`
- **THEN** the instance has all required fields populated and `snapshot_create` defaults to `"always"`, `blockcommit_deep_verify` defaults to `False`, `snapshot_chain_length` defaults to `None`

#### Scenario: VMConfig with targets
- **WHEN** a VMConfig is created with a list of TargetConfig objects
- **THEN** `vm.targets` contains those targets in order

### Requirement: TargetConfig dataclass
The system SHALL provide an immutable `TargetConfig` dataclass representing a backup target: its path, whether incremental backup is enabled, count-based retention overrides (`target_chain_length`, `target_keep_generations`), verification mode, retry controls, compression setting, compression type, and backup stall timeout. The fields `target_preserve` and `target_preserve_min` SHALL NOT exist on `TargetConfig`. The `verify` field SHALL default to `"metadata"` at the dataclass level. When the user explicitly sets `verify` in the TOML config, the explicit value takes precedence. The `compression_type` field SHALL default to `"zstd"` and inherit from `GlobalConfig.compression_type` when not explicitly set. The `backup_stall_timeout` field SHALL default to `"30m"` and inherit from `GlobalConfig.backup_stall_timeout` when not explicitly set.

#### Scenario: TargetConfig with incremental enabled
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and `incremental=True`
- **THEN** both fields are accessible, `backup_retry_max` defaults to `3`, `backup_retry_base` defaults to `"2s"`, `compress` defaults to `True`, `compression_type` defaults to `"zstd"`, `backup_stall_timeout` defaults to `"30m"`, `target_chain_length` defaults to `None`, `target_keep_generations` defaults to `None`, and the instance is frozen

### Requirement: compression_type field in TargetConfig

`TargetConfig` SHALL include a `compression_type: str = "zstd"` field. This field is resolved via option inheritance: if not set in the target's TOML section, it inherits from `GlobalConfig.compression_type`. Valid values are `"zstd"` and `"zlib"`.

#### Scenario: TargetConfig compression_type inherits from global
- **WHEN** a TargetConfig is created without explicit `compression_type`
- **AND** the GlobalConfig has `compression_type="zstd"`
- **THEN** `target.compression_type == "zstd"`

#### Scenario: TargetConfig compression_type overrides global
- **WHEN** a TargetConfig is created with `compression_type="zlib"`
- **AND** the GlobalConfig has `compression_type="zstd"`
- **THEN** `target.compression_type == "zlib"` (target overrides global)

### Requirement: backup_stall_timeout field in TargetConfig

`TargetConfig` SHALL include a `backup_stall_timeout: str = "30m"` field. This field is resolved via option inheritance: if not set in the target's TOML section, it inherits from `GlobalConfig.backup_stall_timeout`. The value is a duration string parsed to seconds via `parse_stall_timeout()`.

#### Scenario: TargetConfig stall timeout inherits from global
- **WHEN** a TargetConfig is created without explicit `backup_stall_timeout`
- **AND** the GlobalConfig has `backup_stall_timeout="1h"`
- **THEN** `target.backup_stall_timeout == "1h"`

#### Scenario: TargetConfig stall timeout overrides global
- **WHEN** a TargetConfig is created with `backup_stall_timeout="15m"`
- **AND** the GlobalConfig has `backup_stall_timeout="30m"`
- **THEN** `target.backup_stall_timeout == "15m"` (target overrides global)



### Requirement: GlobalConfig lockfile field is consumed
The `lockfile` field on `GlobalConfig` (already defined, default `None`) SHALL be consumed by the locking mechanism. If `lockfile` is not `None`, the process SHALL acquire a lock on this path before pipeline execution.

#### Scenario: Lockfile from config is used
- **WHEN** the config has `lockfile = "/var/lock/qsnap.lock"` and no `--lockfile` CLI flag is passed
- **THEN** a lock is acquired on `/var/lock/qsnap.lock`

### Requirement: GlobalConfig timestamp_format field is consumed
The `timestamp_format` field on `GlobalConfig` (default `"long"`) SHALL be consumed by `Core._generate_snapshot_name()` to select the timestamp format string.

#### Scenario: timestamp_format controls snapshot naming
- **WHEN** `timestamp_format = "short"` in the config and a snapshot is created
- **THEN** the snapshot name uses `YYYYMMDD` format



### Requirement: TargetConfig incremental_mode field
`TargetConfig` SHALL have an `incremental_mode: str` field with default value `"bitmap"`. Accepted values SHALL be `"file-copy"` (whole-file copy via rsync) and `"bitmap"` (dirty-block extraction via NBD). The field SHALL be immutable (`frozen=True`). When `incremental_mode="bitmap"` and libvirt < 6.0, the factory SHALL fall back to `FileCopyBackupProvider` without mutating the config.

#### Scenario: Default incremental_mode is bitmap
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and no `incremental_mode`
- **THEN** `target.incremental_mode` is `"bitmap"`

#### Scenario: Explicit file-copy mode
- **WHEN** a TargetConfig is created with `incremental_mode="file-copy"`
- **THEN** `target.incremental_mode` is `"file-copy"`

#### Scenario: Explicit bitmap mode
- **WHEN** a TargetConfig is created with `incremental_mode="bitmap"`
- **THEN** `target.incremental_mode` is `"bitmap"`

### Requirement: VMConfig disks field
`VMConfig` SHALL gain an optional `disks: list[str] | None` field (default `None`). When `None`, `Core` SHALL auto-discover all disks via `virsh domblklist`. When a list is provided, only those disks are snapshotted.

#### Scenario: Disks list is None — auto-discovery
- **WHEN** a VMConfig is created without `disks`
- **THEN** `vm_config.disks` is `None`
- **THEN** Core discovers disks dynamically at runtime

#### Scenario: Explicit disk list
- **WHEN** a VMConfig is created with `disks=["vda", "vdb"]`
- **THEN** only `vda` and `vdb` are snapshotted

### Requirement: TargetConfig verify field
`TargetConfig` SHALL have a `verify: str` field with dataclass-level default `"metadata"`. The default SHALL be `"metadata"`. When the user explicitly sets `verify` in TOML, the explicit value SHALL take precedence. Accepted values SHALL be `"off"` (no verification), `"metadata"` (structural checks), `"compare"` (qemu-img compare chain-traversing content verification). The `"hash"` and `"full"` values are deprecated and treated as `"compare"`. Deprecated values SHALL log a WARNING naming the value. The field SHALL be immutable (`frozen=True`).

#### Scenario: Dataclass-level verify default is metadata
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and no `verify`
- **THEN** `target.verify` is `"metadata"`

#### Scenario: Explicit compare verification
- **WHEN** a TargetConfig is created with `verify="compare"`
- **THEN** `target.verify` is `"compare"`

#### Scenario: Deprecated hash treated as compare
- **WHEN** `verify = "hash"` is set
- **THEN** a WARNING is logged
- **AND** the effective value is `"compare"`

### Requirement: VMConfig snapshot_quiesce field
`VMConfig` SHALL gain a `snapshot_quiesce: bool` field with default `False`. When `True`, snapshot creation SHALL request guest-agent filesystem freeze via `--quiesce`.

#### Scenario: Quiesce default is disabled
- **WHEN** a VMConfig is created without `snapshot_quiesce`
- **THEN** `vm_config.snapshot_quiesce` is `False`



### Requirement: GlobalConfig rate_limit field

`GlobalConfig` SHALL include an optional `rate_limit` field of type `str` with default `"no"`. See `specs/rate-limit/spec.md` for full semantics.

### Requirement: GlobalConfig deferred threshold fields

`GlobalConfig` SHALL include optional deferred threshold fields: `deferred_warn_count` (default `"5"`), `deferred_crit_count` (default `"10"`), `deferred_warn_age` (default `"7d"`), `deferred_crit_age` (default `"14d"`). All SHALL be of type `str`. See `specs/deferred-monitoring/spec.md` for full semantics.

### Requirement: TargetConfig rate_limit field

`TargetConfig` SHALL include an optional `rate_limit` field of type `str` with default `"no"`, inherited from `GlobalConfig.rate_limit` when unset.

### Requirement: GlobalConfig auto_cleanup field
`GlobalConfig` SHALL include an `auto_cleanup: bool` field with default `True`. See `specs/pre-flight-cleanup/spec.md` for full semantics.

#### Scenario: Default auto_cleanup is true
- **WHEN** `GlobalConfig` is constructed without `auto_cleanup`
- **THEN** `auto_cleanup` is `True`

### Requirement: GlobalConfig state_backup_count field
`GlobalConfig` SHALL include a `state_backup_count: int` field with default `2`. See `specs/state-recovery/spec.md` for full semantics.

#### Scenario: Default state_backup_count
- **WHEN** `GlobalConfig` is constructed without `state_backup_count`
- **THEN** `state_backup_count` is `2`

### Requirement: GlobalConfig chain verification fields
`GlobalConfig` SHALL include `chain_verify_before_commit: bool` (default `True`) and `chain_verify_after_commit: bool` (default `True`). See `specs/chain-integrity-verification/spec.md` for full semantics.

#### Scenario: Chain verification enabled by default
- **WHEN** `GlobalConfig` is constructed without chain verify fields
- **THEN** both are `True`

### Requirement: GlobalConfig deep_check_schedule field
`GlobalConfig` SHALL include a `deep_check_schedule: str` field with default `"off"`. See `specs/deep-verification-circuit/spec.md` for full semantics.

#### Scenario: deep_check_schedule defaults to off
- **WHEN** `GlobalConfig` is constructed without `deep_check_schedule`
- **THEN** `deep_check_schedule` is `"off"`

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

### Requirement: TargetConfig backup_retry_max and backup_retry_base fields
`TargetConfig` SHALL include `backup_retry_max: int` (default `3`) and `backup_retry_base: str` (default `"2s"`). See `specs/backup-retry/spec.md` for full semantics.

#### Scenario: Default retry values
- **WHEN** `TargetConfig` is constructed without retry fields
- **THEN** `backup_retry_max` is `3`, `backup_retry_base` is `"2s"`

### Requirement: TargetConfig compress field
`TargetConfig` SHALL have a `compress: bool` field with default `True`. When `True`, FULL backups SHALL be created with `qemu-img convert -c` using the compression algorithm selected by `compression_type` (default `"zstd"`, alternative `"zlib"`). The field SHALL be immutable (`frozen=True`). It SHALL inherit from `GlobalConfig.compress` when not explicitly set on the target.

#### Scenario: Default compress is true
- **WHEN** a TargetConfig is created without `compress`
- **THEN** `target.compress` is `True`

#### Scenario: Explicit compress disabled
- **WHEN** a TargetConfig is created with `compress=False`
- **THEN** `target.compress` is `False`

### Requirement: GlobalConfig compress field
`GlobalConfig` SHALL have a `compress: bool` field with default `True`. This serves as the global default for `TargetConfig.compress` when the target does not explicitly set it.

#### Scenario: Global compress default
- **WHEN** `GlobalConfig` is constructed without `compress`
- **THEN** `compress` is `True`

#### Scenario: Target inherits compress from global
- **WHEN** global config sets `compress = false` and a target does not specify `compress`
- **THEN** `TargetConfig.compress` resolves to `False`

### Requirement: GlobalConfig full_verify_after_create field

`GlobalConfig` SHALL include a `full_verify_after_create: str` field with default `"check"`. Accepted values: `"metadata"` (M1 only), `"check"` (M1+M2), `"compare"` (M1+M2+M3 via qemu-img compare), `"off"` (no verification). The `"hash"` value is deprecated and treated as `"compare"`. Controls FULL backup verification immediately after creation.

#### Scenario: Default is check
- **WHEN** `full_verify_after_create` is not set
- **THEN** the value is `"check"`

#### Scenario: Deprecated hash treated as compare
- **WHEN** `full_verify_after_create = "hash"` is set
- **THEN** a WARNING is logged
- **AND** the effective value is `"compare"`

### Requirement: GlobalConfig full_verify_before_rebase field (REMOVED)

The `full_verify_before_rebase` field is REMOVED from `GlobalConfig`. It was parsed, validated, and stored, but never consumed by any code path. The rebase step it was intended to protect died with `FileCopyBackupProvider`. `ConfigFacade` SHALL NOT parse or validate this field. If the field appears in a TOML config, it SHALL be silently ignored as an unknown key.

#### Scenario: full_verify_before_rebase not in GlobalConfig

- **WHEN** `GlobalConfig` is constructed
- **THEN** the dataclass does not have a `full_verify_before_rebase` field
- **AND** attempting to access `config.full_verify_before_rebase` raises `AttributeError`

#### Scenario: TOML with full_verify_before_rebase is silently ignored

- **WHEN** a TOML config contains `full_verify_before_rebase = "metadata"`
- **THEN** `ConfigFacade` does not raise an error
- **AND** the value is not stored in any config dataclass

### Requirement: GlobalConfig full_verify_before_delete field

`GlobalConfig` SHALL include a `full_verify_before_delete: str` field with default `"check"`. Controls optional M2 verification before cascade-deletion. M1 is always enforced regardless of this setting.

### Requirement: GlobalConfig deep_check_targets field

`GlobalConfig` SHALL include a `deep_check_targets: bool` field with default `False`. When enabled, `qsnap check --deep` also checks FULL and incremental backup files on target directories.



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

### Requirement: GlobalConfig count-based retention fields

`GlobalConfig` SHALL include `snapshot_chain_length: int | None = None`, `target_chain_length: int | None = None`, and `target_keep_generations: int | None = None`. These serve as global defaults for VM-level and target-level overrides.

#### Scenario: Defaults are None
- **WHEN** `GlobalConfig` is constructed without chain_length keys
- **THEN** `snapshot_chain_length`, `target_chain_length`, and `target_keep_generations` are all `None`

### Requirement: VMConfig count-based retention fields

`VMConfig` SHALL include `snapshot_chain_length: int | None = None`, `target_chain_length: int | None = None`, and `target_keep_generations: int | None = None`. These override global defaults when set.

#### Scenario: VM inherits from global
- **WHEN** global sets `snapshot_chain_length = 168` and VM omits it
- **THEN** `VMConfig.snapshot_chain_length` resolves to `168`

### Requirement: TargetConfig count-based retention fields

`TargetConfig` SHALL include `target_chain_length: int | None = None` and `target_keep_generations: int | None = None`. These override VM-level and global defaults when set.

#### Scenario: Target inherits from VM
- **WHEN** VM sets `target_chain_length = 168` and target omits it
- **THEN** `TargetConfig.target_chain_length` resolves to `168`

#### Scenario: Target overrides VM
- **WHEN** VM sets `target_chain_length = 168` and target sets `target_chain_length = 336`
- **THEN** `TargetConfig.target_chain_length` resolves to `336`
