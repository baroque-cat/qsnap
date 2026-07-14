# Rate Limit

## Purpose

Bandwidth control for backup file-copy transfers. When `rate_limit` is set (e.g. `"100M"`), `FileCopyBackupProvider` uses `rsync --bwlimit` instead of `cp`. Fallback to `cp` with WARNING when rsync unavailable. Pre-flight validation checks rsync availability. Full backups (`qemu-img convert`) and bitmap (NBD) backups are unaffected.

## Requirements

### Requirement: Rate limit config field on GlobalConfig

The system SHALL support an optional `rate_limit` field on `GlobalConfig` of type `str`, with default value `"no"` (unlimited). The value SHALL accept the format `"<number><suffix>"` where suffix is one of `K`, `M`, `G`, `T` (binary powers of 1024), or the literal `"no"` / `"0"` for unlimited.

#### Scenario: Global rate_limit is parsed

- **WHEN** the config file has `rate_limit = "100M"` at global scope
- **THEN** `GlobalConfig.rate_limit` is `"100M"`

#### Scenario: Global rate_limit defaults to "no"

- **WHEN** the config file has no `rate_limit` field
- **THEN** `GlobalConfig.rate_limit` is `"no"`

#### Scenario: Invalid rate_limit format raises ConfigError

- **WHEN** the config file has `rate_limit = "abc"` (invalid suffix)
- **THEN** `ConfigFacade` raises `ConfigError` during construction

### Requirement: Rate limit config field on TargetConfig

The system SHALL support an optional `rate_limit` field on `TargetConfig` of type `str`, with default value `"no"`. A target-level value SHALL override the global default; a missing value SHALL inherit from the global level.

#### Scenario: Target overrides global rate_limit

- **WHEN** `GlobalConfig.rate_limit` is `"100M"` and `TargetConfig.rate_limit` is `"500M"`
- **THEN** the resolved rate limit for that target is `"500M"`

#### Scenario: Target inherits global rate_limit

- **WHEN** `GlobalConfig.rate_limit` is `"100M"` and `TargetConfig.rate_limit` is `"no"` (default, meaning unset)
- **THEN** the resolved rate limit for that target is `"100M"`

### Requirement: GlobalConfig immutability includes rate_limit

The system SHALL maintain immutability of `GlobalConfig` after construction. The `rate_limit` field is frozen.

#### Scenario: GlobalConfig with rate_limit is frozen

- **WHEN** a GlobalConfig is created with `rate_limit="100M"`
- **THEN** attempting to set `cfg.rate_limit = "200M"` raises `FrozenInstanceError`

### Requirement: TargetConfig immutability includes rate_limit

The system SHALL maintain immutability of `TargetConfig`. The `rate_limit` field is frozen.

#### Scenario: TargetConfig with rate_limit is frozen

- **WHEN** a TargetConfig is created with `rate_limit="200M"`
- **THEN** attempting to set `tgt.rate_limit = "300M"` raises `FrozenInstanceError`

### Requirement: Rsync used for file-copy transfers when rate_limit is set

When `rate_limit` is set to a value other than `"no"`, `FileCopyBackupProvider.transfer_missing()` SHALL use `rsync --bwlimit=<limit_kib> --partial --progress` instead of `cp` for snapshot file transfers.

#### Scenario: Transfer with rate limit uses rsync

- **WHEN** `rate_limit` is `"100M"`
- **AND** `transfer_missing()` is called for a snapshot
- **THEN** the shell executes `rsync --bwlimit=102400 --partial --progress <source> <target>`

#### Scenario: Transfer without rate limit uses cp (unchanged)

- **WHEN** `rate_limit` is `"no"`
- **AND** `transfer_missing()` is called for a snapshot
- **THEN** the shell executes `cp <source> <target>`

### Requirement: Rsync --partial enables resume-after-interruption

The system SHALL pass `--partial` to `rsync` so that a partially transferred file on the target is kept, enabling `rsync` to resume the transfer on the next run rather than starting from scratch.

#### Scenario: Partial file exists on target

- **WHEN** a previous transfer was interrupted, leaving a partially written file of size smaller than the source
- **AND** `transfer_missing()` runs again for that same snapshot
- **THEN** `rsync --partial` resumes the transfer, producing a complete file

### Requirement: Fallback to cp when rsync is unavailable with rate_limit set

