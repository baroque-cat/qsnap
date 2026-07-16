# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| config-model | TargetConfig dataclass | TargetConfig with incremental enabled | `tests/config/test_model.py` | `test_target_config_incremental` (MODIFY) | config-model-unit |
| config-model | GlobalConfig dataclass | GlobalConfig default values | `tests/config/test_model.py` | `test_global_config_defaults` (MODIFY) | config-model-unit |
| config-model | TargetConfig compress field | Default compress is true | `tests/config/test_model.py` | `test_target_config_compress_default_true` (NEW) | config-model-unit |
| config-model | TargetConfig compress field | Explicit compress disabled | `tests/config/test_model.py` | `test_target_config_compress_explicit_false` (NEW) | config-model-unit |
| config-model | TargetConfig copy_base field | Default copy_base is false | `tests/config/test_model.py` | `test_target_config_copy_base_default_false` (NEW) | config-model-unit |
| config-model | TargetConfig copy_base field | Explicit copy_base enabled | `tests/config/test_model.py` | `test_target_config_copy_base_explicit_true` (NEW) | config-model-unit |
| config-model | GlobalConfig compress field | Global compress default | `tests/config/test_model.py` | `test_global_config_compress_default_true` (NEW) | config-model-unit |
| config-model | GlobalConfig compress field | Target inherits compress from global | `tests/config/test_model.py` | `test_target_inherits_compress_from_global` (NEW) | config-model-unit |
| config-parsing | ConfigFacade parses new fault-tolerance fields | Global safety fields parsed | `tests/config/test_parser.py` | `test_config_parser_reads_auto_cleanup_state_backup_count` (NEW) | config-parsing-unit |
| config-parsing | ConfigFacade parses new fault-tolerance fields | Target compress and copy_base parsed | `tests/config/test_parser.py` | `test_parse_target_compress_and_copy_base` (NEW) | config-parsing-unit |
| config-parsing | ConfigFacade parses new fault-tolerance fields | full_every in config triggers deprecation warning | `tests/config/test_parser.py` | `test_full_every_deprecation_warning` (NEW) | config-parsing-unit |
| config-parsing | ConfigFacade parses new fault-tolerance fields | full_compress mapped to compress | `tests/config/test_parser.py` | `test_full_compress_mapped_to_compress_with_warning` (NEW) | config-parsing-unit |
| config-parsing | ConfigFacade updates example config | Example config is parseable with all fields documented | `tests/config/test_facade.py` | `test_example_config_parseable_all_fields` (NEW) | config-parsing-unit |
| config-parsing | Config validation forbids preserve_min without buckets | preserve_min without buckets rejected | `tests/config/test_facade.py` | `test_preserve_min_without_buckets_rejected` (NEW) | config-parsing-unit |
| config-parsing | Config validation forbids preserve_min without buckets | preserve_min=all without buckets allowed | `tests/config/test_facade.py` | `test_preserve_min_all_without_buckets_allowed` (NEW) | config-parsing-unit |
| config-parsing | Config validation forbids preserve_min without buckets | preserve_min with buckets allowed | `tests/config/test_facade.py` | `test_preserve_min_with_buckets_allowed` (NEW) | config-parsing-unit |
| backup-provider | Transfer missing snapshots to backup target | New snapshot copied to empty target via rsync | `tests/modules/backup/test_copy.py` | `test_transfer_missing_new_snapshot_rsync_empty_target` (NEW) | backup-provider-unit |
| backup-provider | Transfer missing snapshots to backup target | Transfer with rate limit uses rsync --bwlimit | `tests/modules/backup/test_copy.py` | `test_transfer_with_rate_limit_uses_rsync` (MODIFY) | backup-provider-unit |
| backup-provider | Transfer missing snapshots to backup target | Snapshot already exists on target — skipped | `tests/modules/backup/test_copy.py` | `test_transfer_missing_existing_snapshot_skipped` (MODIFY) | backup-provider-unit |
| backup-provider | Transfer missing snapshots to backup target | Incremental backup — rebase backing path | `tests/modules/backup/test_copy.py` | `test_transfer_incremental_rebase_backing_path` (MODIFY) | backup-provider-unit |
| backup-provider | Transfer missing snapshots to backup target | Rebase to FULL anchor when present | `tests/modules/backup/test_copy.py` | `test_transfer_missing_rebases_to_full_anchor` (MODIFY) | backup-provider-unit |
| backup-provider | Transfer missing snapshots to backup target | No FULL anchor preserves existing behavior | `tests/modules/backup/test_copy.py` | `test_transfer_missing_no_full_anchor_uses_source_backing` (MODIFY) | backup-provider-unit |
| backup-provider | Transfer missing snapshots to backup target | Non-incremental backup — no rebase | `tests/modules/backup/test_copy.py` | `test_transfer_non_incremental_no_rebase` (MODIFY) | backup-provider-unit |
| backup-provider | Transfer missing snapshots to backup target | rsync unavailable — transfer fails | `tests/modules/backup/test_copy.py` | `test_rsync_unavailable_transfer_fails_no_cp_fallback` (NEW) | backup-provider-unit |
| backup-provider | Transfer missing snapshots to backup target | Copy fails — disk full or permission error | `tests/modules/backup/test_copy.py` | `test_transfer_rsync_fails_disk_full` (NEW) | backup-provider-unit |
| backup-provider | Transfer missing snapshots to backup target | copy_base=false prevents base.qcow2 duplication | `tests/modules/backup/test_copy.py` | `test_copy_base_false_prevents_base_copy` (NEW) | backup-provider-unit |
| backup-provider | Transfer missing snapshots to backup target | copy_base=true allows legacy base copy | `tests/modules/backup/test_copy.py` | `test_copy_base_true_allows_base_copy` (NEW) | backup-provider-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | Uncompressed full backup succeeds | `tests/modules/backup/test_copy.py` | `test_create_full_backup_uncompressed` (MODIFY) | backup-provider-unit |
| backup-provider | FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target | Compressed full backup succeeds | `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed` (MODIFY) | backup-provider-unit |
| periodic-full-backup | FileCopyBackupProvider creates full backups via qemu-img convert | Uncompressed full backup | `tests/modules/backup/test_copy.py` | `test_create_full_backup_uncompressed` (MODIFY) | backup-provider-unit |
| periodic-full-backup | FileCopyBackupProvider creates full backups via qemu-img convert | Compressed full backup | `tests/modules/backup/test_copy.py` | `test_create_full_backup_compressed` (MODIFY) | backup-provider-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | First backup to target creates FULL | `tests/core/test_pipeline.py` | `test_first_backup_creates_full_via_bucket` (NEW) | core-bucket-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | New monthly period triggers FULL | `tests/core/test_pipeline.py` | `test_new_monthly_period_triggers_full` (NEW) | core-bucket-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | Same bucket period skips FULL | `tests/core/test_pipeline.py` | `test_same_bucket_period_skips_full` (NEW) | core-bucket-unit |
| periodic-full-backup | Core triggers full backup before incremental transfer | Policy with no buckets and preserve_min=all | `tests/core/test_pipeline.py` | `test_no_buckets_preserve_min_all_no_full_created` (NEW) | core-bucket-unit |
| periodic-full-backup | Incremental backups rebase to the FULL anchor | New incremental rebased to FULL | `tests/modules/backup/test_copy.py` | `test_transfer_missing_rebases_to_full_anchor` (MODIFY) | backup-provider-unit |
| periodic-full-backup | Incremental backups rebase to the FULL anchor | No FULL anchor uses source backing | `tests/modules/backup/test_copy.py` | `test_transfer_missing_no_full_anchor_uses_source_backing` (MODIFY) | backup-provider-unit |
| periodic-full-backup | IStateManager tracks full backups per target | Full backup recorded and retrieved | `tests/state/test_manager.py` | `test_record_and_get_full_backups` (NEW) | state-manager-unit |
| periodic-full-backup | Bucket-driven FULL creation logic | Highest bucket is yearly | `tests/core/test_pipeline.py` | `test_should_create_bucket_full_highest_yearly` (NEW) | core-bucket-unit |
| periodic-full-backup | Bucket-driven FULL creation logic | Highest bucket is daily | `tests/core/test_pipeline.py` | `test_should_create_bucket_full_highest_daily` (NEW) | core-bucket-unit |
| periodic-full-backup | Bucket-driven FULL creation logic | No active buckets | `tests/core/test_pipeline.py` | `test_should_create_bucket_full_no_active_buckets` (NEW) | core-bucket-unit |
| cascade-deletion | IStateManager tracks multiple FULLs per target | Multiple FULLs tracked per target | `tests/state/test_manager.py` | `test_multiple_fulls_tracked_per_target` (NEW) | state-manager-unit |
| cascade-deletion | IStateManager tracks multiple FULLs per target | FULL recorded with bucket level | `tests/state/test_manager.py` | `test_full_recorded_with_bucket_level` (NEW) | state-manager-unit |
| cascade-deletion | IStateManager tracks incremental-to-FULL dependencies | Dependency recorded after rebase | `tests/state/test_manager.py` | `test_dependency_recorded_after_rebase` (NEW) | state-manager-unit |
| cascade-deletion | IStateManager tracks incremental-to-FULL dependencies | Multiple incrementals depend on same FULL | `tests/state/test_manager.py` | `test_multiple_incrementals_depend_on_same_full` (NEW) | state-manager-unit |
| cascade-deletion | Core prevents deletion of FULLs with active dependents | FULL kept due to active dependent | `tests/core/test_pipeline.py` | `test_full_kept_due_to_active_dependent` (NEW) | core-cascade-unit |
| cascade-deletion | Core prevents deletion of FULLs with active dependents | FULL deleted when no active dependents | `tests/core/test_pipeline.py` | `test_full_deleted_when_no_active_dependents` (NEW) | core-cascade-unit |
| cascade-deletion | Cascade deletion of orphaned incrementals | Orphaned incrementals cascade-deleted | `tests/core/test_pipeline.py` | `test_orphaned_incrementals_cascade_deleted` (NEW) | core-cascade-unit |
| cascade-deletion | Cascade deletion of orphaned incrementals | Kept incremental rebased to new anchor | `tests/core/test_pipeline.py` | `test_kept_incremental_rebased_to_new_anchor` (NEW) | core-cascade-unit |
| cascade-deletion | _full_backups.json format migration | Old format auto-migrated | `tests/state/test_manager.py` | `test_full_backups_json_old_format_auto_migrated` (NEW) | state-manager-unit |
| cascade-deletion | _full_backups.json format migration | New format loaded as-is | `tests/state/test_manager.py` | `test_full_backups_json_new_format_loaded_as_is` (NEW) | state-manager-unit |
| retention-engine | Dependency-aware deletion is handled by Core, not retention engine | Retention engine returns pure keep/remove | `tests/modules/retention/test_time_based.py` | `test_retention_engine_returns_pure_keep_remove` (NEW) | retention-engine-unit |
| retention-engine | Dependency-aware deletion is handled by Core, not retention engine | Core post-processes retention result for dependencies | `tests/core/test_pipeline.py` | `test_core_post_processes_retention_for_dependencies` (NEW) | core-cascade-unit |
| env-validation | Pre-flight rsync availability check | Rsync available — validation passes | `tests/core/test_validation.py` | `test_rsync_available_validation_passes` (MODIFY) | core-validation-unit |
| env-validation | Pre-flight rsync availability check | Rsync unavailable — pipeline aborts | `tests/core/test_validation.py` | `test_rsync_unavailable_pipeline_aborts` (NEW) | core-validation-unit |
| env-validation | Pre-flight rsync availability check | Rsync check always runs | `tests/core/test_validation.py` | `test_rsync_check_always_runs_regardless_of_rate_limit` (NEW) | core-validation-unit |
| size-estimation | Core logs size estimation on every pipeline run | Size estimation logged during normal run | `tests/core/test_engine.py` | `test_size_estimation_logged_during_normal_run` (NEW) | core-bucket-unit |
| size-estimation | Core logs size estimation on every pipeline run | Size estimation logged during dry-run | `tests/core/test_engine.py` | `test_size_estimation_logged_during_dry_run` (NEW) | core-bucket-unit |
| size-estimation | Core logs size estimation on every pipeline run | Size estimation with no state history | `tests/core/test_engine.py` | `test_size_estimation_no_state_history` (NEW) | core-bucket-unit |
| size-estimation | qsnap estimate CLI command | Estimate for specific VM | `tests/core/test_engine.py` | `test_estimate_method_for_specific_vm` (NEW) | core-bucket-unit |
| size-estimation | qsnap estimate CLI command | Estimate for all VMs | `tests/core/test_engine.py` | `test_estimate_method_for_all_vms` (NEW) | core-bucket-unit |
| size-estimation | Size estimation formula | Compressed FULL projection | `tests/core/test_engine.py` | `test_compressed_full_projection_30_percent` (NEW) | core-bucket-unit |
| size-estimation | Size estimation formula | Uncompressed FULL projection | `tests/core/test_engine.py` | `test_uncompressed_full_projection_100_percent` (NEW) | core-bucket-unit |
| size-estimation | Size estimation formula | Incremental size from state history | `tests/core/test_engine.py` | `test_incremental_size_rolling_average_from_state` (NEW) | core-bucket-unit |
| cli-interface | qsnap estimate subcommand | Estimate for specific VM | `tests/cli/test_commands.py` | `test_estimate_subcommand_specific_vm_dispatches` (NEW) | cli-interface-unit |
| cli-interface | qsnap estimate subcommand | Estimate for all VMs | `tests/cli/test_commands.py` | `test_estimate_subcommand_all_vms_dispatches` (NEW) | cli-interface-unit |
| cli-interface | qsnap estimate subcommand | Estimate respects --format flag | `tests/cli/test_commands.py` | `test_estimate_subcommand_respects_format_flag` (NEW) | cli-interface-unit |
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Empty state produces meaningful simulation | `tests/core/test_schedule_summary.py` | `test_schedule_summary_empty_state_produces_simulation` (MODIFY) | core-schedule-unit |
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Summary logs at INFO on every timer invocation | `tests/core/test_schedule_summary.py` | `test_schedule_summary_logs_info_on_timer` (MODIFY) | core-schedule-unit |
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Summary shows snapshot and backup breakdown with size estimates | `tests/core/test_schedule_summary.py` | `test_schedule_summary_shows_snapshot_and_backup_breakdown` (MODIFY) | core-schedule-unit |
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Summary includes real base image size | `tests/core/test_schedule_summary.py` | `test_schedule_summary_includes_base_image_size` (NEW) | core-schedule-unit |
| schedule-summary | Core.schedule_summary produces a human-readable retention preview | Summary includes average incremental size from history | `tests/core/test_schedule_summary.py` | `test_schedule_summary_includes_avg_incremental_size` (NEW) | core-schedule-unit |
| restore-command | Restore command copies backup chain to target directory | Restore a file-copy backup chain with FULL anchor | `tests/core/test_engine.py` | `test_core_restore_from_backup_returns_restore_result` (MODIFY) | core-bucket-unit |
| restore-command | Restore command copies backup chain to target directory | Restore a nonexistent backup | `tests/cli/test_commands.py` | `test_handle_restore_nonexistent_backup_returns_exit_1` (MODIFY) | cli-interface-unit |
| restore-command | Restore command copies backup chain to target directory | Target directory does not exist | `tests/cli/test_commands.py` | `test_handle_restore_missing_target_dir_returns_exit_1` (MODIFY) | cli-interface-unit |

