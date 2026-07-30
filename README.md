# qsnap

QEMU/KVM snapshot and backup orchestration for qcow2 images on any filesystem (XFS, ext4, etc.), inspired by [btrbk](https://github.com/digint/btrbk).

qsnap manages external disk-only snapshots via `virsh`, detects whether a VM disk has changed, enforces retention policies, performs incremental backups to separate storage, and maintains backing chain integrity via `blockcommit`.

## Features

- **External snapshots** — disk-only, no-metadata snapshots via `virsh snapshot-create-as`
- **Change detection** — skip snapshot creation when the disk hasn't changed (`onchange` mode); independent source-disk-based change detection for backup transfers (`backup_create="onchange"` — queries the VM's active disk directly, decoupled from snapshot existence)
- **Snapshot preservation floor** — `snapshot_preserve_min` guarantees the newest N snapshots are never blockcommitted, even when `snapshot_chain_length` is exceeded
- **Incremental backups** — NBD bitmap-based backup to remote targets, with compression support for FULL backups
- **Periodic full backups** — standalone qcow2 via `qemu-img convert` (default) or `libnbd` pread/pwrite engine, with optional compression and configurable parallel coroutines
- **FULL backup verification** — three-tier integrity check (M1/M2/M3) at post-create, pre-rebase, and pre-deletion lifecycle points. M1 (metadata/corrupt-bit) always enforced at the verify-before-delete gate
- **Incremental backup verification** — four tiers: `off`, `metadata`, `hash` (SHA-256), or `full` (`qemu-img compare`)
- **Retention policies** — count-based retention with configurable chain lengths and generation counts
- **Config validation** — validates chain_length and keep_generations values
- **Schedule preview** — `--print-schedule` / `-S` shows current chain lengths and retention counts
- **Backing chain integrity** — automatic `blockcommit` or `qemu-img commit` to merge old snapshots
- **Stale state self-healing** — automatically cleans up state entries for already-blockcommitted snapshots and externally-deleted FULL backups (phantom entries). Runs at pipeline startup before the onchange gate
- **State reconciliation** — `qsnap reconcile` actively repairs state-vs-disk inconsistencies in both directions: removes phantom state entries (file missing on disk) AND deletes orphan files on disk (not tracked in state). Supports `--dry-run` preview
- **Lock-conflict retry** — snapshot creation retries up to 3 times with exponential backoff on libvirt lock conflicts
- **NBD job cleanup** — `virsh domjobabort` ensures NBD backup jobs are terminated even on failure
- **Deferred operations** — AppArmor/SELinux-blocked blockcommits queued and retried on VM shutdown
- **Pre-flight validation** — environment checks including truncated transfer artifact detection
- **State consistency audit** — `qsnap check --state` detects phantom entries, corrupt state files, and orphaned libvirt checkpoints
- **Quiesce support** — optional `--quiesce` flag for filesystem-consistent snapshots

## Installation

### Arch Linux (PKGBUILD)

```bash
git clone https://github.com/baroque-cat/qsnap.git
cd qsnap
makepkg -si
```

This installs qsnap to the system Python site-packages, the `qsnap` CLI to `/usr/bin/qsnap`, systemd units to `/usr/lib/systemd/system/`, and the config example to `/etc/qsnap/qsnap.toml.example`. System dependencies (`libnbd`, `libvirt`, `qemu-utils`) are pulled in automatically.

### pip / Poetry

```bash
pip install git+https://github.com/baroque-cat/qsnap.git
```

Or clone and install with Poetry:

```bash
git clone https://github.com/baroque-cat/qsnap.git
cd qsnap
poetry install
```

> **Note:** When installing via pip/Poetry in a venv, qsnap automatically appends system site-packages to `sys.path` at runtime so that the system `libnbd` bindings are discoverable. For best results, create the venv with `--system-site-packages` or install via the PKGBUILD (which uses system Python directly).

## Quick Start

1. Create a configuration file at `/etc/qsnap/qsnap.toml`:

