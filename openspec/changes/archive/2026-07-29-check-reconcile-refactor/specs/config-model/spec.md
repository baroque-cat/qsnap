## MODIFIED Requirements

### Requirement: GlobalConfig count-based retention fields

`GlobalConfig` SHALL include `snapshot_chain_length: int | None = 24`, `target_chain_length: int | None = 168`, and `target_keep_generations: int | None = 2`. These serve as global defaults for VM-level and target-level overrides. The previous default of `None` (which resolved to 0/1 via `or` operators, causing extremely aggressive behavior) is replaced with sensible defaults: 24 snapshots (24 hours at hourly runs), 168 incrementals (7 days between FULLs), 2 generations (2 weeks of backup redundancy).

#### Scenario: Defaults are 24/168/2

- **WHEN** `GlobalConfig` is constructed without chain_length keys
- **THEN** `snapshot_chain_length` is `24`
- **AND** `target_chain_length` is `168`
- **AND** `target_keep_generations` is `2`

#### Scenario: Explicit override still works

- **WHEN** `GlobalConfig` is constructed with `snapshot_chain_length=48`
- **THEN** `snapshot_chain_length` is `48` (explicit override takes precedence)

### Requirement: GlobalConfig default values

The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options, including timestamp format, state directory, lockfile path, count-based retention defaults (`snapshot_chain_length=24`, `target_chain_length=168`, `target_keep_generations=2`), deferred monitoring thresholds, fault-tolerance safety controls, compression default, compression type, and backup stall timeout. The fields `preserve_day_of_week`, `snapshot_preserve`, `target_preserve`, `snapshot_preserve_min`, and `target_preserve_min` SHALL NOT exist on `GlobalConfig`.

#### Scenario: GlobalConfig default values

- **WHEN** a GlobalConfig is created with only required fields
- **THEN** optional fields have documented defaults (`state_dir="/var/lib/qsnap/state"`, `lockfile=None`, `compress=True`, `compression_type="zstd"`, `backup_stall_timeout="30m"`, `snapshot_chain_length=24`, `target_chain_length=168`, `target_keep_generations=2`, `auto_cleanup=true`, `state_backup_count=2`, `chain_verify_before_commit=true`, `chain_verify_after_commit=true`, `deep_check_schedule="off"`)
