## REMOVED Requirements

### Requirement: TargetConfig incremental_mode field
**Reason**: There is a single backup mechanism (NBD/libnbd); a mode selector has nothing to select. `FileCopyBackupProvider` and the `"file-copy"` mode are deleted.
**Migration**: Remove `incremental_mode` from TOML target sections. If present, the key is ignored with a deprecation WARNING.

### Requirement: GlobalConfig rate_limit field
**Reason**: `rate_limit` existed solely for `rsync --bwlimit`; the `rate-limit` capability is removed entirely.
**Migration**: Remove `rate_limit` from TOML. Ignored with a deprecation WARNING if present.

### Requirement: TargetConfig rate_limit field
**Reason**: Same as the global field — no throttling mechanism exists for NBD transfers in this change.
**Migration**: Remove `rate_limit` from target sections. Ignored with a deprecation WARNING if present.

### Requirement: TargetConfig copy_base field
**Reason**: `copy_base` was parsed but never implemented in code, and its only consumer concept (copying `base.qcow2` via file transfer) dies with file-copy mode.
**Migration**: Remove `copy_base` from TOML target sections. Ignored with a deprecation WARNING if present. The first backup to a target is always a bucket-driven FULL.

## MODIFIED Requirements

### Requirement: TargetConfig verify field
`TargetConfig` SHALL have a `verify: str` field with default value `"metadata"`. When the user explicitly sets `verify` in TOML, the explicit value SHALL take precedence over the default. Accepted values SHALL be `"off"` (no verification), `"metadata"` (structural checks: format, virtual-size, backing-filename, dirty-size barrier), `"hash"` and `"full"` (content-level verification via chain-traversing `qemu-img compare` — see the `nbd-bitmap-backup` capability). The field SHALL be immutable (`frozen=True`). The default SHALL NOT depend on any transfer mode — NBD/libnbd is the only transfer mechanism, and no mode-dependent default or auto-downgrade SHALL be applied.

#### Scenario: Default verification is metadata
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and no `verify`
- **THEN** `target.verify` is `"metadata"`

#### Scenario: ConfigFacade keeps the metadata default
- **WHEN** `ConfigFacade._build_target()` processes a target with no explicit `verify`
- **THEN** the resulting `TargetConfig.verify` is `"metadata"`

#### Scenario: Explicit full verification
- **WHEN** a TargetConfig is created with `verify="full"`
- **THEN** `target.verify` is `"full"` (no downgrade — chain-traversing compare is meaningful for backing-chained deltas)

#### Scenario: Explicit hash verification
- **WHEN** a TargetConfig is created with `verify="hash"`
- **THEN** `target.verify` is `"hash"` (no downgrade)

#### Scenario: Invalid verify value raises ConfigError
- **WHEN** a TargetConfig is created with `verify="sha1"`
- **THEN** `ConfigFacade` raises `ConfigError` indicating the valid values