```toml
state_dir = "/var/lib/qsnap/state"

# Default: trigger blockcommit after 168 snapshots
snapshot_chain_length = 168

# Default: create new FULL after 100 incrementals, keep 2 generations
target_chain_length = 100
target_keep_generations = 2

# Always keep the newest 24 snapshots (never blockcommit them)
snapshot_preserve_min = 24

[[vm]]
name = "debiantest"
base_image = "/var/lib/libvirt/images/debiantest.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/debiantest"

  [[vm.target]]
  path = "/mnt/backup/debiantest"
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
| `qsnap check [vm]` | Verify backing-chain integrity (`--deep` for corruption check, `--state` for state consistency audit) |
| `qsnap reconcile [vm]` | Actively repair state-vs-disk inconsistencies: remove phantom entries, delete orphan files, clean orphaned checkpoints (`--dry-run` to preview) |
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
| `state_dir` | string | `/var/lib/qsnap/state` | Directory for JSON state files (snapshot records, deferred operations) |
| `lockfile` | string | `/var/lock/qsnap.lock` | Lockfile path to prevent concurrent runs |
| `snapshot_chain_length` | int | none | Max snapshots before blockcommit triggers. 0 = keep all |
| `target_chain_length` | int | none | Max incremental backups before new FULL backup |
| `target_keep_generations` | int | none | Number of FULL backup generations to keep per target |
| `snapshot_preserve_min` | int | `0` | Snapshot preservation floor — guarantees the newest N snapshots are never blockcommitted, even when `snapshot_chain_length` is exceeded. `0` = inactive (default). Inherits from global → VM |
| `compress` | bool | `true` | Compress FULL backups via `qemu-img convert -c -o compression_type=<type>`. Overridden per-VM/target |
| `compression_type` | string | `"zstd"` | Compression algorithm: `"zstd"` (default — 11x faster than zlib) or `"zlib"`. Only effective when `compress = true` |
| `full_transfer_engine` | string | `"qemu-img-convert"` | FULL backup transfer engine: `"qemu-img-convert"` (default — C code, parallel coroutines) or `"libnbd"` (Python `pread`/`pwrite` loop via `INbdClient`). Inherits from global → target |
| `convert_parallel` | int | `4` | `qemu-img convert -m` flag (parallel coroutines, range 1-8). Only consumed when `full_transfer_engine = "qemu-img-convert"`. Inherits from global → target |
| `convert_out_of_order` | bool | `true` | `qemu-img convert -W` flag (out-of-order writes). `true` optimizes for HDDs; `false` for in-order writes. Only consumed when `full_transfer_engine = "qemu-img-convert"`. Inherits from global → target |
| `backup_stall_timeout` | string | `"30m"` | Stall detection timeout for data-transfer commands. Duration string (e.g. `"30m"`, `"1h"`, `"0s"`). `"0s"` disables stall detection (falls back to fixed-timeout `run()`). Inherits from global → VM → target |
| `backup_create` | string | `"always"` | When to create backups: `"always"` (transfer every snapshot) or `"onchange"` (skip transfer when the source disk has not changed since the last backup to that target — queries the VM's active disk directly via `IChangeDetector`, independent of snapshot existence). Inherits from global → VM → target |
| `full_verify_after_create` | string | `"check"` | FULL verification after creation: `"off"`, `"metadata"` (M1), `"check"` (M1+M2), `"hash"` (M1+M2+M3 via qemu-img compare) |
| `full_verify_before_rebase` | string | `"metadata"` | FULL verification before rebasing incrementals: `"metadata"` (M1), `"off"` |
| `full_verify_before_delete` | string | `"check"` | FULL verification at verify-before-delete gate: `"metadata"` (M1 only), `"check"` (M1+M2), `"off"` (M1 still enforced — non-configurable) |
| `deep_check_targets` | bool | `false` | When `true`, `qsnap check --deep` also scans backup target directories |

### VM Keys

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | string | **required** | VM name as known to libvirt |
| `base_image` | string | **required** | Path to the base qcow2 image |
| `snapshot_dir` | string | **required** | Directory where snapshot qcow2 files are stored |
| `snapshot_create` | string | `"always"` | When to create snapshots: `"always"` or `"onchange"` (skip if disk allocation unchanged) |
| `snapshot_chain_length` | int | inherits global | VM-specific snapshot chain length |
| `target_chain_length` | int | inherits global | VM-specific target chain length |
| `target_keep_generations` | int | inherits global | VM-specific FULL generations to keep |
| `snapshot_preserve_min` | int | inherits global | VM-specific snapshot preservation floor. `0` = inactive, positive N = keep newest N snapshots. Overrides global |
| `snapshot_quiesce` | bool | `false` | Use `--quiesce` for filesystem-consistent snapshots |
| `lifecycle_mode` | string | `"virsh"` | How to merge snapshots (adaptive): `"virsh"` — live `virsh blockcommit` of non-active layers while running, offline `qemu-img commit` when shut off; `"qemu-img"` — offline-only, defers while the VM runs |
| `change_detection_mode` | string | `"allocation-size"` | How to detect disk changes (used by both `snapshot_create="onchange"` and `backup_create="onchange"`): `"allocation-size"` (compare `qemu-img info` actual-size) or `"allocation-map"` (compare `qemu-img map` allocated regions) |
| `disks` | list | `null` | Explicit list of disk paths to snapshot (default: all VM disks) |

### Target Keys

| Key | Type | Default | Description |
|---|---|---|---|
| `path` | string | **required** | Directory where backup qcow2 files are stored |
| `incremental` | bool | `true` | Whether to do incremental backups (bitmap deltas with backing chain) |
| `target_chain_length` | int | inherits VM/global | Target-specific chain length |
| `target_keep_generations` | int | inherits VM/global | Target-specific FULL generations to keep |
| `verify` | string | `"metadata"` | Backup verification tier: `"off"`, `"metadata"`, `"hash"`, or `"full"`. Default is `"metadata"`; `"hash"`/`"full"` run chain-traversing `qemu-img compare`. Explicit value takes precedence |
| `compress` | bool | `true` | Compress FULL backups via `qemu-img convert -c -o compression_type=<type>`. Bitmap (NBD) incrementals are always uncompressed — dirty blocks are written via random-access `pwrite`. Inherits from global → VM → target |
| `compression_type` | string | `"zstd"` | Compression algorithm: `"zstd"` (default — 11x faster than zlib) or `"zlib"`. Only effective when `compress = true`. Inherits from global → VM → target |
| `full_transfer_engine` | string | `"qemu-img-convert"` | FULL backup transfer engine: `"qemu-img-convert"` (default) or `"libnbd"`. Inherits from global → VM → target |
| `convert_parallel` | int | `4` | `qemu-img convert -m` flag (parallel coroutines, range 1-8). Only consumed when `full_transfer_engine = "qemu-img-convert"`. Inherits from global → VM → target |
| `convert_out_of_order` | bool | `true` | `qemu-img convert -W` flag (out-of-order writes). Only consumed when `full_transfer_engine = "qemu-img-convert"`. Inherits from global → VM → target |
| `backup_stall_timeout` | string | `"30m"` | Stall detection timeout for data-transfer commands. Duration string. `"0s"` disables stall detection. Inherits from global → VM → target |
| `backup_create` | string | `"always"` | When to create backups: `"always"` or `"onchange"` (skip transfer when the source disk has not changed since the last backup to this target — uses `IChangeDetector` to query the source disk directly, independent of snapshot existence). Inherits from global → VM → target |

## Retention Policy Guide

qsnap uses count-based retention. Four config keys control how long chains are maintained:

- **`snapshot_chain_length`** — Maximum number of snapshots before blockcommit triggers. When the snapshot count exceeds this value, the oldest snapshots are committed (merged) into the base image. Set to `0` to keep all snapshots indefinitely.

- **`snapshot_preserve_min`** — Snapshot preservation floor. Guarantees that the newest N snapshots are **never blockcommitted**, even when `snapshot_chain_length` is exceeded. Applied as a post-processing filter after the oldest-prefix filter: if `len(remove) > len(snapshots) - preserve_min`, the newest excess items are moved from `remove` to `keep`. Set to `0` (default) to disable. Example: with 30 snapshots, `chain_length=6`, `preserve_min=24` — only the 6 oldest are committed, the newest 24 are preserved.

- **`target_chain_length`** — Maximum number of incremental backups before a new FULL backup is created. When `incremental_count > target_chain_length` (strictly greater than), the next backup transfer creates a FULL instead of an incremental.

- **`target_keep_generations`** — Number of FULL backup generations to keep per target. Each FULL followed by its incrementals is a generation. When a new FULL is created and verified, older generations beyond this count are removed.

Retention follows a simple rule: **keep the newest N, remove the oldest**. For snapshots, the N newest snapshots are kept and older ones are blockcommitted. For backups, the N newest FULL generations are kept.

### Example Scenarios

**Home host** — conservative, keep lots of recent data:

```toml
snapshot_chain_length = 336
target_chain_length = 200
target_keep_generations = 3
```

**Server** — aggressive pruning, shorter chains:

```toml
snapshot_chain_length = 168
target_chain_length = 100
target_keep_generations = 2
```

### Schedule Preview

Before running the pipeline, preview the current state:

```bash
qsnap run myvm --print-schedule
# or
qsnap run myvm -S
```

This shows:
- The configured policy (chain lengths and generation counts)
- How many snapshots/backups would be kept vs. removed
- Chain length and generation counts

For cron/systemd timer use, the `--timer` flag logs the schedule summary at INFO level before executing the pipeline:

```bash
qsnap run --timer
```

## Snapshot Lifecycle (Blockcommit)

Snapshots that retention removes are merged back into the base image to keep the backing chain short. qsnap picks the safe mechanism for the VM's **current power state** on every run — this is fully automatic:

| VM state | `lifecycle_mode = "virsh"` (default) | `lifecycle_mode = "qemu-img"` |
|---|---|---|
| **running** | Non-active snapshots committed **live** via `virsh blockcommit`; only the **active layer** is deferred (reason `"vm_running"`) | Everything deferred (reason `"vm_running"`) — `qemu-img` writing into the base image of a live chain is unsafe |
| **shut off** | Offline commit via `qemu-img commit` + child pivot + file deletion | Same as `"virsh"` |
| **paused / other** | Everything deferred (reason `"vm_running"`) | Everything deferred |

### Deferred blockcommits

A deferred entry waits in the state file and drains automatically on a later run when the VM is in a compatible state (a formerly-active layer becomes committable once a newer snapshot exists above it while the VM runs; everything except the tip drains when the VM is shut off). Known deferral reasons:

- `"vm_running"` — the snapshot was the active layer of a running VM, or the VM was paused / in qemu-img mode while running.
- `"active_layer"` — the snapshot is the **XML-referenced tip** of a shut-off domain. It is never committed or deleted offline (deleting it would make the domain unbootable); it drains once it is no longer the tip.
- `"apparmor"` / `"selinux"` — the commit was blocked by MAC policy.

After offline commits, qsnap also refreshes the domain's persistent XML (strips stale `<backingStore>` elements) so `virsh start` re-probes the shortened chain and the VM stays bootable.

## Full Backups

By default, qsnap creates **incremental** backups: each backup file references the previous one via the qcow2 backing chain. This is storage-efficient but means restoring requires the entire chain.

**Full backups** create a standalone qcow2 file with no backing dependencies, using `qemu-img convert`. This provides:
- A self-contained restore point independent of the incremental chain
- A new backing anchor for subsequent incrementals
- Protection against chain corruption

### Count-Based FULL Creation

FULL backups are created when the incremental chain length exceeds `target_chain_length`. When `incremental_count > target_chain_length` (strictly greater than), the next backup transfer creates a new FULL instead of an incremental. The first backup to a target always starts with a FULL.

At most ONE FULL is created per snapshot run.

### How It Works

1. Before each incremental transfer, qsnap counts the incremental backups since the last FULL for the target.
2. If the count exceeds `target_chain_length` (or no prior FULL exists), a new FULL is created.
3. The full backup is named `vm.FULL.YYYYMMDDTHHMM.qcow2`.
4. Subsequent incremental backups are rebased to the FULL anchor instead of the source snapshot backing file.
5. The conversion is atomic: the FULL is written to a `.tmp` file, which is renamed to the final name only on success. For running VMs, the NBD pull-model is used (see [NBD-Based FULL Backups](#nbd-based-full-backups-for-running-vms) below); for stopped VMs, direct `qemu-img convert` from the source qcow2 file is used (no `virsh backup-begin`, no NBD socket — the source path is resolved via `get_first_disk_path`).
6. After creation, the FULL is recorded in state for generation-based deletion tracking.

### NBD-Based FULL Backups for Running VMs

When a VM is **running**, `qemu-img convert` cannot read the active layer directly — QEMU holds an exclusive write lock on it, causing `Failed to get shared "write" lock` errors. qsnap solves this with the **NBD pull-model**:

1. **Detect VM state** — `virsh dominfo --domain <vm>` is called; the `State:` line is parsed. If the VM is running, the NBD path is selected.
2. **Check libvirt version** — `virsh --version` must report major version >= 6. If older, qsnap logs a WARNING and falls back to direct convert (which will fail on a running VM's active layer).
3. **Start NBD export** — `virsh backup-begin --domain <vm> <xml>` is called with a `<domainbackup mode='pull'>` XML that specifies a Unix socket path (`/tmp/qsnap-backup-{pid}.sock`). No `--incremental` flag is used — this is a full export, not an incremental checkpoint.
4. **Pull data via NBD** — `qemu-img convert -O qcow2 -m <parallel> [-W] -p nbd:unix:<socket> <target>.tmp` reads the entire disk through the NBD server, which coordinates with the running QEMU process to provide a consistent view without lock conflicts. The `-m` flag (parallel coroutines) and `-W` flag (out-of-order writes) are configurable via `convert_parallel` and `convert_out_of_order`. When `compress = true`, the command includes `-c -o compression_type=<type>` for optimized C-level zstd compression.
5. **Clean up** — the socket file is removed in a `finally` block, and the `.tmp` file is atomically renamed to `vm.FULL.YYYYMMDD.qcow2` on success (or deleted on failure).

This mechanism is used by the backup provider:

| Provider | FULL Backup Method | Notes |
|---|---|---|
| `BitmapBackupProvider` | `qemu-img convert` (default) or `libnbd` pread/pwrite | FULL backups use `qemu-img convert -m <parallel> [-W] -p` with optional `-c` compression via `run_with_stall_detection()`. The engine is selected by `full_transfer_engine`: `"qemu-img-convert"` (default) or `"libnbd"` (Python `pread`/`pwrite` loop via `INbdClient` — creates an empty qcow2, starts a write-side `qemu-nbd`, and transfers via `_transfer(zero_skip=True)`). For running VMs, `virsh backup-begin` starts the NBD export and a checkpoint is created atomically. For stopped VMs, direct `qemu-img convert <source_path>` is used (no NBD). Incremental backups always use the Python `libnbd` `pread`/`pwrite` loop with dirty-bitmap intersection, regardless of `full_transfer_engine`. |

The FULL timestamp is recorded as the **snapshot's timestamp** (not the NBD export time).

### FULL Backup Transfer Engine

The `full_transfer_engine` key selects how FULL backups are transferred:

| Engine | Value | How it works |
|---|---|---|
| **qemu-img-convert** (default) | `"qemu-img-convert"` | `qemu-img convert` reads from the NBD source (running VM) or source file (stopped VM) and writes the target qcow2. C code with parallel coroutines (`-m`), out-of-order writes (`-W`), and optional compression (`-c`). |
| **libnbd** | `"libnbd"` | Creates an empty qcow2 via `qemu-img create`, starts a write-side `qemu-nbd` (with compress driver when `compress = true`), and transfers data via the Python `libnbd` `pread`/`pwrite` loop with `zero_skip = True`. |

The `convert_parallel` and `convert_out_of_order` keys only apply to the `qemu-img-convert` engine — the `libnbd` engine has no parallelism or out-of-order concept.

Configuration example:

```toml
# Global default: use qemu-img-convert with 8 parallel coroutines
full_transfer_engine = "qemu-img-convert"
convert_parallel = 8
convert_out_of_order = true

