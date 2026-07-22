## REMOVED Requirements

### Requirement: SnapshotResult content_hash field

**Reason**: `content_hash` (SHA-256 of the raw qcow2 file) is semantically incorrect for NBD-created backups — the qcow2 internal structure differs from the source snapshot, so SHA-256 of raw files never matches. The `"hash"` verify tier was repurposed to `qemu-img compare` (which traverses backing chains). `content_hash` has zero consumers.

**Migration**: The field is removed from `SnapshotResult`. `ExternalSnapshotProvider.create()` no longer computes it. `JsonStateManager` no longer serializes it. Old state files with `content_hash` still load (field is silently ignored via `if "content_hash" in d`).

### Requirement: SnapshotInfo content_hash field

**Reason**: Same as above — `SnapshotInfo.content_hash` was persisted in state but never consumed after `verify_backup()` was deleted.

**Migration**: The field is removed from `SnapshotInfo`. State files no longer contain it. Old state files load fine (field ignored).
