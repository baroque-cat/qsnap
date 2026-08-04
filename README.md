# qsnap

QEMU/KVM snapshot and backup orchestration for qcow2 images on any filesystem (XFS, ext4, etc.).

qsnap manages external disk-only snapshots via `virsh`, detects whether a VM disk has changed, enforces retention policies, performs incremental backups to separate storage, and maintains backing chain integrity via `blockcommit`. Every VM is fully **multi-disk aware**: each disk target (`vda`, `vdb`, ...) owns its own base image, snapshot chain, backup chain, and retention — qsnap never mixes disks.

## Features

- **External snapshots** — disk-only, no-metadata snapshots via `virsh snapshot-create-as`, one per disk.
- **Change detection** — skip snapshot creation when a disk hasn't changed (`onchange` mode); independent source-disk-based gate for backup transfers (`backup_create="onchange"`).
- **Snapshot preservation floor** — `snapshot_preserve_min` guarantees the newest N snapshots are never blockcommitted.
- **Incremental backups** — NBD bitmap dirty-block transfer to targets, proportional to dirtied data.
- **Full backups** — standalone qcow2 via `qemu-img convert`, with optional zstd/zlib compression.
- **Verification** — three-tier FULL verification (M1/M2/M3) and per-transfer incremental verification.
- **Count-based retention** — configurable chain lengths and generation counts, evaluated **per disk**.
- **Schedule preview** — `--print-schedule` / `-S` shows current chain lengths and retention counts.
- **Backing chain integrity** — automatic `blockcommit` / `qemu-img commit` per disk, adaptive to VM power state.
- **State self-healing & reconciliation** — `qsnap check --state` audits, `qsnap reconcile` actively repairs state-vs-disk drift.
- **Deferred operations** — MAC-blocked or VM-state-blocked blockcommits queued per disk and drained automatically.
- **Restore / Fork** — flatten a snapshot or backup into a standalone qcow2, replacing a single disk's base image.

## Installation

### Arch Linux (PKGBUILD)

```bash
git clone https://github.com/baroque-cat/qsnap.git
cd qsnap
makepkg -si
```

Installs qsnap to the system Python site-packages, the `qsnap` CLI to `/usr/bin/qsnap`, systemd units to `/usr/lib/systemd/system/`, and the config example to `/etc/qsnap/qsnap.toml.example`. System dependencies (`libnbd`, `libvirt`, `qemu-utils`) are pulled in automatically.

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

> **Note:** When installing via pip/Poetry in a venv, qsnap appends system site-packages to `sys.path` at runtime so the system `libnbd` bindings are discoverable. For best results create the venv with `--system-site-packages`, or install via the PKGBUILD.

## Quick Start

1. Create a configuration file at `/etc/qsnap/qsnap.toml`. Each VM declares one or more disks under `[[vm.disk]]`; each disk carries its own `base_image`:

