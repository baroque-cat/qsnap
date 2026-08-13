## ADDED Requirements

### Requirement: Removed max_commits_per_run key is rejected loudly

ConfigFacade SHALL raise `ConfigError` when the global section of a config file contains the key `max_commits_per_run`. The error message SHALL name the removed option and state that the hysteresis collapse is now a single uncapped bulk blockcommit per trigger (no per-run cap exists). Silent ignoring is forbidden: an old config line must not masquerade as working portioning.

#### Scenario: Legacy config line fails startup

- **WHEN** `/etc/qsnap/qsnap.toml` contains `max_commits_per_run = 12` in the global section
- **THEN** config loading raises `ConfigError` whose message names `max_commits_per_run` and mentions its removal

#### Scenario: Absent key loads normally

- **WHEN** the config file does not mention `max_commits_per_run`
- **THEN** config loading succeeds and no cap of any kind applies to snapshot commits

## MODIFIED Requirements

### Requirement: GlobalConfig default values
The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options, including state directory, lockfile path, count-based retention defaults (`snapshot_chain_length=72`, `target_chain_length=168`, `target_keep_generations=2`), `snapshot_preserve_min=24` (hysteresis collapse floor / snapshot preservation floor; the newest 24 snapshots per disk are never blockcommitted; explicit 0 = inactive), `snapshot_retention_mode="hysteresis"` (retention mode: hysteresis is the default, steady is opt-in), free-space gate controls (`free_space_check="strict"`, `free_space_reserve=0`, `free_space_factor=1.0`), deferred monitoring thresholds, fault-tolerance safety controls, compression default, compression type, convert parallelism, and backup stall timeout. No per-run commit cap field exists.

#### Scenario: GlobalConfig default values
- **WHEN** a `GlobalConfig` is created with only required fields
- **THEN** optional fields have documented defaults: `state_dir="/var/lib/qsnap/state"`, `lockfile=None`, `snapshot_chain_length=72`, `target_chain_length=168`, `target_keep_generations=2`, `snapshot_preserve_min=24`, `snapshot_retention_mode="hysteresis"`, `free_space_check="strict"`, `free_space_reserve=0`, `free_space_factor=1.0`, `compress=True`, `compression_type="zstd"`, `convert_parallel=4`, `convert_out_of_order=True`, `backup_stall_timeout="30m"`, `auto_cleanup=True`, `state_backup_count=2`, `chain_verify_before_commit=True`, `chain_verify_after_commit=True`, `deep_check_schedule="off"`, `full_verify_after_create="check"`, `full_verify_before_delete="check"`, `transaction_log=None`, `backup_create="always"`
- **AND** `GlobalConfig` has no `max_commits_per_run` attribute

## REMOVED Requirements

### Requirement: max_commits_per_run option
**Reason**: The per-run commit cap portioned the hysteresis collapse for the slow one-snapshot-per-job executor. The collapse is now a single bulk segment blockcommit (capability `hysteresis-retention`), so the cap has no function; retaining it would silently reintroduce multi-run collapses.

**Migration**: Delete `max_commits_per_run` from all config files. Configs still containing it fail loading with an actionable `ConfigError` (see ADDED requirement above). No state-file migration is involved.
