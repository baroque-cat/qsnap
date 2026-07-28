# QA Strategy & Test Plan

## Coverage Map

### Count-Based Retention Spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| count-based-retention | Count-based retention policy | Snapshot chain length triggers blockcommit | tests/modules/retention/test_time_based.py | test_snapshot_chain_length_triggers_removal | retention |
| count-based-retention | Count-based retention policy | Snapshot chain length not exceeded | tests/modules/retention/test_time_based.py | test_snapshot_count_within_chain_length_keeps_all | retention |
| count-based-retention | Count-based retention policy | Target chain length triggers new FULL | tests/core/test_pipeline.py | test_incremental_count_exceeds_chain_length_triggers_full | core-orchestration |
| count-based-retention | Count-based retention policy | Target keep generations limits chains | tests/modules/retention/test_time_based.py | test_keep_generations_limits_chains | retention |
| count-based-retention | Count-based retention policy | First backup to target creates FULL | tests/core/test_pipeline.py | test_first_backup_creates_full_regardless_of_chain_length | core-orchestration |
| count-based-retention | Count-based retention engine | Keep newest N items | tests/modules/retention/test_time_based.py | test_keep_newest_n_items | retention |
| count-based-retention | Count-based retention engine | All items within chain length | tests/modules/retention/test_time_based.py | test_all_items_within_chain_length | retention |
| count-based-retention | Count-based retention engine | Empty item list | tests/modules/retention/test_time_based.py | test_empty_item_list_returns_empty | retention |
| count-based-retention | Count-based retention engine | Chain length zero | tests/modules/retention/test_time_based.py | test_chain_length_zero_removes_all | retention |
| count-based-retention | Explain method returns count-based summary | Explain returns counts | tests/modules/retention/test_time_based.py | test_explain_returns_counts | retention |
| count-based-retention | No preserve_day_of_week parameter | Evaluate without preserve_day_of_week | tests/interfaces/test_retention_engine.py | test_evaluate_signature_no_preserve_day_of_week | interfaces-and-models |
| count-based-retention | RetentionPolicy has two fields | Default policy | tests/config/test_model.py | test_retention_policy_defaults | config |
| count-based-retention | RetentionPolicy has two fields | Snapshot policy | tests/config/test_model.py | test_retention_policy_for_snapshots | config |
| count-based-retention | RetentionPolicy has two fields | Target policy | tests/config/test_model.py | test_retention_policy_for_targets | config |

### Config Model Spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| config-model | GlobalConfig dataclass | GlobalConfig is immutable | tests/config/test_model.py | test_global_config_immutable | config |
| config-model | GlobalConfig dataclass | GlobalConfig default values | tests/config/test_model.py | test_global_config_defaults | config |
| config-model | VMConfig dataclass | VMConfig with required fields | tests/config/test_model.py | test_vm_config_required_fields | config |
| config-model | VMConfig dataclass | VMConfig with targets | tests/config/test_model.py | test_vm_config_with_targets | config |
| config-model | TargetConfig dataclass | TargetConfig with incremental enabled | tests/config/test_model.py | test_target_config_with_incremental | config |
| config-model | GlobalConfig count-based retention fields | Defaults are None | tests/config/test_model.py | test_global_chain_length_defaults_none | config |
| config-model | VMConfig count-based retention fields | VM inherits from global | tests/config/test_resolver.py | test_vm_inherits_chain_length_from_global | config |
| config-model | TargetConfig count-based retention fields | Target inherits from VM | tests/config/test_resolver.py | test_target_inherits_chain_length_from_vm | config |
| config-model | TargetConfig count-based retention fields | Target overrides VM | tests/config/test_resolver.py | test_target_overrides_vm_chain_length | config |

