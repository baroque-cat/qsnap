## Context

qsnap currently uses a btrbk-inspired time-bucket retention system with 5 bucket levels (hourly, daily, weekly, monthly, yearly), F-anchor syntax for FULL creation control, preserve_min floor, and preserve_day_of_week for weekly boundaries. This system requires ~500 lines of regex parsing, period-key computation, and multi-level anchor logic across `Core._parse_preserve()`, `BucketFullStrategy`, and `TimeBasedRetention`.

The bucket system creates non-obvious interactions: weekly FULLs inflate hourly/daily bucket counts at the chain level, making it impossible for users to predict how many items will be kept. The system answers a simple question ("how many?") with complex time-period arithmetic.

This change replaces the entire bucket system with a count-based paradigm: two integers (`chain_length`, `keep_generations`) replace 11 config fields, 5 spec files, 2 source files, and ~500 lines of logic.

**Current state:**
- `RetentionPolicy`: 11 fields (hourly, daily, weekly, monthly, yearly, preserve_min, 5× anchor_*)
- `TimeBasedRetention.evaluate()`: ~170 lines (bucket grouping, period keys, preserve_min windows)
- `BucketFullStrategy.should_create_full()`: period-key comparison across 5 bucket levels
- `Core._parse_preserve()`: ~60 lines of regex parsing for `"24h 7d 4w 12m 1y"` strings
- 14 call sites for `_parse_preserve()` across Core
- `FullBackupInfo.bucket_level`: stores which bucket triggered FULL creation
- `IBucketFullStrategy` / `BucketFullStrategy`: 2 files, ~130 lines total

**Constraints:**
- Clean break — no migration path, no backward compatibility
- DI architecture must be preserved (ABC interfaces, factory, state manager)
- Oldest-prefix post-processing for snapshots must remain (blockcommit constraint)
- Per-chain retention grouping for targets must remain (chain integrity)
- FULL verification (M1/M2/M3) must remain — now gates deletion
- systemd controls frequency (default: hourly), not config

## Goals / Non-Goals

**Goals:**
- Replace 11 bucket fields with 2 count-based fields (`chain_length`, `keep_generations`)
- Replace ~170-line bucket evaluate() with ~20-line count-based evaluate()
- Delete `IBucketFullStrategy` / `BucketFullStrategy` entirely — FULL decision becomes a simple count check
- Delete `Core._parse_preserve()` — config values are plain integers
- Remove `bucket_level` from all interfaces and state
- Add verify-before-delete gate: old generations deleted only after new FULL verified
- Add rollback mechanism: failed FULL verification → delete broken FULL + checkpoint → retry
- Simplify `schedule_summary()` / `estimate()` — no synthetic timestamps, no bucket breakdown
- Update all documentation (qsnap.toml.example, README.md, AGENTS.md, TESTING.md)

**Non-Goals:**
- No migration path for existing config files or state JSON
- No backward-compatible parsing of old `"24h 7d"` strings
- No time-based retention fallback
- No new backup provider or snapshot provider
- No changes to NBD transfer, blockcommit, change detection, or state reconciliation
- No changes to per-chain grouping (`_group_backups_by_chain`) or cascade deletion
- No changes to oldest-prefix post-processing for snapshots

## Decisions

### D1: Single `RetentionPolicy` with two fields (not separate snapshot/target policies)

**Choice:** One `RetentionPolicy(chain_length: int, keep_generations: int)` for both snapshots and targets.

**Rationale:** The retention engine is the same for both — "keep newest N, remove oldest." For snapshots, N = `chain_length`. For targets (per-chain), N = `keep_generations`. The engine doesn't care which field it uses — Core passes the appropriate value. This avoids two separate policy classes and keeps the `IRetentionEngine` interface unchanged (it receives a `RetentionPolicy`).

**Alternative considered:** Separate `SnapshotRetentionPolicy(chain_length)` and `TargetRetentionPolicy(chain_length, keep_generations)`. Rejected — adds complexity without benefit. The engine is identical; only the input list differs (individual snapshots vs. chains).

### D2: FULL creation is a simple count check (not a strategy)

**Choice:** Delete `IBucketFullStrategy` / `BucketFullStrategy` entirely. FULL creation decision is inline in `Core._backup_target()`:

```python
incremental_count = len(deps) if all_fulls else 0
should_full = incremental_count > target.chain_length
```

**Rationale:** The bucket strategy existed because FULL creation was tied to calendar period boundaries (yearly, monthly, weekly). With count-based retention, the decision is a single integer comparison. A strategy class for `a > b` is over-engineering.

**Alternative considered:** Keep a strategy interface but simplify to `IFullStrategy.should_create_full(incremental_count, chain_length) -> bool`. Rejected — a one-liner doesn't need a class. If future strategies are needed (e.g., time-based FULLs), a new interface can be added without changing the count-based logic.