## Delegation Groups

### Group: config-model-unit

**Scope:** `tests/config/test_model.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | TargetConfig with incremental enabled, GlobalConfig default values, Default compress is true, Explicit compress disabled, Default copy_base is false, Explicit copy_base enabled, Global compress default, Target inherits compress from global | MODIFY / NEW |

### Group: config-parsing-unit

**Scope:** `tests/config/test_parser.py`, `tests/config/test_facade.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_parser.py` | Global safety fields parsed, Target compress and copy_base parsed, full_every deprecation warning, full_compress mapped to compress | NEW |
| `tests/config/test_facade.py` | Example config parseable, preserve_min without buckets rejected, preserve_min=all without buckets allowed, preserve_min with buckets allowed | NEW |

### Group: backup-provider-unit

**Scope:** `tests/modules/backup/test_copy.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/backup/test_copy.py` | New snapshot via rsync, rate-limit rsync, snapshot skipped, incremental rebase, FULL anchor rebase, no-FULL-anchor rebase, non-incremental no-rebase, rsync unavailable fails, rsync disk-full error, copy_base=false blocks base, copy_base=true legacy, uncompressed full backup, compressed full backup | MODIFY / NEW |

### Group: state-manager-unit

**Scope:** `tests/state/test_manager.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/state/test_manager.py` | Full backup recorded and retrieved, Multiple FULLs tracked per target, FULL recorded with bucket level, Dependency recorded after rebase, Multiple incrementals depend on same FULL, Old format auto-migrated, New format loaded as-is | NEW |

### Group: core-bucket-unit

**Scope:** `tests/core/test_engine.py` (size estimation + estimate method)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_engine.py` | Size estimation logged during normal run, Size estimation logged during dry-run, Size estimation with no state history, Estimate for specific VM, Estimate for all VMs, Compressed FULL projection, Uncompressed FULL projection, Incremental size rolling average, Restore backup chain with FULL anchor | NEW / MODIFY |
| `tests/core/test_pipeline.py` | First backup creates FULL via bucket, New monthly period triggers FULL, Same bucket period skips FULL, No buckets preserve_min=all no FULL, Bucket highest is yearly, Bucket highest is daily, No active buckets | NEW |