### Config Parsing Spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| config-parsing | Option inheritance from global to per-VM to per-target | VM overrides global chain length | tests/config/test_resolver.py | test_vm_overrides_global_chain_length | config |
| config-parsing | Option inheritance from global to per-VM to per-target | Target inherits VM chain length when not overridden | tests/config/test_resolver.py | test_target_inherits_vm_chain_length | config |
| config-parsing | Option inheritance from global to per-VM to per-target | Target overrides VM chain length | tests/config/test_resolver.py | test_target_overrides_vm_keep_generations | config |
| config-parsing | ConfigFacade parses new fault-tolerance fields | Global safety fields parsed | tests/config/test_facade.py | test_global_safety_fields_parsed | config |
| config-parsing | ConfigFacade parses new fault-tolerance fields | Target compress parsed | tests/config/test_facade.py | test_target_compress_parsed | config |
| config-parsing | ConfigFacade parses new fault-tolerance fields | full_every in config triggers deprecation warning | tests/config/test_facade.py | test_full_every_deprecation_warning | config |
| config-parsing | ConfigFacade parses new fault-tolerance fields | full_compress mapped to compress | tests/config/test_facade.py | test_full_compress_mapped_to_compress | config |
| config-parsing | ConfigFacade parses new fault-tolerance fields | VM deep verify fields parsed | tests/config/test_facade.py | test_vm_deep_verify_parsed | config |
| config-parsing | ConfigFacade parses new fault-tolerance fields | Target retry fields parsed | tests/config/test_facade.py | test_target_retry_fields_parsed | config |
| config-parsing | ConfigFacade updates example config | Example config is parseable with all fields documented | tests/config/test_fixtures.py | test_example_config_parseable | config |
| config-parsing | ConfigFacade parses count-based retention fields | Global chain_length parsed | tests/config/test_facade.py | test_global_chain_length_parsed | config |
| config-parsing | ConfigFacade parses count-based retention fields | VM-level chain_length overrides global | tests/config/test_facade.py | test_vm_chain_length_overrides_global | config |
| config-parsing | ConfigFacade parses count-based retention fields | Target-level chain_length overrides VM | tests/config/test_facade.py | test_target_chain_length_overrides_vm | config |
| config-parsing | Count-based retention validation | Valid chain_length | tests/config/test_facade.py | test_valid_chain_length_accepted | config |
| config-parsing | Count-based retention validation | Zero chain_length rejected | tests/config/test_facade.py | test_zero_chain_length_rejected | config |
| config-parsing | Count-based retention validation | Negative keep_generations rejected | tests/config/test_facade.py | test_negative_keep_generations_rejected | config |

### Core Orchestrator Spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| core-orchestrator | Core._evaluate_snapshot_retention uses count-based policy | Snapshot retention with chain_length | tests/core/test_preserve.py | test_snapshot_retention_with_chain_length | core-orchestration |
| core-orchestrator | Core._evaluate_snapshot_retention uses count-based policy | Snapshot retention with no chain_length | tests/core/test_preserve.py | test_snapshot_retention_no_chain_length_uses_zero | core-orchestration |
| core-orchestrator | Core._evaluate_backup_retention uses count-based policy | Backup retention with keep_generations | tests/core/test_preserve.py | test_backup_retention_with_keep_generations | core-orchestration |
| core-orchestrator | Core._backup_target triggers full backup when due | Incremental count exceeds chain length triggers FULL | tests/core/test_pipeline.py | test_incremental_exceeds_chain_triggers_full | core-orchestration |
| core-orchestrator | Core._backup_target triggers full backup when due | First run creates full backup | tests/core/test_pipeline.py | test_first_run_creates_full | core-orchestration |
| core-orchestrator | Core._backup_target triggers full backup when due | Verified FULL triggers retention + cleanup | tests/core/test_full_verification_pipeline.py | test_verified_full_triggers_retention_and_cleanup | core-orchestration |
| core-orchestrator | Core._backup_target triggers full backup when due | Failed FULL verification triggers rollback | tests/core/test_full_verification_pipeline.py | test_failed_full_verification_triggers_rollback | core-orchestration |
| core-orchestrator | Core._backup_target triggers full backup when due | Retries exhausted keeps old generations | tests/core/test_full_verification_pipeline.py | test_retries_exhausted_keeps_old_generations | core-orchestration |
| core-orchestrator | Core._backup_target triggers full backup when due | No bucket strategy obtained from factory | tests/core/test_pipeline.py | test_no_bucket_strategy_used | core-orchestration |
| core-orchestrator | Core.schedule_summary produces count-based summary | Summary includes all VMs when no filter | tests/core/test_schedule_summary.py | test_summary_includes_all_vms_no_filter | retention |
| core-orchestrator | Core.schedule_summary produces count-based summary | Summary filters by VM name | tests/core/test_schedule_summary.py | test_summary_filters_by_vm_name | retention |
| core-orchestrator | Core._cleanup_failed_checkpoint rollback method | Checkpoint cleaned up after failed FULL | tests/core/test_full_verification_pipeline.py | test_checkpoint_cleaned_up_after_failed_full | core-orchestration |

