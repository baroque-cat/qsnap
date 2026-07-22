## REMOVED Requirements

### Requirement: verify_backup supports verify="hash" mode
**Reason**: The `verify_backup()` helper is deleted together with `FileCopyBackupProvider` — SHA-256 post-transfer comparison was only meaningful for rsync's byte-identical copies. NBD-produced qcow2 files (FULL exports, dirty-block deltas) have different internal structure than the source, so digest comparison cannot work.
**Migration**: Content-level verification of backups uses chain-traversing `qemu-img compare` — `verify_bitmap_incremental()` for incrementals (tiers `"hash"`/`"full"`, see `nbd-bitmap-backup`) and `verify_full_backup()` M3 for FULLs (see `backup-full-verification`). `content_hash` on `SnapshotResult`/`SnapshotInfo` is retained (computed at snapshot creation time) for state-level consistency features.
