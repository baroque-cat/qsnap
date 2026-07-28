## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b count-based-retention`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Models & Interfaces (Foundation)

- [x] 2.1 `qsnap/models/config.py` — Replace `RetentionPolicy` fields: remove `hourly`, `daily`, `weekly`, `monthly`, `yearly`, `preserve_min`, `anchor_hourly`, `anchor_daily`, `anchor_weekly`, `anchor_monthly`, `anchor_yearly`; add `chain_length: int = 0` and `keep_generations: int = 1`
- [x] 2.2 `qsnap/models/config.py` — Update `GlobalConfig`: remove `snapshot_preserve`, `target_preserve`, `snapshot_preserve_min`, `target_preserve_min`, `preserve_day_of_week`; add `snapshot_chain_length: int | None = None`, `target_chain_length: int | None = None`, `target_keep_generations: int | None = None`
- [x] 2.3 `qsnap/models/config.py` — Update `VMConfig`: remove `snapshot_preserve`, `target_preserve`, `snapshot_preserve_min`, `target_preserve_min`; add `snapshot_chain_length`, `target_chain_length`, `target_keep_generations` (all `int | None = None`)
- [x] 2.4 `qsnap/models/config.py` — Update `TargetConfig`: remove `target_preserve`, `target_preserve_min`; add `target_chain_length`, `target_keep_generations` (all `int | None = None`)
- [x] 2.5 `qsnap/models/results.py` — Remove `bucket_level` field from `FullBackupInfo`
- [x] 2.6 `qsnap/interfaces/retention.py` — Remove `preserve_day_of_week` parameter from `evaluate()` and `explain()`; update `explain()` return type to `dict[str, int]` with `keep_count` and `remove_count`
- [x] 2.7 `qsnap/interfaces/backup.py` — Remove `bucket_level` parameter from `create_full_backup()`
- [x] 2.8 `qsnap/interfaces/state.py` — Remove `bucket_level` parameter from `record_full_backup()`
- [x] 2.9 `qsnap/interfaces/factory.py` — Remove `create_bucket_full_strategy()` method from `IVMModuleFactory`
- [x] 2.10 Delete file `qsnap/interfaces/bucket_strategy.py` entirely

## 3. Implementations (Workers)

- [x] 3.1 `qsnap/retention/time_based.py` — Replace `TimeBasedRetention.evaluate()`: remove `_bucket_key()`, `_select_by_bucket()`, bucket iteration, `preserve_min` logic; implement count-based: sort by timestamp, keep newest N, remove oldest. Update `explain()` to return `{"keep_count": N, "remove_count": M}`
- [x] 3.2 Delete file `qsnap/modules/backup/bucket_strategy.py` entirely
- [x] 3.3 `qsnap/modules/backup/bitmap.py` — Remove `bucket_level` parameter from `create_full_backup()` (line ~1427). Filename already uses `{vm}.FULL.{date}.qcow2` — no change needed
- [x] 3.4 `qsnap/state/json_manager.py` — Remove `bucket_level` from `record_full_backup()`; make `get_full_backups()`/`get_last_full_backup()` read-tolerant (`entry.get("bucket_level", None)`); remove hardcoded `"monthly"` from `set_last_full_backup()`; remove bucket_driven migration from `_load_full_backups()`
- [x] 3.5 `qsnap/factory/default.py` — Remove `BucketFullStrategy` import and `create_bucket_full_strategy()` method
- [x] 3.6 `qsnap/config/facade.py` — Update `_build_vm()`: parse `snapshot_chain_length`, `target_chain_length`, `target_keep_generations` as integers instead of preserve strings. Update `_build_target()`: parse `target_chain_length`, `target_keep_generations`. Delete F-anchor validation, preserve_min validation, preserve_day_of_week validation, full_every deprecation. Add validation: `chain_length >= 1`, `keep_generations >= 1`

## 4. Core (Orchestrator)

- [x] 4.1 `qsnap/core/__init__.py` — Delete `_parse_preserve()` static method entirely (~60 lines). Replace all 14 call sites with direct `RetentionPolicy(chain_length=..., keep_generations=...)` construction
- [x] 4.2 `qsnap/core/__init__.py` — Update `_evaluate_snapshot_retention()`: construct `RetentionPolicy(chain_length=vm_config.snapshot_chain_length or 0, keep_generations=1)`, call `engine.evaluate(items, policy, datetime.now())` without `preserve_day_of_week`. Oldest-prefix post-processing STAYS
- [x] 4.3 `qsnap/core/__init__.py` — Update `_evaluate_backup_retention()`: construct `RetentionPolicy(chain_length=0, keep_generations=target.keep_generations or 1)`, call engine at chain level. Chain grouping (`_group_backups_by_chain`) and expansion UNCHANGED
- [x] 4.4 `qsnap/core/__init__.py` — Rewrite `_backup_target()` FULL decision: replace `IBucketFullStrategy` delegation with count-based check (`incremental_count > target.chain_length`). Add verify-before-delete gate: create FULL → verify (M1/M2) → if verified: record + evaluate retention + cleanup; if failed: rollback + retry
- [x] 4.5 `qsnap/core/__init__.py` — Add `_cleanup_failed_checkpoint()` method: list checkpoints via `virsh checkpoint-list`, filter for `qsnap-{target_hash}-*`, delete via `virsh checkpoint-delete`
- [x] 4.6 `qsnap/core/__init__.py` — Simplify `schedule_summary()` and `estimate()`: delete `_retention_window()` and `_generate_synthetic_items()`; output shows `chain_length`, `keep_generations`, current counts
- [x] 4.7 `qsnap/core/__init__.py` — Delete ghost-retention INFO log format (dead code referencing ghost-retained FULLs)