### Module Factory Spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| module-factory | IVMModuleFactory ABC | IVMModuleFactory defines all creation methods | tests/interfaces/test_factory.py | test_ivm_module_factory_methods | interfaces-and-models |
| module-factory | DefaultFactory does not create bucket full strategy | Factory has no bucket full strategy method | tests/factory/test_default.py | test_default_factory_no_bucket_strategy | interfaces-and-models |

### Periodic Full Backup Spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| periodic-full-backup | Core triggers full backup before incremental transfer | First backup to target creates FULL | tests/core/test_pipeline.py | test_first_backup_creates_full | core-orchestration |
| periodic-full-backup | Core triggers full backup before incremental transfer | Incremental count exceeds chain length triggers FULL | tests/core/test_pipeline.py | test_incremental_count_exceeds_chain_triggers_full | core-orchestration |
| periodic-full-backup | Core triggers full backup before incremental transfer | Incremental count within chain length skips FULL | tests/core/test_pipeline.py | test_incremental_count_within_chain_skips_full | core-orchestration |
| periodic-full-backup | Core triggers full backup before incremental transfer | Verified FULL triggers retention + cleanup | tests/core/test_full_verification_pipeline.py | test_verified_full_triggers_retention_cleanup | core-orchestration |
| periodic-full-backup | Core triggers full backup before incremental transfer | Failed FULL verification triggers rollback | tests/core/test_full_verification_pipeline.py | test_verification_failure_rollback | core-orchestration |
| periodic-full-backup | Core triggers full backup before incremental transfer | Retries exhausted keeps old generations | tests/core/test_full_verification_pipeline.py | test_retry_exhaustion_keeps_old | core-orchestration |
| periodic-full-backup | Core triggers full backup before incremental transfer | Dry-run logs FULL-would-be-created without executing | tests/core/test_pipeline.py | test_dry_run_logs_full_would_be_created | core-orchestration |
| periodic-full-backup | IStateManager tracks full backups per target | Full backup recorded and retrieved | tests/models/test_results.py | test_full_backup_recorded_and_retrieved | interfaces-and-models |
| periodic-full-backup | IStateManager tracks full backups per target | Old JSON with bucket_level is read-tolerant | tests/models/test_results.py | test_old_json_bucket_level_read_tolerant | interfaces-and-models |

### Schedule Summary Spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Empty state produces meaningful summary | tests/core/test_schedule_summary.py | test_empty_state_summary | retention |
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Summary logs at INFO on every timer invocation | tests/core/test_schedule_summary.py | test_summary_logs_info_on_timer | retention |
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Summary shows snapshot and backup counts | tests/core/test_schedule_summary.py | test_summary_shows_snapshot_and_backup_counts | retention |
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Summary includes real base image size | tests/core/test_schedule_summary.py | test_summary_includes_base_image_size | retention |
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Summary includes average incremental size from history | tests/core/test_schedule_summary.py | test_summary_includes_avg_incremental_size | retention |
| schedule-summary | TimeBasedRetention.explain returns structured metadata | explain returns counts | tests/modules/retention/test_time_based.py | test_explain_returns_keep_remove_counts | retention |
| schedule-summary | TimeBasedRetention.explain returns structured metadata | explain is a pure function | tests/modules/retention/test_time_based.py | test_explain_is_pure_function | retention |

### State Management Spec

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| state-management | IStateManager tracks last full backup per target | Full backup state saved and retrieved | tests/models/test_results.py | test_full_backup_state_saved_retrieved | interfaces-and-models |
| state-management | IStateManager tracks last full backup per target | No full backup returns None | tests/models/test_results.py | test_no_full_backup_returns_none | interfaces-and-models |
| state-management | FullBackupInfo without bucket_level | FullBackupInfo constructed without bucket_level | tests/models/test_results.py | test_full_backup_info_no_bucket_level | interfaces-and-models |
| state-management | FullBackupInfo without bucket_level | Old JSON with bucket_level loaded without error | tests/models/test_results.py | test_old_json_bucket_level_loaded | interfaces-and-models |
| state-management | record_full_backup without bucket_level parameter | record_full_backup called without bucket_level | tests/models/test_results.py | test_record_full_backup_no_bucket_level | interfaces-and-models |

