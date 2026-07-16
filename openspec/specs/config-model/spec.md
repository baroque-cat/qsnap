## Requirements

### Requirement: GlobalConfig dataclass
The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options, including timestamp format, preserve day of week, state directory, lockfile path, snapshot/target preserve policies, rate limit, deferred monitoring thresholds, fault-tolerance safety controls, and compression default.

#### Scenario: GlobalConfig is immutable
- **WHEN** a GlobalConfig instance is created with `timestamp_format="long"` and `preserve_day_of_week="monday"`
- **THEN** attempting to mutate any field raises FrozenInstanceError

#### Scenario: GlobalConfig default values
- **WHEN** a GlobalConfig is created with only required fields
- **THEN** optional fields have documented defaults (`state_dir="/var/lib/qsnap/state"`, `lockfile=None`, `rate_limit="no"`, `compress=True`, `deferred_warn_count="5"`, `deferred_crit_count="10"`, `deferred_warn_age="7d"`, `deferred_crit_age="14d"`, `auto_cleanup=true`, `state_backup_count=2`, `chain_verify_before_commit=true`, `chain_verify_after_commit=true`, `deep_check_schedule="off"`)

### Requirement: VMConfig dataclass
The system SHALL provide an immutable `VMConfig` dataclass representing a single VM's configuration, including its name, base image path, snapshot directory, snapshot creation mode, retention policy, optional targets, and fault-tolerance deep verification controls.

#### Scenario: VMConfig with required fields
- **WHEN** a VMConfig is created with `name="myvm"`, `base_image=Path(...)`, `snapshot_dir=Path(...)`
- **THEN** the instance has all required fields populated and `snapshot_create` defaults to `"always"`, `blockcommit_deep_verify` defaults to `False`, `snapshot_deep_verify` defaults to `False`

#### Scenario: VMConfig with targets
- **WHEN** a VMConfig is created with a list of TargetConfig objects
- **THEN** `vm.targets` contains those targets in order

### Requirement: TargetConfig dataclass
The system SHALL provide an immutable `TargetConfig` dataclass representing a backup target: its path, whether incremental backup is enabled, its retention policy, its rate limit setting, verification mode, retry controls, compression setting, and base-copy behavior.

#### Scenario: TargetConfig with incremental enabled
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and `incremental=True`
- **THEN** both fields are accessible, `rate_limit` defaults to `"no"`, `backup_retry_max` defaults to `3`, `backup_retry_base` defaults to `"2s"`, `compress` defaults to `True`, `copy_base` defaults to `False`, and the instance is frozen

### Requirement: RetentionPolicy dataclass
The system SHALL provide an immutable `RetentionPolicy` dataclass with fields for hourly, daily, weekly, monthly, and yearly retention counts, plus a `preserve_min` string. The `preserve_min` field SHALL accept the value `"latest"` in addition to `"all"` and duration strings like `"6h"`, `"2d"`.

#### Scenario: RetentionPolicy with hourly and daily limits
- **WHEN** a RetentionPolicy is created with `hourly=24`, `daily=2`, `weekly=0`, `monthly=0`, `yearly=0`, `preserve_min="6h"`
- **THEN** all fields are accessible and match the provided values

#### Scenario: RetentionPolicy defaults
- **WHEN** a RetentionPolicy is created with no arguments
- **THEN** all retention counts default to 0 and `preserve_min` defaults to `"all"`

#### Scenario: preserve_min = "latest"
- **WHEN** a RetentionPolicy is created with `preserve_min="latest"`
- **THEN** `retention.preserve_min` is `"latest"`
- **THEN** the retention engine keeps only the most recent item

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

### Requirement: GlobalConfig preserve_day_of_week field is consumed
The `preserve_day_of_week` field on `GlobalConfig` (default `"monday"`) SHALL be passed to `TimeBasedRetention.evaluate()` and used to determine weekly bucket boundaries.

#### Scenario: preserve_day_of_week controls weekly grouping
- **WHEN** `preserve_day_of_week = "sunday"` in the config and `weekly = 2`
- **THEN** retention preserves at most 2 weekly snapshots with Sunday as the week boundary

### Requirement: GlobalConfig preserve_day_of_week validation
`ConfigFacade` SHALL validate that `preserve_day_of_week` is one of: monday, tuesday, wednesday, thursday, friday, saturday, sunday (case-insensitive). Invalid values SHALL raise `ConfigError`.

#### Scenario: Valid day of week
- **WHEN** the config has `preserve_day_of_week = "friday"`
- **THEN** ConfigFacade accepts it and stores "friday" in GlobalConfig

#### Scenario: Invalid day of week
- **WHEN** the config has `preserve_day_of_week = "funday"`
- **THEN** ConfigFacade raises ConfigError with a message indicating the valid values

