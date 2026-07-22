# Backup Hash Verification (Historical Record)

## Purpose

**This capability has been removed.** SHA-256 hash verification was a file-copy-era feature that computed the SHA-256 digest of a qcow2 file at snapshot creation time and compared it after transfer. For NBD-created backups (target created via `pread`/`pwrite`), the qcow2 internal structure differs from the source — SHA-256 of raw files never matches. The `content_hash` field was removed from `SnapshotResult` and `SnapshotInfo`. The `file_sha256()` utility in `qsnap/utils/hash.py` was deleted. The file `qsnap/utils/hash.py` no longer exists.

This spec is retained as a historical record. No code references `content_hash`, `file_sha256`, or `qsnap.utils.hash`. Old state files with `content_hash` keys are read-tolerant (silently ignored by `JsonStateManager`).

## Requirements

*All requirements in this capability have been removed. The following are retained for historical context only.*

### Requirement: SnapshotResult carries content_hash (REMOVED)

`SnapshotResult` no longer has a `content_hash` field. The field was removed because SHA-256 hash verification is incompatible with NBD-created backups — the qcow2 internal structure of an NBD-pulled backup differs from the source file, so raw-file hashing never matches.

**Migration**: No action needed. `SnapshotResult` never had the field in the NBD-only world. Old state files with `content_hash` are silently ignored.

### Requirement: SnapshotInfo stores content_hash in persistent state (REMOVED)

`SnapshotInfo` no longer has a `content_hash` field. `JsonStateManager` silently ignores the key in old state files.

**Migration**: No action needed. Old state files load correctly — the `content_hash` key is silently ignored during deserialization.

### Requirement: Shared hash utility in qsnap.utils (REMOVED)

`file_sha256()` in `qsnap/utils/hash.py` was deleted. The file `qsnap/utils/hash.py` no longer exists. No code references it.

**Migration**: No action needed. No code imports `file_sha256` or `qsnap.utils.hash`.