## Delegation Groups

### Group: retention

**Scope:** `tests/modules/retention/test_time_based.py`, `tests/core/test_schedule_summary.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/retention/test_time_based.py` | 12 | REWRITE |
| `tests/core/test_schedule_summary.py` | 7 | REWRITE |

### Group: config

**Scope:** `tests/config/test_model.py`, `tests/config/test_facade.py`, `tests/config/test_resolver.py`, `tests/config/test_fixtures.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | 9 | MODIFY |
| `tests/config/test_facade.py` | 10 | MODIFY |
| `tests/config/test_resolver.py` | 5 | MODIFY |
| `tests/config/test_fixtures.py` | 1 | MODIFY |

### Group: core-orchestration

**Scope:** `tests/core/test_preserve.py`, `tests/core/test_pipeline.py`, `tests/core/test_full_verification_pipeline.py`, `tests/core/test_engine.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_preserve.py` | 3 | REWRITE |
| `tests/core/test_pipeline.py` | 8 | MODIFY |
| `tests/core/test_full_verification_pipeline.py` | 5 | MODIFY |
| `tests/core/test_engine.py` | 1 | MODIFY |

### Group: interfaces-and-models

**Scope:** `tests/interfaces/test_retention_engine.py`, `tests/interfaces/test_factory.py`, `tests/models/test_results.py`, `tests/factory/test_default.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_retention_engine.py` | 1 | MODIFY |
| `tests/interfaces/test_factory.py` | 1 | MODIFY |
| `tests/models/test_results.py` | 7 | MODIFY |
| `tests/factory/test_default.py` | 1 | MODIFY |

### Group: mocks-and-fixtures

**Scope:** `tests/mocks/mock_factory.py`, `tests/mocks/mock_state.py`, `tests/mocks/mock_modules.py`, `tests/conftest.py`, `tests/fixtures/configs/`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/mocks/mock_factory.py` | — | MODIFY |
| `tests/mocks/mock_state.py` | — | MODIFY |
| `tests/mocks/mock_modules.py` | — | MODIFY |
| `tests/conftest.py` | — | MODIFY |
| `tests/fixtures/configs/bucket_driven.toml` | — | MODIFY |
| `tests/fixtures/configs/preserve_min.toml` | — | MODIFY |
| `tests/fixtures/configs/inheritance.toml` | — | MODIFY |
| `tests/fixtures/configs/global_fields.toml` | — | MODIFY |
| `tests/fixtures/configs/safety_fields.toml` | — | MODIFY |
| `tests/fixtures/configs/deprecated_fields.toml` | — | MODIFY |
| `tests/fixtures/configs/full_backup.toml` | — | MODIFY |

### Group: integration

