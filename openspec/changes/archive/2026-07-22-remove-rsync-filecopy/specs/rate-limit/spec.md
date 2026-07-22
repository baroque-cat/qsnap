## REMOVED Requirements

### Requirement: Rate limit config field on GlobalConfig
**Reason**: `rate_limit` existed solely to throttle `rsync --bwlimit` file-copy transfers. rsync and `FileCopyBackupProvider` are removed; NBD/libnbd is the sole transfer mechanism and has no throttling in this change.
**Migration**: Remove `rate_limit` from TOML configs. If present, the key is ignored with a deprecation WARNING. No replacement exists; a future cgroups-based mechanism would be a new capability.

### Requirement: Rate limit config field on TargetConfig
**Reason**: Same as above — target-level `rate_limit` only fed `rsync --bwlimit`.
**Migration**: Remove `rate_limit` from target sections. Ignored with a deprecation WARNING if present.

### Requirement: GlobalConfig immutability includes rate_limit
**Reason**: The `rate_limit` field is removed from `GlobalConfig`; immutability of a deleted field is moot.
**Migration**: None — `GlobalConfig` remains frozen as a whole.

### Requirement: TargetConfig immutability includes rate_limit
**Reason**: The `rate_limit` field is removed from `TargetConfig`.
**Migration**: None — `TargetConfig` remains frozen as a whole.

### Requirement: Rsync used for file-copy transfers when rate_limit is set
**Reason**: rsync is eliminated as a transfer mechanism; incremental backups are NBD/libnbd dirty-block transfers.
**Migration**: None — incremental transfers require a running VM and libvirt >= 7.2.

### Requirement: Rsync --partial enables resume-after-interruption
**Reason**: rsync-specific mechanics. NBD transfers use atomic `.tmp` → final rename; interrupted transfers leave `.tmp` files cleaned by pre-flight cleanup.
**Migration**: None — resume semantics are replaced by restart-from-scratch with atomic output.

### Requirement: Fallback to cp when rsync is unavailable with rate_limit set
**Reason**: Neither rsync nor cp is used anymore; there is no file-copy fallback of any kind.
**Migration**: None.

### Requirement: Transfer logging for rate-limited transfers
**Reason**: Rate-limited transfers no longer exist.
**Migration**: None — standard transfer logging of the NBD path applies.

### Requirement: Anomalous throughput warning
**Reason**: The warning compared actual throughput against the configured `rate_limit`; the field no longer exists.
**Migration**: None.

### Requirement: Rate limit is parsed with binary Ki/Mi/Gi/Ti suffixes
**Reason**: `parse_rate_limit()` is removed from `qsnap/utils/parsing.py` together with the field.
**Migration**: None.

### Requirement: Rsync bwlimit receives KiB/s value
**Reason**: `rate_limit_to_kib()` is removed; no `--bwlimit` consumer remains.
**Migration**: None.

### Requirement: Full backup and bitmap backup unaffected by rate_limit
**Reason**: Moot — the `rate_limit` field itself is removed.
**Migration**: None.

### Requirement: Pre-flight rsync availability check
**Reason**: rsync is no longer required in PATH; the transfer dependency is `python3-libnbd`, covered by the `env-validation` capability.
**Migration**: None — uninstalling rsync no longer affects qsnap.