## 5. CLI & Config Example

- [x] 5.1 `qsnap/cli/commands.py` — Update `schedule`/`estimate` output format to show `chain_length` and `keep_generations` instead of bucket breakdown
- [x] 5.2 `qsnap.toml.example` — Remove `preserve_day_of_week`, `snapshot_preserve`, `target_preserve`, `*_preserve_min`, F-anchor comments (~20 lines). Add `snapshot_chain_length`, `target_chain_length`, `target_keep_generations` with comments

## 6. Documentation

- [x] 6.1 `README.md` — Update Features list, Quick Start config example, Configuration Reference tables (Global/VM/Target keys), Retention Policy Guide section (replace bucket guide with count-based guide), Full Backups section (remove multi-level anchors, F-syntax), Example Configurations
- [x] 6.2 `AGENTS.md` — Update Pipeline description (remove `_should_create_bucket_full`, ghost retention, cascade deletion references), Factory methods list (remove `create_bucket_full_strategy`), Testing paradigm table (remove `IBucketFullStrategy`/`MockBucketFullStrategy` row)
- [x] 6.3 `TESTING.md` — Update directory structure (remove `test_full_anchor.py`, `test_bucket_full_strategy.py` references), mock list (remove `MockBucketFullStrategy`), testing paradigm table (remove `IBucketFullStrategy` row), example test code (update `RetentionPolicy` usage)

## 7. Specs

- [x] 7.1 Delete spec directories: `openspec/specs/retention-engine/`, `openspec/specs/bucket-full-strategy/`, `openspec/specs/full-anchor-syntax/`, `openspec/specs/multi-level-full-anchors/`, `openspec/specs/preserve-min-config/`
- [x] 7.2 Verify specs NOT touched: `openspec/specs/per-chain-retention/`, `openspec/specs/cascade-deletion/`, `openspec/specs/snapshot-oldest-prefix/`, `openspec/specs/size-estimation/`

## 8. Testing

**CRITICAL INSTRUCTION FOR THE PROGRAMMER AGENT:** When delegating test groups to @Mr.Tester subagents, you MUST pass the TESTING.md document (`/home/openuser/vm/qsnap/TESTING.md`) to EACH tester. This document describes the testing philosophy, directory structure, test categories, mock strategy, and rules. Every tester must read it before writing tests.

Read `test-plan.md` Delegation Groups section. Launch ALL @Mr.Tester subagents IN PARALLEL (single message). Each tester receives:
1. The group's scope (file paths from test-plan.md)
2. The group's scenario list from Coverage Map in test-plan.md
3. The TESTING.md document at `/home/openuser/vm/qsnap/TESTING.md`
4. Instruction: "Read TESTING.md first. Write or fix ONLY the tests in your group's scope. Report source bugs, don't fix them."

- [x] 8.1 Read `test-plan.md` Delegation Groups section
- [x] 8.2 Delegate group `retention` to @Mr.Tester (scope: `tests/modules/retention/test_time_based.py`). Pass TESTING.md.
- [x] 8.3 Delegate group `config` to @Mr.Tester (scope: `tests/config/test_model.py`, `tests/config/test_facade.py`, `tests/config/test_resolver.py`, `tests/config/test_fixtures.py`). Pass TESTING.md.
- [x] 8.4 Delegate group `core-orchestration` to @Mr.Tester (scope: `tests/core/test_pipeline.py`, `tests/core/test_preserve.py`, `tests/core/test_schedule_summary.py`, `tests/core/test_engine.py`, `tests/core/test_full_verification_pipeline.py`). Pass TESTING.md.
- [x] 8.5 Delegate group `interfaces-and-models` to @Mr.Tester (scope: `tests/interfaces/test_retention_engine.py`, `tests/models/test_results.py`, `tests/interfaces/test_bucket_full_strategy.py` [DELETE]). Pass TESTING.md.
- [x] 8.6 Delegate group `mocks-and-fixtures` to @Mr.Tester (scope: `tests/mocks/mock_factory.py`, `tests/mocks/mock_state.py`, `tests/mocks/mock_modules.py`, `tests/conftest.py`, `tests/factory/test_default.py`, TOML fixtures). Pass TESTING.md.
- [x] 8.7 Delegate group `integration` to @Mr.Tester (scope: `tests/integration/test_*.py`, new integration tests). Pass TESTING.md. This group has full access to libvirt and qemu.
- [x] 8.8 Review @Mr.Tester reports and fix any source-level bugs discovered
- [x] 8.9 Re-delegate any groups affected by source fixes
- [x] 8.10 Verify all groups pass and coverage matches `test-plan.md`

## 9. Final Verification

- [x] 9.1 Run full unit test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 9.2 Run ruff linting: `poetry run ruff check qsnap/ tests/`
- [x] 9.3 Run pyright type checking: `poetry run pyright qsnap/`
- [x] 9.4 Verify no references to deleted concepts remain: `grep -r "bucket_level\|IBucketFullStrategy\|_parse_preserve\|preserve_min\|preserve_day_of_week\|snapshot_preserve\|target_preserve" qsnap/ tests/` should return zero results
- [x] 9.5 Run integration tests if libvirt available: `poetry run pytest tests/integration/ -m integration`
