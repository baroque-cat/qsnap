## Context

qsnap currently triggers FULL (standalone, self-contained) backups only at the boundary of the single highest active retention bucket. With `target_preserve = "48h 14d 8w 12m 1y"`, the highest bucket is `yearly` — producing ONE FULL per year and up to 365 incrementals in the chain. A single corrupt incremental makes the entire chain unrecoverable.

Additionally, there is no command to create an independent fork of a VM from a snapshot. Users must manually `qemu-img convert` + craft libvirt XML. This is the most requested operational workflow that qsnap does not automate.

Existing constraints:
- `IStateManager._full_backups.json` already stores a LIST of FULL records with `bucket_level` per entry — multi-FULL tracking infrastructure exists.
- `Core.restore()` already copies entire backing chains and rebases to relative paths — chain resolution infrastructure exists.
- `_parse_preserve()` uses regex `(\d+)([hdwmy])` to parse retention tokens.
- `RetentionPolicy` is a `@dataclass(frozen=True)` with `hourly, daily, weekly, monthly, yearly, preserve_min`.
- Modules do NOT inherit from Core (design D1). New functionality must follow the same ABC/factory/injection pattern.

## Goals / Non-Goals

**Goals:**
1. Reduce maximum incremental chain length from ~365 days to ~7 days for typical multi-bucket policies.
2. Provide manual F-syntax override for environments where automatic multi-level FULLs are undesirable.
3. Provide `qsnap fork` — one-command creation of a fully independent VM from a snapshot or backup.
4. Provide `qsnap deploy` — deploy a backup as a running VM.
5. Maintain backward compatibility: policies without F-prefix parse identically (except the new all-buckets behavior).

**Non-Goals:**
- Fork tracking (preventing parent snapshot deletion when a fork exists). Out of scope — fork creates a standalone qcow2, so deletion is already impossible.
- Cross-VM dependency tracking for forks. The fork is fully independent.
- Changing the CLI beyond adding `fork` and `deploy` subcommands.
- Changing the storage format of `_full_backups.json`.

## Decisions

### Decision 1: All-active-buckets default (not optional)

**Choice:** All active buckets trigger FULLs by default. No config knob to revert to old behavior.

**Rationale:** The old behavior (highest-only) is universally worse — it produces fragile chains. Users who want fewer FULLs can reduce their active bucket counts or use F-syntax. A config knob would be dead weight (never intentionally chosen).

**Alternatives considered:** A `full_anchor_mode` config field (`"highest"` / `"all"` / `"manual"`). Rejected — over-engineering for a behavior nobody wants.

### Decision 2: F-syntax integrated into preserve string

**Choice:** `24h 7Fd 4w` — F is a prefix on the count, inside the existing `target_preserve` string. Not a separate config field.

**Rationale:** Single source of truth. btrbk-like elegance. Prevents inconsistency between retention counts and anchor schedule (can't have F on a 0-count bucket).

**Alternatives considered:** Separate `full_anchor = "daily weekly"` field. Rejected — two fields that must stay in sync is a UX hazard.

### Decision 3: Fork uses qemu-img convert, not chain copy

**Choice:** `qsnap fork` runs `qemu-img convert -O qcow2 <snapshot> <target>` to produce a single standalone qcow2. Not copy-the-chain-and-rebase.

**Rationale:** The standalone file is immune to parent snapshot deletion. No backing chain. One file. The entire point of fork is independence — a chain copy would still be a chain, just in a different directory.

**Alternatives considered:** `qsnap restore` (copy chain + rebase). Rejected — produces a chain that is still fragile. The user explicitly asked for FULL-based fork.

### Decision 4: Fork uses the restored/converted file directly as the VM disk

**Choice:** No additional overlay on top of the converted file.

**Rationale:** `qemu-img convert` produces a writable qcow2. An overlay would add unnecessary complexity. The user can snapshot the fork-VM with qsnap afterward if they want a clean base.

### Decision 5: Deploy is a thin wrapper around fork

**Choice:** `qsnap deploy` = `qsnap restore` (for chain resolution from backups) + `qemu-img convert` (for flattening) + VM creation (same as fork).

**Rationale:** No new code paths for chain handling. Both snapshots and backups use the same resolution logic already in `Core.restore()`.

### Decision 6: IStateManager format unchanged

**Choice:** No migration of `_full_backups.json`. Use existing `get_full_backups()` method and filter by `bucket_level` in memory.

**Rationale:** The format already stores `bucket_level` per FULL record and supports multiple entries per target. No migration means zero risk of state corruption.

### Decision 7: `_should_create_bucket_full` gets last_full from full list

**Choice:** Change `Core._backup_target()` to call `state.get_full_backups(target.path)` (returns list) instead of `state.get_last_full_backup(target.path)` (returns one). Pass the full list to `_should_create_bucket_full`.

**Rationale:** Enables per-bucket period comparison without changing IStateManager interface. The full list is typically small (1-5 entries).

## Risks / Trade-offs

**[Risk] Policies like "48h 14d 8w 12m 1y" now produce many more FULLs**
→ Mitigation: Each FULL is ~the size of a base image. Users can reduce active bucket counts (e.g., remove `8w`) to reduce FULL frequency. The F-syntax provides explicit control. Dry-run + `--print-schedule` shows what will happen before it happens.

**[Risk] F-syntax is backward-incompatible with older qsnap versions**
→ Mitigation: Older qsnap would fail to parse `"7Fd"` as a valid token. This is acceptable — the F-syntax is opt-in. Documentation must clearly state the minimum qsnap version for F-syntax.

**[Risk] `qemu-img convert` on a large VM is slow (reads entire chain)**
→ Mitigation: Fork is an interactive command, not scheduled. The user expects it to take time. We log progress. For extremely large VMs, the user can create a FULL backup first (which also runs convert) and fork from that.

**[Risk] Fork produces a file as large as the full virtual disk (not sparse like the chain)**
→ Mitigation: This is inherent to standalone qcow2. The user accepts the trade-off of independence vs. storage efficiency. We log the estimated size before starting.

**[Risk] Deploy from incremental backup requires chain resolution through the backup target**
→ Mitigation: `Core.restore()` already handles this — it follows backing-filename references through the backup target's directory.

## Migration Plan

1. **Deploy new code.** No config changes needed for existing users — policies without `F` continue to parse identically. Behavior change: multi-bucket policies now produce FULLs at all levels instead of just the highest. Users who want the old behavior must reduce their bucket counts or add F-syntax.

2. **No state migration.** `_full_backups.json` format is unchanged.

3. **Rollback:** Downgrade to previous qsnap version. Existing FULL records and incremental dependencies are unaffected. The next run will use old highest-bucket logic.

## Open Questions

1. **Should `qsnap fork --add-to-config` auto-generate a complete VM config block?** Or just the `[[vm]]` header with `base_image` and `snapshot_dir`? Decision: generate a minimal but complete block with `snapshot_create = "always"` and an empty `targets` list. The user can edit it afterward.

2. **Should fork auto-increment VM name suffix?** E.g., `my-vm` → `my-vm-fork1`, `my-vm-fork2`. Decision: no. The `--as-vm` flag is explicit. Auto-increment would be surprising.

3. **Should fork require the source VM to be shut off?** `qemu-img convert` can read a live VM's chain via `--force-share`. Decision: no. The snapshot files are read-only once created, so convert is safe on a running VM's snapshots. But we should log a WARNING if the VM is running.