**Scope:** `tests/integration/`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/integration/test_full_backup.py` | — | MODIFY |
| `tests/integration/test_incremental_backup.py` | — | MODIFY |
| `tests/integration/test_onchange.py` | — | MODIFY |
| `tests/integration/test_blockcommit_defer.py` | — | MODIFY |
| `tests/integration/test_blockcommit_recovery.py` | — | MODIFY |
| `tests/integration/test_broken_chain.py` | — | MODIFY |
| `tests/integration/test_infrastructure.py` | — | MODIFY |
| `tests/integration/test_reconcile.py` | — | MODIFY |
| `tests/integration/test_preserve_all.py` | — | REWRITE |
| `tests/integration/test_auto_recovery.py` | — | REWRITE |
| `tests/integration/test_count_based_full.py` | 4 | NEW |
| `tests/integration/test_verify_before_delete.py` | 2 | NEW |
| `tests/integration/test_rollback_retry.py` | 2 | NEW |

## Tests to Delete

### Entire Files to Delete

| File | Reason |
|---|---|
| `tests/modules/retention/test_bucket_full_strategy.py` | `BucketFullStrategy` class and `IBucketFullStrategy` interface are deleted (design D2). All ~24 tests are for period-key computation, bucket-level delegation, and F-anchor resolution — concepts that no longer exist. |
| `tests/core/test_full_anchor.py` | Placeholder file (18 lines). All tests were already migrated to `test_bucket_full_strategy.py` and `test_pipeline.py`. Now dead code. |
| `tests/interfaces/test_bucket_full_strategy.py` | Contract test for `IBucketFullStrategy` ABC. The ABC no longer exists (deleted from `qsnap/interfaces/bucket_strategy.py`). Single test function. |

### Individual Test Functions to Delete

| File | Tests/Functions | Reason |
|---|---|---|
| `tests/core/test_preserve.py` | All tests involving `_parse_preserve()` (~17 functions): `test_parse_preserve_all_produces_correct_policy`, `test_parse_preserve_all_keeps_all_snapshots`, `test_parse_preserve_24h_*` variations, etc. | `Core._parse_preserve()` is deleted (design D2). The test file is rewritten entirely to cover count-based policy construction. |
| `tests/core/test_pipeline.py` | `test_core_delegates_bucket_decision_to_strategy`, `test_backup_target_passes_full_list_to_strategy`, and any test that references `IBucketFullStrategy` or `create_bucket_full_strategy()`. | Bucket strategy no longer exists. Replaced by inline count checks. |
| `tests/core/test_schedule_summary.py` | `test_retention_window_*` helpers, `test_generate_synthetic_items_*`, any test asserting per-bucket breakdown output. | `_retention_window()` and `_generate_synthetic_items()` are deleted (core-orchestrator spec). Output format changes from bucket breakdown to count-based summary. |
| `tests/core/test_engine.py` | Any test passing `preserve_day_of_week` to the retention engine. | `preserve_day_of_week` parameter removed from `IRetentionEngine.evaluate()`. |
| `tests/integration/test_preserve_all.py` | `test_parse_preserve_all_produces_correct_policy`, `test_pipeline_preserve_all_keeps_all_snapshots` | `_parse_preserve()` deleted. The concept of `preserve_all` as `"all"` string has no count-based analog. Tests are rewritten for chain_length semantics. |
| `tests/core/test_full_verification_pipeline.py` | Any test asserting `bucket_level` is passed through or recorded. | `bucket_level` removed from `create_full_backup()`, `record_full_backup()`, and `FullBackupInfo`. |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/config/test_model.py` | Replace `RetentionPolicy(hourly=24, daily=7, ...)` with `RetentionPolicy(chain_length=168, keep_generations=2)`. Assert only 2 fields. Add tests for `chain_length` and `keep_generations` defaults. Remove tests for all 11 old fields. | RetentionPolicy reduced from 11 bucket fields to 2 count fields. |
| `tests/config/test_facade.py` | Remove tests for `preserve_min` validation, `preserve_day_of_week` validation, F-anchor parsing. Add tests for `chain_length >= 1` validation, `keep_generations >= 1` validation, `None` defaults. Replace `snapshot_preserve`/`target_preserve` string parsing with `snapshot_chain_length`/`target_chain_length` integer parsing. | Config facade parses integer fields instead of regex preserve strings. |
| `tests/config/test_resolver.py` | Replace inheritance tests for `snapshot_preserve`/`target_preserve` with `snapshot_chain_length`/`target_chain_length`/`target_keep_generations`. | Inheritance chain unchanged; only field names change. |
| `tests/config/test_fixtures.py` | Update fixture assertions to reflect new field names (`snapshot_chain_length`, `target_chain_length`, `target_keep_generations`). | TOML fixtures updated with new field names. |
| `tests/core/test_pipeline.py` | Replace bucket-strategy delegation assertions with count-based decision assertions. Verify `create_bucket_full_strategy()` is NOT called. Verify `create_full_backup()` is called without `bucket_level`. Add verify-before-delete gate assertions. | Count-based FULL decision replaces bucket strategy delegation. |
| `tests/core/test_full_verification_pipeline.py` | Remove `bucket_level` from `create_full_backup()` calls and `record_full_backup()` calls. Add rollback assertions: after verification failure, assert `provider.delete()` called, `_cleanup_failed_checkpoint()` called, `state.remove_full_backup()` called. Add retry exhaustion assertion: old generations retained. | VERIFY-before-delete gate and rollback mechanism added. |
| `tests/core/test_engine.py` | Remove `preserve_day_of_week` from retention engine calls. | Parameter removed from `IRetentionEngine.evaluate()`. |
| `tests/modules/retention/test_time_based.py` | Complete rewrite from ~14 bucket-based tests to ~12 count-based tests. Remove: `_bucket_key()`, `_select_by_bucket()`, bucket iteration, `preserve_min` logic, hour/day/week/month/year boundary tests. Add: keep-newest-N, all-within-limit, empty-list, zero-length, explain-counts, keep_generations, deterministic output. | Retention engine simplified from ~170 lines to ~20 lines. Pure function, no calendar boundaries. |
| `tests/core/test_schedule_summary.py` | Rewrite from ~8 tests to ~7 tests. Remove: synthetic timestamp generation tests, retention window tests, per-bucket breakdown tests. Add: empty-state summary, snapshot/chains count display, chain_length/keep_generations display, real base image size, average incremental size. | `schedule_summary()` no longer generates synthetic timestamps or bucket breakdowns. |
| `tests/interfaces/test_retention_engine.py` | Remove tests for `preserve_day_of_week` parameter. Verify `evaluate()` signature is `evaluate(items, policy, now)`. | Contract test reflects updated interface. |
| `tests/interfaces/test_factory.py` | Remove assertion for `create_bucket_full_strategy()`. Verify the method is absent. | Interface contract updated for deleted method. |
| `tests/factory/test_default.py` | Remove tests for `create_bucket_full_strategy()`. Verify `DefaultFactory` does not import `BucketFullStrategy` or `IBucketFullStrategy`. | Factory no longer creates bucket strategy instances. |
| `tests/models/test_results.py` | Remove `bucket_level` from `FullBackupInfo` construction. Add test for construction with only `name`, `path`, `timestamp`. Add read-tolerance test for old JSON with `bucket_level`. | `FullBackupInfo` reduced to 3 fields. |
| `tests/mocks/mock_factory.py` | Remove `MockBucketFullStrategy` import and instance attribute. Remove `create_bucket_full_strategy()` method. | Interface method deleted. |
| `tests/mocks/mock_state.py` | Remove `bucket_level` parameter from `record_full_backup()`. Remove `"monthly"` hardcode from `set_last_full_backup()`. Remove `bucket_level` from `FullBackupInfo` construction. | State persists only `name`, `path`, `timestamp`. |
| `tests/mocks/mock_modules.py` | Remove `MockBucketFullStrategy` class entirely. Remove `bucket_level` parameter from `MockBackupProvider.create_full_backup()` and `MockBitmapBackupProvider.create_full_backup()`. Remove `bucket_level` from FULL target path construction. Remove `preserve_day_of_week` from `MockRetentionEngine.evaluate()`. | Mocks reflect updated interfaces. |
| `tests/conftest.py` | Replace `preserve_day_of_week`, `snapshot_preserve`, `target_preserve`, `snapshot_preserve_min`, `target_preserve_min` parameters with `snapshot_chain_length`, `target_chain_length`, `target_keep_generations` parameters in `make_global_config()`. | Config fixture matches new model. |
| `tests/integration/test_full_backup.py` | Remove `bucket_level="monthly"` from all `create_full_backup()` calls (~8 tests). | Parameter removed from interface. |
| `tests/integration/test_incremental_backup.py` | Remove `bucket_level` references (~4 tests). | Parameter removed from interface. |
| `tests/integration/test_onchange.py` | Replace `snapshot_preserve`/`target_preserve` config with `snapshot_chain_length`/`target_chain_length` (~5 tests). | Config fields renamed. |
| `tests/integration/test_blockcommit_defer.py` | Replace preserve config with chain_length config (~4 tests). | Config fields renamed. |
| `tests/integration/test_blockcommit_recovery.py` | Replace preserve config with chain_length config (~4 tests). | Config fields renamed. |
| `tests/integration/test_broken_chain.py` | Replace preserve config with chain_length config (~4 tests). | Config fields renamed. |
| `tests/integration/test_infrastructure.py` | Remove `bucket_level` from FULL creation calls (~4 tests). | Parameter removed. |
| `tests/integration/test_reconcile.py` | Update skip message to remove bucket_level references (~1 test). | Fix skip reason text. |
| `tests/integration/test_preserve_all.py` | COMPLETE REWRITE: Replace `_parse_preserve("all")` tests and pipeline tests with count-based equivalents. 2 tests become tests for `snapshot_chain_length=None` behavior or are absorbed into other integration tests. | `_parse_preserve()` deleted. "preserve all" has no count-based analog. |
| `tests/integration/test_auto_recovery.py` | Replace bucket-based FULL logic with count-based FULL logic. Remove bucket_level assertions (~6 tests). Add verify-before-delete and rollback assertions. | Core pipeline changed — bucket delegation replaced by count check + rollback. |
| `tests/fixtures/configs/bucket_driven.toml` | Replace `snapshot_preserve = "24h 7d 4w"` / `target_preserve = "7Fd 4w 12m"` with `snapshot_chain_length = 168`, `target_chain_length = 168`, `target_keep_generations = 3`. | Bucket fields replaced by count fields. |
| `tests/fixtures/configs/preserve_min.toml` | Replace `snapshot_preserve_min = "24h"` / `target_preserve_min = "7d"` with `snapshot_chain_length = 24`, `target_chain_length = 24`. Remove `preserve_min` entirely — `chain_length` IS the minimum. | No preserve_min concept in count-based retention. |
| `tests/fixtures/configs/inheritance.toml` | Replace `snapshot_preserve = "24h"` / `target_preserve = "7d"` at global/VM/target levels with `snapshot_chain_length` / `target_chain_length` / `target_keep_generations` overrides. | Inheritance tests now use count fields. |
| `tests/fixtures/configs/global_fields.toml` | Replace `preserve_day_of_week`, `snapshot_preserve`, `target_preserve`, `*_preserve_min` with `snapshot_chain_length`, `target_chain_length`, `target_keep_generations`. | Global defaults use new field names. |
| `tests/fixtures/configs/safety_fields.toml` | Replace preserve fields with chain_length fields. | Config fixture updated. |
| `tests/fixtures/configs/deprecated_fields.toml` | Remove `full_every`, `full_compress`, `rate_limit`, `incremental_mode`, `copy_base` tests. Add tests for old `snapshot_preserve`/`target_preserve` triggering deprecation WARNING. | Deprecated field tests reflect new expected behavior. |
| `tests/fixtures/configs/full_backup.toml` | Replace `target_preserve = "7Fd 4w 12m 1y"` with `target_chain_length = 168`, `target_keep_generations = 2`. | FULL backup anchor config uses count fields. |

