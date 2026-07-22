## REMOVED Requirements

### Requirement: SnapshotResult carries content_hash
**Reason**: `content_hash` was a SHA-256 hash of the raw qcow2 file. For file-copy backups (target = byte-identical copy of source), this worked. For NBD-created backups (target created via `pread`/`pwrite`), the qcow2 internal structure differs from the source — SHA-256 of raw files never matches. The field was removed in the `2026-07-23-unify-nbd-transfer` change. `SnapshotResult` no longer has a `content_hash` field.
**Migration**: No action needed. `SnapshotResult` never had the field in the NBD-only world. Old state files with `content_hash` are read-tolerant (silently ignored by `JsonStateManager`).

### Requirement: SnapshotInfo stores content_hash in persistent state
**Reason**: Same as above. `SnapshotInfo` no longer has a `content_hash` field. `JsonStateManager` silently ignores the key in old state files.
**Migration**: No action needed. Old state files load correctly — the `content_hash` key is silently ignored during deserialization.

### Requirement: Shared hash utility in qsnap.utils
**Reason**: `file_sha256()` in `qsnap/utils/hash.py` was deleted in the `2026-07-23-unify-nbd-transfer` change. The file `qsnap/utils/hash.py` no longer exists. No code references it.
**Migration**: No action needed. No code imports `file_sha256` or `qsnap.utils.hash`.