[[vm]]
name = "myvm"
base_image = "/var/lib/libvirt/images/myvm.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/myvm"

  # Per-target override: use libnbd engine for this target
  [[vm.target]]
  path = "/mnt/backup/myvm"
  full_transfer_engine = "libnbd"
```

### Bitmap Mode (NBD Incremental Backups)

qsnap uses the NBD pull-model for incremental transfers via `virsh backup-begin` with checkpoint-based dirty-block tracking. An in-process copy loop (over the `python3-libnbd` bindings) negotiates the `base:allocation` and `qemu:dirty-bitmap:backup-<disk>` NBD meta-contexts and copies **only dirty, allocated blocks** into a backing-chained qcow2 delta (chained to the previous backup, FULL for the first incremental) — so an incremental is proportional to the dirtied data, not the disk size. Bitmap mode requires **libvirt >= 7.2** (the incremental backup API, including the checkpoint XML argument of `backup-begin`, is complete since 7.2) — older libvirt is a hard configuration error, there is no fallback. It also requires the `python3-libnbd` system package (see [Requirements](#requirements)).

**Atomic checkpoints:** Every `virsh backup-begin` issued in bitmap mode — both FULL exports via `create_full_backup()` and incrementals via `transfer_missing()` — receives a checkpoint XML as its third positional argument, so the successor checkpoint (named `qsnap-{target_hash}-{yyyymmddTHHMMSS}`) is created **atomically at the export's freeze point**. The dirty-bitmap baseline therefore always coincides with the exported point-in-time view, and the backup chain (FULL + incrementals) is gap-free by construction. After a successful and verified export, all superseded (older) qsnap checkpoints for that VM+target are deleted via `virsh checkpoint-delete --metadata`, keeping exactly one baseline checkpoint.

**First incremental after a FULL:** The first incremental after a bitmap FULL contains all blocks written since the FULL **started** — this is intentional (the checkpoint baseline is anchored at the FULL's freeze point), not a duplicate transfer. Its size is bounded by guest write rate × FULL duration, so **schedule FULLs during low write activity**.

**First-run behavior:** On the first count-based run, `create_full_backup()` leaves an atomic checkpoint baseline. `transfer_missing()` then discovers that checkpoint (newest-wins via `virsh checkpoint-list`) and performs a real incremental against it, embedding `<incremental><checkpoint></incremental>` in the backup XML to export only dirty blocks (see [libvirt Incremental Backup API](#libvirt-incremental-backup-api) below). When no prior checkpoint exists at all, a full NBD export with an atomic checkpoint is performed — safe degradation towards redundant work, never towards data loss.

### Bitmap Mode Limitations

Bitmap mode has several inherent constraints due to its NBD/checkpoint architecture:

- **Single-disk only** — only the first disk target (via `get_first_disk_target`) is pulled through the NBD export. Multi-disk VMs are not fully covered by bitmap backups.
- **Incrementals are uncompressed** — qcow2 compressed clusters can only be produced by `qemu-img convert`; the dirty-block copy loop writes via random-access `pwrite`. `compress` / `compression_type` still apply to FULL backups (where the bulk of the bytes are). Because an incremental is proportional to dirtied data, the capacity impact is minor.
- **`verify="metadata"` recommended** — `verify="hash"` and `verify="full"` are supported: both run `qemu-img compare -q --force-share` to compare the source snapshot chain against the FULL+delta chain (chain-traversing, meaningful now that deltas are backing-chained). On a running VM the comparison carries a reliability caveat (the guest may write during the compare), so `"metadata"` remains the default.
- **Checkpoints live in libvirt, not in state files** — bitmap mode creates libvirt checkpoints (atomically via the checkpoint XML argument of `virsh backup-begin`) that track dirty-bitmap boundaries. These are stored by libvirt, not in qsnap's JSON state. Use `qsnap check --state` to detect orphaned checkpoints (checkpoints whose target no longer matches any configured target path), and `qsnap reconcile` to automatically delete them.

### `compress` and `compression_type`

| | Uncompressed (`compress = false`) | zstd (`compress = true`, `compression_type = "zstd"`) | zlib (`compress = true`, `compression_type = "zlib"`) |
|---|---|---|---|
| Speed | Fastest conversion | Fast (850 MB/s) | Slow (77 MB/s) |
| Size | Full size | ~5-10% larger than zlib | ~30-50% smaller than uncompressed |
| Compatibility | Standard qcow2 | Standard qcow2 (requires qemu-img 5.2+) | Standard qcow2 (zlib clusters) |
| Restore | Direct | Direct (qemu-img handles transparently) | Direct (qemu-img handles transparently) |

zstd is the default because it is 11x faster than zlib, transitioning backups from CPU-bound to I/O-bound. Use `compression_type = "zlib"` if you need maximum compression or compatibility with older qemu-img versions (< 5.2).

### Cascade Deletion

When a new FULL is created and verified, older generations beyond `target_keep_generations` are removed. qsnap checks whether any incremental backups still depend on the FULL (via `IStateManager.get_incremental_dependencies()`):

- **If dependents exist** — the FULL is retained (kept but not counted toward generation limits). This prevents breaking the incremental chain.
- **If no dependents remain** — the FULL and its orphaned incrementals are deleted.

## Incremental Backup Verification (per-transfer)

qsnap offers four verification tiers for incremental backups, configured per target via the `verify` key:

| Tier | Description | Overhead | When to use |
|---|---|---|---|
| `"off"` | No verification after backup | None | When target is trusted and speed is critical |
| `"metadata"` | Compare qcow2 metadata: format and virtual-size (actual-size is NOT checked — unreliable for live sources) | Low | Default. Catches format mismatches and size corruption |
| `"hash"` | Metadata check + `qemu-img compare -q --force-share` across the source and FULL+delta chains | High | Detects silent bit-rot or transfer corruption. Equivalent to `"full"` (chain-traversing content compare) |
| `"full"` | Metadata check + `qemu-img compare -q --force-share` against the source | High (reads entire source and target) | Maximum integrity, detects all corruption. `--force-share` avoids lock errors on live sources; a WARNING is logged because results may be unreliable if the VM writes during comparison |

Bitmap incrementals additionally verify, on every tier except `"off"`: the delta's `backing-filename` equals the resolved previous backup, and a **dirty-size regression barrier** (`actual-size ≤ dirty_bytes × 2 + 64 MiB`) that fails the transfer if the engine ever regresses to copying the full disk.

### Default Tier

The default `verify` tier is `"metadata"` (cheap structural check; `"hash"`/`"full"` are supported but compare content with a live-source reliability caveat). When the user explicitly sets `verify`, the explicit value takes precedence.

### Bitmap Content Verification

`"hash"` and `"full"` both run `qemu-img compare -q --force-share <snapshot> <delta>`, which traverses both backing chains and compares the virtual disk content visible to the guest. This is meaningful because bitmap deltas are backing-chained to their FULL — the delta chain and the source snapshot chain resolve to the same freeze-point content. On a running VM the guest may write during the comparison, so a WARNING is logged and results may be unreliable; `"metadata"` remains the recommended default.

### Choosing a Tier

- **Home host with USB drive** — `"metadata"` is sufficient; the transfer is local and fast
- **Server with network target** — `"hash"` provides integrity without the overhead of `qemu-img compare`
- **Compliance/audit** — `"full"` gives maximum assurance at the cost of reading both files

### NBD bitmap backup architecture

`BitmapBackupProvider` (NBD dirty-block transfer) is the single backup provider:

- **libvirt >= 7.2 and python3-libnbd are hard requirements** — older libvirt or a missing libnbd package is a hard error.
- **Incremental backups require a running VM** — the NBD pull-model reads through the running QEMU process. FULL backups support both running and stopped VMs: running VMs use `virsh backup-begin` + `qemu-img convert nbd:unix:<socket>`; stopped VMs use direct `qemu-img convert <source_path>`.
- **Removed TOML keys are warned-and-ignored** — `incremental_mode`, `rate_limit`, and `copy_base` in existing configs log a deprecation WARNING naming the field and are otherwise ignored.

## FULL Backup Verification

Beyond per-backup verification of incrementals (above), qsnap applies a separate **three-tier verification** to FULL backup files at critical lifecycle points:

| Tier | Name | What it checks | When |
|:----:|------|---------------|------|
| M1 | Metadata | Format is `qcow2`, no `corrupt` feature bit | Post-create, pre-rebase, pre-deletion |
| M2 | Structural | `qemu-img check` — zero errors and leaks | Post-create, pre-deletion (when configured) |
| M3 | Content | `qemu-img compare -q --force-share <source> <target>` — byte-level content comparison | Post-create (when configured as `"hash"`) |

### Lifecycle Points

| Point | Config key | Default | Notes |
|-------|-----------|---------|-------|
| **Post-create** | `full_verify_after_create` | `"check"` | Runs immediately after `qemu-img convert` completes. Failed FULLs are deleted and NOT recorded in state |
| **Pre-rebase** | `full_verify_before_rebase` | `"metadata"` | Runs on FULL anchor before rebasing incrementals. Failing anchors are skipped; alternative (older) anchors are tried |
| **Pre-deletion** | `full_verify_before_delete` | `"check"` | Runs at verify-before-delete gate before removing old generations. **M1 is always enforced** regardless of this setting — it cannot be disabled. Failed FULLs block deletion entirely with a CRITICAL log |

### Why Separate FULL Verification?

Unlike incremental backups (which are verified against their source via `verify_backup()`), FULL backups are standalone qcow2 files created by `qemu-img convert`. They have no source file to compare against in the traditional sense. M3 solves this with `qemu-img compare`, which traverses the qcow2 backing chain to compare the virtual-disk content that the guest OS sees — a fundamentally different approach from SHA-256 file hashing (which would always differ between a snapshot delta and a standalone NBD-converted FULL).

### Phantom FULL Detection

Before making count-based FULL creation decisions, qsnap verifies that every FULL in state actually exists on disk. Phantom FULLs (deleted externally but still recorded in state) are automatically removed from state with a WARNING. This prevents phantom entries from blocking legitimate FULL creation.

## State Self-Healing

qsnap provides three levels of state-vs-disk consistency management, from passive audit to active repair:

### 1. Read-Only Audit: `qsnap check --state`

Detects and reports inconsistencies **without fixing anything**:

- Phantom snapshots (state has entry, file missing on disk)
- Phantom FULLs (state has entry, file missing on disk)
- Stale incremental dependencies (dep record exists, file missing)
- Corrupt state JSON files
- Orphaned libvirt checkpoints (checkpoint target hash matches no configured target)

Output is a summary table with per-VM status flags. Use this to assess state health before making changes.

### 2. Automatic Self-Healing (Pipeline Startup)

Every `qsnap run`, `qsnap snapshot`, and `qsnap backup` invocation runs `_validate_state_at_startup()` **before** the onchange gate and backup transfer. This is non-fatal (logs warnings, never raises) and handles:

| What | Direction | Action |
|---|---|---|
| Phantom FULLs | state → disk | Remove FULL record + cascade-clean all linked incremental dependencies |
| Stale baselines | state → disk | Clear `last_backup_allocation` when no FULLs remain for a target |

This ensures the onchange gate sees correct state — phantom entries don't block legitimate FULL creation, and stale baselines don't confuse future runs. Orphaned checkpoints and orphan files on disk are **not** cleaned at startup (only `reconcile` does that).

### 3. Active Repair: `qsnap reconcile`

The `reconcile` command actively repairs **both directions** of state-vs-disk inconsistency:

| Step | Direction | What it does |
|---|---|---|
| 1. Phantom snapshots | state → disk | Remove snapshot records whose files are missing |
| 2. Phantom FULLs | state → disk | Remove FULL records + cascade-clean incremental dependencies |
| 3. Stale baselines | state → disk | Clear `last_backup_allocation` when no FULLs remain |
| 4. Stale deps | state → disk | Remove incremental dependency records whose files are missing |
| 5. Orphan checkpoints | disk → state | Delete libvirt checkpoints whose target hash matches no configured target (`virsh checkpoint-delete --metadata`) |
| 6. Orphan files on target | disk → state | Delete `.qcow2` files on target not tracked in state (matching qsnap naming pattern only) |
| 7. Orphan snapshot files | disk → state | Delete `.qcow2` files in snapshot_dir not tracked in state |

```bash
# Preview what would be fixed (no changes made)
qsnap reconcile --dry-run