## New Integration Tests

### File: `tests/integration/test_count_based_full.py`

**Purpose:** Verify count-based FULL backup creation behavior end-to-end with real virsh/qemu-img.

| Test Name | Scenario | Description |
|---|---|---|
| `test_full_created_when_incrementals_exceed_chain_length` | Count-based FULL creation | Create `target_chain_length + 1` incrementals on a target, run pipeline, verify a new FULL is created via `virsh backup-begin`. Assert no `create_bucket_full_strategy` call path exists. |
| `test_full_not_created_when_incrementals_within_chain_length` | Count-based FULL skip | Create `target_chain_length - 1` incrementals, run pipeline, verify NO FULL is created. Only incremental transfer occurs. |
| `test_first_backup_to_target_always_creates_full` | First backup creates FULL | Empty target, no prior FULLs. Run pipeline. Verify a FULL is created even with `target_chain_length=168` and 0 incrementals. |
| `test_dry_run_does_not_create_full` | Dry-run logs but skips | Run pipeline with `dry_run=True` when incrementals exceed chain length. Verify FULL-would-be-created log appears but no actual `virsh backup-begin` call. |

### File: `tests/integration/test_verify_before_delete.py`

**Purpose:** Verify that old generations are NOT deleted until the new FULL passes M1/M2 verification.

