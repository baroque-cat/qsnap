## Why

Currently FULL backups are triggered only by the single highest active retention bucket (e.g., `1y` means one FULL per year), leading to chains of up to 365 incrementals. A single corrupt incremental in a year-long chain makes restoration impossible. Additionally, there is no mechanism to create an independent fork of a VM from a snapshot — users must manually copy files and craft libvirt XML. Both gaps increase fragility and operational burden.

## What Changes

### Multi-level FULL anchors (automatic mode)
- **ALL active retention buckets trigger FULL backups**, not just the highest. A policy like `"48h 14d 8w 12m 1y"` creates FULLs at weekly, monthly, AND yearly boundaries, capping incremental chains at ~7 days.
- The existing highest-active-bucket behavior becomes the fallback for simple policies like `"24h 7d 1w 0m 0y"`.
- `IStateManager.get_full_backups()` (already returns all FULL records with bucket_level) is used instead of `get_last_full_backup()` for per-bucket period comparison.

### Manual F-syntax (override mode)
- Config syntax `"24h 7Fd 4w 12m 1y"` marks a bucket as a FULL anchor with the `F` prefix. When ANY F-anchor is present, automatic mode is disabled — FULLs are created ONLY at F-marked levels.
- Multiple F-anchors supported: `"24Fh 7Fd 4Fw 12Fm 1Fy"` (FULL at every level).
- `RetentionPolicy` frozen dataclass gains per-bucket boolean fields (`anchor_hourly`, `anchor_daily`, etc.).
- `_parse_preserve()` extended to parse `F` prefix: regex `(\d+)(F?)([hdwmy])`.

### Fork mode
- New `qsnap fork <snapshot-name> --as-vm <new-vm-name>` command creates a standalone, self-sufficient qcow2 via `qemu-img convert` from the selected snapshot, then defines a new libvirt VM.
- The fork is immune to parent snapshot deletion — the converted file has no backing dependencies.
- `qsnap deploy <backup-name> --as-vm <new-vm-name>` extends fork semantics to archived backups.
- Fork can operate from snapshots (IStateManager) or backups (backup providers), reusing existing restore resolution logic.

## Capabilities

### New Capabilities
- `multi-level-full-anchors`: Automatic FULL backup creation at all active retention bucket boundaries (not just the highest), dramatically reducing maximum incremental chain length.
- `full-anchor-syntax`: Manual F-prefix syntax (`"7Fd"`) for explicit per-bucket FULL anchor control, overriding automatic behavior.
- `fork-mode`: One-command creation of a fully independent VM from any snapshot or backup, using `qemu-img convert` to produce a standalone, writable disk image with no backing chain dependencies.

### Modified Capabilities
- `periodic-full-backup`: **BREAKING** — `_should_create_bucket_full` behavior changes from highest-active-bucket to all-active-buckets. Policies with a single active bucket are unaffected. Policies with multiple active buckets (e.g., `"8w 12m 1y"`) will now produce FULLs at each level instead of only the highest.
- `config-model`: `RetentionPolicy` gains five new boolean fields: `anchor_hourly`, `anchor_daily`, `anchor_weekly`, `anchor_monthly`, `anchor_yearly`. All default to `False`. Config serialization/deserialization must round-trip these fields.
- `config-parsing`: `_parse_preserve()` must handle the `F` prefix in bucket tokens. Validation must reject F-anchors on buckets with zero count.
- `core-orchestrator`: `_should_create_bucket_full()` logic changes significantly. New `Core.fork()` method added. `_backup_target()` rebase target selection remains "most recent FULL" but now must consider multiple FULLs from different bucket levels.
- `restore-command`: Fork mode reuses restore's chain resolution and directory preparation logic. `Core.restore()` may be refactored to expose shared primitives.
- `cli-interface`: New `fork` and `deploy` subcommands with `--as-vm`, `--storage`, `--add-to-config` flags.

## Impact

- **Config format**: Backward-compatible — policies without `F` prefix parse identically. With `F`, the token is backward-incompatible with older qsnap versions (parse error).
- **IStateManager JSON format**: No migration needed — `_full_backups.json` already stores `bucket_level` per FULL record and supports multiple entries per target.
- **Core._should_create_bucket_full()**: Signature unchanged. Internal logic rewritten. Static method — no cascade effects on caller.
- **Core._backup_target()**: Minor change — uses `get_full_backups()` instead of `get_last_full_backup()`, then filters by bucket_level. Rebase target selection unchanged (always most recent FULL).
- **Core.restore()**: May be refactored to extract `_resolve_snapshot()` and `_restore_chain()` as reusable methods for fork.
- **New dependencies**: `uuid` stdlib module for VM UUID generation in fork.
- **Systemd units**: Unchanged. Fork and deploy are interactive commands, not scheduled.