# Repair all VMs
qsnap reconcile

# Repair a specific VM
qsnap reconcile myvm

# Long-format output
qsnap reconcile --format long
```

**Non-qsnap files** (`.qcow2` files on target that don't match the `{vm_name}.*` pattern) are **not** deleted — a WARNING is logged and the file is skipped.

**When to use reconcile:**
- After manually deleting backup files from the target
- After a crash between backup transfer and state recording (file on disk, not in state)
- After removing a VM or target from the config (orphaned checkpoints)
- Periodically as part of maintenance (e.g., weekly cron)

## `--force-share` Safety Classification

qsnap adds the `--force-share` flag to `qemu-img` commands that operate on files that may be the **active layer** of a running VM. This flag tells qemu-img to use `BDRV_O_RDWR` with shared write permissions, avoiding `Failed to get shared "write" lock` errors when reading metadata from a live disk.

### Safe vs. Dangerous Operations

| Operation | `--force-share` | Why |
|---|---|---|
| `qemu-img info` | **Yes** | Metadata-only read; safe to share with running QEMU |
| `qemu-img info --backing-chain` | **Yes** | Metadata-only read of chain; active layer may be live |
| `qemu-img map` | **Yes** | Reads allocation map metadata; safe to share |
| `qemu-img check` | **Yes** | Read-only consistency check; safe to share |
| `qemu-img rebase -u` | **Yes** | Unsafe rebase only changes metadata (no data copy); safe to share |
| `qemu-img convert` | **No** | Data-copying operation; `--force-share` would corrupt the output if the source is being written to |
| `qemu-img compare` | **Yes** (in `verify_backup` full mode) | `--force-share` is added to avoid hard lock errors on live sources. A WARNING is logged because results may be unreliable if the VM writes during comparison. A potential false mismatch is better than no verification |
| `qemu-img commit` | **No** | Data-merging operation; `--force-share` is not applicable |

### Where `--force-share` Is Applied

qsnap applies `--force-share` on these metadata-only calls when the file may be an active layer:

- `ExternalSnapshotProvider.create()` — post-snapshot `qemu-img info` (the new snapshot IS the active layer)
- `MapChangeDetector.has_changed()` — `qemu-img map` on the most recent snapshot
- `Core.check_integrity()` — `qemu-img info --backing-chain` on snapshots (most recent may be active)
- `Core._deep_check_file()` — `qemu-img check` when the file may be the active layer
- `Core._verify_backing_chain()` / `Core._get_chain_length()` — `qemu-img info --backing-chain` (already had it)
- `Core.fork()` — `qemu-img info --backing-chain` for chain-size estimation
- `verify_backup()` — source-side `qemu-img info` (source may be active layer). `qemu-img compare` (full verify) now uses `--force-share` to avoid lock errors on live sources; a WARNING is logged advising that `verify=full` on a running VM's active layer may produce unreliable results.
- `verify_full_backup()` — M3 (`qemu-img compare` in `"hash"` mode) uses `--force-share` to safely compare source snapshot content (which may be the active layer of a running VM) against the FULL backup. This is safe because qemu-img compare reads both files read-only and `--force-share` allows shared read access to the active layer.

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
3. **Convert** the backing chain into a single standalone qcow2. If the source VM is running, the NBD pull-model is used (`virsh backup-begin` + `qemu-img convert -n nbd:unix:<socket>`); if stopped, direct `qemu-img convert -O qcow2` is used.
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
qsnap deploy vm.FULL.20260701T1200 --as-vm recovered-vm

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
- **Source VM running:** Fork does NOT require the source VM to be stopped. If the source VM is running, the NBD pull-model is used to avoid lock conflicts on the active layer. If stopped, direct `qemu-img convert` is used.
- **Performance:** Conversion reads the entire backing chain. For large VMs, consider forking from a FULL backup (which is already standalone) for near-instant conversion.

## Example Configurations

### Home Host with USB Backup Target

A desktop machine running a few VMs, backing up to a hot-plugged USB drive. Conservative retention, hash verification:

```toml
state_dir = "/var/lib/qsnap/state"
lockfile = "/var/lock/qsnap.lock"