### Group: core-cascade-unit

**Scope:** `tests/core/test_pipeline.py` (cascade-deletion scenarios)

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_pipeline.py` | FULL kept due to active dependent, FULL deleted when no active dependents, Orphaned incrementals cascade-deleted, Kept incremental rebased to new anchor, Core post-processes retention for dependencies | NEW |

### Group: core-validation-unit

**Scope:** `tests/core/test_validation.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_validation.py` | Rsync available — validation passes, Rsync unavailable — pipeline aborts, Rsync check always runs regardless of rate_limit | MODIFY / NEW |

### Group: core-schedule-unit

**Scope:** `tests/core/test_schedule_summary.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_schedule_summary.py` | Empty state simulation, Summary logs at INFO, Snapshot and backup breakdown with size estimates, Includes base image size, Includes average incremental size | MODIFY / NEW |

### Group: retention-engine-unit

**Scope:** `tests/modules/retention/test_time_based.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/retention/test_time_based.py` | Retention engine returns pure keep/remove | NEW |

### Group: cli-interface-unit

**Scope:** `tests/cli/test_commands.py`, `tests/cli/test_app.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/cli/test_commands.py` | Estimate for specific VM, Estimate for all VMs, Estimate respects --format flag, Restore nonexistent backup, Target directory does not exist | NEW / MODIFY |
| `tests/cli/test_app.py` | `estimate` subcommand registered in argparser | NEW |

### Group: interfaces-contract

**Scope:** `tests/interfaces/test_state_manager.py`, `tests/interfaces/test_backup_provider.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/interfaces/test_state_manager.py` | IStateManager declares `get_full_backups`, `record_full_backup`, `record_incremental_dependency`, `get_incremental_dependencies` as abstract | MODIFY |
| `tests/interfaces/test_backup_provider.py` | IBackupProvider declares `create_full_backup` with `bucket_level` parameter | MODIFY |

### Group: mocks-unit

**Scope:** `tests/mocks/mock_state.py`, `tests/mocks/test_mock_state.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/mocks/mock_state.py` | Add `get_full_backups`, `record_full_backup`, `record_incremental_dependency`, `get_incremental_dependencies` | MODIFY |
| `tests/mocks/test_mock_state.py` | Verify InMemoryStateManager implements all new IStateManager methods | NEW |

### Group: conftest-fixtures

**Scope:** `tests/conftest.py`, `tests/fixtures/configs/`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/conftest.py` | Add `compress` parameter to `make_global_config` fixture, add `compress` and `copy_base` to `make_target` fixture, ensure `which rsync` always succeeds in `mock_shell` | MODIFY |
| `tests/fixtures/configs/bucket_driven.toml` | TOML fixture with `compress`, `copy_base`, no `full_every` | NEW |
| `tests/fixtures/configs/deprecated_fields.toml` | TOML fixture with `full_every` and `full_compress` for deprecation test | NEW |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| `tests/config/test_model.py` | REMOVE `test_target_config_full_every_defaults_zero_d` and `test_target_config_full_compress_defaults_false` | `full_every` and `full_compress` fields are removed from `TargetConfig` (config-model REMOVED Requirement, design D1/D6) |
| `tests/config/test_model.py` | ADD `test_target_config_compress_default_true`, `test_target_config_compress_explicit_false`, `test_target_config_copy_base_default_false`, `test_target_config_copy_base_explicit_true`, `test_global_config_compress_default_true`, `test_target_inherits_compress_from_global` | New `compress` and `copy_base` fields on `TargetConfig` and `GlobalConfig` (config-model ADDED Requirements) |
| `tests/config/test_model.py` | MODIFY `test_target_config_incremental` to verify `compress` and `copy_base` defaults | TargetConfig dataclass now includes new fields (config-model MODIFIED TargetConfig dataclass) |
| `tests/config/test_model.py` | MODIFY `test_global_config_defaults` to assert `compress=True` | GlobalConfig gains `compress` field defaulting to `True` (config-model ADDED GlobalConfig compress field) |
| `tests/config/test_parser.py` | ADD tests for `full_every` deprecation, `full_compress`→`compress` mapping, `compress`/`copy_base` parsing, `auto_cleanup`/`state_backup_count` global fields | ConfigFacade must parse new fields and handle deprecated ones (config-parsing MODIFIED Requirement, design D6) |
| `tests/config/test_facade.py` | ADD config validation tests for `preserve_min` without buckets | New config validation rule: all-zero buckets require `preserve_min="all"` (config-parsing ADDED Requirement) |
| `tests/modules/backup/test_copy.py` | REMOVE all `cp`-based transfer tests (`test_transfer*` using `cp` expectations) | `cp` fallback is removed; only `rsync` transfers (backup-provider REMOVED Requirement, design D3) |
| `tests/modules/backup/test_copy.py` | REWRITE transfer tests to use `rsync` exclusively with `--partial` | `rsync` is the sole transfer mechanism (backup-provider MODIFIED transfer_req) |
| `tests/modules/backup/test_copy.py` | ADD `test_rsync_unavailable_transfer_fails_no_cp_fallback` | `rsync` is a hard requirement; no `cp` fallback (backup-provider rsync-unavailable scenario, design D3) |
| `tests/modules/backup/test_copy.py` | MODIFY `test_create_full_backup_uncompressed` and `test_create_full_backup_compressed` to accept and pass `bucket_level` parameter | `create_full_backup` gains `bucket_level` parameter (periodic-full-backup MODIFIED Requirement, design D1) |
| `tests/modules/backup/test_copy.py` | ADD `test_copy_base_false_prevents_base_copy` and `test_copy_base_true_allows_base_copy` | `copy_base` default changes to `False` — first backup is always FULL (backup-provider copy_base scenarios, design D4) |
| `tests/core/test_pipeline.py` | REMOVE `test_backup_target_first_run_creates_full_backup` and `test_backup_target_interval_not_elapsed_skips_full` | These tests exercise `full_every` interval logic which is removed (periodic-full-backup REMOVED, design D1) |
| `tests/core/test_pipeline.py` | ADD bucket-driven FULL tests: `test_first_backup_creates_full_via_bucket`, `test_new_monthly_period_triggers_full`, `test_same_bucket_period_skips_full`, `test_should_create_bucket_full_highest_yearly`, `test_should_create_bucket_full_highest_daily`, `test_should_create_bucket_full_no_active_buckets`, `test_no_buckets_preserve_min_all_no_full_created` | Bucket-driven FULL creation replaces `full_every` (periodic-full-backup ADDED Requirement, design D1) |
| `tests/core/test_pipeline.py` | ADD cascade deletion tests: `test_full_kept_due_to_active_dependent`, `test_full_deleted_when_no_active_dependents`, `test_orphaned_incrementals_cascade_deleted`, `test_kept_incremental_rebased_to_new_anchor`, `test_core_post_processes_retention_for_dependencies` | Dependency-aware cascade deletion is new (cascade-deletion ADDED Requirements, design D2) |
| `tests/core/test_validation.py` | REWRITE `test_rsync_unavailable_warning` to `test_rsync_unavailable_pipeline_aborts` (hard error, not warning) | `rsync` check becomes blocking; pipeline must abort if `rsync` not found (env-validation MODIFIED Requirement, design D3) |
| `tests/core/test_validation.py` | REWRITE `test_rsync_check_skipped_when_rate_limit_no` to `test_rsync_check_always_runs_regardless_of_rate_limit` | `which rsync` must always run, regardless of rate_limit (env-validation rsync-check-always-runs scenario) |
| `tests/core/test_engine.py` | ADD eight size estimation tests and `estimate()` method tests | New size estimation logging and `Core.estimate()` method (size-estimation ADDED Requirements, design D5) |
| `tests/core/test_engine.py` | MODIFY `test_core_restore_from_backup_returns_restore_result` to resolve chain through FULL anchors | Restore must work with bucket-driven FULL anchors (restore-command MODIFIED Requirement) |
| `tests/core/test_schedule_summary.py` | ADD `test_schedule_summary_includes_base_image_size` and `test_schedule_summary_includes_avg_incremental_size` | `schedule_summary` must include size projections (schedule-summary MODIFIED Requirement, design D5) |
| `tests/state/test_manager.py` | ADD `test_record_and_get_full_backups`, `test_multiple_fulls_tracked_per_target`, `test_full_recorded_with_bucket_level`, `test_dependency_recorded_after_rebase`, `test_multiple_incrementals_depend_on_same_full`, `test_full_backups_json_old_format_auto_migrated`, `test_full_backups_json_new_format_loaded_as_is` | `IStateManager` gains multi-FULL tracking and dependency tracking (cascade-deletion ADDED Requirements, design D2) |
| `tests/mocks/mock_state.py` | ADD `get_full_backups`, `record_full_backup`, `record_incremental_dependency`, `get_incremental_dependencies` methods; change `_full_backups` from `dict[str, FullBackupInfo]` to `dict[str, list[FullBackupInfo]]` | `IStateManager` interface expands to multi-FULL + dependency tracking (cascade-deletion ADDED Requirements, design D2) |
| `tests/mocks/test_mock_state.py` | ADD tests verifying new methods on `InMemoryStateManager` | Contract: mock must implement all ABC methods |
| `tests/conftest.py` | ADD `compress` kwarg to `make_global_config`; ADD `compress` and `copy_base` kwargs to `make_target` | Fixtures must match new dataclass fields (config-model ADDED Requirements) |
| `tests/interfaces/test_state_manager.py` | ADD contract tests asserting `get_full_backups`, `record_full_backup`, `record_incremental_dependency`, `get_incremental_dependencies` are abstract on `IStateManager` | Contract: every ABC method must be abstract |
| `tests/interfaces/test_backup_provider.py` | ADD contract test verifying `create_full_backup` signature includes `bucket_level` parameter | `IBackupProvider.create_full_backup` gains `bucket_level` parameter (periodic-full-backup MODIFIED Requirement) |
| `tests/cli/test_commands.py` | ADD `test_estimate_subcommand_specific_vm_dispatches`, `test_estimate_subcommand_all_vms_dispatches`, `test_estimate_subcommand_respects_format_flag` | New `qsnap estimate` CLI subcommand (cli-interface ADDED Requirement, design D5) |
| `tests/cli/test_app.py` | ADD test that `estimate` subcommand is registered in argparser with optional VM positional arg | New CLI subcommand must be wired through argparser (cli-interface ADDED Requirement) |

