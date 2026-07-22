## REMOVED Requirements

### Requirement: Metadata verification after transfer
**Reason**: This requirement described the file-copy/rsync provider path (`FileCopyBackupProvider`, `verify_backup()`). The provider and helper are deleted. Bitmap incremental transfers verify via `verify_bitmap_incremental()` (see the `nbd-bitmap-backup` capability); FULL backups verify via `verify_full_backup()`.
**Migration**: Use `target.verify` tiers as specified in `nbd-bitmap-backup` (incrementals) and `backup-full-verification` (FULLs).

### Requirement: Full verification via qemu-img compare
**Reason**: Same file-copy-era provider path. For bitmap chains, content-level comparison is chain-traversing `qemu-img compare` inside `verify_bitmap_incremental()` at tiers `"hash"`/`"full"`.
**Migration**: Configure `verify = "hash"` or `verify = "full"`; semantics are defined in the `nbd-bitmap-backup` capability.

### Requirement: Hash verification tier (verify="hash")
**Reason**: The `verify_backup()` helper implementing SHA-256 post-transfer comparison is deleted (file-copy-specific: NBD-produced qcow2 files never share the source's digest, so SHA-256 comparison is meaningless for the remaining transfer mechanism).
**Migration**: The `"hash"` verify tier for bitmap chains means chain-traversing `qemu-img compare` in `verify_bitmap_incremental()` — see the `nbd-bitmap-backup` capability.

## MODIFIED Requirements

### Requirement: TargetConfig verify field

`TargetConfig` SHALL gain a `verify: str` field with default value `"metadata"`. Accepted values SHALL be `"off"` (no verification), `"metadata"` (structural checks), `"hash"` and `"full"` (content-level verification via chain-traversing `qemu-img compare`). The default SHALL NOT depend on any transfer mode.

#### Scenario: Default verification is metadata

- **WHEN** a TargetConfig is created without explicit `verify`
- **THEN** `target.verify` is `"metadata"`
