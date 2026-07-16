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
| `qsnap fork <snapshot> --as-vm <name>` | Create a standalone VM from a snapshot or backup |
| `qsnap deploy <backup> --as-vm <name>` | Deploy a backup as a standalone VM |

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
| `compress` | bool | `true` | Compress full backups with zlib (`-c` flag on `qemu-img convert`). Overridden per-VM/target |

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
| `compress` | bool | `true` | Compress full backups with zlib. Inherits from global → VM → target |
| `copy_base` | bool | `false` | Copy the base image to the target on first backup. When `false` (default), the first backup is always a FULL via `qemu-img convert` |

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

**Full backups** create a standalone qcow2 file with no backing dependencies, using `qemu-img convert`. This provides:
- A self-contained restore point independent of the incremental chain
- A new backing anchor for subsequent incrementals
- Protection against chain corruption

### Multi-Level FULL Anchors (automatic mode)

**ALL active retention buckets trigger FULL backups** — not just the highest. A policy like `"48h 14d 8w 12m 1y"` creates FULLs at **weekly, monthly, AND yearly** boundaries, capping incremental chains at ~7 days (the shortest active bucket above hourly/daily). This dramatically reduces the risk of chain corruption making restoration impossible.

For each active bucket, qsnap checks whether the current snapshot falls in a new period compared to the most recent FULL with matching `bucket_level`. Period keys are:
- **yearly** — `YYYY`
- **monthly** — `YYYYMM`
- **weekly** — `YYYY-WNN` (ISO week)
- **daily** — `YYYYMMDD`
- **hourly** — `YYYYMMDDHH`

**Short-circuit:** at most ONE FULL is created per snapshot. Buckets are checked in descending order (yearly → monthly → weekly → daily → hourly), and the first bucket whose period has changed wins.

| Policy | Active Buckets | FULL Frequency | Max Incremental Chain |
|---|---|---|---|
| `"48h 14d 8w 12m 1y"` | h, d, w, m, y | Weekly + Monthly + Yearly | ~7 days |
| `"48h 14d 8w"` | h, d, w | Weekly | ~7 days |
| `"24h 7d"` | h, d | Daily | ~1 day |
| `"1y"` | y | Yearly | ~365 days |
| (all zero) | — | No FULLs | Unlimited |

Single-bucket policies preserve the old highest-only behavior. A policy with only `"1y"` active creates exactly one FULL per year.

### Manual F-Syntax (override mode)

The `F` prefix on a bucket token marks it as a **FULL anchor**. When ANY F-anchor is present, automatic multi-level mode is **disabled** — FULLs are created ONLY at F-marked levels:

```toml
# Automatic mode: FULLs at weekly, monthly, yearly
target_preserve = "48h 14d 8w 12m 1y"

# Manual F-syntax: FULLs at daily boundaries only
target_preserve = "48h 7Fd 8w 12m 1y"

# FULLs at every level (hourly + daily + weekly + monthly + yearly)
target_preserve = "48Fh 7Fd 4Fw 12Fm 1Fy"

# Weekly-only FULLs (ignore daily, monthly, yearly for FULL creation)
target_preserve = "24h 7d 4Fw 12m 1y"
```

Non-F buckets still participate in retention — `"24h 7d 4Fw"` retains 24 hourly, 7 daily, and 4 weekly snapshots, but FULLs are created only at weekly boundaries.

**Validation:** an F-anchor on a zero-count bucket is rejected at parse time. `"0Fh 7d"` raises `ConfigError: F-anchor on bucket 'h' requires count > 0`.

### How It Works

1. Before each incremental transfer, qsnap retrieves ALL full backups for the target (`get_full_backups()`).
2. If any `F`-marked buckets exist, only those are checked. Otherwise, all buckets with `count > 0` are checked in descending order.
3. For each checked bucket: find the most recent FULL with matching `bucket_level`, compare period keys. If the period changed (or no prior FULL exists), create a new FULL.
4. The full backup is named `vm.FULL.YYYYMMDD.qcow2`.
5. Subsequent incremental backups are rebased to the FULL anchor instead of the source snapshot backing file.
6. The conversion is atomic: `qemu-img convert` writes to a `.tmp` file, which is renamed only on success.
7. After creation, the FULL is recorded in state with its `bucket_level` for cascade deletion tracking.

### `compress` Trade-off

| | Uncompressed (`false`) | Compressed (`true`) |
|---|---|---|
| Speed | Faster conversion | Slower (zlib overhead) |
| Size | Full size | ~30-50% smaller (data-dependent) |
| Compatibility | Standard qcow2 | Standard qcow2 (zlib clusters) |
| Restore | Direct | Direct (qemu-img handles transparently) |

### Cascade Deletion

When a FULL backup falls out of all retention buckets, qsnap checks whether any incremental backups still depend on it (via `IStateManager.get_incremental_dependencies()`):

- **If dependents exist in the keep-set** — the FULL is retained as a "ghost" (kept but not counted by retention). This prevents breaking the incremental chain.
- **If no dependents remain** — the FULL is deleted, and any orphaned incrementals (not in the keep-set) are cascade-deleted.

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

