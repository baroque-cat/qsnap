## Context

qsnap's retention pipeline currently hardcodes `preserve_min="0h"` for any time-bucket policy via `Core._parse_preserve()`. The `GlobalConfig`, `VMConfig`, and `TargetConfig` dataclasses have no fields for `preserve_min`—making it impossible to configure btrbk-style "keep all snapshots for at least N hours" behavior. Users configure retention blindly with no tooling to preview the outcome. Backup verification at `"metadata"` level checks qcow2 headers but cannot detect silent `cp` corruption or filesystem-level bit rot. The incremental backup chain on the target is a single linked list of backing files—one corrupted file breaks all subsequent backups. The README exists but lacks production deployment guidance.

The project follows a strict DI/ABC paradigm: immutable frozen dataclasses for config, `Core` as sole orchestrator, modules as stateless workers receiving config as method parameters, and `IShell` wrapping all external commands.

## Goals / Non-Goals

**Goals:**
- Make `preserve_min` a first-class config key with global→VM→target inheritance
- Provide a `schedule_summary()` that shows expected chain structure before any mutations
- Add `verify="hash"` as a fast (seconds) binary integrity check between `"metadata"` and `"full"`
- Add periodic anchor full backups on the target to protect incremental chains
- Provide a comprehensive README with production deployment scenarios

**Non-Goals:**
- SSH remote target support (separate change)
- `qsnap archive` command (separate change)
- `qsnap clone` / fork-to-new-VM (separate change)
- Full-disk SHA-256 of virtual disk contents (too slow; `verify="full"` already exists for byte-level comparison)
- Automatic compaction/de-fragmentation of qcow2 (separate concern)

## Decisions

### D1 — preserve_min as separate config keys with full inheritance

**Choice**: Add `snapshot_preserve_min` and `target_preserve_min` fields to `GlobalConfig`, `VMConfig`, and `TargetConfig` (all `str | None = None`). `ConfigFacade` parses them from TOML and applies inheritance: global → VM → target (same chain as existing `snapshot_preserve` / `target_preserve`). `Core._parse_preserve()` gains an optional `preserve_min` parameter; when `None`, preserves existing behavior (`"0h"` for time-bucket policies).

**Rationale**: btrbk has separate `*_preserve` (time-bucket schedule) and `*_preserve_min` (age-based floor) keys. qsnap's config model already supports global→VM→target inheritance via `_build_vm()` and `_build_target()`—adding new keys follows the same pattern with no new mechanism. A single key combining both (e.g., `"24h 2d +3h"`) would be harder to parse and less discoverable.

**Alternatives**: (1) Parse preserve_min from the preserve string itself (e.g., split on `+`)—rejected: surprising syntax, harder to validate. (2) Only at VM level—rejected: global defaults avoid boilerplate.

### D2 — schedule_summary as a Core method, not a CLI concern

**Choice**: `Core.schedule_summary(vm_filter) -> str` simulates retention against a realistic timestamp distribution (one snapshot per hour for the configured retention window + 50% margin). It uses the same `TimeBasedRetention.evaluate()` engine but feeds it synthetic `RetentionItem` timestamps. `TimeBasedRetention` gains an `explain()` method that returns a structured dict (`{"hourly": {"count": N, "range": (start,end)}, ...}`). The summary is logged at INFO every pipeline run and displayed by `qsnap --print-schedule`.

**Rationale**: The simulation should use the SAME retention engine—not a separate calculator—to guarantee fidelity. Keeping it in Core (not CLI) means it works regardless of entry point (CLI, systemd timer, programmatic). Using synthetic timestamps avoids coupling to actual state which may be empty on first run.

**Alternatives**: (1) Simulate only from actual recorded snapshots—rejected: first run has no snapshots, produces empty output. (2) Add a CLI-only `--explain` flag—rejected: useful for programmatic/log use too.

### D3 — verify="hash" as binary SHA-256 of the qcow2 file, not the virtual content

**Choice**: New `verify="hash"` tier in `verify_backup()`. `ExternalSnapshotProvider.create()` computes `hashlib.sha256()` of the newly created qcow2 file in 8MB chunks, returned in `SnapshotResult.content_hash`. `Core._create_snapshot()` stores it in `SnapshotInfo.content_hash` via `IStateManager.record_snapshot()`. During backup, `verify_backup()` receives `expected_hash`, computes the hash of the target file, and compares.

