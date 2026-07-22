# Backup Verification

## Purpose

Post-transfer verification of backup integrity — ensures copied qcow2 files are structurally valid and, optionally, byte-for-byte identical to the source. Supports metadata-only checks (fast) and full `qemu-img compare` (thorough) to detect corruption introduced during transfer.

## Requirements

### Requirement: TargetConfig verify field

`TargetConfig.verify` controls post-transfer verification. Valid values: `"off"` (no verification), `"metadata"` (structural checks: format, virtual-size, corrupt-bit, backing-filename, dirty-size barrier), `"compare"` (metadata + `qemu-img compare` chain-traversing content comparison). The `"hash"` and `"full"` values are deprecated — both ran `qemu-img compare`; they are now unified to `"compare"`. Existing configs with `"hash"` or `"full"` SHALL log a deprecation WARNING and be treated as `"compare"`. Default is `"metadata"`.

#### Scenario: Default verification is metadata

- **WHEN** `verify` is not set in the TOML
- **THEN** `TargetConfig.verify` defaults to `"metadata"`

#### Scenario: Explicit compare verification

- **WHEN** `verify = "compare"` is set in the TOML
- **THEN** `TargetConfig.verify` is `"compare"`

#### Scenario: Deprecated hash treated as compare

- **WHEN** `verify = "hash"` is set in the TOML
- **THEN** a WARNING is logged naming the deprecated value
- **AND** `TargetConfig.verify` is treated as `"compare"`

#### Scenario: Deprecated full treated as compare

- **WHEN** `verify = "full"` is set in the TOML
- **THEN** a WARNING is logged naming the deprecated value
- **AND** `TargetConfig.verify` is treated as `"compare"`

#### Scenario: Invalid verify value raises ConfigError

- **WHEN** `verify = "invalid"` is set in the TOML
- **THEN** `ConfigError` is raised

### Requirement: verify_full_backup function for standalone FULL verification

`verify_full_backup(shell, target_path, verify_mode, source_path=None, expected_virtual_size=None) -> str | None` verifies a standalone FULL backup file without source comparison. Unlike `verify_backup()` which compares source and target, this function only checks the target file's structural integrity.

Supported modes:
- `"metadata"` (M1): `qemu-img info` checks format is `"qcow2"` and no `"corrupt"` feature bit
- `"check"` (M2): M1 + `qemu-img check` verifies zero errors and leaks
- `"hash"` (M3): M1 + M2 + `qemu-img compare -q --force-share <source_path> <target_path>` for byte-level content comparison. Note: M3 for `verify_full_backup` uses `qemu-img compare` (not SHA-256) to correctly compare virtual-disk content across backing chains.
- `"off"`: Skip all checks

Returns `None` on success or an error string on failure.
