# Backup Verification

## Purpose

Post-transfer verification of backup integrity — ensures transferred qcow2 files are structurally valid and, optionally, content-identical to the source. Supports metadata-only checks (fast), structural checks via `qemu-img check`, and full `qemu-img compare` (thorough) to detect corruption introduced during transfer.

## Requirements

### Requirement: TargetConfig verify field

`TargetConfig.verify` controls post-transfer verification. Valid values: `"off"` (no verification), `"metadata"` (structural checks: format, virtual-size, corrupt-bit, backing-filename, dirty-size barrier), `"check"` (metadata + `qemu-img check` structural verification: errors, leaks, corruptions), `"compare"` (check + `qemu-img compare` chain-traversing content comparison). The `"hash"` and `"full"` values are deprecated — both ran `qemu-img compare`; they are now unified to `"compare"`. Existing configs with `"hash"` or `"full"` SHALL log a deprecation WARNING and be treated as `"compare"`. Default is `"metadata"`.

#### Scenario: Default verification is metadata

- **WHEN** `verify` is not set in the TOML
- **THEN** `TargetConfig.verify` defaults to `"metadata"`

#### Scenario: Explicit compare verification

- **WHEN** `verify = "compare"` is set in the TOML
- **THEN** `TargetConfig.verify` is `"compare"`

#### Scenario: Explicit check verification

- **WHEN** `verify = "check"` is set in the TOML
- **THEN** `TargetConfig.verify` is `"check"`
- **AND** `verify_bitmap_incremental()` runs `qemu-img check` in addition to metadata checks

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

`verify_full_backup(shell, target_path, verify_mode, source_path=None, expected_virtual_size=None) -> str | None` SHALL verify a standalone FULL backup file. Unlike verification for incrementals, this function only checks the target file's structural integrity and optionally compares against a source path.

Supported modes:
- `"off"`: Skip all checks
- `"metadata"` (M1): `qemu-img info` checks format is `"qcow2"` and no `"corrupt"` feature bit. Optionally checks `virtual-size` matches `expected_virtual_size`.
- `"check"` (M2): M1 + `qemu-img check --output=json` verifies `errors`, `leaks`, and `corruptions` are all zero.
- `"compare"` (M3): M1 + M2 + `qemu-img compare -q --force-share <source_path> <target_path>` for chain-traversing byte-level content comparison.
- `"hash"` (deprecated): Treated as `"compare"` with a deprecation WARNING.

Returns `None` on success or an error string on failure.

#### Scenario: verify_full_backup checks format is qcow2

- **WHEN** `verify_full_backup(shell, path, "metadata")` is called and `qemu-img info` reports `"format": "qcow2"`
- **THEN** the format check passes

#### Scenario: verify_full_backup detects corrupt bit

- **WHEN** `verify_full_backup(shell, path, "metadata")` is called and incompatible-features includes `"corrupt"`
- **THEN** the function returns `"verification failed: FULL backup has corrupt bit set — file is damaged"`

#### Scenario: verify_full_backup detects wrong format

- **WHEN** `verify_full_backup(shell, path, "metadata")` is called and `qemu-img info` reports `"format": "raw"`
- **THEN** the function returns `"verification failed: expected format qcow2, got raw"`


### Requirement: verify_bitmap_incremental function for incremental verification

`verify_bitmap_incremental(shell, source_path, delta_path, expected_backing, dirty_bytes, verify_mode) -> str | None` SHALL verify a bitmap-mode incremental delta (backing-chained qcow2). Checks when `verify_mode != "off"`: (a) format is `"qcow2"`, (b) `virtual-size` matches the source disk, (c) `backing-filename` equals `expected_backing`, (d) `actual-size` does not exceed `dirty_bytes × 2 + 64 MiB`. For `verify_mode` of `"check"` or `"compare"`, additionally runs `qemu-img check` for zero errors/leaks/corruptions. For `verify_mode` of `"compare"`, additionally runs `qemu-img compare` chain-traversing content comparison. `"hash"` and `"full"` are deprecated and treated as `"compare"`. Returns `None` on success or an error string on failure.

#### Scenario: Incremental passes metadata verification

- **WHEN** `verify_mode = "metadata"` and all checks pass
- **THEN** the function returns `None`

#### Scenario: Incremental fails backing-filename check

- **WHEN** the delta's `backing-filename` does not match the expected previous backup path
- **THEN** the function returns `"verification failed: backing-filename mismatch ..."`

#### Scenario: Incremental fails dirty-size regression barrier

- **WHEN** the delta's `actual-size` exceeds `dirty_bytes × 2 + 64 MiB`
- **THEN** the function returns `"verification failed: delta actual-size N exceeds dirty-data barrier ... — engine regressed to full copy"`

#### Scenario: Incremental compare on live source logs WARNING

- **WHEN** `verify_mode = "compare"` and the source is a running VM's active layer
- **THEN** a WARNING is logged: "verify=compare on running VM active layer ... — results may be unreliable"
- **AND** `qemu-img compare` is still executed with `--force-share`
