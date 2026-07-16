## MODIFIED Requirements

### Requirement: ConfigFacade parses new fault-tolerance fields
`ConfigFacade` SHALL parse `auto_cleanup`, `state_backup_count`, `chain_verify_before_commit`, `chain_verify_after_commit`, and `deep_check_schedule` from the global section. It SHALL parse `blockcommit_deep_verify` and `snapshot_deep_verify` from each `[[vm]]` section. It SHALL parse `backup_retry_max`, `backup_retry_base`, `compress`, and `copy_base` from each `[[vm.target]]` section. All fields SHALL use their documented defaults when absent. `ConfigFacade` SHALL NOT parse `full_every` (removed). If `full_every` is present in TOML, a deprecation WARNING SHALL be logged. If `full_compress` is present and `compress` is not, `full_compress` SHALL be mapped to `compress` with a deprecation WARNING.

#### Scenario: Global safety fields parsed
- **WHEN** config TOML contains `auto_cleanup = true`, `state_backup_count = 2`, `chain_verify_before_commit = true`
- **THEN** `GlobalConfig.auto_cleanup` is `True`, `state_backup_count` is `2`, `chain_verify_before_commit` is `True`

#### Scenario: Target compress and copy_base parsed
- **WHEN** a `[[vm.target]]` section contains `compress = true` and `copy_base = false`
- **THEN** that target's `TargetConfig.compress` is `True` and `copy_base` is `False`

#### Scenario: full_every in config triggers deprecation warning
- **WHEN** a `[[vm.target]]` section contains `full_every = "7d"`
- **THEN** a WARNING is logged: "full_every is deprecated, FULLs are now bucket-driven"
- **AND** the value is ignored

#### Scenario: full_compress mapped to compress
- **WHEN** a `[[vm.target]]` section contains `full_compress = true` but no `compress`
- **THEN** `TargetConfig.compress` is `True`
- **AND** a WARNING is logged: "full_compress is deprecated, use compress instead"

### Requirement: ConfigFacade updates example config
The shipped `qsnap.toml.example` SHALL document all fault-tolerance fields plus all existing-but-not-shown fields: `snapshot_preserve_min`, `target_preserve_min`, `rate_limit`, `compress`, `copy_base`, `incremental_mode`, `change_detection_mode`, `disks`, `deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age`. It SHALL NOT document `full_every` or `full_compress` (removed).

#### Scenario: Example config is parseable with all fields documented
- **WHEN** `qsnap -c qsnap.toml.example list config` is executed
- **THEN** the config is parsed successfully and all documented fields are visible in the output

## ADDED Requirements

### Requirement: Config validation forbids preserve_min without buckets
`ConfigFacade` SHALL validate that if `target_preserve` is not `None` and not `"latest"` and all parsed bucket counts are 0, then `target_preserve_min` SHALL be `"all"`. If `target_preserve_min` is not `"all"` and all bucket counts are 0, `ConfigFacade` SHALL raise `ConfigError` with a message explaining that `preserve_min` without buckets requires a FULL anchor which cannot be created without at least one active bucket.

#### Scenario: preserve_min without buckets rejected
- **WHEN** a target has `target_preserve = "0h 0d 0w 0m 0y"` and `target_preserve_min = "48h"`
- **THEN** `ConfigError` is raised with message: "preserve_min without active buckets is not allowed — at least one bucket must have count > 0"

#### Scenario: preserve_min=all without buckets allowed
- **WHEN** a target has `target_preserve = "0h 0d 0w 0m 0y"` and `target_preserve_min = "all"`
- **THEN** no error is raised (chain grows indefinitely, nothing is deleted)

#### Scenario: preserve_min with buckets allowed
- **WHEN** a target has `target_preserve = "24h 7d"` and `target_preserve_min = "6h"`
- **THEN** no error is raised (buckets are active, FULLs will be created)
