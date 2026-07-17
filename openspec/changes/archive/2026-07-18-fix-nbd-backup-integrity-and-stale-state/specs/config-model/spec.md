## ADDED Requirements

### Requirement: GlobalConfig full_verify_after_create field

`GlobalConfig` SHALL include a `full_verify_after_create: str` field with default value `"check"`. Accepted values SHALL be `"metadata"` (M1 only — qemu-img info + corrupt-bit check), `"check"` (M1 + M2 — additionally qemu-img check), `"hash"` (M1 + M2 + M3 — additionally qemu-img compare byte-level comparison), and `"off"` (no verification). This controls verification of FULL backups immediately after `create_full_backup()` completes.

#### Scenario: Default is check (M1 + M2)
- **WHEN** GlobalConfig is created without explicit `full_verify_after_create`
- **THEN** the field value is `"check"`
- **AND** FULL backups are verified with both `qemu-img info` and `qemu-img check` after creation

#### Scenario: User sets metadata only
- **WHEN** TOML config contains `full_verify_after_create = "metadata"`
- **THEN** `GlobalConfig.full_verify_after_create` is `"metadata"`
- **AND** only `qemu-img info` is run after FULL creation

#### Scenario: User sets hash (M1 + M2 + M3)
- **WHEN** TOML config contains `full_verify_after_create = "hash"`
- **THEN** `GlobalConfig.full_verify_after_create` is `"hash"`
- **AND** `qemu-img info`, `qemu-img check`, and `qemu-img compare` content comparison are all run

### Requirement: GlobalConfig full_verify_before_rebase field

`GlobalConfig` SHALL include a `full_verify_before_rebase: str` field with default value `"metadata"`. Accepted values SHALL be `"metadata"` (M1 only) and `"off"`. Note: M1 at rebase point is minimal — the FULL was already verified at creation. This is a lightweight re-check for bit-rot between creation and rebase.

#### Scenario: Default is metadata
- **WHEN** GlobalConfig is created without explicit `full_verify_before_rebase`
- **THEN** the field value is `"metadata"`
- **AND** M1 is performed before each rebase to a FULL anchor

### Requirement: GlobalConfig full_verify_before_delete field

`GlobalConfig` SHALL include a `full_verify_before_delete: str` field with default value `"check"`. Accepted values SHALL be `"metadata"` (M1 only, enforced), `"check"` (M1 + M2), and `"off"`. Regardless of this field's value, M1 verification SHALL ALWAYS execute before cascade-deletion. If set to `"off"`, only M1 runs (the minimum). If set to `"check"`, both M1 and M2 run.

#### Scenario: Default is check (M1 + M2)
- **WHEN** GlobalConfig is created without explicit `full_verify_before_delete`
- **THEN** the field value is `"check"`
- **AND** both M1 and M2 run before cascade-deletion

#### Scenario: Set to off — M1 still enforced
- **WHEN** TOML config contains `full_verify_before_delete = "off"`
- **THEN** M2 (`qemu-img check`) is NOT executed before cascade-deletion
- **AND** M1 (`qemu-img info` + corrupt-bit check) IS still executed (not configurable)

### Requirement: GlobalConfig deep_check_targets field

`GlobalConfig` SHALL include a `deep_check_targets: bool` field with default value `False`. When `True`, `qsnap check --deep` SHALL additionally verify FULL and incremental backup files on backup target directories.

#### Scenario: deep_check_targets disabled by default
- **WHEN** GlobalConfig is created without explicit `deep_check_targets`
- **THEN** the field is `False`
- **AND** `qsnap check --deep` only checks snapshot files

#### Scenario: deep_check_targets enabled
- **WHEN** `deep_check_targets = true`
- **AND** `qsnap check --deep` is run
- **THEN** FULL and incremental backup files on target directories are also checked via `qemu-img check`

### Requirement: Config validation — active retention buckets required when targets configured

When a VM has one or more backup targets configured, at least one retention bucket (hourly, daily, weekly, monthly, yearly) SHALL have a count > 0, or `preserve_min` SHALL be `"all"`. A configuration with targets but all-zero bucket counts SHALL raise a `ConfigError` at parse time. This prevents a scenario where no FULL backup is ever created (bucket-driven mechanism requires active buckets), leaving incrementals without a valid anchor.

#### Scenario: All-zero buckets with targets raises ConfigError
- **WHEN** TOML config has a `[[vm]]` with a `[[vm.target]]`
- **AND** the target's `target_preserve` is `"0h 0d 0w 0m 0y"` (all buckets zero)
- **AND** `target_preserve_min` is not `"all"`
- **THEN** `ConfigError` is raised with message about at least one active retention bucket required

#### Scenario: preserve_min="all" allows all-zero buckets
- **WHEN** TOML config has `target_preserve = "0h 0d 0w 0m 0y"` with `target_preserve_min = "all"`
- **THEN** no `ConfigError` is raised (preserve_min="all" keeps at least the latest)
- **AND** the first backup effectively keeps one FULL forever
