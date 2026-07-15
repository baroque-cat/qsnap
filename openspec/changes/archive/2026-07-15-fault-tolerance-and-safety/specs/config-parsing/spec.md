## ADDED Requirements

### Requirement: ConfigFacade parses new fault-tolerance fields
`ConfigFacade` SHALL parse `auto_cleanup`, `state_backup_count`, `chain_verify_before_commit`, `chain_verify_after_commit`, and `deep_check_schedule` from the global section. It SHALL parse `blockcommit_deep_verify` and `snapshot_deep_verify` from each `[[vm]]` section. It SHALL parse `backup_retry_max` and `backup_retry_base` from each `[[vm.target]]` section. All fields SHALL use their documented defaults when absent.

#### Scenario: Global safety fields parsed
- **WHEN** config TOML contains `auto_cleanup = true`, `state_backup_count = 2`, `chain_verify_before_commit = true`
- **THEN** `GlobalConfig.auto_cleanup` is `True`, `state_backup_count` is `2`, `chain_verify_before_commit` is `True`

#### Scenario: VM deep verify fields parsed
- **WHEN** a `[[vm]]` section contains `blockcommit_deep_verify = true`
- **THEN** that VM's `VMConfig.blockcommit_deep_verify` is `True`

#### Scenario: Target retry fields parsed
- **WHEN** a `[[vm.target]]` section contains `backup_retry_max = 5` and `backup_retry_base = "10s"`
- **THEN** that target's `TargetConfig.backup_retry_max` is `5` and `backup_retry_base` is `"10s"`

### Requirement: ConfigFacade updates example config
The shipped `qsnap.toml.example` SHALL document all fault-tolerance fields plus all existing-but-not-shown fields: `snapshot_preserve_min`, `target_preserve_min`, `rate_limit`, `full_every`, `full_compress`, `incremental_mode`, `change_detection_mode`, `disks`, `deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age`.

#### Scenario: Example config is parseable with all fields documented
- **WHEN** `qsnap -c qsnap.toml.example list config` is executed
- **THEN** the config is parsed successfully and all documented fields are visible in the output