## Risks & Edge Cases

- **[Cascade deletion removes needed data]** Design mitigation: `--preserve` flag, dry-run logging. → Tests: `test_full_kept_due_to_active_dependent` (FULL ghost-retained), `test_orphaned_incrementals_cascade_deleted` (only orphaned, not kept), `test_preserve_flag_prevents_all_deletion` (dry-run shows planned deletions without executing them)
- **[`_full_backups.json` format migration]** Old format is `{target_path: {name, timestamp}}`, new format is `{target_path: [{name, timestamp, bucket_level}]}`. → Tests: `test_full_backups_json_old_format_auto_migrated`, `test_full_backups_json_new_format_loaded_as_is`
- **[rsync unavailable on minimal systems]** Hard error in `_validate_environment()` with clear message. → Tests: `test_rsync_unavailable_pipeline_aborts` (validation fails, pipeline does not proceed), `test_rsync_unavailable_transfer_fails_no_cp_fallback` (backup provider returns `BackupResult(success=False)`)
- **[Size estimation inaccuracy]** Logs as "approximate", uses rolling average. → Tests: `test_size_estimation_no_state_history` (edge case: no historical data, uses base image size), `test_incremental_size_rolling_average_from_state` (verifies formula correctness)
- **[Bucket boundary edge cases]** FULLs created at year/month/week/day/hour boundaries. → Tests: `test_should_create_bucket_full_highest_yearly` (timestamp at Dec 31 vs Jan 1), `test_should_create_bucket_full_highest_daily` (timestamp at day boundary), `test_new_monthly_period_triggers_full` (month-boundary timestamp)
- **[`copy_base=false` breaks existing targets with base.qcow2]** Mitigation: WARNING log, don't delete. → Tests: `test_existing_base_qcow2_on_target_logs_warning` (detection and WARNING), `test_copy_base_false_prevents_base_copy` (new targets never copy base)
- **[Breaking change for users with `full_every` in config]** Deprecation WARNING, value ignored. → Tests: `test_full_every_deprecation_warning`, `test_full_compress_mapped_to_compress_with_warning`, `test_full_every_ignored_in_behavior` (config with `full_every` runs but uses bucket-driven logic)
- **[All buckets zero + preserve_min != "all"]** Config validation rejects it. → Tests: `test_preserve_min_without_buckets_rejected` (raises ConfigError), `test_preserve_min_all_without_buckets_allowed` (no error)
- **[No FULL anchor exists for kept incremental]** Logs WARNING, incremental flagged. → Tests: `test_kept_incremental_rebased_to_new_anchor` (rebases when anchor exists), `test_kept_incremental_no_anchor_logs_warning` (logs when no anchor available)
- **[Bucket_level tracking in state]** FULLs must record which bucket level triggered them. → Tests: `test_full_recorded_with_bucket_level` (monthly in state), `test_create_full_backup_uncompressed` (verifies bucket_level="monthly" passed to state)
- **[Incremental re-rebasing on FULL anchor change]** When a new FULL is created, existing incrementals may need to rebase to it. → Tests: `test_existing_incrementals_rebase_to_new_full_anchor` (after new FULL, incrementals point to newest FULL)
- **[Atomic FULL creation]** Use `.tmp` + rename pattern. → Tests: `test_create_full_backup_atomic_rename` (converts to .tmp, renames on success; .tmp cleaned up on failure)