snapshot_chain_length = 336
target_chain_length = 200
target_keep_generations = 3

[[vm]]
name = "desktop-vm"
base_image = "/var/lib/libvirt/images/desktop-vm.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/desktop-vm"
snapshot_create = "onchange"    # skip if disk hasn't changed
snapshot_quiesce = true        # filesystem-consistent snapshots

  [[vm.target]]
  path = "/mnt/usb-backup/desktop-vm"
  verify = "hash"              # SHA-256 verification
  compress = false             # USB is slow, skip compression overhead
```

### Server with Persistent Network Target

A production server backing up to a persistent NFS/NAS mount. Aggressive pruning, full verification, full backups with compression:

```toml
state_dir = "/var/lib/qsnap/state"
lockfile = "/var/lock/qsnap.lock"

snapshot_chain_length = 168
target_chain_length = 100
target_keep_generations = 2

[[vm]]
name = "prod-server"
base_image = "/var/lib/libvirt/images/prod-server.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/prod-server"
snapshot_quiesce = true
lifecycle_mode = "virsh"

  [[vm.target]]
  path = "/mnt/nas-backup/prod-server"
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

qsnap logs factual backup size data on every run (including dry-run). The estimate includes:

- **Base image actual-size** — obtained via `qemu-img info`
- **Compression type** — from config (`zstd` or `zlib`)