**Rationale**: `qsnap` uses `cp` to transfer files—the files should be byte-identical. Hashing the binary qcow2 file catches bit rot and silent copy errors. This is fast (~200-500 MB/s on SSD; a typical 2GB overlay hashes in 4-10 seconds). Hashing virtual disk content (via `qemu-img convert -O raw | sha256sum`) would be 10-100x slower because it reads the entire virtual disk including unallocated regions backfilled from the backing chain. `verify="full"` already exists for byte-level virtual-content comparison via `qemu-img compare`.

**Alternatives**: (1) Hash only the allocated extents via `qemu-img map`—rejected: adds complexity for marginal speed gain on typical overlay sizes. (2) Use `qemu-img check` for integrity—rejected: checks internal qcow2 consistency, not file-level corruption.

### D4 — Full backup via qemu-img convert, incrementals rebase to the anchor

**Choice**: `TargetConfig` gains `full_every: str = "0d"` (duration string, `"0d"` = disabled) and `full_compress: bool = False`. `Core._backup_target()` checks `IStateManager.get_last_full_backup(target.path)` before the incremental transfer loop. If the configured interval has elapsed, it calls `FileCopyBackupProvider.create_full_backup()` which runs `qemu-img convert [-c] -f qcow2 -O qcow2 <latest_snapshot> <target>/vm.FULL.YYYYMMDD.qcow2`. After successful creation, `IStateManager.set_last_full_backup()` records the timestamp. `FileCopyBackupProvider.transfer_missing()` checks for an existing FULL anchor: if one exists, it rebases new incrementals to `./vm.FULL.YYYYMMDD.qcow2` instead of the source backing filename.

**Rationale**: `qemu-img convert` creates a standalone file with all unique data merged—no backing chain dependency. Rebase via `qemu-img rebase -u` is metadata-only (instant) and safe because the FULL file contains all guest-visible data at all offsets. The anchor is a regular qcow2 file—target retention treats it just like any other backup.

**Why `rebase -u` is safe**: The incremental overlay stores only COW clusters referenced by GUEST offsets. QEMU reads these and asks the backing file for guest offset X. Since the FULL file contains data for ALL guest offsets (it's a complete merged copy), every read will succeed regardless of where the data lives internally in the FULL file. The `-u` (unsafe) flag skips the consistency check, which is fine because we KNOW the content is identical.

**Alternatives**: (1) Use `qemu-img commit` on the target—rejected: would destroy the target's incremental chain, and qemu-img commit modifies files in place (dangerous on a backup). (2) Create a new base after every N incrementals without convert—rejected: doesn't protect against chain corruption, just makes a longer chain. (3) Never rebase, just keep the anchor as bonus—rejected: the old fragile chain remains.

### D5 — full_compress as optional, default off

**Choice**: `full_compress: bool = False`. When True, `qemu-img convert -c` enables zlib compression on the output qcow2.

**Rationale**: Compression saves 20-40% space but is 3-5x slower (CPU-bound). For small home VMs, the space savings matter. For production TB-scale VMs, the CPU cost during a time-sensitive backup window is unacceptable. Making it optional lets users decide per target.

## Risks / Trade-offs

| Risk | Impact | Mitigation |
|---|---|---|
| **preserve_min="0h" with empty time-bucket policy** — all snapshots removed immediately | Data loss | `schedule_summary()` warns when total kept == 0. Default `preserve_min=None` for backwards compat, but document strongly that users should set it. |
| **SHA-256 of multi-GB overlay on slow disk** — snapshot creation stalls for 30-60s | Pipeline latency | Hash only when `target.verify` requires it (computed lazily or skipped if no target uses `verify="hash"`). Hash in 8MB chunks to limit memory. |
| **qemu-img convert fails mid-transfer** — partial FULL file on target, no anchor | Next cycle retries | Atomicity: convert to `.tmp` file, `mv` to final name on success. State only updated after successful rename. |
| **FULL + new incrementals uses MORE space than old chain temporarily** | Target disk pressure during transition | Order: create FULL first, THEN delete old chain (not before). Old chain stays until retention cleanup runs—2x space temporarily. |
| **FULL file naming collision** — two FULLs on same day | Second FULL overwrites first | Include `_N` suffix like snapshot naming. Multiple FULLs per day are allowed (if `full_every` is short). Retention handles them normally. |

## Open Questions

- Should `schedule_summary` run on EVERY pipeline invocation (INFO log) or only on `--print-schedule`? Resolution: INFO log on every timer invocation, `--print-schedule` on CLI. This gives visibility in journald without spamming interactive use.
- Should `full_every` accept count-based intervals (e.g., `"10 backups"`) or only time-based (`"7d"`)? Resolution: time-based only for v1. Count-based requires a persistent counter per target—adds state complexity without clear use case.