| Test Name | Scenario | Description |
|---|---|---|
| `test_old_generation_not_deleted_on_failed_verification` | Verify-before-delete gate | Set `target_keep_generations=1`. Create old FULL generation, then trigger new FULL creation. Force M1/M2 verification to fail (corrupt qcow2 header). Assert old generation files are still on disk and NOT deleted. |
| `test_old_generation_deleted_after_successful_verification` | Successful FULL enables cleanup | Set `target_keep_generations=1`. Create old FULL generation, trigger new FULL creation which passes verification. Assert old generation files are deleted and only new FULL remains. |

### File: `tests/integration/test_rollback_retry.py`

**Purpose:** Verify rollback mechanism cleans up broken FULLs and retries.

| Test Name | Scenario | Description |
|---|---|---|
| `test_rollback_deletes_broken_full_and_checkpoint` | Rollback on failed FULL | Create a scenario where FULL verification fails. Assert: (a) broken FULL qcow2 file is deleted from disk, (b) libvirt checkpoint is deleted via `virsh checkpoint-delete`, (c) no orphaned checkpoint remains in `virsh checkpoint-list`. |
| `test_retry_after_rollback_succeeds` | Retry after rollback | First FULL attempt fails verification (e.g., disk I/O simulated failure), triggering rollback. Second attempt succeeds. Assert: (a) first attempt's artifacts are cleaned up, (b) second FULL is created and verified, (c) final state has exactly one valid FULL. |

