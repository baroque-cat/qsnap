## REMOVED Requirements

### Requirement: TargetConfig contains target_preserve_min, full_every, and full_compress

**Reason**: `full_every` is replaced by bucket-driven FULL creation. `full_compress` is renamed to `compress`.
**Migration**: Remove `full_every` from TOML. Replace `full_compress` with `compress`.

## MODIFIED Requirements

### Requirement: TargetConfig dataclass
The system SHALL provide an immutable `TargetConfig` dataclass representing a backup target: its path, whether incremental backup is enabled, its retention policy, its rate limit setting, verification mode, retry controls, compression setting, and base-copy behavior.

#### Scenario: TargetConfig with incremental enabled
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and `incremental=True`
- **THEN** both fields are accessible, `rate_limit` defaults to `"no"`, `backup_retry_max` defaults to `3`, `backup_retry_base` defaults to `"2s"`, `compress` defaults to `True`, `copy_base` defaults to `False`, and the instance is frozen

### Requirement: GlobalConfig dataclass
The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options, including timestamp format, preserve day of week, state directory, lockfile path, snapshot/target preserve policies, rate limit, deferred monitoring thresholds, fault-tolerance safety controls, and compression default.

#### Scenario: GlobalConfig default values
- **WHEN** a GlobalConfig is created with only required fields
- **THEN** optional fields have documented defaults (`state_dir="/var/lib/qsnap/state"`, `lockfile=None`, `rate_limit="no"`, `compress=True`, `deferred_warn_count="5"`, `deferred_crit_count="10"`, `deferred_warn_age="7d"`, `deferred_crit_age="14d"`, `auto_cleanup=true`, `state_backup_count=2`, `chain_verify_before_commit=true`, `chain_verify_after_commit=true`, `deep_check_schedule="off"`)

## ADDED Requirements

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