No size projections are made — the previous `base_size × 0.3` formula was removed because it cannot predict data compressibility (real data has wildly different ratios from 0.1 to 0.8).

### `qsnap estimate` Command

Use the `estimate` subcommand to preview factual storage data without running the pipeline:

```bash
qsnap estimate [vm]
```

Example output:

```
=== prod-server ===
  Base image actual-size: 53687091200 B
  Backups [/mnt/nas-backup/prod-server]:
    Policy: chain_length=168 keep_generations=2
    Expected kept:   25
    Expected remove: 0
    Compression: zstd (compress=True)
```

## Dry-Run Mode

The `--dry-run` / `-n` flag prints planned actions without executing any mutations. qsnap's dry-run mode provides a **safe preview** of what would happen:

### What Dry-Run Does

- **Runs environment validation** — all pre-flight checks (`virsh`, `qemu-img` availability; libnbd importability; directory writability; libvirt access) are executed. Failures are logged as **WARNING** (non-fatal) instead of raising `RuntimeError`. This lets you see configuration issues without aborting the preview.
- **Logs FULL-would-be-created** — when a FULL backup would be triggered by exceeding target_chain_length, qsnap logs:
  ```
  [dry-run] Would create FULL backup (chain_length=100, method=NBD, VM=running)
  ```
  The `method` field is `NBD` for running VMs or `direct` for stopped VMs, and `VM` shows the detected running state. No FULL backup is actually created.