### Requirement: TargetConfig incremental_mode field
`TargetConfig` SHALL gain an `incremental_mode: str` field with default value `"file-copy"`. Accepted values SHALL be `"file-copy"` (whole-file copy) and `"bitmap"` (dirty-block extraction via checkpoint). The field SHALL be immutable (`frozen=True`).

#### Scenario: Default incremental_mode is file-copy
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and no `incremental_mode`
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
`TargetConfig` SHALL gain a `verify: str` field with default value `"metadata"`. Accepted values SHALL be `"off"` (no verification), `"metadata"` (qemu-img info consistency check), and `"full"` (qemu-img compare byte-level verification). The field SHALL be immutable (`frozen=True`).

#### Scenario: Default verify is metadata
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and no `verify`
- **THEN** `target.verify` is `"metadata"`

#### Scenario: Explicit full verification
- **WHEN** a TargetConfig is created with `verify="full"`
- **THEN** `target.verify` is `"full"`

### Requirement: VMConfig snapshot_quiesce field
`VMConfig` SHALL gain a `snapshot_quiesce: bool` field with default `False`. When `True`, snapshot creation SHALL request guest-agent filesystem freeze via `--quiesce`.

#### Scenario: Quiesce default is disabled
- **WHEN** a VMConfig is created without `snapshot_quiesce`
- **THEN** `vm_config.snapshot_quiesce` is `False`

### Requirement: GlobalConfig contains snapshot_preserve_min and target_preserve_min

`GlobalConfig` SHALL contain fields `snapshot_preserve_min: str | None = None` and `target_preserve_min: str | None = None`.

#### Scenario: Defaults are None
- **WHEN** `GlobalConfig` is constructed with no preserve_min keys
- **THEN** `snapshot_preserve_min` and `target_preserve_min` are both `None`

### Requirement: VMConfig contains snapshot_preserve_min and target_preserve_min

`VMConfig` SHALL contain fields `snapshot_preserve_min: str | None = None` and `target_preserve_min: str | None = None`.

#### Scenario: VM inherits from global
- **WHEN** global sets `snapshot_preserve_min = "3h"` and VM omits it
- **THEN** `VMConfig.snapshot_preserve_min` resolves to `"3h"`

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
`VMConfig` SHALL include `blockcommit_deep_verify: bool` (default `False`) and `snapshot_deep_verify: bool` (default `False`). See `specs/deep-verification-circuit/spec.md` for full semantics.

#### Scenario: Deep verify defaults to off
- **WHEN** `VMConfig` is constructed without deep verify fields
- **THEN** both `blockcommit_deep_verify` and `snapshot_deep_verify` are `False`

### Requirement: TargetConfig backup_retry_max and backup_retry_base fields
`TargetConfig` SHALL include `backup_retry_max: int` (default `3`) and `backup_retry_base: str` (default `"2s"`). See `specs/backup-retry/spec.md` for full semantics.

#### Scenario: Default retry values
- **WHEN** `TargetConfig` is constructed without retry fields
- **THEN** `backup_retry_max` is `3`, `backup_retry_base` is `"2s"`

### Requirement: TargetConfig compress field
`TargetConfig` SHALL have a `compress: bool` field with default `True`. When `True`, FULL backups SHALL be created with `qemu-img convert -c` (zlib compression). The field SHALL be immutable (`frozen=True`). It SHALL inherit from `GlobalConfig.compress` when not explicitly set on the target.

#### Scenario: Default compress is true
- **WHEN** a TargetConfig is created without `compress`
- **THEN** `target.compress` is `True`

#### Scenario: Explicit compress disabled
- **WHEN** a TargetConfig is created with `compress=False`
- **THEN** `target.compress` is `False`

### Requirement: TargetConfig copy_base field
`TargetConfig` SHALL have a `copy_base: bool` field with default `False`. When `False`, `base.qcow2` SHALL NOT be copied to the target — the first backup is always a FULL via `qemu-img convert`. When `True`, the legacy behavior of copying `base.qcow2` to the target is preserved.

#### Scenario: Default copy_base is false
- **WHEN** a TargetConfig is created without `copy_base`
- **THEN** `target.copy_base` is `False`

#### Scenario: Explicit copy_base enabled
- **WHEN** a TargetConfig is created with `copy_base=True`
- **THEN** `target.copy_base` is `True`

### Requirement: GlobalConfig compress field
`GlobalConfig` SHALL have a `compress: bool` field with default `True`. This serves as the global default for `TargetConfig.compress` when the target does not explicitly set it.

#### Scenario: Global compress default
- **WHEN** `GlobalConfig` is constructed without `compress`
- **THEN** `compress` is `True`

#### Scenario: Target inherits compress from global
- **WHEN** global config sets `compress = false` and a target does not specify `compress`
- **THEN** `TargetConfig.compress` resolves to `False`