This copies the backup chain (full or incremental) from the target to the specified directory, resolving backing file references. The target directory contains a **FULL anchor** (`*.FULL.*.qcow2`) that serves as the base of the incremental chain. Incremental backups are rebased to this FULL anchor, so the chain structure on the target is: `FULL → incremental1 → incremental2 → ...`.

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

## Fork and Deploy

`qsnap fork` and `qsnap deploy` create a fully independent, standalone VM from any qsnap-managed snapshot or backup. The resulting VM has no backing dependencies on the source — it is immune to source snapshot deletion.

### fork — Create a VM from a snapshot

```bash
qsnap fork <snapshot-name> --as-vm <new-vm-name> [--storage <dir>] [--add-to-config]
```

**Steps performed:**

1. **Resolve** the snapshot/backup by name across all configured VMs (state + backup targets).
2. **Estimate** total chain size via `qemu-img info --backing-chain` and log it.
3. **Convert** the backing chain into a single standalone qcow2 via `qemu-img convert -O qcow2`.
4. **Obtain** the source VM's XML via `virsh dumpxml`.
5. **Modify** the XML: new VM name, new UUID, updated disk paths, MAC address removed.
6. **Define** the new VM via `virsh define`.
7. Optionally **append** a `[[vm]]` block to the qsnap config file (`--add-to-config`).

The resulting qcow2 file has **no backing file** — it is fully self-contained and writable.

```bash
# Basic fork
qsnap fork myvm.20260701T1200 --as-vm myvm-clone

# Fork to custom storage with auto-config
qsnap fork myvm.20260701T1200 --as-vm myvm-clone \
    --storage /mnt/fast-ssd --add-to-config

# Fork from a specific VM (when names collide across VMs)
qsnap fork myvm.20260701T1200 --as-vm recovered-vm prod-server
```

### deploy — Deploy a backup as a VM

```bash
qsnap deploy <backup-name> --as-vm <new-vm-name> [--storage <dir>] [--add-to-config]
```

`deploy` is a thin wrapper around `fork` — it resolves the backup from backup targets and delegates everything to the same `qemu-img convert` + VM creation pipeline.

```bash
# Deploy a FULL backup
qsnap deploy vm.FULL.20260701.monthly --as-vm recovered-vm

# Deploy an incremental backup (chain is flattened via qemu-img convert)
qsnap deploy vm.20260715T1200 --as-vm recovered-vm \
    --storage /mnt/vms --add-to-config
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--as-vm` | **required** | Name for the new VM |
| `--storage` | `/var/lib/libvirt/images` | Directory where the VM disk and XML are stored. A subdirectory `/<new-vm-name>/` is created inside |
| `--add-to-config` | `false` | Append a minimal `[[vm]]` block to the qsnap config file, enabling qsnap to manage the new VM going forward |

### Generated `[[vm]]` Block

When `--add-to-config` is used, the following block is appended:

```toml
[[vm]]
name = "myvm-clone"
base_image = "/var/lib/libvirt/images/myvm-clone/myvm-clone.qcow2"
snapshot_dir = "/var/lib/libvirt/images/myvm-clone/snapshots"
snapshot_create = "always"
```

The `snapshot_dir` is created automatically. You can add targets and customize retention policies afterward.

### Important Notes

- **Disk space:** `qemu-img convert` produces a file as large as the full virtual disk (not sparse like the backing chain). The estimated size is logged before conversion begins.
- **Source VM running:** Fork does NOT require the source VM to be stopped — snapshot files are read-only once created. A WARNING is logged if the source VM is running.
- **Performance:** Conversion reads the entire backing chain. For large VMs, consider forking from a FULL backup (which is already standalone) for near-instant conversion.

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
  compress = false             # USB is slow, skip compression overhead
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
  compress = true              # compress to save NAS space

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
  compress = true
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

## Size Estimation

qsnap logs projected backup size estimates on every run (including dry-run). The estimate includes:

- **Base image actual-size** — obtained via `qemu-img info`
- **Average incremental size** — computed from state history (past snapshot allocations)
- **Projected FULL count** — based on the number of active retention buckets
- **Projected total size** — `num_fulls × full_size + num_incs × inc_size`
- **Compressed FULL size** — when `compress = true` (default), estimated as `base_size × 0.3`
- **Current target size** — obtained via `du -sb`

### `qsnap estimate` Command

Use the `estimate` subcommand to preview projected storage usage without running the pipeline:

```bash
qsnap estimate [vm]
```

Example output:

```
=== prod-server ===
  Base image actual-size: 53687091200 B
  Avg incremental size:   524288000 B
  Backups [/mnt/nas-backup/prod-server]:
    Policy: hourly=0 daily=7 weekly=4 monthly=12 yearly=2 preserve_min=4h
    Expected kept:   25
    Expected remove: 0
    Projected FULLs: 1
    Projected incrementals: 24
    Projected total size: 22548578304 B
    Current target size: 0 B
```

The size estimate is also logged at INFO level during every pipeline run (including `--dry-run`), allowing monitoring systems to track projected storage growth.

## Requirements

- Python 3.11+
- libvirt + virsh (libvirt 6.0+ for NBD bitmap backup)
- qemu-img
- rsync (hard requirement for all backup transfers)
- QEMU/KVM hypervisor
