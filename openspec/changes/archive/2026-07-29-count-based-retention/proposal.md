## Why

The current bucket-based retention system (hourly/daily/weekly/monthly/yearly buckets, F-anchor syntax, preserve_min floor, preserve_day_of_week) is over-engineered and ambiguous. Users cannot intuitively predict how many snapshots or backup chains will be kept. The bucket semantics interact in non-obvious ways (e.g., weekly FULLs inflate hourly/daily bucket counts at the chain level). The system needs ~500 lines of regex parsing, period-key computation, and multi-level anchor logic to answer a simple question: "how many items should I keep?"

The new paradigm is count-based: specify chain length (how many items accumulate before triggering blockcommit or new FULL) and, for targets only, how many generations (FULL chains) to keep. Snapshots have no generations — blockcommit is destructive. Frequency is controlled by systemd (default: hourly), not by config. This is a clean break — no migration path, no backward compatibility.

## What Changes

- **BREAKING**: Replace `RetentionPolicy` dataclass (11 bucket fields) with 2 count-based fields: `chain_length: int` and `keep_generations: int`
- **BREAKING**: Replace `TimeBasedRetention.evaluate()` (~170 lines of bucket grouping, period keys, preserve_min windows) with ~20 lines of count-based logic (keep newest N, remove oldest)
- **BREAKING**: Delete `IBucketFullStrategy` / `BucketFullStrategy` entirely — FULL creation is now a simple count check: `incremental_count > target_chain_length`
- **BREAKING**: Delete `Core._parse_preserve()` (~60 lines of regex parsing for `"24h 7d 4w 12m 1y"` strings) — config values are now plain integers
- **BREAKING**: Remove `bucket_level` from `FullBackupInfo`, `IBackupProvider.create_full_backup()`, `IStateManager.record_full_backup()`
- **BREAKING**: Remove `preserve_day_of_week`, `snapshot_preserve`, `target_preserve`, `snapshot_preserve_min`, `target_preserve_min` from `GlobalConfig`, `VMConfig`, `TargetConfig`
- **BREAKING**: Add `snapshot_chain_length`, `target_chain_length`, `target_keep_generations` to `GlobalConfig`, `VMConfig`, `TargetConfig`
- **BREAKING**: Remove `IVMModuleFactory.create_bucket_full_strategy()` from factory interface and all implementations
- New: FULL creation transaction with verify-before-delete gate — old generations are only deleted after the new FULL passes M1/M2 verification; on failure, the broken FULL + checkpoint are rolled back and retried
- New: `_cleanup_failed_checkpoint()` method in Core for rollback (deletes orphaned checkpoint + FULL file after verification failure)
- Simplify: `schedule_summary()` / `estimate()` — delete `_retention_window()`, `_generate_synthetic_items()`; output shows chain_length and keep_generations instead of bucket breakdown
- Update: `qsnap.toml.example`, `README.md`, `AGENTS.md`, `TESTING.md` to reflect count-based paradigm
- Delete specs: `retention-engine`, `bucket-full-strategy`, `full-anchor-syntax`, `multi-level-full-anchors`, `preserve-min-config`
- Rewrite specs: `schedule-summary`, `periodic-full-backup`, `config-model`, `config-parsing`, `core-orchestrator`

## Capabilities

### New Capabilities

- `count-based-retention`: Count-based retention engine replacing bucket-based. Defines `RetentionPolicy(chain_length, keep_generations)`, count-based `evaluate()` (keep newest N, remove oldest), and `explain()` returning `{"keep_count": N, "remove_count": M}`. Snapshots use `chain_length` as keep count; targets use `keep_generations` as keep count at the chain level.

### Modified Capabilities

- `config-model`: `RetentionPolicy` fields replaced (11 bucket fields → 2 count fields). `GlobalConfig`/`VMConfig`/`TargetConfig` lose preserve/preserve_min/preserve_day_of_week fields, gain chain_length/keep_generations integer fields.
- `config-parsing`: `_parse_preserve()` regex deleted. ConfigFacade parses integer fields directly. F-anchor validation, preserve_min validation, preserve_day_of_week validation deleted. New validation: `chain_length >= 1`, `keep_generations >= 1`.
- `core-orchestrator`: `_parse_preserve()` deleted (14 call sites). `_evaluate_snapshot_retention()` uses `chain_length`. `_evaluate_backup_retention()` uses `keep_generations` at chain level. `_backup_target()` replaces bucket-strategy with count-based FULL decision + verify-before-delete transaction + rollback. `schedule_summary()`/`estimate()` simplified. `_retention_window()`/`_generate_synthetic_items()` deleted. Ghost-retention logs deleted.
- `periodic-full-backup`: FULL creation triggered by `incremental_count > target_chain_length` instead of bucket period keys. Verify-before-delete gate: old generations deleted only after new FULL verified. Rollback on verification failure: delete broken FULL + checkpoint, retry.
- `schedule-summary`: Output shows `chain_length`, `keep_generations`, current counts instead of bucket breakdown. `explain()` returns count-based dict.
- `state-management`: `FullBackupInfo.bucket_level` field removed. `record_full_backup()` loses `bucket_level` parameter. JSON read-tolerant for old files with `bucket_level`.
- `module-factory`: `create_bucket_full_strategy()` method removed from `IVMModuleFactory` and `DefaultFactory`.

## Impact

**Source code (~15 files):**
- `qsnap/models/config.py` — RetentionPolicy, GlobalConfig, VMConfig, TargetConfig field replacement
- `qsnap/models/results.py` — FullBackupInfo.bucket_level removed
- `qsnap/retention/time_based.py` — evaluate() rewritten (~170 → ~20 lines)
- `qsnap/interfaces/retention.py` — evaluate() signature simplified, explain() return type changed
- `qsnap/interfaces/bucket_strategy.py` — **DELETED**
- `qsnap/modules/backup/bucket_strategy.py` — **DELETED**
- `qsnap/interfaces/backup.py` — create_full_backup() loses bucket_level param
- `qsnap/interfaces/state.py` — record_full_backup() loses bucket_level param
- `qsnap/interfaces/factory.py` — create_bucket_full_strategy() removed
- `qsnap/modules/backup/bitmap.py` — create_full_backup() loses bucket_level param
- `qsnap/state/json_manager.py` — record_full_backup() loses bucket_level, read-tolerant
- `qsnap/factory/default.py` — BucketFullStrategy import + method removed
- `qsnap/config/facade.py` — preserve string parsing → integer field parsing
- `qsnap/core/__init__.py` — _parse_preserve() deleted, _backup_target() rewritten, schedule_summary() simplified, _cleanup_failed_checkpoint() added
- `qsnap/cli/commands.py` — schedule/estimate output format

**Documentation (4 files):**
- `qsnap.toml.example` — new config fields, preserve fields removed
- `README.md` — retention guide, config reference, quick start, examples
- `AGENTS.md` — pipeline description, factory methods, testing paradigm table
- `TESTING.md` — directory structure, mock list, testing paradigm table

**Tests (~50 files):**
- Unit tests: ~80 rewrite/delete, ~30 modify
- Integration tests: ~8 rewrite, ~25 modify
- Mocks: 4 files updated
- TOML fixtures: 7 files updated

**Specs (14 files):**
- 5 deleted, 5 rewritten, 4 unchanged

**No migration path** — clean break. Old config files with `snapshot_preserve = "24h 7d"` will fail with `ConfigError: unknown key 'snapshot_preserve'`. Users must update to `snapshot_chain_length = 168`.
