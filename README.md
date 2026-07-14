# qsnap

QEMU/KVM snapshot and backup orchestration for qcow2 images on any filesystem (XFS, ext4, etc.), inspired by [btrbk](https://github.com/digint/btrbk).

qsnap manages external disk-only snapshots via `virsh`, detects whether a VM disk has changed, enforces retention policies, performs incremental backups to separate storage, and maintains backing chain integrity via `blockcommit`.

## Features

- **External snapshots** — disk-only, no-metadata snapshots via `virsh snapshot-create-as`
- **Change detection** — skip snapshot creation when the disk hasn't changed (`onchange` mode)
- **Incremental backups** — file-copy or NBD bitmap-based backup to remote targets
- **Periodic full backups** — standalone qcow2 via `qemu-img convert`, with optional compression
- **Backup verification** — four tiers: `off`, `metadata`, `hash` (SHA-256), or `full` (`qemu-img compare`)
- **Retention policies** — time-based retention with hourly/daily/weekly/monthly/yearly buckets
- **Minimum retention floor** — `preserve_min` keeps recent snapshots/backups regardless of bucket counts
- **Schedule preview** — `--print-schedule` / `-S` simulates retention against synthetic timestamps
- **Backing chain integrity** — automatic `blockcommit` or `qemu-img commit` to merge old snapshots
- **Deferred operations** — AppArmor/SELinux-blocked blockcommits queued and retried on VM shutdown
- **Pre-flight validation** — environment checks before every pipeline run
- **Quiesce support** — optional `--quiesce` flag for filesystem-consistent snapshots

## Installation

```bash
pip install git+https://github.com/baroque-cat/qsnap.git
```

Or clone and install with Poetry:

```bash
git clone https://github.com/baroque-cat/qsnap.git
cd qsnap
poetry install
```

## Quick Start

1. Create a configuration file at `/etc/qsnap/qsnap.toml`:

```toml
timestamp_format = "long"
preserve_day_of_week = "monday"
state_dir = "/var/lib/qsnap/state"

# Default retention: keep 24 hourly, 7 daily, 4 weekly, 12 monthly, 1 yearly
snapshot_preserve = "24h 7d 4w 12m 1y"
target_preserve = "48h 14d 8w 12m 1y"

# Minimum retention floor: always keep last 2 hours of snapshots/backups
# regardless of bucket counts above
snapshot_preserve_min = "2h"
target_preserve_min = "4h"

[[vm]]
name = "debiantest"
base_image = "/var/lib/libvirt/images/debiantest.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/debiantest"

  [[vm.target]]
  path = "/mnt/backup/debiantest"
  incremental = true
  verify = "hash"
```

2. Run the full pipeline (snapshot + backup + retention):

```bash
qsnap run debiantest
```

3. Preview what retention will keep before running:

```bash
qsnap run debiantest --print-schedule
```

4. Or run individual steps:

```bash
qsnap snapshot debiantest    # create snapshots only
qsnap backup debiantest      # transfer backups only
qsnap prune debiantest       # retention + cleanup only
```

## Commands Reference

| Command | Description |
|---|---|
| `qsnap run [vm]` | Full pipeline: snapshot -> backup -> retention |
| `qsnap snapshot [vm]` | Create snapshots only |
| `qsnap backup [vm]` | Transfer backups to targets only |
| `qsnap prune [vm]` | Apply retention policies and cleanup only |
| `qsnap list snapshots [vm]` | List snapshots (use `--tree` for backing chain view) |
| `qsnap list backups [vm]` | List backups across all targets |
| `qsnap list latest [vm]` | Show most recent snapshot per VM |
| `qsnap list config` | Show parsed VM configurations |
| `qsnap stats [vm]` | Show snapshot/backup counts and sizes |
| `qsnap check [vm]` | Verify backing-chain integrity (`--deep` for corruption check) |
| `qsnap restore <name> <dir> [vm]` | Restore a backup chain to a directory |

### Global Flags

| Flag | Description |
|---|---|
| `--config`, `-c` | Path to TOML config file (default: `/etc/qsnap/qsnap.toml`) |
| `--dry-run`, `-n` | Print planned actions without executing |
| `--preserve` | Skip all deletion (snapshots and backups) |
| `--preserve-snapshots` | Skip snapshot deletion only |
| `--preserve-backups` | Skip backup deletion only |
| `--verbose`, `-v` | Enable DEBUG-level logging |
| `--quiet`, `-q` | Enable ERROR-level logging only |
| `--loglevel`, `-l` | Set log level explicitly |
| `--format` | Output format: `table` (default), `long`, `raw`, `col:<columns>` |
| `-L` | Shortcut for `--format long` |
| `--lockfile` | Override lockfile path |

### Action-Specific Flags

The `run`, `snapshot`, `backup`, and `prune` subcommands accept these additional flags:

| Flag | Description |
|---|---|
| `--print-schedule`, `-S` | Print the retention schedule simulation and exit (no pipeline execution) |
| `--timer` | Log the schedule summary at INFO level, then continue with normal pipeline execution. Designed for cron/systemd timer use. |

## Configuration Reference

All configuration is in TOML format. Keys are organized in three levels: **global** (top-level), **per-VM** (`[[vm]]`), and **per-target** (`[[vm.target]]`). Values at lower levels override the global defaults.

### Global Keys

| Key | Type | Default | Description |
|---|---|---|---|
| `timestamp_format` | string | `"short"` | Snapshot name format: `"short"` (`20250714T130000`) or `"long"` (`2025-07-14T13:00:00`) |
| `preserve_day_of_week` | string | `"monday"` | Day used as the weekly bucket boundary: `monday`-`sunday` |
| `state_dir` | string | `/var/lib/qsnap/state` | Directory for JSON state files (snapshot records, deferred operations) |
| `lockfile` | string | `/var/lock/qsnap.lock` | Lockfile path to prevent concurrent runs |
| `snapshot_preserve` | string | none | Retention policy for snapshots, e.g. `"24h 7d 4w 12m 1y"` |
| `snapshot_preserve_min` | string | `"all"` | Minimum retention floor for snapshots, e.g. `"2h"`. Keeps recent snapshots regardless of bucket counts |
| `target_preserve` | string | none | Retention policy for backups on targets, e.g. `"48h 14d 8w 12m 1y"` |
| `target_preserve_min` | string | `"all"` | Minimum retention floor for backups on targets |

### VM Keys

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | string | **required** | VM name as known to libvirt |
| `base_image` | string | **required** | Path to the base qcow2 image |
| `snapshot_dir` | string | **required** | Directory where snapshot qcow2 files are stored |
| `snapshot_create` | string | `"always"` | When to create snapshots: `"always"` or `"onchange"` (skip if disk allocation unchanged) |
| `snapshot_preserve` | string | inherits global | VM-specific snapshot retention (overrides global) |
| `snapshot_preserve_min` | string | inherits global | VM-specific minimum retention floor (overrides global) |
| `target_preserve` | string | inherits global | VM-specific backup retention (overrides global) |
| `target_preserve_min` | string | inherits global | VM-specific minimum backup retention floor (overrides global) |
| `snapshot_quiesce` | bool | `false` | Use `--quiesce` for filesystem-consistent snapshots |
| `lifecycle_mode` | string | `"virsh"` | How to merge snapshots: `"virsh"` (blockcommit) or `"qemu-img"` (commit) |
| `change_detection_mode` | string | `"allocation-size"` | How to detect disk changes: `"allocation-size"` or `"none"` |
| `disks` | list | `null` | Explicit list of disk paths to snapshot (default: all VM disks) |

### Target Keys

| Key | Type | Default | Description |
|---|---|---|---|
| `path` | string | **required** | Directory where backup qcow2 files are stored |
| `incremental` | bool | `true` | Whether to do incremental backups (file-copy with backing chain) |
| `incremental_mode` | string | `"file-copy"` | Backup method: `"file-copy"` or `"nbd-bitmap"` |
| `target_preserve` | string | inherits VM/global | Target-specific backup retention (overrides VM and global) |
| `target_preserve_min` | string | inherits VM/global | Target-specific minimum backup retention floor |
| `verify` | string | `"metadata"` | Backup verification tier: `"off"`, `"metadata"`, `"hash"`, or `"full"` |
| `full_every` | string | `"0d"` (disabled) | Interval for periodic full backups, e.g. `"7d"` for weekly. See [Full Backups](#full-backups) |
| `full_compress` | bool | `false` | Compress full backup with zlib (`-c` flag on `qemu-img convert`) |

## Retention Policy Guide

qsnap uses time-based retention with configurable buckets. The `snapshot_preserve` and `target_preserve` strings specify how many snapshots/backups to keep in each time bucket:

```
"24h 7d 4w 12m 1y"
│    │  │  │   │
│    │  │  │   └─ yearly: keep 1
│    │  │  └───── monthly: keep 12
│    │  └──────── weekly: keep 4
│    └─────────── daily: keep 7
└──────────────── hourly: keep 24
```

### Bucket Semantics

- **hourly** (`h`) — keeps the earliest snapshot in each hour
- **daily** (`d`) — keeps the earliest snapshot in each calendar day
- **weekly** (`w`) — keeps the earliest snapshot in each ISO week (boundary set by `preserve_day_of_week`)
- **monthly** (`m`) — keeps the earliest snapshot in each calendar month
- **yearly** (`y`) — keeps the earliest snapshot in each calendar year

Within each bucket, the count selects the N most recent bucket groups. For example, `7d` keeps snapshots from the 7 most recent days, one per day.

### Minimum Retention Floor (`preserve_min`)

The `snapshot_preserve_min` and `target_preserve_min` keys provide a safety net. They ensure that recent snapshots/backups are never removed, even if the bucket counts would otherwise remove them.

| `preserve_min` value | Behavior |
|---|---|
| `"all"` (default) | Never remove any snapshot/backup (only bucket counts apply) |
| `"latest"` | Always keep at least the most recent snapshot/backup |
| `"2h"` | Keep all snapshots/backups from the last 2 hours |
| `"7d"` | Keep all snapshots/backups from the last 7 days |
| `"0h"` | No minimum floor — bucket counts alone determine retention |

**Example:** With `snapshot_preserve = "24h 7d 4w"` and `snapshot_preserve_min = "2h"`, all snapshots from the last 2 hours are always kept, plus the bucket-selected ones from the last 24 hours, 7 days, and 4 weeks.

### Example Scenarios

**Home host** — conservative, keep everything recent:

```toml
snapshot_preserve = "48h 14d 8w 6m 1y"
snapshot_preserve_min = "12h"
target_preserve = "72h 30d 12w 12m 1y"
target_preserve_min = "24h"
```

**Server** — aggressive pruning, long-term monthly/yearly:

```toml
snapshot_preserve = "24h 7d 4w 12m 1y"
snapshot_preserve_min = "2h"
target_preserve = "48h 14d 8w 24m 2y"
target_preserve_min = "4h"
```

### Schedule Preview

Before running the pipeline, preview what retention will keep:

```bash
qsnap run myvm --print-schedule
# or
qsnap run myvm -S
```

This simulates retention against synthetic timestamps (one per hour over the retention window) and shows:
- The configured policy
- How many snapshots/backups would be kept vs. removed
- Per-bucket breakdown (hourly, daily, weekly, etc.)

For cron/systemd timer use, the `--timer` flag logs the schedule summary at INFO level before executing the pipeline:

```bash
qsnap run --timer
```

## Full Backups

By default, qsnap creates **incremental** backups: each backup file references the previous one via the qcow2 backing chain. This is storage-efficient but means restoring requires the entire chain.

**Periodic full backups** create a standalone qcow2 file with no backing dependencies, using `qemu-img convert`. This provides:
- A self-contained restore point independent of the incremental chain
- A new backing anchor for subsequent incrementals
- Protection against chain corruption

### Configuration

Set `full_every` on a target to enable periodic full backups:

```toml
[[vm]]
name = "myvm"
base_image = "/var/lib/libvirt/images/myvm.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/myvm"

  [[vm.target]]
  path = "/mnt/backup/myvm"
  incremental = true
  full_every = "7d"          # create a full backup every 7 days
  full_compress = true       # compress with zlib (smaller, slower)
```

| Key | Default | Description |
|---|---|---|
| `full_every` | `"0d"` (disabled) | Interval between full backups. Format: `<count><unit>` where unit is `h` (hours), `d` (days), `w` (weeks), `m` (months), `y` (years). `"0d"` disables full backups. |
| `full_compress` | `false` | When `true`, passes `-c` to `qemu-img convert` for zlib compression. Reduces file size at the cost of conversion time. |

### How It Works

1. Before each incremental transfer, qsnap checks the last full backup timestamp via `IStateManager`.
2. If the `full_every` interval has elapsed (or no previous full backup exists), qsnap calls `qemu-img convert` to create a standalone qcow2.
3. The full backup is named `vm.FULL.YYYYMMDD.qcow2` (with `_N` suffix on same-day collisions).
4. Subsequent incremental backups are rebased to the FULL anchor instead of the source snapshot backing file.
5. The conversion is atomic: `qemu-img convert` writes to a `.tmp` file, which is renamed only on success.

### When to Enable

- **Long-running VMs with many incrementals** — a full backup every 1-2 weeks prevents chain bloat
- **Compliance/audit requirements** — a standalone full backup is easier to verify and restore
- **Unreliable backup targets** — if the target might lose files, a periodic full reduces chain dependency

### `full_compress` Trade-off

| | Uncompressed (`false`) | Compressed (`true`) |
|---|---|---|
| Speed | Faster conversion | Slower (zlib overhead) |
| Size | Full size | ~30-50% smaller (data-dependent) |
| Compatibility | Standard qcow2 | Standard qcow2 (zlib clusters) |
| Restore | Direct | Direct (qemu-img handles transparently) |

## Backup Verification

qsnap offers four verification tiers, configured per target via the `verify` key:

| Tier | Description | Overhead | When to use |
|---|---|---|---|
| `"off"` | No verification after backup | None | When target is trusted and speed is critical |
| `"metadata"` | Compare qcow2 metadata: format, virtual size, actual size (tolerance) | Low | Default. Catches format mismatches and size corruption |
| `"hash"` | Compute SHA-256 of the qcow2 file at snapshot creation, compare after transfer | Medium (hash at creation + hash at target) | Detects silent bit-rot or transfer corruption |
| `"full"` | Metadata check + `qemu-img compare` against the source | High (reads entire source and target) | Maximum integrity, detects all corruption |

### How `hash` Verification Works

1. When a snapshot is created, qsnap computes the SHA-256 of the snapshot qcow2 file (reading in 8 MB chunks).
2. The hash is stored in `SnapshotInfo.content_hash` and persisted in the state file.
3. After transferring the backup to the target, qsnap computes the SHA-256 of the target file and compares it to the stored hash.
4. If the hashes match, verification passes. If they differ, the backup is flagged as failed.

### Choosing a Tier

- **Home host with USB drive** — `"metadata"` is sufficient; the transfer is local and fast
- **Server with network target** — `"hash"` provides integrity without the overhead of `qemu-img compare`
- **Compliance/audit** — `"full"` gives maximum assurance at the cost of reading both files

## Restore

To restore a backup chain to a directory:

```bash
qsnap restore <snapshot_name> <restore_dir> [vm]
```

This copies the backup chain (full or incremental) from the target to the specified directory, resolving backing file references.

### Manual Restore

For manual restore from backup files:

1. **Identify the chain** — the most recent backup file references its backing file. Use `qemu-img info --backing-chain <file.qcow2>` to see the full chain.

2. **Copy the chain** — copy all files in the chain to your restore directory:

```bash
TARGET="/mnt/backup/myvm"
RESTORE="/tmp/restore"
mkdir -p "$RESTORE"
cp "$TARGET"/myvm.20250714T130000*.qcow2 "$RESTORE"/
# Copy all backing files in the chain
cp "$TARGET"/myvm.FULL.*.qcow2 "$RESTORE"/
cp "$TARGET"/myvm.20250713T130000*.qcow2 "$RESTORE"/
# ... etc for each file in the chain
```

3. **Fix backing references** — rebase the chain to use relative paths in the restore directory:

```bash
cd "$RESTORE"
qemu-img rebase -u -b ./myvm.FULL.20250713.qcow2 myvm.20250713T130000.qcow2
qemu-img rebase -u -b ./myvm.20250713T130000.qcow2 myvm.20250714T130000.qcow2
```

4. **Convert to a flat image** (optional) — if you need a standalone image without backing dependencies:

```bash
qemu-img convert -O raw myvm.20250714T130000.qcow2 restored.img
```

5. **Boot the restored VM** — attach the restored image to a new or existing VM definition.

## Example Configurations

### Home Host with USB Backup Target

A desktop machine running a few VMs, backing up to a hot-plugged USB drive. Conservative retention, hash verification:

```toml
timestamp_format = "long"
preserve_day_of_week = "monday"
state_dir = "/var/lib/qsnap/state"
lockfile = "/var/lock/qsnap.lock"

# Keep lots of recent snapshots, prune older ones
snapshot_preserve = "48h 14d 8w 6m 1y"
snapshot_preserve_min = "12h"

# Backups: keep longer on the USB drive
target_preserve = "72h 30d 12w 12m 1y"
target_preserve_min = "24h"

[[vm]]
name = "desktop-vm"
base_image = "/var/lib/libvirt/images/desktop-vm.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/desktop-vm"
snapshot_create = "onchange"    # skip if disk hasn't changed
snapshot_quiesce = true        # filesystem-consistent snapshots

  [[vm.target]]
  path = "/mnt/usb-backup/desktop-vm"
  incremental = true
  verify = "hash"              # SHA-256 verification
  full_every = "14d"           # full backup every 2 weeks
  full_compress = false        # USB is slow, skip compression overhead
```

### Server with Persistent Network Target

A production server backing up to a persistent NFS/NAS mount. Aggressive pruning, full verification, weekly full backups with compression:

```toml
timestamp_format = "long"
preserve_day_of_week = "monday"
state_dir = "/var/lib/qsnap/state"
lockfile = "/var/lock/qsnap.lock"

# Server: prune aggressively, keep long-term monthly/yearly
snapshot_preserve = "24h 7d 4w 12m 1y"
snapshot_preserve_min = "2h"

target_preserve = "48h 14d 8w 24m 2y"
target_preserve_min = "4h"

[[vm]]
name = "prod-server"
base_image = "/var/lib/libvirt/images/prod-server.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/prod-server"
snapshot_quiesce = true
lifecycle_mode = "virsh"

  [[vm.target]]
  path = "/mnt/nas-backup/prod-server"
  incremental = true
  verify = "full"              # maximum integrity via qemu-img compare
  full_every = "7d"            # weekly full backup
  full_compress = true         # compress to save NAS space

[[vm]]
name = "db-server"
base_image = "/var/lib/libvirt/images/db-server.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/db-server"
snapshot_quiesce = true

  # Different target with hash verification (faster than full for large DBs)
  [[vm.target]]
  path = "/mnt/nas-backup/db-server"
  incremental = true
  verify = "hash"
  full_every = "7d"
  full_compress = true
```

### Minimal Configuration

The simplest possible config — one VM, one target, no retention (snapshots accumulate):

```toml
[[vm]]
name = "test-vm"
base_image = "/var/lib/libvirt/images/test-vm.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/test-vm"

  [[vm.target]]
  path = "/tmp/backups/test-vm"
```

## Requirements

- Python 3.11+
- libvirt + virsh (libvirt 6.0+ for NBD bitmap backup)
- qemu-img
- QEMU/KVM hypervisor
