## Context

qsnap currently uses a `full_every` interval-based config option to trigger periodic FULL backups on targets. This is disconnected from the retention policy — a user can have `target_preserve = "24h 7d 1w 0m 0y"` (no monthly/yearly buckets) but `full_every = "30d"`, creating FULLs that don't align with any retention bucket. The system also lacks dependency tracking between FULLs and incrementals: `_find_full_anchor()` picks the newest FULL by mtime (not timestamp), and incrementals always rebase to whatever FULL is most recent, not the one they were created against. Additionally, `cp` is used as a fallback transfer mechanism alongside `rsync`, creating two code paths. The system has no runtime size estimation, leaving operators blind to projected storage growth.

## Goals / Non-Goals

**Goals:**
- Unify FULL backup creation with the retention policy: FULLs are created at the highest active bucket boundary
- Track dependencies between FULLs and incrementals to enable safe cascade deletion
- Replace all `cp` usage with `rsync` (single code path, hard requirement)
- Add runtime size estimation logging on every pipeline run
- Add `qsnap estimate` CLI command for standalone projection
- Stop copying `base.qcow2` to targets (first backup is always FULL)
- Update README.md to reflect all changes

**Non-Goals:**
- Fork/clone command (`qemu-img create -b` + XML modification + pin mechanism) — separate future change
- Pin mechanism for snapshots — depends on fork/clone
- Compression of incremental snapshots on target — separate research
- BitmapBackupProvider FULL logic changes — NBD mode uses different transfer mechanism, needs separate analysis
- Changes to `TimeBasedRetention.evaluate()` logic — the retention engine remains a pure function; dependency-aware deletion is handled by Core

## Decisions

### D1: Bucket-driven FULL creation (replaces `full_every`)

**Decision:** FULL creation is triggered by the retention policy's highest active bucket. The first snapshot entering a new period of the highest bucket (yearly > monthly > weekly > daily > hourly) becomes a FULL via `qemu-img convert`.

**Rationale:** `full_every` is a separate concept that doesn't align with retention. If a user has `24h 7d 1w 0m 0y`, FULLs should be weekly (the highest bucket), not arbitrary intervals. This unifies the two concepts.

**Alternatives considered:**
- Keep `full_every` but auto-derive it from the highest bucket — rejected: adds complexity, users might override it incorrectly
- Create FULL when oldest incremental is about to be deleted (reactive) — rejected: creates FULLs at unpredictable times, harder to reason about

**Edge case:** If all bucket counts are 0 and `preserve_min = "all"`, no FULL is created (chain grows indefinitely, nothing is deleted). If all bucket counts are 0 and `preserve_min != "all"`, config validation rejects it (would break the chain without an anchor).

### D2: Dependency-aware cascade deletion

**Decision:** A FULL is only deleted when BOTH conditions are met: (1) it falls out of ALL retention buckets, AND (2) no incremental in the keep-set references it. When a FULL is deleted, all orphaned incrementals (those that referenced it and are not in the keep-set) are cascade-deleted.

**Rationale:** Without this, deleting a FULL breaks all incrementals that reference it. The current system has no dependency tracking at all.

**Implementation:** `IStateManager` gains `get_full_backups(target_path) -> list[FullBackupInfo]` (replaces single-FULL tracking), `record_full_backup(target_path, name, timestamp, bucket_level)`, `get_incremental_dependencies(target_path, full_name) -> list[str]`, and `record_incremental_dependency(target_path, incremental_name, full_name)`. Core's `_cleanup_backups()` checks dependencies before deleting any FULL.

### D3: rsync as sole transfer mechanism

**Decision:** Remove `cp` fallback. Always use `rsync` (with `--bwlimit` when rate limiting is configured, without when not). `rsync` becomes a hard requirement in `_validate_environment()`.

**Rationale:** Two code paths (rsync + cp) add complexity. rsync is available on virtually all Linux systems. rsync provides `--partial` (resume on interruption), better error codes, and progress reporting even without rate limiting.

### D4: `copy_base=false` default (first backup is FULL)

**Decision:** `base.qcow2` is never copied to the target. The first backup to a target is always a FULL (via `qemu-img convert`). A new `copy_base: bool = False` field on `TargetConfig` allows opting back into the old behavior.

**Rationale:** Copying `base.qcow2` duplicates the entire base image on the target. A FULL backup via `qemu-img convert` is standalone and serves as a proper anchor for incrementals.

### D5: Size estimation logging

**Decision:** On every pipeline run (including dry-run), Core logs a projected target size estimate based on: `qemu-img info` actual-size of the base image, average incremental size from state history, and retention policy bucket counts. Also exposed via `qsnap estimate [vm]` CLI command.

**Rationale:** Operators need visibility into projected storage growth. The data is already available (base image size, snapshot sizes in state, retention policy) — it just needs to be computed and logged.

### D6: `full_compress` renamed to `compress`

**Decision:** `full_compress` is renamed to `compress` (target-level, default `true`). A global `compress` default is added to `GlobalConfig`.

**Rationale:** With `full_every` removed, the `full_` prefix is meaningless. Default changed to `true` because compression typically saves 50-70% on qcow2 FULLs with negligible CPU overhead.

## Risks / Trade-offs

- **[Cascade deletion removes needed data]** → Mitigation: `--preserve` flag skips all deletion; dry-run logs planned deletions; unit tests with edge cases (FULL with dependents, FULL without dependents, mixed keep-set)
- **[`_full_backups.json` format migration]** → Mitigation: Auto-migration on load — if value is a dict (old format, single FULL), wrap it in a list. If list, use as-is.
- **[rsync unavailable on minimal systems]** → Mitigation: Hard error in `_validate_environment()` with clear message: "rsync is required but not found in PATH. Install rsync or use a system that includes it."
- **[Size estimation inaccuracy]** → Mitigation: Log as "approximate"; use rolling average of last N snapshot sizes; document that churn is variable
- **[Bucket boundary edge cases]** → Mitigation: Tests with timestamps at exact bucket boundaries (month start, week start, day start)
- **[`copy_base=false` breaks existing targets with base.qcow2]** → Mitigation: If `base.qcow2` is detected on an existing target, log a WARNING and continue (don't delete it, just don't create new ones)
- **[Breaking change for users with `full_every` in config]** → Mitigation: Config parsing ignores `full_every` with a deprecation WARNING. `full_compress` is mapped to `compress` if `compress` is not explicitly set.