- **Does NOT create snapshots** — `snapshot_provider.create()` is never called.
- **Does NOT transfer backups** — no `qemu-img convert` or NBD data-copying commands are executed.
- **Does NOT delete anything** — retention is evaluated but no snapshots or backups are removed.

### What Dry-Run Allows

Dry-run mode permits **read-only** shell commands for validation:

- `test -d`, `test -w`, `test -f` — directory/file existence checks
- `which virsh`, `which qemu-img` — binary discovery
- `virsh dominfo` — VM running-state detection
- `qemu-img info` — metadata reads for base-size reporting
- `find` — stale file discovery

## Troubleshooting

### Orphaned Checkpoints (Bitmap Mode)

Bitmap mode creates libvirt checkpoints to track dirty-block boundaries. A checkpoint becomes **orphaned** when its target no longer matches any configured target path — for example, after a VM is removed from config, a target is removed, or a target path changes. Orphaned checkpoints accumulate in libvirt with no automatic cleanup.

**Detection:**

```bash
qsnap check --state
```

The state consistency audit includes an "Orphaned Checkpoints" section listing any checkpoints whose `qsnap-{hash}-` naming prefix does not match a configured target's hash. The `orphan_ckpts` column in the summary table shows the count per VM.

**Cleanup:**

```bash
# Automatic cleanup via reconcile (recommended)
qsnap reconcile [vm]

# Or delete manually
virsh checkpoint-list --domain <vm> --name
virsh checkpoint-delete --domain <vm> <checkpoint-name> --metadata
```