```toml
state_dir = "/var/lib/qsnap/state"

snapshot_chain_length = 24      # blockcommit after 24 snapshots per disk
target_chain_length = 168       # new FULL after 168 incrementals per disk
target_keep_generations = 2     # keep 2 FULL chains per target
snapshot_preserve_min = 24      # never blockcommit the newest 24 snapshots

[[vm]]
name = "debiantest"
snapshot_dir = "/var/lib/libvirt/snapshots/debiantest"

  [[vm.disk]]
  target = "vda"
  base_image = "/var/lib/libvirt/images/debiantest.qcow2"

  [[vm.target]]
  path = "/mnt/backup/debiantest"
  verify = "metadata"
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
| `qsnap list snapshots [vm]` | List snapshots with a `DISK` column (`--tree` for per-disk backing-chain view) |
| `qsnap list backups [vm]` | List backups across all targets (`--tree` for VM -> Target -> Disk -> chain hierarchy) |
| `qsnap list latest [vm]` | Show most recent snapshot per VM per disk |
| `qsnap list config` | Show parsed VM configurations (disks and targets) |
| `qsnap list deferred` | Show deferred blockcommit operations (per VM per disk) |
| `qsnap stats [vm]` | Show snapshot/backup counts and sizes |
| `qsnap check [vm]` | Verify backing-chain integrity (`--deep` for corruption check, `--state` for state audit) |
| `qsnap reconcile [vm]` | Actively repair state-vs-disk inconsistencies (`--dry-run` to preview) |
| `qsnap restore <name> [vm]` | Replace a stopped VM's disk with a flattened qcow2 from a snapshot/backup (`--dry-run`, `--yes`) |
| `qsnap fork <name> --output <path> [vm]` | Create a standalone qcow2 from a snapshot or backup |
| `qsnap estimate [vm]` | Preview factual storage data without running the pipeline |

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

The `run`, `snapshot`, `backup`, and `prune` subcommands additionally accept:

| Flag | Description |
|---|---|
| `--print-schedule`, `-S` | Print the retention schedule simulation and exit |
| `--timer` | Log the schedule summary at INFO level, then continue (for cron/systemd) |

## Configuration Reference

Configuration is TOML. Keys are organized in four levels: **global** (top-level), **per-VM** (`[[vm]]`), **per-disk** (`[[vm.disk]]`), and **per-target** (`[[vm.target]]`). Values at lower levels override the defaults above them.

### Global Keys

| Key | Type | Default | Description |
|---|---|---|---|
| `state_dir` | string | `/var/lib/qsnap/state` | Directory for JSON state files |
| `lockfile` | string | none | Lockfile path to prevent concurrent runs (unset = no locking) |
| `snapshot_chain_length` | int | `24` | Max snapshots per disk before blockcommit triggers |
| `target_chain_length` | int | `168` | Max incremental backups per disk before a new FULL |
| `target_keep_generations` | int | `2` | FULL backup generations (chains) to keep per target |
| `snapshot_preserve_min` | int | `0` | Newest N snapshots never blockcommitted. `0` = inactive |
| `compress` | bool | `true` | Compress FULL backups via `qemu-img convert -c` |
| `compression_type` | string | `"zstd"` | `"zstd"` (fast) or `"zlib"` (smaller). Only when `compress = true` |
| `convert_parallel` | int | `4` | `qemu-img convert -m` parallel coroutines (1-8) |
| `convert_out_of_order` | bool | `true` | `qemu-img convert -W` out-of-order writes |
| `backup_stall_timeout` | string | `"30m"` | Kill a transfer if the output file stops growing for this long. `"0s"` disables |
| `backup_create` | string | `"always"` | `"always"` or `"onchange"` (skip transfer when the source disk is unchanged) |
| `full_verify_after_create` | string | `"check"` | FULL verification after creation: `"off"`, `"metadata"`, `"check"`, `"compare"` |
| `full_verify_before_delete` | string | `"check"` | FULL verification before deletion: `"metadata"`, `"check"`, `"off"` (M1 always enforced) |
| `auto_cleanup` | bool | `true` | Remove stale `.tmp`/`.partial` files and NBD sockets before each run |
| `chain_verify_before_commit` | bool | `true` | Verify backing-chain integrity before blockcommit |
| `chain_verify_after_commit` | bool | `true` | Verify chain length decreased after blockcommit |
| `deep_check_schedule` | string | `"off"` | Deep-check cadence for `qsnap check --deep` reporting: `"off"`, `"weekly"`, `"monthly"` |
| `transaction_log` | string | none | Path to an append-only transaction log (one line per action) |

### VM Keys

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | string | **required** | VM name as known to libvirt |
| `snapshot_dir` | string | none | Default directory for this VM's snapshot overlays. Each disk may override it |
| `snapshot_create` | string | `"always"` | `"always"` or `"onchange"` (skip if disk allocation unchanged) |
| `snapshot_chain_length` | int | inherits global | VM-specific snapshot chain length |
| `target_chain_length` | int | inherits global | VM-specific target chain length |
| `target_keep_generations` | int | inherits global | VM-specific FULL generations to keep |
| `snapshot_preserve_min` | int | inherits global | VM-specific snapshot preservation floor |
| `snapshot_quiesce` | bool | `false` | Use `--quiesce` for filesystem-consistent snapshots (requires qemu-guest-agent) |
| `lifecycle_mode` | string | `"virsh"` | `"virsh"` (live blockcommit while running, offline commit when shut off) or `"qemu-img"` (offline-only) |
| `change_detection_mode` | string | `"allocation-map"` | `"allocation-map"` (compare `qemu-img map` regions) or `"allocation-size"` (compare `qemu-img info` actual-size) |
| `blockcommit_deep_verify` | bool | `false` | Run `qemu-img check` on the base image after offline commits |

### Disk Keys (`[[vm.disk]]`)

Each VM must declare at least one disk. Every disk owns its own base image and backing chain.

| Key | Type | Default | Description |
|---|---|---|---|
| `target` | string | **required** | libvirt device target name (e.g. `"vda"`, `"vdb"`) |
| `base_image` | string | **required** | Path to this disk's base qcow2 image |
| `snapshot_dir` | string | inherits VM | Optional per-disk snapshot directory override |

### Target Keys (`[[vm.target]]`)

| Key | Type | Default | Description |
|---|---|---|---|
| `path` | string | **required** | Directory where backup qcow2 files are stored |
| `target_chain_length` | int | inherits VM/global | Target-specific chain length |
| `target_keep_generations` | int | inherits VM/global | Target-specific FULL generations to keep |
| `verify` | string | `"metadata"` | `"off"`, `"metadata"`, or `"compare"` (chain-traversing `qemu-img compare`) |
| `compress` | bool | `true` | Compress FULL backups. Bitmap incrementals are always uncompressed |
| `compression_type` | string | `"zstd"` | `"zstd"` or `"zlib"`. Only when `compress = true` |
| `convert_parallel` | int | `4` | `qemu-img convert -m` parallel coroutines |
| `convert_out_of_order` | bool | `true` | `qemu-img convert -W` out-of-order writes |
| `backup_stall_timeout` | string | `"30m"` | Stall detection timeout. `"0s"` disables |
| `backup_create` | string | `"always"` | `"always"` or `"onchange"` |
| `backup_retry_max` | int | `3` | Retries for transient backup failures (exponential backoff) |
| `backup_retry_base` | string | `"2s"` | Base delay for retry backoff |

## The Multi-Disk Model

Every pipeline stage is keyed by disk target. For a VM with disks `vda` and `vdb`:

- **Snapshots** — one external overlay per disk per run, named `{vm}.{timestamp}_{disk}_{6hex}.qcow2`, each backing-chained to that disk's previous active layer.
- **Blockcommit** — each disk's chain is merged into **its own** `base_image`, independently. The active layer / XML tip of each disk is excluded and deferred on its own.
- **Retention** — `snapshot_chain_length`, `snapshot_preserve_min`, and the oldest-prefix filter are evaluated per disk.
- **Change detection** — allocation baselines are stored per `(vm, disk)`.
- **Backups** — each disk gets its own FULL and incremental chain on each target; checkpoints and NBD sockets are per disk.
- **Restore / Fork** — resolve the disk from the snapshot/backup name and act on that disk only.

`virsh snapshot-create-as --diskspec {disk},file={path},snapshot=external` already targets a single disk; qsnap issues one call per disk. Blockcommit passes `--path {disk}` and `--base {that disk's base_image}`.

## Retention Policy Guide

qsnap uses count-based retention. Four keys control chain length:

- **`snapshot_chain_length`** — max snapshots per disk before blockcommit merges the oldest into the base image.
- **`snapshot_preserve_min`** — the newest N snapshots are never blockcommitted, even when the chain length is exceeded. Applied after the oldest-prefix filter. `0` disables.
- **`target_chain_length`** — max incrementals per disk before the next backup creates a new FULL.
- **`target_keep_generations`** — FULL chains to keep per target. A FULL plus its incrementals is one generation.

Rule of thumb: **keep the newest N, remove the oldest.** For snapshots the newest N are kept and older ones are committed; for backups the newest N FULL generations are kept.

Preview the schedule before running:

```bash
qsnap run myvm --print-schedule     # or -S
qsnap run --timer                   # for cron/systemd: log summary, then run
```

## Snapshot Lifecycle (Blockcommit)

Snapshots that retention removes are merged back into their disk's base image to keep the backing chain short. qsnap picks the safe mechanism from the VM's **current power state** on every run, per disk:

| VM state | `lifecycle_mode = "virsh"` (default) | `lifecycle_mode = "qemu-img"` |
|---|---|---|
| **running** | Non-active snapshots committed **live** via `virsh blockcommit`; the active layer is deferred (`"vm_running"`) | Everything deferred (`"vm_running"`) — writing into a live base is unsafe |
| **shut off** | Offline `qemu-img commit` + child pivot + file deletion | Same as `"virsh"` |
| **paused / other** | Everything deferred (`"vm_running"`) | Everything deferred |

Deferred entries wait in state and drain automatically on a later run once the VM is in a compatible state. Deferral reasons:

- `"vm_running"` — the snapshot was the active layer of a running VM (or the VM was paused / in qemu-img mode).
- `"active_layer"` — the snapshot is the XML-referenced tip of a shut-off domain; never deleted offline (it would make the domain unbootable). Drains once it is no longer the tip.
- `"apparmor"` / `"selinux"` — the commit was blocked by MAC policy.

After offline commits, qsnap strips stale `<backingStore>` elements from the domain XML and redefines it, so `virsh start` re-probes the shortened chain and the VM stays bootable.

## Backups

Backups use the **NBD bitmap pull-model** (`virsh backup-begin` with checkpoint-based dirty-block tracking). `libvirt >= 7.2` and `python3-libnbd` are hard requirements.

### FULL backups

A FULL is a standalone qcow2 with no backing dependency, created via `qemu-img convert`. It is the anchor for subsequent incrementals and a self-contained restore point.

- The first backup to a target always starts with a FULL.
- A new FULL is created when a disk's incremental count exceeds `target_chain_length`.
- Running VMs: `virsh backup-begin` starts an NBD export and `qemu-img convert` reads `nbd:unix:<socket>`. Stopped VMs: direct `qemu-img convert` from the disk's source file.
- The FULL is written to a `.tmp` file and atomically renamed on success.
- Naming: `{vm}.FULL.{timestamp}_{disk}_{6hex}.qcow2`.

### Incremental backups

Each incremental is a backing-chained qcow2 delta (chained to the previous backup for the same disk, or to the FULL for the first one). An in-process copy loop over the libnbd bindings negotiates the `base:allocation` and `qemu:dirty-bitmap:backup-{disk}` meta-contexts and copies **only dirty, allocated blocks** — so an incremental is proportional to dirtied data, not disk size.

- Every `backup-begin` receives a checkpoint XML, so the successor checkpoint is created **atomically at the export's freeze point** — the backup chain is gap-free by construction.
- Checkpoint naming: `qsnap-{target_hash}-{disk}-{timestamp}-{6hex}`. Superseded checkpoints are deleted after a successful export, keeping one baseline per disk.
- Naming: `{vm}.{timestamp}_{disk}_{6hex}.qcow2`.

**First incremental after a FULL** contains all blocks written since the FULL's freeze point (the checkpoint baseline is anchored there). Its size is bounded by guest write rate × FULL duration, so schedule FULLs during low write activity.

### Backup limitations

- **Incrementals are uncompressed** — compressed qcow2 clusters can only be produced by `qemu-img convert`; the dirty-block loop writes via random-access `pwrite`. `compress`/`compression_type` apply to FULL backups, where the bulk of the bytes are.
- **Checkpoints live in libvirt, not in state files** — use `qsnap check --state` to detect orphaned checkpoints and `qsnap reconcile` to delete them.

### Verification

Per-transfer incremental verification is set per target via `verify`:

| Tier | Description |
|---|---|
| `"off"` | No verification |
| `"metadata"` | Default. qcow2 format + virtual-size; for incrementals also backing-filename and a dirty-size regression barrier |
| `"compare"` | Metadata + chain-traversing `qemu-img compare` content verification |

FULL backups get a separate three-tier check at lifecycle points:

| Tier | Checks | When |
|:----:|---|---|
| M1 | Format is `qcow2`, no `corrupt` bit | Post-create, pre-deletion (always enforced) |
| M2 | `qemu-img check` — zero errors/leaks | Post-create / pre-deletion when configured |
| M3 | `qemu-img compare` content comparison | Post-create when configured as `"compare"` |

A FULL that fails post-create verification is deleted and not recorded; a FULL that fails pre-deletion verification blocks deletion of its generation with a CRITICAL log.

## State Management

qsnap keeps per-VM JSON state under `state_dir` and offers three levels of consistency management:

1. **Read-only audit** — `qsnap check --state` reports phantom snapshots/FULLs, stale dependencies, corrupt state files, and orphaned checkpoints, without changing anything.
2. **Automatic self-healing** — every pipeline run validates state vs. disk at startup (non-fatal): phantom FULLs are removed from state with their dependencies, and stale allocation baselines are cleared.
3. **Active repair** — `qsnap reconcile` fixes both directions: removes phantom state entries and deletes orphan files/checkpoints on disk.

```bash
qsnap reconcile --dry-run    # preview
qsnap reconcile [vm]         # repair
```

Non-qsnap files on a target (those not matching the `{vm}.*` naming pattern) are never deleted — a WARNING is logged and they are skipped.

## Restore

`qsnap restore` replaces a stopped VM's disk with a flattened standalone qcow2 created from the named snapshot or backup. The disk is resolved from the snapshot/backup name.

```bash
qsnap restore <snapshot_name> [vm] [--dry-run] [--yes]
```

Steps:

1. **Resolve** the snapshot/backup by name across configured VMs (state + backup targets).
2. **Verify** the VM is stopped — aborts if running.
3. **Pre-verify** source chain integrity — aborts if broken.
4. **Convert** the source to a temporary standalone qcow2.
5. **Delete** that disk's old snapshot overlays.
6. **Atomically replace** the disk's base image (`os.replace`).
7. **Update domain XML** for that disk only — strip `<backingStore>`, update `<source file>`, `virsh define`.
8. **Reset state** for the VM and its targets.
9. **Best-effort** deletion of all `qsnap-*` checkpoints.

`--dry-run` logs planned actions without executing; `--yes` skips the confirmation prompt. After restore, the disk is a standalone qcow2 with no backing chain, and the next run creates a fresh snapshot and a new FULL.

## Fork

`qsnap fork` creates a standalone qcow2 from any qsnap-managed snapshot or backup. The result has no backing dependencies; creating a VM from it is the operator's responsibility.

```bash
qsnap fork <snapshot-name> --output <path> [vm]
```

1. **Resolve** the snapshot/backup by name across configured VMs.
2. **Estimate** total chain size and log it.
3. **Convert** to a standalone qcow2 via `qemu-img convert --force-share -O qcow2`.

`--force-share` allows reading the source even if it is the active layer of a running VM; the resulting image may be inconsistent if the guest is writing — stop the VM or fork a non-active snapshot for consistency. Fork produces a file as large as the full virtual disk (not sparse).

## Example Configurations

### Single-disk VM

```toml
[[vm]]
name = "desktop-vm"
snapshot_dir = "/var/lib/libvirt/snapshots/desktop-vm"
snapshot_create = "onchange"
snapshot_quiesce = true

  [[vm.disk]]
  target = "vda"
  base_image = "/var/lib/libvirt/images/desktop-vm.qcow2"

  [[vm.target]]
  path = "/mnt/usb-backup/desktop-vm"
  verify = "compare"
  compress = false
```

### Multi-disk VM

Each disk gets its own base image, chain, and backups:

```toml
[[vm]]
name = "prod-server"
snapshot_dir = "/var/lib/libvirt/snapshots/prod-server"
snapshot_quiesce = true

  [[vm.disk]]
  target = "vda"
  base_image = "/var/lib/libvirt/images/prod-server-os.qcow2"

  [[vm.disk]]
  target = "vdb"
  base_image = "/var/lib/libvirt/images/prod-server-data.qcow2"
  snapshot_dir = "/fast-nvme/snapshots/prod-server"   # per-disk override

  [[vm.target]]
  path = "/mnt/nas-backup/prod-server"
  verify = "compare"
  compress = true
```

### Minimal configuration

```toml
[[vm]]
name = "test-vm"
snapshot_dir = "/var/lib/libvirt/snapshots/test-vm"

  [[vm.disk]]
  target = "vda"
  base_image = "/var/lib/libvirt/images/test-vm.qcow2"

  [[vm.target]]
  path = "/tmp/backups/test-vm"
```

## Requirements

- Python 3.11+
- libvirt + virsh (**7.2+** — the NBD bitmap incremental backup API is complete since 7.2)
- qemu-img + qemu-nbd
- python3-libnbd (system package, e.g. `apt install python3-libnbd`) — hard requirement
- QEMU/KVM hypervisor

### libvirt Incremental Backup API

Incremental backups use the `<incremental>` XML element inside the `<domainbackup>` document passed to `virsh backup-begin` (there is no `--incremental` CLI flag):

```bash
virsh backup-begin --domain <vm> <backup.xml> <checkpoint.xml>
```

`backup.xml` references the prior checkpoint and the NBD socket; `checkpoint.xml` names the successor checkpoint created atomically at the export's freeze point. FULL exports omit the `<incremental>` element.

### Libvirt Permissions

The qsnap user needs libvirt access. Either add it to the `libvirt` group (`sudo usermod -aG libvirt qsnap`, then re-login) or configure polkit. Without access you will see errors like `Failed to connect to system bus`, `unauthorized`, or `Failed to get shared "write" lock`.

## Troubleshooting

- **Orphaned checkpoints** — `qsnap check --state` lists checkpoints whose target hash matches no configured target; `qsnap reconcile` deletes them (`virsh checkpoint-delete --metadata`).
- **`No space left on device` / `Permission denied`** — backup failures that are not retried; fix the target storage.
- **Broken backing chain** — `qsnap check` reports the broken file; qsnap halts destructive operations on a broken chain and commits only the intact prefix.