When `rate_limit` is set but `rsync` is not available on the system, the system SHALL log a WARNING and fall back to `cp` as if `rate_limit` were `"no"`. The transfer SHALL succeed (no error raised).

#### Scenario: Rsync not found with rate_limit set

- **WHEN** `rate_limit` is `"100M"` and `which rsync` returns non-zero
- **THEN** a WARNING is logged: "rsync not found — rate limiting disabled for target <path>"
- **AND** the transfer proceeds using `cp`

### Requirement: Transfer logging for rate-limited transfers

The system SHALL log rate-limit information at INFO level before and after each transfer, and the full command line at DEBUG level.

#### Scenario: Pre-transfer INFO log

- **WHEN** a rate-limited transfer begins
- **THEN** an INFO message is logged: "Transferring <snapshot_name> to <target_path> (rate limit: <rate_limit>)"

#### Scenario: Post-transfer INFO log with throughput

- **WHEN** a rate-limited transfer completes
- **THEN** an INFO message is logged containing the bytes transferred, elapsed time in seconds, and computed average speed

#### Scenario: DEBUG log contains full rsync command

- **WHEN** a rate-limited transfer runs at DEBUG log level
- **THEN** the full `rsync` command with all flags and paths is logged

### Requirement: Anomalous throughput warning

When the actual average transfer speed is less than 10% of the configured rate limit, the system SHALL log a WARNING suggesting the user check target disk health.

#### Scenario: Slow transfer triggers warning

- **WHEN** `rate_limit` is `"100M"` but actual throughput is 5 MiB/s
- **THEN** a WARNING is logged: "Transfer of <snapshot> slower than expected: 5 MiB/s (limit: 100 MiB/s). Check target disk health."

### Requirement: Rate limit is parsed with binary Ki/Mi/Gi/Ti suffixes

The parsed value SHALL use binary (1024-based) interpretation: `K` = 1024 bytes/s, `M` = 1024² bytes/s, `G` = 1024³ bytes/s, `T` = 1024⁴ bytes/s.

#### Scenario: "500K" parsed correctly

- **WHEN** `rate_limit` is `"500K"`
- **THEN** the effective bytes-per-second limit is `500 * 1024 = 512000`

#### Scenario: "100M" parsed correctly

- **WHEN** `rate_limit` is `"100M"`
- **THEN** the effective bytes-per-second limit is `100 * 1024 * 1024 = 104857600`

### Requirement: Rsync bwlimit receives KiB/s value

The `--bwlimit` argument to `rsync` SHALL receive the rate limit expressed in KiB/s (integer division of bytes-per-second by 1024).

#### Scenario: 100M rate_limit becomes rsync bwlimit 102400

- **WHEN** `rate_limit` is `"100M"` (104857600 bytes/s)
- **THEN** `rsync --bwlimit=102400` is invoked (104857600 / 1024 = 102400)

### Requirement: Full backup and bitmap backup unaffected by rate_limit

`FileCopyBackupProvider.create_full_backup()` and `BitmapBackupProvider.transfer_missing()` SHALL NOT be affected by the `rate_limit` configuration field. They SHALL continue to use `qemu-img convert` as before.

#### Scenario: Full backup ignores rate_limit

- **WHEN** `rate_limit` is set to `"100M"` and `create_full_backup()` is called
- **THEN** `qemu-img convert` is called without any bandwidth-limiting flags

#### Scenario: Bitmap backup ignores rate_limit

- **WHEN** `rate_limit` is set to `"100M"` and bitmap `transfer_missing()` is called
- **THEN** `qemu-img convert -n nbd:...` is called without any bandwidth-limiting flags

### Requirement: Pre-flight rsync availability check

When `rate_limit` is not `"no"`, the environment validation step (`_validate_environment()`) SHALL check that `rsync` is in PATH. If `rsync` is missing, a WARNING is logged. This SHALL NOT block the pipeline.

#### Scenario: Rsync available — silent

- **WHEN** `rate_limit` is `"100M"` and `which rsync` succeeds
- **THEN** no WARNING is logged; validation passes

#### Scenario: Rsync unavailable — WARNING

- **WHEN** `rate_limit` is `"100M"` and `which rsync` returns non-zero
- **THEN** a WARNING is logged: "rsync not found — rate limiting disabled for target <path>"
- **AND** validation does not fail (pipeline continues)

#### Scenario: Rate limit not set — rsync not checked

- **WHEN** `rate_limit` is `"no"`
- **THEN** `which rsync` is never called during validation
