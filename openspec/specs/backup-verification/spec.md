# Backup Verification

## Purpose

Post-transfer verification of backup integrity — ensures copied qcow2 files are structurally valid and, optionally, byte-for-byte identical to the source. Supports metadata-only checks (fast) and full `qemu-img compare` (thorough) to detect corruption introduced during transfer.

## Requirements

### Requirement: TargetConfig verify field

`TargetConfig` SHALL have a `verify: str` field with default value `"metadata"`. Accepted values SHALL be `"off"` (no verification), `"metadata"` (structural checks: format, virtual-size, backing-filename, dirty-size barrier for bitmap incrementals), `"hash"` and `"full"` (content-level verification via chain-traversing `qemu-img compare` — see the `nbd-bitmap-backup` capability). The field SHALL be immutable (`frozen=True`). The default SHALL NOT depend on any transfer mode.

#### Scenario: Default verification is metadata

- **WHEN** a TargetConfig is created without explicit `verify`
- **THEN** `target.verify` is `"metadata"`

### Requirement: verify_full_backup function for standalone FULL verification

`verify_full_backup(shell, target_path, verify_mode, source_path=None, expected_virtual_size=None) -> str | None` verifies a standalone FULL backup file without source comparison. Unlike `verify_backup()` which compares source and target, this function only checks the target file's structural integrity.

Supported modes:
- `"metadata"` (M1): `qemu-img info` checks format is `"qcow2"` and no `"corrupt"` feature bit
- `"check"` (M2): M1 + `qemu-img check` verifies zero errors and leaks
- `"hash"` (M3): M1 + M2 + `qemu-img compare -q --force-share <source_path> <target_path>` for byte-level content comparison. Note: M3 for `verify_full_backup` uses `qemu-img compare` (not SHA-256) to correctly compare virtual-disk content across backing chains.
- `"off"`: Skip all checks

Returns `None` on success or an error string on failure.