## Risks & Edge Cases

- **[Risk: `keep_generations=1` + failed verification → no valid generation]** → Test `test_old_generation_not_deleted_on_failed_verification` in `tests/integration/test_verify_before_delete.py` ensures old generation is preserved when new FULL fails. The verify-before-delete gate is exercised with `keep_generations=1` (most dangerous case).

- **[Risk: Rollback incomplete — broken FULL stays on disk]** → Test `test_rollback_deletes_broken_full_and_checkpoint` in `tests/integration/test_rollback_retry.py` verifies both `provider.delete()` and `_cleanup_failed_checkpoint()` execute. Unit test `test_retries_exhausted_keeps_old_generations` in `tests/core/test_full_verification_pipeline.py` covers the memory path. Integration test verifies the on-disk result.

- **[Risk: Infinite retry on systematic FULL creation failure]** → Unit test `test_retries_exhausted_keeps_old_generations` in `tests/core/test_full_verification_pipeline.py` verifies CRITICAL log after `backup_retry_max` retries and that old generations survive. Config test `test_target_retry_fields_parsed` in `tests/config/test_facade.py` ensures `backup_retry_max` is properly parsed from TOML.

- **[Risk: Oldest-prefix post-processing becomes dead code]** → Covered by existing tests in `tests/core/test_preserve.py` (rewritten): the oldest-prefix post-processing remains as a safety net. Tests verify that with manually gapped snapshot sets (gaps created by simulated manual deletion), the post-processing still identifies the correct contiguous oldest prefix for blockcommit.

- **[Risk: Old JSON state files with `bucket_level` cause crashes]** → Test `test_old_json_bucket_level_read_tolerant` in `tests/models/test_results.py` and test `test_old_json_bucket_level_loaded` in `tests/models/test_results.py` verify `JsonStateManager` reads entries with `bucket_level` field without crashing. When old JSON has `"bucket_level": "monthly"`, the field is silently ignored.

- **[Risk: Old TOML configs with removed fields cause confusion]** → Test `test_full_every_deprecation_warning` in `tests/config/test_facade.py` verifies deprecation WARNING is logged for removed fields (`snapshot_preserve`, `target_preserve`, `full_every`, `full_compress`, `rate_limit`, `incremental_mode`, `copy_base`). Unit tests for `ConfigFacade` validation (`test_zero_chain_length_rejected`, `test_negative_keep_generations_rejected`) verify clear error messages for invalid count-based config.

- **[Risk: `chain_length=0` acts as a wildcard that deletes everything]** → Test `test_chain_length_zero_removes_all` in `tests/modules/retention/test_time_based.py` verifies the explicit behavior: when `chain_length=0`, all items are marked for removal. This is the expected behavior for unset `snapshot_chain_length` (which maps to `0`). The config-layer validation (`chain_length >= 1`) only applies when the field is set, not when it is `None`/unset — the docstring on `RetentionPolicy` clarifies that `0` means "keep nothing."

- **[Trade-off: No time-based retention at all]** → Covered by the entire test suite for count-based retention. There are no regression tests for time-based behavior because time-based is intentionally removed (non-goal: no time-based retention fallback). Documentation notes that users wanting "keep 7 days" with hourly snapshots must compute `chain_length=168` themselves.
