## REMOVED Requirements

### Requirement: file_sha256 utility

**Reason**: `file_sha256()` in `qsnap/utils/hash.py` has zero consumers after `verify_backup()` was deleted and `content_hash` is removed. The entire `hash.py` file is deleted.

**Migration**: No replacement. The `"compare"` verify tier uses `qemu-img compare` (chain-traversing content comparison), not SHA-256 of raw files.
