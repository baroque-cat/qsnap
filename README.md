# qsnap

QEMU/KVM snapshot and backup orchestration for qcow2 images on any filesystem (XFS, ext4, etc.), inspired by [btrbk](https://github.com/digint/btrbk).

qsnap manages external disk-only snapshots via `virsh`, detects whether a VM disk has changed, enforces retention policies, performs incremental backups to separate storage, and maintains backing chain integrity via `blockcommit`.

## Features

- **External snapshots** — disk-only, no-metadata snapshots via `virsh snapshot-create-as`
- **Change detection** — skip snapshot creation when the disk hasn't changed (`onchange` mode)
- **Incremental backups** — file-copy or NBD bitmap-based backup to remote targets
- **Backup verification** — metadata or full `qemu-img compare` verification after backup
- **Retention policies** — time-based retention with hourly/daily/weekly/monthly/yearly buckets
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

[[vm]]
name = "debiantest"
base_image = "/var/lib/libvirt/images/debiantest.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/debiantest"

# Optional: skip snapshots when disk hasn't changed
# snapshot_create = "onchange"

# Optional: use --quiesce for filesystem-consistent snapshots
# snapshot_quiesce = false

# Optional: lifecycle mode ("virsh" or "qemu-img")
# lifecycle_mode = "virsh"

  [[vm.target]]
  path = "/mnt/backup/debiantest"
  incremental = true
  # Backup verification: "metadata" (default), "full", or "off"
  # verify = "metadata"
```

2. Run the full pipeline (snapshot + backup + retention):

```bash
qsnap run debiantest
```

3. Or run individual steps:

```bash
qsnap snapshot debiantest    # create snapshots only
qsnap backup debiantest      # transfer backups only
qsnap prune debiantest       # retention + cleanup only
```

## Commands Reference

| Command | Description |
|---|---|
| `qsnap run [vm]` | Full pipeline: snapshot → backup → retention |
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

## Documentation

- [AGENTS.md](AGENTS.md) — Architecture, design patterns, and module contracts
- [TESTING.md](TESTING.md) — Test architecture and conventions

## Requirements

- Python 3.11+
- libvirt + virsh (libvirt 6.0+ for NBD bitmap backup)
- qemu-img
- QEMU/KVM hypervisor

## License

MIT