### D3: Verify-before-delete gate in `_backup_target()`

**Choice:** Old generations are deleted only after the new FULL passes M1/M2 verification. The flow is:

```
1. Create FULL → verify
2. If verified → record in state → evaluate retention → cleanup old generations
3. If NOT verified → rollback (delete FULL file + checkpoint) → retry
4. If retries exhausted → CRITICAL log, keep old generations
```

**Rationale:** The current system creates FULL, verifies, and records — but deletion of old generations happens in a separate retention step that doesn't check if the new FULL is valid. With `keep_generations=1`, a failed FULL would leave no valid generation if the old one was already deleted.

**Alternative considered:** Verify in `_cleanup_backups()` before deleting. Rejected — too late. The cleanup operates on a retention result that was already computed; gating it on verification status is fragile. Better to gate the entire retention+cleanup sequence.

### D4: Rollback deletes FULL file + checkpoint + state records

**Choice:** On verification failure, Core calls:
1. `provider.delete(SnapshotInfo(...))` — deletes the FULL file from disk
2. `_cleanup_failed_checkpoint(vm_config, target, full_result)` — deletes the libvirt checkpoint via `virsh checkpoint-delete`
3. `state.remove_full_backup(target_path, full_name)` — removes any state record

**Rationale:** The FULL creation via `virsh backup-begin` atomically creates both the FULL file and a checkpoint. If verification fails, both must be cleaned up to prevent orphaned checkpoints from confusing the next `transfer_missing()` call (which discovers checkpoints via `virsh checkpoint-list`).

**Alternative considered:** Let the next run's stale-state self-healing clean up. Rejected — too slow. The retry loop needs a clean slate immediately.

### D5: `snapshot_chain_length` trigger is `>` (strictly greater than)

**Choice:** Blockcommit triggers when `len(snapshots) > snapshot_chain_length`. With `chain_length=168` and hourly snapshots, blockcommit happens on the 169th snapshot.

**Rationale:** The user specified `>` semantics. With `chain_length=168`, exactly 168 snapshots live at any time; the 169th triggers blockcommit of the oldest prefix. After blockcommit, the chain shortens and accumulation restarts.

### D6: `target_chain_length` trigger is `>` (strictly greater than)

**Choice:** New FULL triggers when `incremental_count > target_chain_length`. With `chain_length=168` and hourly backups, a new FULL is created on the 169th incremental.

**Rationale:** Consistent with D5. The first FULL is created when no FULLs exist (first backup to target).

### D7: No `preserve_min` analog

**Choice:** No minimum retention floor. `chain_length` IS the minimum — you always keep exactly N items.

**Rationale:** `preserve_min` existed to prevent bucket counts from deleting very recent items. With count-based retention, the count IS the floor — you always keep the newest N. There's no scenario where a count-based engine would delete items newer than the keep window.

### D8: Read-tolerant JSON state for old `bucket_level` field

**Choice:** `JsonStateManager` reads old JSON files with `entry.get("bucket_level", None)` — the field is ignored if present. New records are written without `bucket_level`.

**Rationale:** Even though there's no migration path, users may have old state files. Read-tolerance prevents crashes on old state. The field is simply ignored — no warning, no migration.

## Risks / Trade-offs

- [Risk: Users with existing configs must manually update] → Mitigation: Clear error message on unknown TOML keys (`snapshot_preserve`, `target_preserve`). Document the migration in README.
- [Risk: Old JSON state files with `bucket_level` cause confusion] → Mitigation: Read-tolerant — field is silently ignored. No crash, no warning.
- [Risk: Rollback incomplete — broken FULL stays on disk] → Mitigation: `_cleanup_failed_checkpoint()` + `provider.delete()` in the rollback path. If both fail, the next run's stale-state self-healing catches it.
- [Risk: `keep_generations=1` + failed verification → no valid generation] → Mitigation: Verify-before-delete gate ensures old generation is NOT deleted until new FULL is verified. If verification fails, old generation stays.
- [Risk: Infinite retry on systematic FULL creation failure] → Mitigation: `backup_retry_max` limits retries. CRITICAL log after exhaustion. Pipeline continues to incremental transfer.
- [Risk: Oldest-prefix post-processing becomes dead code] → Mitigation: Keep it as a safety net. Count-based retention naturally produces a contiguous oldest prefix, but the post-processing catches edge cases (e.g., manually deleted snapshots creating gaps).
- [Trade-off: No time-based retention at all] → Users who want "keep 7 daily snapshots" must calculate: with hourly snapshots, `chain_length=168` gives 7 days. This is less intuitive for time-based use cases but more predictable for count-based use cases.