`qsnap reconcile` detects and deletes orphaned checkpoints via `virsh checkpoint-delete --metadata` in a single pass. Use `--dry-run` to preview before making changes. The `--metadata` flag removes the checkpoint definition without merging its dirty-bitmap data back into the active disk — safe for orphaned checkpoints because their data is no longer referenced by any qsnap-managed backup.

### Broken Incremental Backups (Pre-Fix Runs)

Before the `<incremental>` XML fix, bitmap mode incremental backups failed with `error: command 'backup-begin' doesn't support option --incremental`. If you have orphaned checkpoints or failed state entries from these runs:

1. Run `qsnap check --state` to identify orphaned checkpoints and phantom entries.
2. Run `qsnap reconcile` to automatically delete orphaned checkpoints and clean stale state entries.
3. The next `qsnap run` will create a fresh checkpoint and resume normal incremental flow.

## Requirements

- Python 3.11+
- libvirt + virsh (**libvirt 7.2+** required — the NBD bitmap incremental backup API, including the checkpoint XML argument of `backup-begin`, is complete since 7.2)
- qemu-img + qemu-nbd
- python3-libnbd (system package, e.g. `apt install python3-libnbd`) — **hard requirement**; the incremental dirty-block copy loop uses the libnbd bindings. There is no fallback: a missing package is a hard pre-flight validation error.
- QEMU/KVM hypervisor

### Development environment (libnbd in the Poetry venv)

The libnbd Python bindings ship only with the OS package (there is no PyPI distribution), compiled for the system Python's ABI. To run the libnbd-dependent integration tests inside the Poetry venv, point Poetry at the system interpreter and let the venv see system site-packages (the repo's committed `poetry.toml` already enables `system-site-packages`):

```bash
poetry env use /usr/bin/python3   # the interpreter python3-libnbd was built for
poetry install
poetry run python -c "import nbd"  # sanity check
```

On hosts without libnbd (or with a different system Python) this is optional: `import nbd` is lazy, all unit tests pass without it, and the libnbd-dependent integration tests skip automatically via `pytest.importorskip`.

### libvirt Incremental Backup API

Bitmap mode incremental backups use the **`<incremental>` XML element** inside the `<domainbackup>` document passed to `virsh backup-begin`, **not** a CLI flag. The `--incremental` flag does not exist in any version of virsh. The invocation is:

```bash
virsh backup-begin --domain <vm> <backup.xml> <checkpoint.xml>
```

where `backup.xml` contains:

```xml
<domainbackup mode='pull'>
  <incremental>qsnap-<hash>-<yyyymmddTHHMMSS></incremental>
  <server>
    <transport unix>
      <socket>/tmp/qsnap-backup-<pid>.sock</socket>
    </transport>
  </server>
</domainbackup>
```

and `checkpoint.xml` names the successor checkpoint created atomically at the export's freeze point:

```xml
<domaincheckpoint><name>qsnap-<hash>-<yyyymmddTHHMMSS></name></domaincheckpoint>
```

The `<incremental>` element references a prior checkpoint name. libvirt uses it to export only the dirty blocks (regions modified since that checkpoint) via the NBD server. FULL exports omit the `<incremental>` element entirely, producing a complete disk export. The checkpoint XML (third positional argument) is what anchors the next incremental's baseline to exactly this export's freeze point — no separate `virsh checkpoint-create-as` call is involved.

### Libvirt Permissions

The user running qsnap must have **libvirt access** to manage VMs, create snapshots, and initiate NBD backups. There are two common approaches:

**Option A: Group membership (recommended)**

Add the qsnap user to the `libvirt` group:

```bash
sudo usermod -aG libvirt qsnap
```

The user must log out and back in for the group change to take effect. Verify with:

```bash
virsh list --all   # should list VMs without sudo
```

**Option B: Polkit configuration**

If group membership is not suitable, configure polkit to grant libvirt access:

```bash
sudo cat > /etc/polkit-1/rules.d/50-qsnap.rules << 'EOF'
polkit.addRule(function(action, subject) {
    if (action.id == "org.libvirt.api.domain" &&
        subject.user == "qsnap") {
        return polkit.Result.YES;
    }
});
EOF
sudo systemctl restart polkit
```

**Without libvirt access**, qsnap will fail with errors like:
- `error: Failed to connect to system bus` — no D-Bus/libvirt access
- `error: unauthorized` — polkit denied access
- `Failed to get shared "write" lock` — NBD backup-begin fails without proper permissions
